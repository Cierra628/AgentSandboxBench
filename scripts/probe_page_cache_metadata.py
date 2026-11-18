"""Calibrate an event-maintained page table on one owned host file, without VM pause.

This is a host method check, not an AgentENV guest collector. Raw PFNs stay local.
Only the newly created fixture receives POSIX_FADV_DONTNEED; no global cache drop.
"""
import argparse
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

PAGE = 4096
SIZE = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


class PageCacheTable:
    """Track complete add/delete events from an initially empty declared file."""
    def __init__(self):
        self.pages = {}
        self.added_bytes = self.deleted_bytes = self.peak_bytes = 0

    def apply(self, operation, pfn, device, inode, index, order):
        if operation not in ('ADD', 'DELETE') or min(pfn, device, inode, index, order) < 0 or order > 20:
            raise ValueError('invalid page-cache event')
        row = dict(device=device, inode=inode, index=index, order=order)
        size = PAGE << order
        if operation == 'ADD':
            if pfn in self.pages:
                raise ValueError('PFN added again before removal')
            self.pages[pfn] = row
            self.added_bytes += size
        else:
            if self.pages.get(pfn) != row:
                raise ValueError('removal has no matching live page identity')
            del self.pages[pfn]
            self.deleted_bytes += size
        current = self.added_bytes - self.deleted_bytes
        self.peak_bytes = max(self.peak_bytes, current)
        return current


def resident_bytes(fd):
    with mmap.mmap(fd, SIZE, access=mmap.ACCESS_COPY) as mapping:
        vector = (ctypes.c_ubyte * (SIZE // PAGE))()
        address = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        if libc.mincore(address, SIZE, vector):
            raise OSError(ctypes.get_errno(), 'mincore')
        return sum(value & 1 for value in vector) * PAGE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT / '.artifacts'):
        parser.error('output must be a new project .artifacts directory')
    if os.sysconf('SC_PAGE_SIZE') != PAGE:
        parser.error('this calibration requires 4096-byte base pages')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    fixture = out / 'owned-fixture.bin'
    result = dict(status='FAIL', scope='host owned 8 MiB regular file only; not AgentENV guest',
                  vm_pause=False, global_drop_caches=False, page_size=PAGE,
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  kernel=os.uname().release, observations=[])
    tracer = None
    reader = None
    fd = None
    stage = 'fixture'
    table = PageCacheTable()
    ready, lock, errors = threading.Event(), threading.Lock(), []
    try:
        fd = os.open(fixture, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.posix_fallocate(fd, 0, SIZE)
        info = os.fstat(fd)
        device = (os.major(info.st_dev) << 20) | os.minor(info.st_dev)
        result['fixture'] = dict(device=device, inode=info.st_ino, size_bytes=SIZE)
        assert resident_bytes(fd) == 0, 'new unwritten fixture was already cached'
        program = '''
BEGIN { printf("READY\\n"); }
tracepoint:filemap:mm_filemap_add_to_page_cache,
tracepoint:filemap:mm_filemap_delete_from_page_cache
/args->s_dev == DEVICE && args->i_ino == INODE/
{
  printf("%s %llu %lu %u %lu %lu %u\\n", probe, nsecs,
         args->pfn, args->s_dev, args->i_ino, args->index, args->order);
}
interval:s:30 { exit(); }
'''.replace('DEVICE', str(device)).replace('INODE', str(info.st_ino))
        (out / 'capture.bt').write_text(program)
        stage = 'attach'
        with (out / 'trace.stderr').open('w') as stderr:
            tracer = subprocess.Popen(['bpftrace', '-q', '-B', 'line', str(out / 'capture.bt')],
                                      stdout=subprocess.PIPE, stderr=stderr, text=True)

        def consume():
            with (out / 'events.tsv').open('x') as raw, (out / 'curve.tsv').open('x') as curve:
                curve.write('event_monotonic_ns\tresident_bytes\n')
                for line in tracer.stdout:
                    raw.write(line)
                    raw.flush()
                    if line.strip() == 'READY':
                        ready.set()
                        continue
                    parts = line.split()
                    if not parts or not parts[0].startswith('tracepoint:filemap:'):
                        continue
                    try:
                        if len(parts) != 7:
                            raise ValueError('unexpected trace event fields')
                        operation = 'ADD' if parts[0].endswith(':mm_filemap_add_to_page_cache') else 'DELETE'
                        timestamp, pfn, dev, ino, index, order = map(int, parts[1:])
                        if dev != device or ino != info.st_ino:
                            raise ValueError('event outside declared fixture')
                        with lock:
                            current = table.apply(operation, pfn, dev, ino, index, order)
                        curve.write(f'{timestamp}\t{current}\n')
                    except Exception as exc:
                        errors.append(str(exc))

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        deadline = time.monotonic() + 20
        while not ready.wait(.05):
            assert tracer.poll() is None, 'trace attach failed; inspect trace.stderr'
            assert time.monotonic() < deadline, 'trace readiness timeout'

        def observe(label, expected):
            deadline = time.monotonic() + 5
            while True:
                with lock:
                    current = table.added_bytes - table.deleted_bytes
                    metadata = [dict(pfn=pfn, **row) for pfn, row in table.pages.items()]
                if errors:
                    raise RuntimeError(errors[0])
                actual = resident_bytes(fd)
                if current == actual == expected:
                    break
                assert tracer.poll() is None, 'tracer stopped before observation'
                assert time.monotonic() < deadline, f'{label}: table={current}, mincore={actual}, expected={expected}'
                time.sleep(.05)
            (out / (label + '-metadata.json')).write_text(json.dumps(metadata) + '\n')
            result['observations'].append(dict(label=label, timestamp_ns=time.time_ns(),
                                               metadata_bytes=current, mincore_bytes=actual))

        stage = 'empty'
        observe(stage, 0)
        stage = 'read'
        remaining = SIZE
        while remaining:
            data = os.read(fd, min(1024 * 1024, remaining))
            assert data and not any(data), 'fixture read failed or content differs'
            remaining -= len(data)
        observe(stage, SIZE)
        stage = 'release'
        os.posix_fadvise(fd, 0, SIZE, os.POSIX_FADV_DONTNEED)
        observe(stage, 0)
        stage = 'stop'
        tracer.send_signal(signal.SIGINT)
        tracer.wait(timeout=10)
        reader.join(timeout=5)
        assert not reader.is_alive(), 'trace output reader did not finish'
        assert tracer.returncode == 0 and not errors, 'trace collection failed'
        assert not re.search(r'\b(lost|dropped)\b', (out / 'trace.stderr').read_text(), re.I), 'trace reports event loss'
        result.update(status='PASS', added_bytes=table.added_bytes, deleted_bytes=table.deleted_bytes,
                      observed_peak_bytes=table.peak_bytes, remaining_folios=len(table.pages),
                      interpretation='current cache membership, not cumulative allocated bytes',
                      limitations=['Known empty file at attachment; no existing-cache bootstrap.',
                                   'No forced migration, folio split, checkpoint or restore calibration.',
                                   'Per-event export overhead is not a production monitoring overhead measurement.',
                                   'No guest-to-host PFN mapping or cross-VM deduplication in this check.'])
    except Exception as exc:
        result.update(failed_stage=stage, error=str(exc))
    finally:
        if tracer is not None and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            try:
                tracer.wait(timeout=10)
            except subprocess.TimeoutExpired:
                tracer.kill()
                tracer.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=5)
        if fd is not None:
            os.close(fd)
        if fixture.exists():
            fixture.unlink()
        result['cleanup'] = dict(fixture_removed=not fixture.exists(), tracer_stopped=tracer is None or tracer.poll() is not None)
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(status=result['status'], failed_stage=result.get('failed_stage'), result_dir=str(out))))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
