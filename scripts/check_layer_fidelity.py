"""Exercise the senior layer sealer in a private mount namespace, owned images only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parent.parent
SOURCE=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/checkpoint_dax.py'


def manifest(root):
    result={}
    for path in sorted(root.rglob('*')):
        s=path.lstat()
        item=dict(mode=stat.S_IMODE(s.st_mode),uid=s.st_uid,gid=s.st_gid,
                  xattrs={key:os.getxattr(path,key,follow_symlinks=False).hex()
                          for key in os.listxattr(path,follow_symlinks=False)})
        if stat.S_ISREG(s.st_mode):
            item.update(kind='file',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=s.st_size)
        elif stat.S_ISDIR(s.st_mode):
            item['kind']='dir'
        elif stat.S_ISLNK(s.st_mode):
            item.update(kind='symlink',target=os.readlink(path))
        elif stat.S_ISCHR(s.st_mode):
            item.update(kind='char',rdev=[os.major(s.st_rdev),os.minor(s.st_rdev)])
        else:
            raise RuntimeError(f'unexpected file type: {path}')
        result[str(path.relative_to(root))]=item
    return result


def inside(out):
    mounts=[]
    stage='fixture'
    status='FAIL'
    checks={}
    command_index=0

    def run(*args):
        nonlocal command_index
        command_index+=1
        p=subprocess.run(args,capture_output=True,timeout=30)
        stem=out/f'command-{command_index:03}'
        stem.with_suffix('.json').write_text(json.dumps(dict(argv=args,returncode=p.returncode)))
        stem.with_suffix('.stdout').write_bytes(p.stdout)
        stem.with_suffix('.stderr').write_bytes(p.stderr)
        p.check_returncode()
        return p.stdout

    def mount(target,*args):
        target.mkdir()
        run('mount',*args,str(target))
        mounts.append(target)

    def unmount(target):
        run('umount',str(target))
        mounts.remove(target)

    def save(name,data):
        (out/name).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')

    def interrupted(signum,frame):
        raise RuntimeError(f'interrupted by signal {signum}')

    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        assert os.readlink('/proc/self/ns/mnt') != (out/'parent-mount-ns.txt').read_text().strip()
        temp=out/'temporary-mounts'
        temp.mkdir()
        tempfile.tempdir=str(temp)
        sys.dont_write_bytecode=True
        spec=importlib.util.spec_from_file_location('senior_sealer',SOURCE)
        sealer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sealer)
        base=out/'base'
        base.mkdir()
        (base/'unchanged').write_text('original unchanged\n')
        (base/'modified').write_text('old\n')
        (base/'deleted').write_text('must not return\n')
        (base/'opaque').mkdir()
        (base/'opaque/old-child').write_text('must not return\n')
        upper_image=out/'upper.ext4'
        with upper_image.open('wb') as f:
            f.truncate(128*1024**2)
        run('mkfs.ext4','-q','-F',str(upper_image))
        upper=out/'upper-mount'
        mount(upper,'-o','loop',str(upper_image))
        (upper/'root').mkdir()
        (upper/'work').mkdir()
        merged=out/'merged-before'
        mount(merged,'-t','overlay','overlay','-o',f'lowerdir={base},upperdir={upper}/root,workdir={upper}/work')
        (merged/'modified').write_text('new content\n')
        (merged/'deleted').unlink()
        shutil.rmtree(merged/'opaque')
        (merged/'opaque').mkdir()
        (merged/'opaque/new-child').write_text('replacement\n')
        (merged/'metadata').write_bytes(b'private metadata payload\n')
        os.chown(merged/'metadata',1234,1235)
        (merged/'metadata').chmod(0o640)
        os.setxattr(merged/'metadata','user.asb-fidelity',b'attribute-value')
        run('setfacl','-m','u:1236:r--',str(merged/'metadata'))
        os.link(merged/'metadata',merged/'hardlink')
        (merged/'symlink').symlink_to('metadata')
        before=manifest(merged)
        assert 'deleted' not in before and 'opaque/old-child' not in before
        assert 'opaque/new-child' in before
        raw_upper=manifest(upper/'root')
        assert raw_upper['opaque']['xattrs'].get('trusted.overlay.opaque')=='79', 'fixture has no opaque directory marker'
        save('before.json',before)
        save('upper-before.json',raw_upper)
        checks[stage]='PASS'
        stage='seal'
        unmount(merged)
        unmount(upper)
        sealer.archive_upper(upper_image,out/'upper.tar')
        sealer.build_layer(out/'upper.tar',out/'checkpoint-0001.ext4')
        checks[stage]='PASS'
        stage='reconstruct'
        layer=out/'layer-mount'
        mount(layer,'-o','loop,ro,noload',str(out/'checkpoint-0001.ext4'))
        raw_layer=manifest(layer/'delta')
        save('layer-after.json',raw_layer)
        fresh=out/'fresh-upper'
        next_upper_image=out/'second-upper.ext4'
        with next_upper_image.open('wb') as f:
            f.truncate(128*1024**2)
        run('mkfs.ext4','-q','-F',str(next_upper_image))
        mount(fresh,'-o','loop',str(next_upper_image))
        (fresh/'root').mkdir()
        (fresh/'work').mkdir()
        restored=out/'merged-after'
        mount(restored,'-t','overlay','overlay','-o',f'lowerdir={layer}/delta:{base},upperdir={fresh}/root,workdir={fresh}/work')
        after=manifest(restored)
        save('after.json',after)
        differences={name:dict(before=before.get(name),after=after.get(name))
                     for name in sorted(set(before)|set(after)) if before.get(name)!=after.get(name)}
        save('differences.json',differences)
        raw_differences={name:dict(before=raw_upper.get(name),after=raw_layer.get(name))
                         for name in sorted(set(raw_upper)|set(raw_layer)) if raw_upper.get(name)!=raw_layer.get(name)}
        save('raw-differences.json',raw_differences)
        assert not differences, f'filesystem semantics changed: {list(differences)}'
        assert not raw_differences, f'upper metadata changed: {list(raw_differences)}'
        assert (restored/'metadata').stat().st_ino==(restored/'hardlink').stat().st_ino
        checks[stage]='PASS'
        stage='second_generation'
        first_layer_hash=hashlib.sha256((out/'checkpoint-0001.ext4').read_bytes()).hexdigest()
        (restored/'modified').unlink()
        (restored/'deleted').write_text('explicitly recreated in second generation\n')
        shutil.rmtree(restored/'opaque')
        (restored/'opaque').mkdir()
        (restored/'opaque/second-child').write_text('second generation only\n')
        (restored/'permissions').mkdir()
        run('setfacl','-m','d:u:1236:r-x',str(restored/'permissions'))
        (restored/'permissions/child').write_text('inherits default ACL\n')
        (restored/'executable').write_text('#!/bin/sh\nexit 0\n')
        (restored/'executable').chmod(0o751)
        os.setxattr(restored/'opaque','user.second-generation',b'yes')
        second_before=manifest(restored)
        second_upper=manifest(fresh/'root')
        save('second-before.json',second_before)
        save('second-upper-before.json',second_upper)
        unmount(restored)
        unmount(fresh)
        sealer.archive_upper(next_upper_image,out/'second-upper.tar')
        sealer.build_layer(out/'second-upper.tar',out/'checkpoint-0002.ext4')
        second_layer=out/'second-layer-mount'
        mount(second_layer,'-o','loop,ro,noload',str(out/'checkpoint-0002.ext4'))
        save('second-layer-after.json',manifest(second_layer/'delta'))
        final_upper=out/'final-upper'
        final_upper.mkdir()
        (final_upper/'root').mkdir()
        (final_upper/'work').mkdir()
        final=out/'merged-final'
        mount(final,'-t','overlay','overlay','-o',
              f'lowerdir={second_layer}/delta:{layer}/delta:{base},upperdir={final_upper}/root,workdir={final_upper}/work')
        second_after=manifest(final)
        save('second-after.json',second_after)
        second_diff={name:dict(before=second_before.get(name),after=second_after.get(name))
                     for name in sorted(set(second_before)|set(second_after)) if second_before.get(name)!=second_after.get(name)}
        save('second-differences.json',second_diff)
        assert not second_diff, f'second-generation semantics changed: {list(second_diff)}'
        assert second_upper==manifest(second_layer/'delta'), 'second upper metadata changed'
        assert hashlib.sha256((out/'checkpoint-0001.ext4').read_bytes()).hexdigest()==first_layer_hash
        assert 'modified' not in second_after and 'opaque/new-child' not in second_after and 'opaque/old-child' not in second_after
        assert second_after['deleted']['sha256']==hashlib.sha256(b'explicitly recreated in second generation\n').hexdigest()
        inherited=out/'linked-layers'
        inherited.mkdir()
        names=sealer.link_layers(out,inherited)
        assert names==['checkpoint-0001.ext4','checkpoint-0002.ext4']
        assert all((out/name).stat().st_ino==(inherited/name).stat().st_ino for name in names)
        save('inheritance.json',dict(layers=names,first_layer_unchanged=True,hardlink_identity='PASS'))
        checks[stage]='PASS'
        status='PASS'
    except Exception:
        checks[stage]='FAIL'
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup=True
        for target in reversed(mounts[:]):
            try:
                unmount(target)
            except Exception:
                cleanup=False
                (out/'cleanup-failure.txt').write_text(traceback.format_exc())
        # Includes mounts created internally by the actual sealer.
        remaining=[line for line in Path('/proc/self/mountinfo').read_text().splitlines() if str(out) in line]
        save('remaining-mounts.json',remaining)
        checks['cleanup']='PASS' if cleanup and not remaining else 'FAIL'
        if checks['cleanup']!='PASS':
            status='FAIL'
        save('result.json',dict(status=status,last_stage=stage,checks=checks))
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--inside':
        return inside(Path(sys.argv[2]).resolve())
    assert os.geteuid()==0
    os.umask(0o077)
    out=ROOT/'.artifacts/layer-fidelity'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    (out/'parent-mount-ns.txt').write_text(os.readlink('/proc/self/ns/mnt'))
    for path in (Path(__file__),SOURCE):
        (out/path.name).write_bytes(path.read_bytes())
    (out/'config.json').write_text(json.dumps(dict(host_kernel=os.uname().release,
        sealer_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        scope='private host mount namespace; cleanly unmounted upper; no guest or DAX or live capture'),indent=2))
    with (out/'run.stdout').open('wb') as stdout,(out/'run.stderr').open('wb') as stderr:
        p=subprocess.run(['timeout','--signal=TERM','--kill-after=10s','180','unshare','--mount','--propagation','private',
                          sys.executable,str(Path(__file__).resolve()),'--inside',str(out)],stdout=stdout,stderr=stderr)
    (out/'wrapper-exit-code.txt').write_text(str(p.returncode)+'\n')
    if (out/'result.json').exists():
        print((out/'run.stdout').read_text().splitlines()[-1])
    else:
        print(f'status=FAIL stage=namespace-or-timeout result_dir={out}')
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
