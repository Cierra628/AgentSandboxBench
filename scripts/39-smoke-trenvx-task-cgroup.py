#!/usr/bin/env python3
"""No-network QEMU TCG guest testing the real taskcgroup implementation.

No KVM, sudo, host cgroup writes, shared services, or benchmark workloads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
def hashfile(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def cpio(tree,output):
    # Write newc as an unprivileged user, including a console device entry.
    with output.open('wb') as f:
        ino=0
        def entry(name,data=b'',mode=0o100755,rmajor=0,rminor=0):
            nonlocal ino
            ino+=1;encoded=name.encode()+b'\0'
            fields=[ino,mode,0,0,1,0,len(data),0,0,rmajor,rminor,len(encoded),0]
            header=b'070701'+b''.join(f'{n:08x}'.encode() for n in fields)
            f.write(header+encoded);f.write(b'\0'*(-(110+len(encoded))%4));f.write(data);f.write(b'\0'*(-len(data)%4))
        for p in sorted(tree.rglob('*')):
            name=str(p.relative_to(tree))
            if p.is_symlink():entry(name,os.readlink(p).encode(),0o120777)
            elif p.is_dir():entry(name,mode=0o41777 if name=='tmp' else 0o40755)
            else:entry(name,p.read_bytes(),0o100755)
        entry('dev/console',mode=0o20600,rmajor=5,rminor=1)
        entry('TRAILER!!!',mode=0)
        f.write(b'\0'*(-f.tell()%512))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kernel',type=Path,required=True)
    parser.add_argument('--launchers',action='store_true',help='also test the patched envd launch paths')
    parser.add_argument('--busybox',type=Path,default=Path('/usr/bin/busybox'));parser.add_argument('--timeout',type=int,default=60)
    args=parser.parse_args();out=ROOT/'.artifacts/trenvx-task-cgroup'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}');out.mkdir(parents=True)
    tree=out/'initrd-tree';(tree/'bin').mkdir(parents=True)
    for d in ['dev','proc','sys','tmp']:(tree/d).mkdir()
    shutil.copy2(args.busybox,tree/'bin/busybox')
    for name in ['sh','mount','mkdir','setsid','sleep','poweroff','ln']:(tree/'bin'/name).symlink_to('busybox')
    (tree/'tmp').chmod(0o1777)
    env=dict(os.environ,CGO_ENABLED='0',GOCACHE=os.environ.get('GOCACHE','/tmp/asb-go-cache'))
    subprocess.run(['go','test','-c','-o',str(tree/'asb.test')],cwd=ROOT/'backend/trenvx/taskcgroup',env=env,check=True,timeout=60)
    launcher_cmd = ""
    if args.launchers:
        fork=ROOT/'IncrementalDAX_moti/baselines/TrEnv-X/packages/envd'
        subprocess.run(['go','test','-c','-o',str(tree/'launchers.test'),'./internal/process'],cwd=fork,env=dict(env,GOPATH=os.environ.get('GOPATH','/tmp/asb-go-path')),check=True,timeout=60)
        (tree/'etc').mkdir()
        (tree/'etc/passwd').write_text('root:x:0:0:root:/tmp:/bin/bash\nuser:x:1000:1000:user:/tmp:/bin/bash\n')
        (tree/'etc/group').write_text('root:x:0:\nuser:x:1000:\n')
        shell=Path('/bin/bash')
        shutil.copy2(shell,tree/'bin/bash')
        deps=subprocess.run(['ldd',str(shell)],capture_output=True,text=True,check=True).stdout
        for dep in re.findall(r'(/[^\s()]+)',deps):
            target=tree/dep.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dep,target)
        launcher_cmd="ASB_CGROUP_SMOKE_PARENT=/sys/fs/cgroup /launchers.test -test.v -test.run '^TestIsolatedEnvd' -test.timeout 20s\nrc=$((rc + $?))\n"
    (tree/'init').write_text('''#!/bin/sh
export PATH=/bin
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts
mount -t devpts devpts /dev/pts -o newinstance,ptmxmode=0666
ln -sf /dev/pts/ptmx /dev/ptmx
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mkdir -p /sys/fs/cgroup
mount -t cgroup2 none /sys/fs/cgroup
ASB_CGROUP_SMOKE_PARENT=/sys/fs/cgroup /asb.test -test.v -test.run '^TestKernel' -test.timeout 20s
rc=$?
''' + launcher_cmd + '''echo ASB_KERNEL_SMOKE_RC=$rc
poweroff -f
''')
    archive=out/'initrd.cpio';cpio(tree,archive)
    argv=['qemu-system-x86_64','-accel','tcg','-m','256','-smp','2','-nodefaults','-no-reboot','-display','none','-serial','stdio','-monitor','none','-nic','none','-kernel',str(args.kernel),'-initrd',str(archive),'-append','console=ttyS0 rdinit=/init panic=-1']
    result={'scope':('real kernel + actual envd launchers/HTTP handlers; no full service/controller/DAX restore' if args.launchers else 'real cgroup kernel fixture; no envd service/controller/DAX restore'),'kernel_sha256':hashfile(args.kernel),'busybox_sha256':hashfile(args.busybox),'test_sha256':hashfile(tree/'asb.test'),'argv':argv,'status':'FAIL'}
    try:
        proc=subprocess.run(argv,capture_output=True,timeout=args.timeout)
        (out/'serial.log').write_bytes(proc.stdout);(out/'qemu.stderr').write_bytes(proc.stderr)
        result['qemu_rc']=proc.returncode
        if proc.returncode==0 and b'ASB_KERNEL_SMOKE_RC=0' in proc.stdout:result['status']='PASS'
    except subprocess.TimeoutExpired as e:
        (out/'serial.log').write_bytes(e.stdout or b'');(out/'qemu.stderr').write_bytes(e.stderr or b'');result['error']='QEMU timeout (process killed and reaped)'
    finally:(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'evidence':str(out)}));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
