#!/usr/bin/env python3
"""Exercise the installed VMM's KVM setup, rejecting /dev/null as its kernel."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from trenvx_kvm_capture import Capture

ROOT=Path(__file__).resolve().parents[1]
CH=Path('/var/lib/trenvx/deps/cloud-hypervisor/target/release/cloud-hypervisor')


def inner(out,boot_kernel=False,nested=False):
    assert os.getpid()==1
    group=Path('/sys/fs/cgroup')/('asb-kvm-no-boot-'+out.name)
    group.mkdir();(group/'cgroup.procs').write_text('1')
    capture=Capture(out);child=None;result=dict(status='FAIL')
    # The extra probe uses this owned cgroup as an independent diagnostic filter.
    capture.path.write_text(capture.path.read_text()+'''
tracepoint:syscalls:sys_enter_ioctl /cgroup == CGID && args->cmd != 0xae80/ {
  $t=(struct task_struct *)curtask;
  printf("DIAG\\t%d\\t%d\\t%lx\\t%lu\\t%s\\n",pid,tid,args->cmd,$t->nsproxy->pid_ns_for_children->ns.inum,comm);
}
'''.replace('CGID',str(group.stat().st_ino)))
    try:
        capture.start()
        with (out/'cloud-hypervisor.log').open('w') as log:
            kernel='/var/lib/trenvx/kernels/ch-6.1.134/vmlinux' if boot_kernel else '/dev/null'
            argv=[str(CH),'--api-socket',str(out/'api.sock'),'--kernel',kernel,
                '--memory','size=128M' if boot_kernel else 'size=32M','--cpus','boot=1',
                '--cmdline','console=ttyS0 panic=0','--console','off','--serial','file='+str(out/'serial.log')]
            if nested:argv=['unshare','--pid','--fork','--kill-child','--',*argv]
            child=subprocess.Popen(argv,stdout=log,stderr=log)
            if boot_kernel:
                time.sleep(3)
                capture.stop()
                rows=[line.split('\t') for line in (capture.directory/'raw.tsv').read_text().splitlines() if len(line.split('\t'))==9]
                assert rows,'real VMM registration missing'
                verified=[]
                for row in rows:
                    proc=Path('/proc')/row[0]
                    assert (proc/'exe').resolve()==CH.resolve()
                    ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19])
                    assert ticks==int(row[8])*os.sysconf('SC_CLK_TCK')//1_000_000_000
                    assert int(row[5])==0 and int(row[3],16)>0
                    assert (proc/'ns/pid').stat().st_ino!=capture.namespace_inode if nested else (proc/'ns/pid').stat().st_ino==capture.namespace_inode
                    verified.append(dict(pid=int(row[0]),start_time_ticks=ticks,namespace_inode=(proc/'ns/pid').stat().st_ino))
                (out/'identity-check.json').write_text(json.dumps(dict(status='PASS',nested=nested,processes=verified),indent=2)+'\n')
                # This fixture has no disk. PID 1 in the nested namespace can
                # ignore TERM; kill only the VMM whose identity was just verified.
                for pid in {r['pid'] for r in verified}:os.kill(pid,9)
            rc=child.wait(timeout=20)
        time.sleep(.3);capture.stop()
        raw=(capture.directory/'raw.tsv').read_text()
        result.update(status='PASS' if boot_kernel else 'DIAGNOSTIC',vmm_exit_code=rc,registration_rows=sum(len(line.split('\t'))==9 for line in raw.splitlines()),
            scope=('owned 128 MiB kernel-only VM, no disk/root filesystem; not a task acceptance result' if boot_kernel else
                   'owned VMM initialization with invalid kernel, no disk image; not a guest or task acceptance result'))
    except BaseException as exc:result['error']=repr(exc)
    finally:
        if child is not None and child.poll() is None:child.kill();child.wait(timeout=5)
        try:capture.stop()
        except BaseException as exc:result['stop_error']=repr(exc)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['status'] in ('PASS','DIAGNOSTIC') and 'stop_error' not in result else 1


def main():
    p=argparse.ArgumentParser();p.add_argument('--inner',type=Path);p.add_argument('--boot-kernel',action='store_true');p.add_argument('--nested',action='store_true');args=p.parse_args()
    if args.inner:return inner(args.inner,args.boot_kernel,args.nested)
    os.umask(0o077)
    out=ROOT/'.artifacts/trenvx-ch-capture'/f'{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}'
    out.mkdir(parents=True)
    for name in ['smoke_trenvx_ch_capture.py','trenvx_kvm_capture.py','measure_file_physical.py']:
        shutil.copy2(ROOT/'scripts'/name,out/name)
    with (out/'inner.log').open('w') as log:
        r=subprocess.run(['timeout','--kill-after=5','45','unshare','--mount','--pid','--fork','--mount-proc','--kill-child',
            'python3',str(out/'smoke_trenvx_ch_capture.py'),'--inner',str(out)]+(['--boot-kernel'] if args.boot_kernel else [])+(['--nested'] if args.nested else []),stdout=log,stderr=log,timeout=55)
    group=Path('/sys/fs/cgroup')/('asb-kvm-no-boot-'+out.name)
    if group.exists():group.rmdir()
    (out/'cleanup.json').write_text(json.dumps(dict(exit_code=r.returncode,cgroup_removed=not group.exists()))+'\n')
    print(json.dumps(dict(exit_code=r.returncode,result_dir=str(out))))
    return r.returncode


if __name__=='__main__':raise SystemExit(main())
