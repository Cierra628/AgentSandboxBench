#!/usr/bin/env python3
"""Bounded KVM registration fixture; no guest boot or shared service."""
import argparse
import ctypes
import datetime
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import subprocess
import time

from trenvx_kvm_capture import Capture,attest


def region():
    import struct
    control=os.open('/dev/kvm',os.O_RDWR)
    vm=None;memory=None
    try:
        vm=fcntl.ioctl(control,0xae01,0)
        memory=mmap.mmap(-1,4096)
        memory[:]=hashlib.sha256(b'AgentSandboxBench KVM capture fixture').digest()*128
        address=ctypes.addressof(ctypes.c_char.from_buffer(memory))
        fcntl.ioctl(vm,0x4020ae46,struct.pack('=IIQQQ',0,0,0,4096,address))
        return control,vm,memory,address
    except BaseException:
        if memory is not None:memory.close()
        if vm is not None:os.close(vm)
        os.close(control)
        raise


def close_region(value):
    control,vm,memory,_=value
    os.close(vm);memory.close();os.close(control)


def inner(out):
    cap=Capture(out);value=None;result={}
    try:
        cap.start();(out/'ready').touch()
        deadline=time.monotonic()+15
        while not (out/'foreign-done').exists():
            if time.monotonic()>deadline:raise TimeoutError('foreign fixture timeout')
            time.sleep(.05)
        value=region()
        time.sleep(.3)
        cap.stop()
        stat=Path('/proc/self/stat').read_text().split(') ',1)[1].split()
        vm=dict(pid=os.getpid(),start_time_ticks=int(stat[19]))
        proof=attest(cap.directory,vm,'00000000-00000fff : System RAM\n')
        assert len(proof['slots'])==1 and proof['slots'][0]['host']==value[3]
        rows=[line.split('\t') for line in (cap.directory/'raw.tsv').read_text().splitlines() if len(line.split('\t'))==9]
        assert len(rows)==1 and int(rows[0][6])>1,'unexpected captured process'
        foreign=json.loads((out/'foreign.json').read_text())
        assert int(rows[0][6])!=foreign['pid'],'foreign namespace was captured'
        result=dict(status='PASS',attestation=proof,host_pid=int(rows[0][6]),foreign_pid=foreign['pid'],
                    foreign_namespace_excluded=True,
                    scope='one owned KVM registration; declared fixture range, no guest boot, no sandbox memory result')
    except BaseException as exc:
        result=dict(status='FAIL',error=repr(exc))
    finally:
        try:cap.stop()
        except BaseException as exc:result.update(status='FAIL',stop_error=repr(exc))
        if value is not None:close_region(value)
        result['cleanup']='PASS' if value is not None else 'NO_REGION_CREATED'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['status']=='PASS' else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--inner',type=Path);args=parser.parse_args()
    if args.inner:return inner(args.inner)
    root=Path(__file__).resolve().parents[1]
    out=root/'.artifacts/trenvx-kvm-capture'/f'{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}'
    out.mkdir(parents=True,mode=0o700)
    for name in ['smoke_trenvx_kvm_capture.py','trenvx_kvm_capture.py','measure_file_physical.py']:
        (out/name).write_bytes((root/'scripts'/name).read_bytes())
    with (out/'inner.log').open('w') as log:
        child=subprocess.Popen(['timeout','--signal=TERM','--kill-after=5','60','unshare','--mount','--pid','--fork',
                                '--mount-proc','--kill-child','python3',str(out/'smoke_trenvx_kvm_capture.py'),'--inner',str(out)],stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+25
            while not (out/'ready').exists():
                if child.poll() is not None:raise RuntimeError('private fixture exited before readiness')
                if time.monotonic()>deadline:raise TimeoutError('private fixture readiness timeout')
                time.sleep(.1)
            foreign=region()
            try:(out/'foreign.json').write_text(json.dumps(dict(pid=os.getpid(),address=foreign[3]))+'\n')
            finally:close_region(foreign)
            (out/'foreign-done').touch()
            rc=child.wait(timeout=30)
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=6)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
    print(json.dumps(dict(exit_code=rc,result_dir=str(out))))
    return rc


if __name__=='__main__':raise SystemExit(main())
