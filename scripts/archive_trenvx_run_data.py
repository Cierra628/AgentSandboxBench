"""Archive one finished TrEnv-X run's private images before removing their source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from experiment_disk_guard import guarded_run, writer_lock, RESERVE_BYTES


ROOT = Path(__file__).resolve().parents[1] / '.artifacts/trenvx-service-smoke'


def sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def allocated_bytes(path):
    seen = set()
    total = 0
    for entry in path.rglob('*'):
        info = entry.lstat()
        inode = (info.st_dev, info.st_ino)
        if inode not in seen:
            total += info.st_blocks * 512
            seen.add(inode)
    return total


def archive_run_data(run, *, root=ROOT, compressor='gzip'):
    if compressor not in ('gzip', 'pigz-fast'):
        raise ValueError(f'unsupported compressor: {compressor}')
    if compressor == 'pigz-fast' and not shutil.which('pigz'):
        raise ValueError('pigz-fast requested but pigz is unavailable')
    run = Path(run).resolve(strict=True)
    if run.parent != Path(root).resolve(strict=True):
        raise ValueError('run must be an immediate child of the TrEnv-X result root')
    result = json.loads((run/'result.json').read_text())
    if result['run_id'] != run.name:
        raise ValueError('result run_id does not match directory')
    cleanup = result.get('host_cleanup', {})
    if not (cleanup.get('cgroup_removed') is True and cleanup.get('loop_query_exit_code') == 0
            and not cleanup.get('owned_loop_backings')):
        raise ValueError('run-owned cgroup or loop cleanup is not confirmed')
    if result.get('checks', {}).get('service_shutdown') == 'FAIL':
        raise ValueError('service shutdown failed')
    loops = subprocess.run(['losetup', '--list', '--noheadings', '--output', 'BACK-FILE'],
                           capture_output=True, text=True, timeout=10, check=True)
    if any(str(run) in backing for backing in loops.stdout.splitlines()):
        raise ValueError('run image is still attached to a loop device')
    data = run/'data'
    archive = run/'data.tar.gz'
    partial = run/'data.tar.gz.partial'
    if not data.is_dir() or data.is_symlink():
        raise ValueError('private data directory is absent or is a symlink')
    if partial.exists() or archive.is_symlink():
        raise ValueError('unfinished or symlinked archive requires manual inspection')
    if not archive.exists() and shutil.disk_usage(run).free < RESERVE_BYTES + 6 * 1024**3:
        raise ValueError('archive requires 20 GiB reserve plus 6 GiB staging headroom')
    start = time.monotonic()
    source_allocated = allocated_bytes(data)
    with (run/'archive.log').open('a') as log:
        if not archive.exists():
            with partial.open('xb'):
                pass
            try:
                tar_command = (['tar', '-I', 'pigz -1 -p 2', '-cf', partial, '-C', run, 'data']
                               if compressor == 'pigz-fast'
                               else ['tar', '-czf', partial, '-C', run, 'data'])
                guarded_run(tar_command, directory=run, log=log, timeout=900)
                guarded_run(['gzip', '-t', partial], directory=run, log=log, timeout=180)
                guarded_run(['tar', '--compare', '-f', partial, '-C', run],
                            directory=run, log=log, timeout=900)
                partial.rename(archive)
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        else:
            guarded_run(['gzip', '-t', archive], directory=run, log=log, timeout=180)
            guarded_run(['tar', '--compare', '-f', archive, '-C', run],
                        directory=run, log=log, timeout=900)
    digest = sha256(archive)
    manifest_path = run/'archive-manifest.json'
    if manifest_path.exists():
        prior = json.loads(manifest_path.read_text())
        if prior['archive_sha256'] != digest:
            raise ValueError('existing archive manifest hash differs')
        if prior.get('compressor', 'gzip') != compressor:
            raise ValueError('existing archive was created with a different compressor')
    manifest = dict(run_id=run.name, source='data/', archive=archive.name,
        compressor=compressor,
        source_allocated_scope='sum of distinct inode st_blocks; reflink sharing not resolved',
        source_allocated_bytes=source_allocated, archive_bytes=archive.stat().st_size,
        archive_sha256=digest, gzip_test='PASS', tar_compare='PASS',
        source_removed=False, archive_elapsed_seconds=round(time.monotonic()-start, 3))
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.rmtree(data)
    manifest['source_removed'] = not data.exists()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('--compressor', choices=('gzip', 'pigz-fast'), default='gzip')
    args = parser.parse_args()
    try:
        with writer_lock():
            manifest = archive_run_data(args.run, compressor=args.compressor)
    except BaseException as exc:
        print(f'status=FAIL result_dir={args.run} error={exc!r}')
        raise SystemExit(1)
    print(f"status=PASS result_dir={args.run} archive_bytes={manifest['archive_bytes']}")
