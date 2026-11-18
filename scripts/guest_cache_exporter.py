"""Publish kernel cache metadata on host request in private anonymous memory."""
import ctypes
import json
import mmap
import os
from pathlib import Path
import struct
import time

from kernel_cache_metadata import decode, decode_header

PAGE = 4096
CAPACITY = 16 * 2**20 + PAGE  # all cache records in a 4 GiB guest, plus header
REQUEST = 128
PUBLICATION = 136
CONTROL = struct.Struct('<9Q')
STOP = 1 << 63


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sandbox_id')
    parser.add_argument('directory', type=Path)
    parser.add_argument('--file-cache-path', type=Path,
                        help='Explicit one-file query plus read-only DAMON sidecars; needs extended guest kernel')
    args = parser.parse_args()
    sid, directory = args.sandbox_id, args.directory
    assert directory.parent == Path('/dev/shm') and directory.name.startswith('asb-export-')
    directory.mkdir(exist_ok=False)
    if args.file_cache_path is not None:
        assert args.file_cache_path.is_absolute()
        # Unsupported kernels fail before readiness, even if the target is not yet created.
        assert Path('/proc/asb_filecache').is_file(), 'named-file kernel query unavailable'
        from guest_file_cache_query import capture
        sidecars = directory/'file-query'
        sidecars.mkdir()
    memory = mmap.mmap(-1, PAGE + CAPACITY, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    address = ctypes.addressof(ctypes.c_char.from_buffer(memory))
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mlock.argtypes = libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    locked = False
    try:
        # Only collector memory is faulted/locked; never task file pages.
        memory[:] = b'\0' * len(memory)
        assert libc.mlock(address, len(memory)) == 0, 'metadata mlock failed'
        locked = True
        text = ('ASB-EXPORT:' + sid).encode()
        memory[:len(text)] = text
        with open('/proc/self/pagemap', 'rb', buffering=0) as pm:
            pfns = [struct.unpack('<Q', os.pread(pm.fileno(), 8, (address // PAGE + i) * 8))[0]
                    & ((1 << 55) - 1) for i in range(len(memory) // PAGE)]
        assert all(pfns) and len(set(pfns)) == len(pfns)
        # Locked anonymous pages retain their identity; check their exclusion
        # once. The host validates every complete publication independently.
        _, initial_entries = decode(Path('/proc/asb_pagecache').read_bytes())
        assert not set(pfns).intersection(initial_entries), 'anonymous exporter counted as cache'
        del initial_entries
        bootstrap = dict(sandbox_id=sid, text=text.decode(), pfn=pfns[0], export_pfns=pfns,
                         locked_anonymous_bytes=len(memory), capacity_bytes=CAPACITY,
                         iomem=Path('/proc/iomem').read_text(), kernel=os.uname().release,
                         pid=os.getpid(), boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        if args.file_cache_path is not None:
            bootstrap['file_cache_query'] = dict(version=1, path=str(args.file_cache_path),
                endpoint='/proc/asb_filecache', sidecar_directory=str(sidecars))
        (directory/'bootstrap.json').write_text(json.dumps(bootstrap) + '\n')
        previous = count = cpu_sum = 0
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            request = struct.unpack_from('<Q', memory, REQUEST)[0]
            if request & STOP:
                break
            if request and request != previous:
                count += 1
                # Odd sequence invalidates the old publication before copying.
                struct.pack_into('<Q', memory, PUBLICATION + 8, count * 2 - 1)
                cpu_start = time.process_time_ns()
                started = time.monotonic_ns()
                if args.file_cache_path is not None:
                    capture(sidecars, count, args.file_cache_path, sid, bootstrap['boot_id'])
                raw = Path('/proc/asb_pagecache').read_bytes()
                decode_header(raw)
                assert len(raw) <= CAPACITY, 'guest metadata exceeds export capacity'
                counters = {p[0].rstrip(':'): int(p[1]) * 1024
                            for line in Path('/proc/meminfo').read_text().splitlines()
                            if len(p := line.split()) == 3 and p[2] == 'kB'}
                memory[PAGE:PAGE+len(raw)] = raw
                ended = time.monotonic_ns()
                cpu = time.process_time_ns() - cpu_start
                cpu_sum += cpu
                CONTROL.pack_into(memory, PUBLICATION, request, count * 2 - 1, len(raw),
                                  started, ended, counters['Cached'], counters['Shmem'],
                                  counters['Buffers'], cpu)
                struct.pack_into('<Q', memory, PUBLICATION + 8, count * 2)
                previous = request
            time.sleep(.02)
        else:
            raise TimeoutError('host did not release guest exporter')
        (directory/'result.json').write_text(json.dumps(dict(status='PASS', publications=count,
            publication_cpu_ns=cpu_sum, locked_anonymous_bytes=len(memory)))+'\n')
    except BaseException:
        import traceback
        (directory/'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        if locked:
            libc.munlock(address, len(memory))
        memory.close()
    (directory/'done').touch()


if __name__ == '__main__':
    main()
