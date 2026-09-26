"""Host-only live fixture for the senior PFN joiner; synthetic guest slots, no VM."""
import array
import contextlib
import ctypes
import hashlib
import importlib.util
import json
import mmap
import multiprocessing
import os
from pathlib import Path
import signal
import struct
import sys
import time
import traceback

PAGE = 4096
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/physical_cache.py'


def worker(connection, path, marker_text):
    mapped = marker = None
    try:
        with open(path, 'rb') as f:
            mapped = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY)
        marker = mmap.mmap(-1, PAGE)
        marker[:len(marker_text)] = marker_text.encode()
        # Read every page without writing the MAP_PRIVATE file mapping.
        sum(mapped[i] for i in range(0, len(mapped), PAGE))
        info = dict(pid=os.getpid(), address=ctypes.addressof(ctypes.c_char.from_buffer(mapped)),
                    marker_address=ctypes.addressof(ctypes.c_char.from_buffer(marker)),
                    marker_ascii=marker_text, size=len(mapped))
        connection.send(info)
        while connection.poll(120):
            command = connection.recv()
            if command == 'exit':
                break
            if command == 'cow':
                for i in range(0, len(mapped), 2 * PAGE):
                    mapped[i] ^= 1
            elif command != 'hash':
                raise ValueError(command)
            connection.send(hashlib.sha256(mapped).hexdigest())
    except Exception:
        connection.send({'error': traceback.format_exc()})
    finally:
        if mapped is not None:
            mapped.close()
        if marker is not None:
            marker.close()
        connection.close()


def receive(connection):
    if not connection.poll(15):
        raise TimeoutError('fixture worker did not respond')
    result = connection.recv()
    if isinstance(result, dict) and 'error' in result:
        raise RuntimeError(result['error'])
    return result


def main():
    mib = int(sys.argv[1])
    assert mib in (1, 16, 32)
    assert os.sysconf('SC_PAGE_SIZE') == PAGE, 'collector requires 4 KiB pages'
    os.umask(0o077)
    out = ROOT / '.artifacts/host-pfn-calibration' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    out.mkdir(parents=True)
    (out / 'collector-source.py').write_bytes(SOURCE.read_bytes())
    (out / 'driver-source.py').write_bytes(Path(__file__).read_bytes())
    config = dict(mib_per_mapping=mib, page_size=PAGE, kernel=os.uname().release,
                  collector_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  scope='host process fixture; synthetic guest PFNs/KVM slots; no guest, KVM or DAX validation')
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location('senior_physical_cache', SOURCE)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    count = mib * 1024 * 1024 // PAGE
    data = b''.join(struct.pack('<Q', i + 1) * (PAGE // 8) for i in range(count))
    original_sha = hashlib.sha256(data).hexdigest()
    altered = bytearray(data)
    for i in range(0, len(altered), 2 * PAGE):
        altered[i] ^= 1
    cow_sha = hashlib.sha256(altered).hexdigest()
    paths = [out / 'shared.bin', out / 'private-a.bin', out / 'private-b.bin']
    for path in paths:
        path.write_bytes(data)
    children = []
    cases = []
    status = 'FAIL'
    negative = {}
    cleanup = True

    def start(paths_to_map):
        ctx = multiprocessing.get_context('spawn')
        for index, path in enumerate(paths_to_map):
            parent, child = ctx.Pipe()
            process = ctx.Process(target=worker, args=(child, str(path), f'ASB-PFN-{os.getpid()}-{index}'))
            process.start()
            child.close()
            children.append((process, parent, None))
            children[-1] = (process, parent, receive(parent))

    def stop():
        nonlocal cleanup
        for process, connection, _ in children:
            try:
                connection.send('exit')
            except (BrokenPipeError, EOFError):
                pass
            process.join(3)
            if process.is_alive():
                process.terminate()
                process.join(3)
            if process.is_alive():
                process.kill()
                process.join(3)
            cleanup = cleanup and not process.is_alive() and process.exitcode == 0
            connection.close()
        children.clear()

    def scan(name, expected_unique, expected_intersection):
        case = out / name
        (case / 'host').mkdir(parents=True)
        slots = []
        pfn_sets = []
        for index, (_, connection, info) in enumerate(children):
            probe = case / 'host/probes' / str(index)
            probe.mkdir(parents=True)
            marker = dict(sandbox_id=f'fixture-{index}', marker_pfn=1, marker_ascii=info['marker_ascii'])
            (probe / 'marker.json').write_text(json.dumps(marker))
            (probe / 'file-pfns.bin').write_bytes(array.array('I', range(0x1000, 0x1000 + count)).tobytes())
            slots += [f"{info['pid']}\t0\t{PAGE:x}\t{PAGE:x}\t{info['marker_address']:x}",
                      f"{info['pid']}\t1\t{0x1000*PAGE:x}\t{info['size']:x}\t{info['address']:x}"]
            fd = os.open(f"/proc/{info['pid']}/pagemap", os.O_RDONLY)
            try:
                entries = os.pread(fd, count * 8, info['address'] // PAGE * 8)
            finally:
                os.close(fd)
            assert len(entries) == count * 8
            (probe / 'host-pagemap.bin').write_bytes(entries)
            pfns = []
            for (entry,) in struct.iter_unpack('Q', entries):
                assert entry & (1 << 63), 'nonresident fixture page'
                pfn = entry & ((1 << 55) - 1)
                assert pfn, 'PFNs hidden; physical dedup cannot be validated'
                pfns.append(pfn)
            pfn_sets.append(set(pfns))
            connection.send('hash')
            mapped_sha = receive(connection)
            assert mapped_sha == (cow_sha if name == 'cow' and index == 1 else original_sha)
            (probe / 'fixture.json').write_text(json.dumps(dict(info, mapped_sha256=mapped_sha), indent=2))
        (case / 'host/kvm-slots.tsv').write_text('\n'.join(slots) + '\n')
        start_ns = time.monotonic_ns()
        with (case / 'collector.log').open('w') as log, contextlib.redirect_stdout(log):
            collector.main(case, expected=2)
        elapsed = time.monotonic_ns() - start_ns
        measured = json.loads((case / 'host/physical-memory.json').read_text())
        assert len(pfn_sets[0] | pfn_sets[1]) == expected_unique
        assert len(pfn_sets[0] & pfn_sets[1]) == expected_intersection
        assert measured['guest_file_cache_logical_pages'] == 2 * count
        assert measured['guest_file_cache_resident_mappings'] == 2 * count
        assert measured['guest_file_cache_host_physical_mib'] == expected_unique * PAGE / 1024**2
        assert all(hashlib.sha256(path.read_bytes()).hexdigest() == original_sha for path in paths)
        cases.append(dict(case=name, status='PASS', logical_pages=2*count,
                          expected_unique_pages=expected_unique, measured_unique_pages=len(pfn_sets[0] | pfn_sets[1]),
                          intersection_pages=len(pfn_sets[0] & pfn_sets[1]), scan_ns=elapsed))
        print(f'status=CASE_PASS case={name} result_dir={out}', flush=True)
        return case

    try:
        start([paths[0], paths[0]])
        shared_case = scan('shared', count, count)
        children[1][1].send('cow')
        assert receive(children[1][1]) == cow_sha
        scan('cow', count + count // 2, count // 2)
        # A guest PFN outside every advertised slot must never be silently ignored.
        bad = shared_case / 'host/probes/0/file-pfns.bin'
        original = bad.read_bytes()
        bad.write_bytes(original + array.array('I', [0x1000000]).tobytes())
        try:
            with (out / 'negative-control.log').open('w') as log, contextlib.redirect_stdout(log):
                collector.main(shared_case, expected=2, output_path=out / 'negative-control-output.json')
            negative = dict(status='FAIL', reason='collector accepted a guest PFN outside all slots')
        except RuntimeError as exc:
            negative = dict(status='PASS' if 'unmapped guest PFN' in str(exc) else 'FAIL', error=str(exc))
        finally:
            bad.write_bytes(original)
        stop()
        start([paths[1], paths[2]])
        scan('private', 2 * count, 0)
        assert negative['status'] == 'PASS', negative
        status = 'PASS'
    except Exception:
        (out / 'failure.txt').write_text(traceback.format_exc())
    finally:
        stop()
        if not cleanup:
            status = 'FAIL'
        result = dict(status=status, config=config, cases=cases, unmapped_pfn_control=negative,
                      cleanup='PASS' if cleanup else 'FAIL',
                      limitations=['synthetic guest addresses, no real VM slot discovery', 'no DAX or guest PFN enumeration calibration'])
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(f'status={status} result_dir={out}', flush=True)
    return 0 if status == 'PASS' else 1


if __name__ == '__main__':
    def timeout(signum, frame):
        raise TimeoutError('120 second fixture deadline exceeded')
    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(120)
    raise SystemExit(main())
