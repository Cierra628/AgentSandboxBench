"""Small host calibration reusing calibrate_host_pfn.worker; no VM/service.

Shared files, private files, MAP_PRIVATE CoW, and write-then-file-copy are
different cases. Missing PFN permission is a failed calibration, never zero.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import struct
import time
import traceback

from calibrate_host_pfn import worker, receive
from measure_file_physical import PAGE, collect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mib', type=int, choices=(1, 16), default=1)
    args = parser.parse_args()
    os.umask(0o077)
    out = ROOT / '.artifacts/file-physical-calibration' / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}')
    out.mkdir(parents=True)
    source_names = ('calibrate_file_physical.py', 'measure_file_physical.py', 'calibrate_host_pfn.py')
    source_hashes = {}
    for name in source_names:
        source = (ROOT / 'scripts' / name).read_bytes()
        (out / name).write_bytes(source)
        source_hashes[name] = hashlib.sha256(source).hexdigest()
    data = b''.join(struct.pack('<Q', i + 1) * (PAGE // 8) for i in range(args.mib * 256))
    paths = {name: out / (name + '.bin') for name in ('shared', 'private-a', 'private-b', 'written', 'copied')}
    for path in paths.values():
        path.write_bytes(data)
    # File copy is not MAP_PRIVATE CoW. Both resulting inodes contain changed data.
    with paths['written'].open('r+b') as stream:
        stream.write(b'X')
        stream.flush()
        os.fsync(stream.fileno())
    shutil.copyfile(paths['written'], paths['copied'])
    expected_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
    assert paths['written'].stat().st_ino != paths['copied'].stat().st_ino
    children, cases = [], []
    failure = None
    cleanup = True

    def stop():
        nonlocal cleanup
        for process, connection, _ in children:
            try:
                connection.send('exit')
            except (BrokenPipeError, EOFError):
                pass
            process.join(3)
            if process.is_alive():
                process.terminate(); process.join(3)
            if process.is_alive():
                process.kill(); process.join(3)
            cleanup = cleanup and process.exitcode == 0
            connection.close()
        children.clear()

    def start(names):
        stop()
        for index, name in enumerate(names):
            parent, child = multiprocessing.get_context('spawn').Pipe()
            process = multiprocessing.get_context('spawn').Process(target=worker,
                args=(child, str(paths[name]), f'ASB-FILE-{os.getpid()}-{index}'))
            process.start(); child.close()
            children.append((process, parent, None))
            children[-1] = (process, parent, receive(parent))

    def sample(name, mapped_names, expected_file, expected_cow=0):
        vms = []
        hashes = []
        for index, ((process, connection, info), file_name) in enumerate(zip(children, mapped_names)):
            proc = Path('/proc') / str(process.pid)
            group = (proc / 'cgroup').read_text().strip().split('::', 1)[1]
            ticks = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
            vms.append(dict(id=f'host-worker-{index}', pid=process.pid, start_time_ticks=ticks,
                process_kind='host_fixture',
                executable=os.readlink(proc / 'exe'), cgroup=group,
                files=[dict(backing=str(paths[file_name]), owner=file_name,
                            offsets=list(range(0, len(data), PAGE)), allow_private_cow=True)],
                guest_memory={'status': 'NOT_APPLICABLE', 'scope': 'host fixture, no guest'}))
            connection.send('hash'); hashes.append(receive(connection))
        manifest = dict(page_size=PAGE, owned_root=str(out), vms=vms,
                        scope='host known-file fixture; not actual VM calibration')
        result, rows = collect(manifest)
        result['manifest_sha256'] = hashlib.sha256((json.dumps(manifest, indent=2) + '\n').encode()).hexdigest()
        (out / (name + '-manifest.json')).write_text(json.dumps(manifest, indent=2) + '\n')
        (out / (name + '-pages.json')).write_text(json.dumps(rows) + '\n')
        (out / (name + '-result.json')).write_text(json.dumps(result, indent=2) + '\n')
        case_dir = out / name; case_dir.mkdir()
        for source, target in [('manifest', 'manifest.json'), ('pages', 'pages.json'), ('result', 'result.json')]:
            os.link(out / (name + '-' + source + '.json'), case_dir / target)
        case = dict(case=name, status=result['status'], file_content=result['file_content'],
                    scan_elapsed_ns=result['scan_elapsed_ns'], first_failure=result['first_failure'],
                    expected_file_pages=expected_file, expected_anonymous_cow_pages=expected_cow,
                    mapped_sha256=hashes)
        cases.append(case)
        if result['status'] != 'PASS':
            raise RuntimeError(str(result['first_failure']))
        if result['file_content']['file_content_unique_pages'] != expected_file:
            raise RuntimeError(f'{name}: unique file-content count differs from {expected_file}')
        if result['file_content']['anonymous_cow_unique_pages'] != expected_cow:
            raise RuntimeError(f'{name}: anonymous CoW count differs from {expected_cow}')
        expected = [expected_hashes[n] for n in mapped_names]
        if name == 'private-cow':
            altered = bytearray(data)
            for i in range(0, len(data), PAGE * 2):
                altered[i] ^= 1
            expected[1] = hashlib.sha256(altered).hexdigest()
        assert hashes == expected, f'{name}: mapped content hash mismatch'
        case['status'] = 'PASS'

    count = len(data) // PAGE
    try:
        start(['shared', 'shared']); sample('shared', ['shared', 'shared'], count)
        children[1][1].send('cow'); receive(children[1][1])
        sample('private-cow', ['shared', 'shared'], count, count // 2)
        start(['private-a', 'private-b']); sample('private-files', ['private-a', 'private-b'], count * 2)
        start(['written', 'copied']); sample('write-then-copy', ['written', 'copied'], count * 2)
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == expected_hashes[n] for n, p in paths.items())
    except BaseException as exc:
        failure = str(exc)
        if cases:
            cases[-1]['status'] = 'FAIL'
        (out / 'failure.txt').write_text(traceback.format_exc())
    finally:
        stop()
    result = dict(status='PASS' if failure is None and cleanup else 'FAIL', page_size=PAGE,
                  mib_per_mapping=args.mib, cases=cases, first_failure=failure,
                  cleanup='PASS' if cleanup else 'FAIL',
                  not_run=[name for name in ('shared', 'private-cow', 'private-files', 'write-then-copy')
                           if name not in {case['case'] for case in cases}],
                  scope='host process fixture; no VM, DAX, OverlayFS or Exp1 verification',
                  sources=source_hashes)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f"status={result['status']} result_dir={out}")
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
