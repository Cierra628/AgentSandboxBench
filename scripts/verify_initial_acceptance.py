"""Recompute fixed initial acceptance in a new directory, with writes forbidden during audit."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
_read_only = False


def reject_writes(event, args):
    if not _read_only:
        return
    if event == 'open':
        _, mode, flags = args
        if (mode and any(x in mode for x in 'wax+')) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise PermissionError('acceptance audit cannot open files for writing')
    elif event in ('os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.chmod',
                   'os.chown', 'os.truncate', 'os.link', 'os.symlink', 'os.chdir',
                   'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        raise PermissionError('acceptance audit forbids ' + event)


sys.addaudithook(reject_writes)


@contextmanager
def read_only_audit():
    global _read_only
    previous = _read_only
    _read_only = True
    try:
        yield
    finally:
        _read_only = previous


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_inputs(path):
    cfg = json.loads(path.read_text())
    assert cfg['version'] == 1
    for key, expected in cfg['sha256'].items():
        assert digest(ROOT / cfg[key]) == expected, 'fixed input changed: ' + key
    return cfg


def inventory(cfg):
    """Record input metadata; reads may change atime, which is intentionally excluded."""
    directories = [ROOT / cfg[k] for k in ('agentenv', 'trenvx')]
    for driver in directories.copy():
        value = json.loads((driver / 'result.json').read_text())
        directories.extend(Path(row['raw_dir']) for pair in value['pairs'] for row in pair)
    history = json.loads((ROOT / cfg['history_config']).read_text())
    directories.extend(ROOT / row[k] for row in history['runs'] for k in ('raw', 'audit') if k in row)
    directories.extend(ROOT / p for p in history['bundles'].values())
    paths = {ROOT / cfg[k] for k in cfg['sha256']}
    for directory in directories:
        for base, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            paths.update(Path(base) / name for name in files)
    result = {}
    for path in sorted(paths):
        info = path.lstat()
        result[str(path)] = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/initial-acceptance-delivery.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    out = args.output or ROOT / '.artifacts/initial-acceptance-delivery' / (
        time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + str(os.getpid()))
    out = out.resolve()
    if out.parent != ROOT / '.artifacts/initial-acceptance-delivery':
        print('status=FAIL output_must_be_new_child_of=.artifacts/initial-acceptance-delivery', file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.mkdir(mode=0o700)
    except FileExistsError:
        print('status=FAIL existing_output_directory=' + str(out), file=sys.stderr)
        return 2
    os.umask(0o077)
    print('status=RUNNING result_dir=' + str(out), flush=True)
    started = time.monotonic_ns()
    receipt = dict(status='FAIL', result_dir=str(out), scope='offline recount only; no new live experiment')
    try:
        cfg = load_inputs(args.config.resolve())
        before = inventory(cfg)
        os.chdir(ROOT)
        with read_only_audit():
            from report_kernel_acceptance import acceptance
            value = acceptance(*(Path(cfg[k]) for k in (
                'agentenv', 'trenvx', 'smokes', 'history_config', 'closeout')))
            value = json.loads(json.dumps(value))
            expected = json.loads((ROOT / cfg['expected_report']).read_text())
            assert value == expected, 'recomputed report differs from fixed evidence'
            assert inventory(cfg) == before, 'input metadata changed during audit'
            load_inputs(args.config.resolve())
        with (out / 'recomputed.json').open('x') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
        receipt.update(status='PASS', initial_acceptance=value['initial_acceptance'],
                       formal_run_count=20, historical_run_count=4,
                       expected_report_sha256=cfg['sha256']['expected_report'],
                       config_sha256=digest(args.config.resolve()),
                       verifier_sha256=digest(Path(__file__).resolve()),
                       audited_input_file_count=len(before), input_metadata_unchanged=True,
                       recomputed_sha256=digest(out / 'recomputed.json'),
                       python_version=sys.version, existing_report_identical=True,
                       write_and_subprocess_guard='enabled during audit')
    except Exception as exc:
        receipt['error'] = repr(exc)
    receipt['elapsed_ns'] = time.monotonic_ns() - started
    with (out / 'result.json').open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print('status=' + receipt['status'] + ' result_dir=' + str(out))
    return 0 if receipt['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
