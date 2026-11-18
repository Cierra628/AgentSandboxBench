"""Build the pinned diagnostic guest kernel in an owned 8 GiB RAM filesystem.

Requires existing host build tools and root for the private tmpfs. Installs no
packages, changes no host kernel, and never restarts the original AgentENV service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
ASSETS={
 'microvm-base.config':('https://raw.githubusercontent.com/kvcache-ai/firecracker/9fbc98e4dc05d03e1f00902ba4bb796395465099/resources/guest_configs/microvm-kernel-ci-x86_64-6.1.config','391d8d498f89e56b391be5ea4a7501ebc44e6d094331ecaabdefed1da6b28a96'),
 'linux-6.1.175.tar.xz':('https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.1.175.tar.xz','37e984a9e449eb15023b3063c3fd6f64fdd1d83af5bb6a944cd7244f40beca85')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--assets',type=Path,help='reuse verified source archive and base config')
    parser.add_argument('--extra-patch',type=Path,help='Explicit local diagnostic patch, applied after cache metadata; original build unchanged when absent')
    parser.add_argument('--localversion',default='-asb-cache-v3',help='Guest-only kernel release suffix')
    args=parser.parse_args();out=args.output.resolve()
    assert os.geteuid()==0 and out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    assert shutil.disk_usage(ROOT).free>22*2**30
    available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
    assert available>16*2**30, '8 GiB build tmpfs plus memory headroom required'
    for tool in ['gcc','make','flex','bison','bc','tar','xz','patch','mount','umount']:
        assert shutil.which(tool), 'required existing build tool missing: '+tool
    os.umask(0o077);out.mkdir(parents=True);work=out/'build-ram';work.mkdir()
    mounted=False;started=time.monotonic_ns();stage='assets'
    result=dict(status='FAIL',host_kernel_changed=False,build_jobs=4,ram_limit_bytes=8*2**30)

    def run(label,argv,timeout=60):
        with (out/(label+'.log')).open('x') as log:
            process=subprocess.Popen(list(map(str,argv)),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code=process.wait(timeout=timeout)
                if code:raise RuntimeError(label+': exit '+str(code))
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
                raise
    try:
        for name,(url,digest) in ASSETS.items():
            p=out/name
            if args.assets:shutil.copyfile(args.assets/name,p)
            else:
                with urllib.request.urlopen(url,timeout=30) as src,p.open('xb') as dst:shutil.copyfileobj(src,dst)
            assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==digest, 'source hash mismatch: '+name
        stage='extract'
        run('mount',['mount','-t','tmpfs','-o','size=8G,nosuid,nodev,mode=0700','tmpfs',work]);mounted=True
        run('extract',['tar','-xJf',out/'linux-6.1.175.tar.xz','-C',work],120)
        source=work/'linux-6.1.175'
        patch=ROOT/'patches/agentenv-cache-metadata/linux-6.1.175.patch'
        with patch.open('rb') as stream,(out/'patch.log').open('x') as log:
            subprocess.run(['patch','--batch','--forward','-p1','-d',str(source)],stdin=stream,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
        if args.extra_patch:
            extra=args.extra_patch.resolve();assert extra.is_relative_to(ROOT/'patches') and extra.is_file()
            shutil.copyfile(extra,out/'extra.patch')
            with extra.open('rb') as stream,(out/'extra-patch.log').open('x') as log:
                subprocess.run(['patch','--batch','--forward','-p1','-d',str(source)],stdin=stream,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
            result['extra_patch']=str(extra.relative_to(ROOT))
        shutil.copyfile(out/'microvm-base.config',source/'.config')
        stage='configure'
        run('config',[source/'scripts/config','--file',source/'.config','--enable','ASB_PAGECACHE',
            '--enable','IKCONFIG','--enable','IKCONFIG_PROC','--enable','DAMON_RECLAIM',
            '--enable','VIRTIO_MMIO_CMDLINE_DEVICES','--enable','PCI','--enable','PCI_MSI',
            '--disable','TRANSPARENT_HUGEPAGE','--set-str','LOCALVERSION',args.localversion])
        run('olddefconfig',['make','-C',source,'olddefconfig'])
        shutil.copyfile(source/'.config',out/'diagnostic.config')
        stage='compile';print('status=COMPILING result_dir='+str(out),flush=True)
        run('build',['make','-C',source,'-j4','vmlinux'],1500)
        shutil.copyfile(source/'vmlinux',out/'vmlinux.bin');shutil.copyfile(source/'System.map',out/'System.map')
        result.update(status='BUILT',runtime_validation='NOT_RUN',kernel_sha256=hashlib.file_digest((out/'vmlinux.bin').open('rb'),'sha256').hexdigest(),
            patch_sha256=hashlib.file_digest(patch.open('rb'),'sha256').hexdigest(),config_sha256=hashlib.file_digest((out/'diagnostic.config').open('rb'),'sha256').hexdigest(),
            source_assets={name:dict(url=url,sha256=digest) for name,(url,digest) in ASSETS.items()},
            baseline_relation='vanilla 6.1.175 with pinned Firecracker config; not the original vendor binary/config')
    except BaseException as exc:
        result.update(failed_stage=stage,error=repr(exc))
    finally:
        if mounted:run('unmount',['umount',work])
        result['elapsed_ns']=time.monotonic_ns()-started
        (out/'build-result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status=result['status'],result_dir=str(out),failed_stage=result.get('failed_stage'))))
    return 0 if result['status']=='BUILT' else 1


if __name__=='__main__':raise SystemExit(main())
