"""Test copying a mounted ext4 upper via the controller's Btrfs reflink path."""
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parent.parent
SEALER=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/checkpoint_dax.py'


def syncfs(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:
        libc=ctypes.CDLL(None,use_errno=True)
        assert libc.syncfs(fd)==0, f'syncfs failed errno={ctypes.get_errno()}'
    finally:
        os.close(fd)


def inside(out):
    stage='setup'
    status='FAIL'
    checks={}
    mounts=[]
    worker=None
    continue_worker=threading.Event()
    worker_mid=threading.Event()
    worker_done=threading.Event()
    worker_failure=[]
    index=0

    def run(*argv):
        nonlocal index
        index+=1
        p=subprocess.run(argv,capture_output=True,timeout=40)
        stem=out/f'command-{index:03}'
        stem.with_suffix('.json').write_text(json.dumps(dict(argv=argv,returncode=p.returncode)))
        stem.with_suffix('.stdout').write_bytes(p.stdout)
        stem.with_suffix('.stderr').write_bytes(p.stderr)
        p.check_returncode()
        return p.stdout

    def mount(target,*args):
        target.mkdir(parents=True)
        run('mount',*args,str(target))
        mounts.append(target)

    def unmount(target):
        run('umount',str(target))
        mounts.remove(target)

    def contents(root,subdir='root'):
        return {name:(root/subdir/name).read_text().strip() for name in ('first','second')}

    def write_state(path,data):
        path.write_text(data+'\n')
        with path.open('rb') as f:
            os.fsync(f.fileno())
        dirfd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)

    def interrupted(signum,frame):
        raise RuntimeError(f'interrupted {signum}')

    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        assert os.readlink('/proc/self/ns/mnt')!=(out/'parent-mount-ns.txt').read_text().strip()
        sys.dont_write_bytecode=True
        tempfile.tempdir=str(out)
        spec=importlib.util.spec_from_file_location('sealer',SEALER)
        sealer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sealer)
        store=out/'store.btrfs'
        with store.open('wb') as f:
            f.truncate(768*1024**2)
        run('mkfs.btrfs','-q','-f',str(store))
        btrfs=out/'btrfs'
        mount(btrfs,'-o','loop,noatime,compress=no',str(store))
        upper=btrfs/'writable-rootfs.ext4'
        with upper.open('wb') as f:
            f.truncate(128*1024**2)
        run('mkfs.ext4','-q','-F',str(upper))
        live=out/'live-upper'
        mount(live,'-o','loop',str(upper))
        (live/'root').mkdir()
        (live/'work').mkdir()
        write_state(live/'root/first','0')
        write_state(live/'root/second','0')
        syncfs(live)
        assert contents(live)=={'first':'0','second':'0'}
        checks[stage]='PASS'
        stage='online_clone'

        def background_write():
            try:
                write_state(live/'root/first','1')
                worker_mid.set()
                if not continue_worker.wait(30):
                    raise TimeoutError('writer was not released')
                write_state(live/'root/second','1')
            except Exception:
                worker_failure.append(traceback.format_exc())
            finally:
                worker_done.set()

        worker=threading.Thread(target=background_write,name='owned-background-writer')
        worker.start()
        assert worker_mid.wait(10), 'background writer did not reach middle state'
        assert not worker_done.is_set() and contents(live)=={'first':'1','second':'0'}
        if os.environ.get('ONLINE_UPPER_SYNC_BEFORE_CLONE')=='1':
            syncfs(live)
        # Same reflink-capable Btrfs volume and exact command used by controller.
        clone=btrfs/'captured-upper.ext4'
        run('cp','--reflink=auto','--sparse=always',str(upper),str(clone))
        clone_stat=clone.stat()
        assert clone_stat.st_ino!=upper.stat().st_ino
        (out/'clone-metadata.json').write_text(json.dumps(dict(source_inode=upper.stat().st_ino,
            clone_inode=clone_stat.st_ino,clone_size=clone_stat.st_size,
            source_contents_at_copy=contents(live)),indent=2)+'\n')
        checks[stage]='PASS'
        stage='seal_clone'
        # The actual sealer mounts the copied image ro,noload, then rebuilds a layer.
        sealer.archive_upper(clone,out/'captured-upper.tar')
        sealer.build_layer(out/'captured-upper.tar',out/'checkpoint-0001.ext4')
        sealed=out/'sealed-layer'
        mount(sealed,'-o','loop,ro,noload',str(out/'checkpoint-0001.ext4'))
        captured=contents(sealed/'delta','.')
        (out/'captured-state.json').write_text(json.dumps(captured,indent=2)+'\n')
        checks[stage]='PASS'
        stage='parent_finishes'
        continue_worker.set()
        assert worker_done.wait(10), 'writer did not finish'
        worker.join(timeout=1)
        assert not worker_failure, worker_failure
        syncfs(live)
        parent=contents(live)
        (out/'parent-state.json').write_text(json.dumps(parent,indent=2)+'\n')
        assert parent=={'first':'1','second':'1'}
        assert contents(sealed/'delta','.')==captured
        checks[stage]='PASS'
        stage='copy_fidelity'
        checks[stage]='PASS' if captured=={'first':'1','second':'0'} else 'FAIL'
        status=checks[stage]
    except Exception:
        checks[stage]='FAIL'
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        continue_worker.set()
        if worker is not None:
            worker.join(timeout=10)
        cleanup=True
        for target in reversed(mounts[:]):
            try:
                unmount(target)
            except Exception:
                cleanup=False
                (out/'cleanup-failure.txt').write_text(traceback.format_exc())
        remaining=[line for line in Path('/proc/self/mountinfo').read_text().splitlines() if str(out) in line]
        checks['cleanup']='PASS' if cleanup and not remaining and (worker is None or not worker.is_alive()) else 'FAIL'
        if checks['cleanup']!='PASS':
            status='FAIL'
        (out/'result.json').write_text(json.dumps(dict(status=status,last_stage=stage,checks=checks),indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--inside':
        return inside(Path(sys.argv[2]).resolve())
    assert os.geteuid()==0
    os.umask(0o077)
    out=ROOT/'.artifacts/online-upper-consistency'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    (out/'parent-mount-ns.txt').write_text(os.readlink('/proc/self/ns/mnt'))
    for source in (Path(__file__),SEALER):
        (out/source.name).write_bytes(source.read_bytes())
    (out/'config.json').write_text(json.dumps(dict(host_kernel=os.uname().release,
        sealer_sha256=hashlib.sha256(SEALER.read_bytes()).hexdigest(),
        sync_before_clone=os.environ.get('ONLINE_UPPER_SYNC_BEFORE_CLONE')=='1',
        scope='owned Btrfs loop image in private mount namespace; mounted ext4 upper and controlled background writer',
        caveat='two ordinary writes are not an application transaction; this demonstrates captured state boundary only'),indent=2)+'\n')
    with (out/'run.stdout').open('wb') as stdout,(out/'run.stderr').open('wb') as stderr:
        p=subprocess.run(['timeout','--signal=TERM','--kill-after=10s','180',
                          'unshare','--mount','--propagation','private',sys.executable,
                          str(Path(__file__).resolve()),'--inside',str(out)],stdout=stdout,stderr=stderr)
    (out/'wrapper-exit-code.txt').write_text(str(p.returncode)+'\n')
    if (out/'result.json').exists():
        print((out/'run.stdout').read_text().splitlines()[-1])
    else:
        print(f'status=FAIL stage=namespace-or-timeout result_dir={out}')
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
