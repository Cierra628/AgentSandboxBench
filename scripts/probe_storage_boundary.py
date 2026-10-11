"""Small owned-filesystem probe; never changes an existing service or filesystem."""
import ctypes
import errno
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import traceback

from experiment_disk_guard import writer_lock

ROOT = Path(__file__).resolve().parents[1]
SIZE = 256 * 1024**2


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def command(out, label, argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, timeout=90)
    (out/(label+'.stdout')).write_bytes(p.stdout)
    (out/(label+'.stderr')).write_bytes(p.stderr)
    dump(out/(label+'.json'), dict(command=list(map(str, argv)), exit_code=p.returncode))
    p.check_returncode()
    return p.stdout.decode().strip()


class Quota(ctypes.Structure):
    _fields_ = [(k, ctypes.c_uint64) for k in (
        'hard_blocks', 'soft_blocks', 'space_bytes', 'hard_inodes',
        'soft_inodes', 'inodes', 'block_time', 'inode_time')] + [('valid', ctypes.c_uint32)]


def quota(device, project, value=None):
    libc = ctypes.CDLL(None, use_errno=True)
    q = value if value is not None else Quota()
    # linux/quota.h: QCMD(Q_SETQUOTA or Q_GETQUOTA, PRJQUOTA).
    cmd = ((0x800008 if value is not None else 0x800007) << 8) | 2
    rc = libc.quotactl(ctypes.c_int(cmd), os.fsencode(device), project, ctypes.byref(q))
    if rc != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return {k: getattr(q, k) for k, _ in q._fields_}


def child(out, device):
    out = out.resolve()
    assert out.is_relative_to(ROOT/'.artifacts/storage-boundary')
    assert (out/'volume.ext4').stat().st_size == SIZE
    assert command(out, 'check-own-loop', ['losetup', '-j', out/'volume.ext4']).startswith(device+':')
    mountpoint = out/'mount'
    result = dict(status='FAIL', scope='256 MiB isolated fixture, not production storage')
    mounted = False
    try:
        command(out, 'private-mounts', ['mount', '--make-rprivate', '/'])
        command(out, 'mount', ['mount', '-t', 'ext4', '-o', 'nodiscard,prjquota', device, mountpoint])
        mounted = True
        result['mount'] = command(out, 'mount-info', ['findmnt', '-T', mountpoint, '-J'])
        result['backing_allocated_after_mkfs_bytes'] = (out/'volume.ext4').stat().st_blocks*512
        project = mountpoint/'project'
        project.mkdir()
        command(out, 'inherit-project', ['chattr', '-p', '7319', '+P', project])
        limit = Quota(hard_blocks=8192, valid=1)
        result['quota_before'] = quota(device, 7319, limit)
        cap = next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
        result['root_has_cap_sys_resource'] = bool(int(cap, 16) & (1 << 24))
        assert result['root_has_cap_sys_resource']
        # Root retains CAP_SYS_RESOURCE: a 16 MiB allocation against an 8 MiB limit.
        with (project/'root-write').open('xb') as stream:
            os.posix_fallocate(stream.fileno(), 0, 16*1024**2)
            os.fsync(stream.fileno())
        result['quota_after_root'] = quota(device, 7319)
        assert result['quota_after_root']['space_bytes'] > 8*1024**2
        result['privileged_root_bypassed_project_hard_limit'] = True
        writer = 'import errno,os,sys; f=open(sys.argv[1],"xb");\ntry: os.posix_fallocate(f.fileno(),0,1024*1024)\nexcept OSError as e: sys.exit(0 if e.errno==errno.EDQUOT else 2)\nsys.exit(1)'
        argv = [sys.executable, '-c', writer, str(project/'limited-write')]
        command(out, 'without-resource-capability', ['capsh', '--drop=cap_sys_resource', '--', '-c', shlex.join(argv)])
        result['without_capability_rejected_with_edquot'] = True
        (project/'root-write').unlink()
        (project/'limited-write').unlink()
        # Reserve allocated space for a cleanup receipt before intentionally filling.
        reserve = mountpoint/'cleanup-reserve'
        with reserve.open('xb') as stream:
            os.posix_fallocate(stream.fileno(), 0, 8*1024**2)
        written = 0
        error = None
        with (mountpoint/'fill').open('xb', buffering=0) as stream:
            for _ in range(512):
                try:
                    written += stream.write(b'X'*1024**2)
                    os.fsync(stream.fileno())
                except OSError as exc:
                    error = exc.errno
                    break
        assert error == errno.ENOSPC, (written, error)
        result.update(root_capacity_limit_errno=error, fill_written_bytes=written,
                      backing_allocated_at_full_bytes=(out/'volume.ext4').stat().st_blocks*512)
        reserve.unlink()
        with (mountpoint/'cleanup-receipt').open('xb') as stream:
            stream.write(b'owned cleanup remains possible\n')
            stream.flush()
            os.fsync(stream.fileno())
        result['cleanup_write_after_reserve_release'] = True
        (mountpoint/'fill').unlink()
        result['backing_allocated_after_delete_bytes'] = (out/'volume.ext4').stat().st_blocks*512
        trim = subprocess.run(['fstrim', str(mountpoint)], capture_output=True, timeout=20)
        (out/'disabled-discard.stderr').write_bytes(trim.stderr)
        result['trim_exit_code'] = trim.returncode
        assert trim.returncode != 0
        result['backing_allocated_after_trim_attempt_bytes'] = (out/'volume.ext4').stat().st_blocks*512
        assert all(result[k] >= SIZE for k in (
            'backing_allocated_after_mkfs_bytes', 'backing_allocated_at_full_bytes',
            'backing_allocated_after_delete_bytes', 'backing_allocated_after_trim_attempt_bytes'))
        result['status'] = 'PASS'
    except BaseException as exc:
        result['error'] = repr(exc)
        (out/'child-failure.txt').write_text(traceback.format_exc())
    finally:
        if mounted:
            try:
                command(out, 'unmount', ['umount', mountpoint])
                result['unmount'] = 'PASS'
            except BaseException as exc:
                result.update(status='FAIL', unmount='FAIL', unmount_error=repr(exc))
        dump(out/'probe.json', result)
    return 0 if result['status'] == 'PASS' else 1


def identities():
    rows = []
    for p in Path('/proc').iterdir():
        try:
            if not p.name.isdigit() or (p/'comm').read_text().strip() != 'firecracker':
                continue
            rows.append(dict(pid=int(p.name), start_ticks=(p/'stat').read_text().rsplit(')', 1)[1].split()[19],
                             cgroup=(p/'cgroup').read_text()))
        except OSError:
            continue
    return sorted(rows, key=lambda r: r['pid'])


def main():
    os.umask(0o077)
    assert os.geteuid() == 0
    out = ROOT/'.artifacts/storage-boundary'/f'{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True)
    result = dict(status='FAIL', capacity_bytes=SIZE, result_dir=str(out), production_boundary='NOT_CONFIGURED')
    image = out/'volume.ext4'
    device = None
    before = identities()
    dump(out/'firecracker-before.json', before)
    result['parent_available_before_bytes'] = shutil.disk_usage(out).free
    print(f'status=RUNNING result_dir={out}', flush=True)
    try:
        assert result['parent_available_before_bytes'] > (20*1024**3 + SIZE)
        with image.open('xb') as stream:
            os.posix_fallocate(stream.fileno(), 0, SIZE)
            os.fsync(stream.fileno())
        assert image.stat().st_size == SIZE and image.stat().st_blocks*512 >= SIZE
        command(out, 'mkfs', ['mkfs.ext4', '-q', '-F', '-m', '0', '-O', 'quota,project',
                             '-E', 'quotatype=prjquota,nodiscard,lazy_itable_init=0,lazy_journal_init=0', image])
        (out/'mount').mkdir()
        device = command(out, 'attach', ['losetup', '--find', '--show', '--', image])
        # Only this new loop: prevent TRIM from punching holes in the backing file.
        discard = Path('/sys/class/block')/Path(device).name/'queue/discard_max_bytes'
        discard.write_text('0\n')
        assert discard.read_text().strip() == '0'
        result['own_loop_discard_max_bytes'] = 0
        command(out, 'isolated-probe', ['unshare', '--mount', '--propagation', 'private',
                                      sys.executable, Path(__file__).resolve(), '--child', out, device])
        result['probe'] = json.loads((out/'probe.json').read_text())
        result['status'] = 'PASS'
    except BaseException as exc:
        result['error'] = repr(exc)
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        try:
            if device:
                # The child has exited; its private mount namespace has gone away.
                command(out, 'detach', ['losetup', '-d', device])
            attached = command(out, 'remaining-own-loops', ['losetup', '-j', image])
            assert not attached
            if image.exists():
                result['removed_fixture_allocated_bytes'] = image.stat().st_blocks*512
                image.unlink()
            result['fixture_cleanup'] = 'PASS'
        except BaseException as exc:
            result.update(status='FAIL', fixture_cleanup='FAIL', cleanup_error=repr(exc))
        after = identities()
        dump(out/'firecracker-after.json', after)
        result['original_firecracker_identities_unchanged'] = before == after
        if before != after:
            result['status'] = 'FAIL'
        result['parent_available_after_bytes'] = shutil.disk_usage(out).free
        dump(out/'result.json', result)
    print(f'status={result["status"]} result_dir={out}', flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--child':
        raise SystemExit(child(Path(sys.argv[2]), sys.argv[3]))
    if len(sys.argv) != 1:
        raise SystemExit('usage: python3 scripts/probe_storage_boundary.py')
    with writer_lock():
        raise SystemExit(main())
