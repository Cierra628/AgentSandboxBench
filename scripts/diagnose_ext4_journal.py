"""Compare readonly ext4 lookup before/after journal-only replay on a new copy.

Only inactive images in .artifacts/checkpoint-image-diagnostic are accepted.
This does not run a VM, repair the input image, or clear host caches.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys

from experiment_disk_guard import writer_lock
from isolated_storage import available, GIB

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'.artifacts/checkpoint-image-diagnostic'


def source_image(path):
    image=path.resolve(strict=True)
    if not image.is_relative_to(FIXTURES) or not image.is_file() or not 4096<=image.stat().st_size<=8*GIB:
        raise ValueError('input must be a diagnostic copy of an image, 4 KiB..8 GiB; original volumes are refused')
    return image


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def tool(argv,log,timeout=30):
    with log.open('x') as stream:
        result=subprocess.run(list(map(str,argv)),stdout=stream,stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL,timeout=timeout)
    return result.returncode


def probe(image,out):
    image=source_image(image);out=out.resolve()
    if not out.is_relative_to(FIXTURES) or out.exists() or image.is_relative_to(out):
        raise ValueError('probe output must be a new diagnostic directory')
    if subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).strip():
        raise ValueError('probe input is already attached')
    out.mkdir(exist_ok=False);mount=out/'mount';mount.mkdir()
    device=None;mounted=False;row=dict(status='FAIL',read_only=True,noload=True)
    def cancelled(signum,frame):raise RuntimeError('readonly probe interrupted')
    signal.signal(signal.SIGTERM,cancelled)
    try:
        device=subprocess.check_output(['losetup','--read-only','--find','--show','--',str(image)],text=True,timeout=10).strip()
        if subprocess.check_output(['blockdev','--getro',device],text=True,timeout=10).strip()!='1':
            raise ValueError('diagnostic loop is not readonly')
        subprocess.run(['mount','-t','ext4','-o','ro,noload',device,str(mount)],check=True,capture_output=True,timeout=10);mounted=True
        try:
            value=(mount/'root').stat()
            row.update(status='READABLE',root_inode=value.st_ino,root_is_directory=stat.S_ISDIR(value.st_mode))
        except OSError as exc:row.update(status='READ_ERROR',errno=exc.errno,error=exc.strerror)
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        if mounted:subprocess.run(['umount',str(mount)],check=True,capture_output=True,timeout=10)
        if device:
            backing=subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10)
            if not backing.startswith(device+':'):raise ValueError('owned loop backing changed')
            subprocess.run(['losetup','-d',device],check=True,capture_output=True,timeout=10)
        if subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).strip():
            raise ValueError('owned loop remains attached')
        row['owned_mount_and_loop_removed']=True
        (out/'result.json').write_text(json.dumps(row,indent=2)+'\n')
    return row


def isolated_probe(image,out):
    argv=['unshare','--mount','--propagation','private',sys.executable,'-B',__file__,'_probe',str(image),str(out)]
    with out.with_suffix('.log').open('x') as log:
        worker=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:code=worker.wait(timeout=45)
        except BaseException:
            try:os.killpg(worker.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:worker.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(worker.pid,signal.SIGKILL);worker.wait(timeout=10)
            # Namespace exit removes its mounts; detach only this probe's readonly backing.
            for line in subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).splitlines():
                subprocess.run(['losetup','-d',line.split(':',1)[0]],check=True,timeout=10)
            raise
    if code:raise RuntimeError('readonly probe failed; inspect private log')
    return json.loads((out/'result.json').read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args();image=source_image(args.image);out=args.output.resolve()
    if not out.is_relative_to(FIXTURES) or out.exists() or image.is_relative_to(out):
        raise ValueError('output must be a new diagnostic directory separate from input')
    if args.check_only:
        print('status=VALIDATED scope=paths-only');return 0
    def cancelled(signum,frame):raise RuntimeError('diagnostic interrupted')
    signal.signal(signal.SIGTERM,cancelled)
    if os.geteuid()!=0:raise RuntimeError('root required for owned readonly loop and private mount')
    with writer_lock():
        if subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).strip():
            raise ValueError('input is already attached to a loop device')
        if available(ROOT)<=26*GIB+image.stat().st_size:raise RuntimeError('insufficient diagnostic disk budget')
        os.umask(0o077);out.mkdir(parents=True,exist_ok=False);clone=out/'journal-replay-copy.ext4'
        row=dict(status='FAIL',scope='diagnostic image only; not checkpoint/restore acceptance')
        original=digest(image)
        try:
            subprocess.run(['cp','--reflink=never','--sparse=always','--',str(image),str(clone)],check=True,timeout=30)
            if digest(clone)!=original:raise ValueError('clone content differs from input')
            row['before']=isolated_probe(image,out/'before')
            row['journal_only_exit_code']=tool(['e2fsck','-p','-E','journal_only',clone],out/'journal-only.log')
            if row['journal_only_exit_code'] not in (0,1):raise RuntimeError('journal-only replay failed')
            row['after']=isolated_probe(clone,out/'after')
            row['readonly_fsck_exit_code']=tool(['e2fsck','-f','-n',clone],out/'readonly-fsck.log')
            text=(out/'readonly-fsck.log').read_text()
            row['allocation_count_warnings']=[line for line in text.splitlines() if 'count wrong' in line]
            row['readonly_fsck_clean']=row['readonly_fsck_exit_code']==0 and not row['allocation_count_warnings'] and 'Fix? no' not in text
            reproduced=row['before'].get('errno')==74 and row['after']['status']=='READABLE'
            row['status']='JOURNAL_LOOKUP_MECHANISM_REPRODUCED' if reproduced else 'MECHANISM_NOT_REPRODUCED'
        except Exception as exc:row['error']=repr(exc)
        finally:
            row.update(original_sha256=original,original_sha256_after=digest(image),source_unchanged=digest(image)==original)
            if not row['source_unchanged']:row['status']='FAIL'
            if clone.exists():row['replayed_copy_sha256']=digest(clone)
            (out/'result.json').write_text(json.dumps(row,indent=2)+'\n')
        print('status='+row['status']+' result_dir='+str(out))
        return 1 if row['status']=='FAIL' else 0


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='_probe':probe(Path(sys.argv[2]),Path(sys.argv[3]))
    else:raise SystemExit(main())
