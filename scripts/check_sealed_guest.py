"""Boot the actual two sealed layers on the pinned CH guest and compare visible state."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parent.parent
CH=Path('/var/lib/trenvx/deps/cloud-hypervisor/target/release/cloud-hypervisor')
KERNEL=Path('/var/lib/trenvx/kernels/ch-6.1.134/vmlinux')


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    source=Path(sys.argv[1]).resolve()
    assert source.is_relative_to(ROOT/'.artifacts/layer-fidelity')
    result=json.loads((source/'result.json').read_text())
    assert result['status']=='PASS' and result['checks']['second_generation']=='PASS'
    assert os.geteuid()==0
    os.umask(0o077)
    out=ROOT/'.artifacts/sealed-guest'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    (out/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    stage,status='prepare','FAIL'
    proc=None
    def run(label,args,**kwargs):
        p=subprocess.run(args,capture_output=True,timeout=30,**kwargs)
        (out/f'{label}.stdout').write_bytes(p.stdout)
        (out/f'{label}.stderr').write_bytes(p.stderr)
        (out/f'{label}.rc').write_text(str(p.returncode)+'\n')
        p.check_returncode()
        return p.stdout
    try:
        expected=json.loads((source/'second-after.json').read_text())
        (out/'expected.json').write_text(json.dumps(expected,indent=2)+'\n')
        layer_hashes={name:sha(source/name) for name in ('checkpoint-0001.ext4','checkpoint-0002.ext4')}
        (out/'config.json').write_text(json.dumps(dict(source_result=str(source),layer_sha256=layer_hashes,
            kernel_sha256=sha(KERNEL),ch_sha256=sha(CH),host_kernel=os.uname().release,
            busybox_sha256=sha(Path('/usr/bin/busybox')),memory_mib=4096,cpu=2,
            limitation='guest checks content, visible paths, mode, uid/gid and links; ACL/xattr checked on host only'),indent=2)+'\n')
        for name in layer_hashes:
            os.link(source/name,out/name)
        for name in ('rootfs.ext4','writable-rootfs.ext4'):
            with (out/name).open('wb') as f:
                f.truncate(256*1024**2)
            args=['mkfs.ext4','-q','-F','-b','4096']
            if name=='rootfs.ext4':
                args+=['-d',str(source/'base')]
            run('mkfs-'+name,args+[str(out/name)])
        # mkfs adds this directory outside the original base-tree fixture.
        run('remove-generated-lost-found',['debugfs','-w','-R','rmdir /lost+found',str(out/'rootfs.ext4')])
        initrd=out/'initrd-tree'
        for name in ('bin','dev','proc','sys'):
            (initrd/name).mkdir(parents=True)
        shutil.copy2('/usr/bin/busybox',initrd/'bin/busybox')
        for name in ('sh','mount','mkdir','cat','find','sort','cmp','stat','sha256sum','cut','readlink','uname','poweroff'):
            (initrd/'bin'/name).symlink_to('busybox')
        os.mknod(initrd/'dev/console',stat.S_IFCHR|0o600,os.makedev(5,1))
        (initrd/'expected-paths').write_text(''.join('./'+name+'\n' for name in sorted(expected)))
        commands=['#!/bin/sh','set -eu','export PATH=/bin',
            'trap \'echo ASB_RESTORE_FAIL; poweroff -f\' 0',
            'mount -t proc proc /proc','mount -t sysfs sysfs /sys','mount -t devtmpfs devtmpfs /dev',
            'mkdir -p /base /layer1 /layer2 /upper /merged',
            'mount -t ext4 -o ro,dax=always /dev/pmem0 /base',
            'mount -t ext4 -o ro,dax=always /dev/pmem1 /layer1',
            'mount -t ext4 -o ro,dax=always /dev/pmem2 /layer2',
            'mount -t ext4 /dev/vda /upper','mkdir -p /upper/root /upper/work',
            'mount -t overlay overlay -o lowerdir=/layer2/delta:/layer1/delta:/base,upperdir=/upper/root,workdir=/upper/work /merged',
            'cat /proc/mounts', 'uname -r',
            '(cd /merged && find . -mindepth 1 | sort) >/actual-paths',
            'echo ASB_ACTUAL_PATHS','cat /actual-paths',
            'cmp /expected-paths /actual-paths']
        for name,item in sorted(expected.items()):
            path=shlex.quote('/merged/'+name)
            commands.append('echo '+shlex.quote('ASB_CHECK '+name))
            commands.append(f'test "$(stat -c %a:%u:%g {path})" = {item["mode"]:o}:{item["uid"]}:{item["gid"]}')
            if item['kind']=='file':
                commands += [f'test -f {path}',f'test "$(sha256sum {path} | cut -d\' \' -f1)" = {item["sha256"]}']
            elif item['kind']=='dir':
                commands.append(f'test -d {path}')
            elif item['kind']=='symlink':
                commands.append(f'test "$(readlink {path})" = {shlex.quote(item["target"])}')
            else:
                raise RuntimeError('unexpected visible file type')
        commands += ['test "$(stat -c %i /merged/metadata)" = "$(stat -c %i /merged/hardlink)"',
                     'echo ASB_RESTORE_PASS','trap - 0','poweroff -f']
        (initrd/'init').write_text('\n'.join(commands)+'\n')
        (initrd/'init').chmod(0o755)
        names=['.']+[str(p.relative_to(initrd)) for p in sorted(initrd.rglob('*'))]
        (out/'initrd.cpio').write_bytes(run('cpio',['cpio','--null','-o','-H','newc'],
            input=('\0'.join(names)+'\0').encode(),cwd=initrd))
        stage='guest_restore'
        args=[str(CH),'--kernel',str(KERNEL),'--initramfs',str(out/'initrd.cpio'),
              '--cmdline','console=ttyS0 rdinit=/init panic=1','--cpus','boot=2','--memory','size=4G',
              '--console','off','--serial',f'file={out}/serial.log','--pmem']
        args += [f'file={out}/{name},discard_writes=on,id=layer{i}'
                 for i,name in enumerate(('rootfs.ext4','checkpoint-0001.ext4','checkpoint-0002.ext4'))]
        args += ['--disk',f'path={out}/writable-rootfs.ext4,readonly=off,id=upper']
        (out/'argv.json').write_text(json.dumps(args,indent=2))
        with (out/'vmm.stdout').open('wb') as stdout,(out/'vmm.stderr').open('wb') as stderr:
            proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            proc.wait(timeout=40)
        assert proc.returncode==0
        serial=(out/'serial.log').read_text()
        assert 'ASB_RESTORE_PASS' in serial and 'ASB_RESTORE_FAIL' not in serial
        assert '\n6.1.134\n' in serial
        for device in ('pmem0','pmem1','pmem2'):
            assert any(line.startswith('/dev/'+device+' ') and 'dax=always' in line for line in serial.splitlines())
        assert all(sha(source/name)==digest for name,digest in layer_hashes.items())
        status='PASS'
    except Exception:
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        cleanup=proc is None or proc.poll() is not None
        (out/'result.json').write_text(json.dumps(dict(status=status,last_stage=stage,
            cleanup='PASS' if cleanup else 'FAIL',guest_exit_code=proc.returncode if proc else None),indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
