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


def discard_success_images(run, *, root=ROOT,verified_checkpoint=False):
    """Opt-in cleanup of successful non-checkpoint Exp1 images; keep raw results."""
    run = Path(run).resolve(strict=True)
    if run.parent != Path(root).resolve(strict=True):
        raise ValueError('run must be an immediate child of the TrEnv-X result root')
    result = json.loads((run/'result.json').read_text())
    config = json.loads((run/'config.json').read_text())
    required = ('private_template_prepare', 'template_build', 'frozen_input_hashes',
                'unfixed_seed_oracle', 'exp1_replay_correctness', 'sdk_cleanup', 'service_shutdown')
    if (result.get('run_id') != run.name or result.get('status') != 'PASS'
            or result.get('full_trajectory_verified') is not True
            or any(result.get('checks', {}).get(key) != 'PASS' for key in required)):
        raise ValueError('successful full Exp1 replay and cleanup are required')
    if verified_checkpoint:
        checkpoint_checks=('checkpoint_after_022','file_restore_interface','modified_source_preserved',
                           'prefix_state_preserved','patch_matches_full_replay','official_task_tests')
        if (config.get('scenario')!='exp1' or config.get('checkpoint_after')!='022' or
                config.get('discard_verified_checkpoint_images') is not True or
                config.get('monitoring_control') not in ('off','on') or config.get('keep_data') or
                any(result.get('checks',{}).get(key)!='PASS' for key in checkpoint_checks)):
            raise ValueError('explicit verified checkpoint control cleanup is required')
        if json.loads((run/'checkpoint.json').read_text())['next_step']!='023':
            raise ValueError('checkpoint continuation boundary is incorrect')
        official=json.loads((run/'official-task-result.json').read_text())
        if official['status']!='PASS' or official['parser_results']['num_tests_passed']!=4:
            raise ValueError('official continuation correctness is required')
    elif (config.get('scenario') != 'exp1' or config.get('checkpoint_after') is not None
            or config.get('discard_success_images') is not True or config.get('keep_data')):
        raise ValueError('explicit non-checkpoint image cleanup configuration is required')
    cleanup = result.get('host_cleanup', {})
    if not (cleanup.get('cgroup_removed') is True and cleanup.get('loop_query_exit_code') == 0
            and cleanup.get('owned_loop_backings') == []):
        raise ValueError('run-owned resource cleanup is not confirmed')
    loops = subprocess.run(['losetup', '--list', '--noheadings', '--output', 'BACK-FILE'],
                           capture_output=True, text=True, timeout=10, check=True)
    if any(str(run) in backing for backing in loops.stdout.splitlines()):
        raise ValueError('run image is still attached to a loop device')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mountpoint = line.split()[4]
        if mountpoint == str(run) or mountpoint.startswith(str(run) + '/'):
            raise ValueError('run still contains a mounted filesystem')
    data = run/'data'
    if not data.is_dir() or data.is_symlink():
        raise ValueError('private data directory is absent or is a symlink')
    if any((run/name).exists() for name in ('data.tar.gz', 'data.tar.gz.partial', 'image-cleanup.json')):
        raise ValueError('existing archive or cleanup record requires inspection')
    seed = Path(config['seed'])
    if not seed.is_file() or sha256(seed) != config['seed_sha256']:
        raise ValueError('fixed input image is missing or changed; preserve private images')
    started = time.monotonic()
    entries = []
    for path in sorted(data.rglob('*')):
        relative = path.relative_to(data).as_posix()
        info = path.lstat()
        if path.is_symlink():
            if relative != 'kernels' or os.readlink(path) != '/var/lib/trenvx/kernels':
                raise ValueError('unexpected private-data symlink; preserve images')
            entries.append(dict(path=relative, kind='symlink', target=os.readlink(path)))
        elif path.is_file():
            entries.append(dict(path=relative, kind='file', bytes=info.st_size,
                                allocated_bytes=info.st_blocks*512, sha256=sha256(path)))
        elif not path.is_dir():
            raise ValueError('unexpected private-data entry; preserve images')
    manifest = dict(run_id=run.name, mode=('verified-checkpoint-control-images-discarded' if verified_checkpoint
                                          else 'successful-exp1-images-discarded'),
                    source='data/', source_removed=False, entries=entries,
                    seed=str(seed), seed_sha256=config['seed_sha256'],
                    source_allocated_bytes=allocated_bytes(data),
                    limitation='image contents are not retained; raw trajectory/configuration/metrics remain; '
                               'no restoration or image reanalysis from this run',
                    inventory_elapsed_seconds=time.monotonic()-started)
    path = run/'image-cleanup.json'
    with path.open('x') as stream:
        json.dump(manifest, stream, indent=2)
        stream.write('\n')
    shutil.rmtree(data)
    manifest.update(source_removed=not data.exists(), elapsed_seconds=time.monotonic()-started)
    path.write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


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
    if not archive.exists():
        from isolated_storage import configured_boundary
        boundary = configured_boundary(run)
        if boundary:
            boundary.check(volume_headroom=6*1024**3)
        elif shutil.disk_usage(run).free < RESERVE_BYTES + 6*1024**3:
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
