"""Short live checks of the four CPU policies authorized for this experiment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from cpu_frequency_lease import CPUS, PolicyLease, read_policy, restore, save

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child',type=Path)
    args=parser.parse_args()
    if args.child:
        obj=PolicyLease(args.child,'B',timeout=15)
        obj.verify()
        os._exit(0)  # Deliberately skip finally; independent watcher must recover.
    out=ROOT/'.artifacts/cpu-frequency-recovery'/time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    out.mkdir(parents=True)
    result=dict(status='RUNNING',checks={},scope='authorized CPUs 2,4,66,68; no VM or workload replay')
    for name in ('cpu_frequency_lease.py','validate_cpu_frequency_recovery.py'):
        data=(ROOT/'scripts'/name).read_bytes();(out/name).write_bytes(data)
    before={str(c):read_policy(Path(f'/sys/devices/system/cpu/cpu{c}/cpufreq')) for c in CPUS}
    result['before']=before
    save(out/'result.json',result)
    try:
        obj=PolicyLease(out/'normal','B',timeout=15)
        try:
            obj.verify();time.sleep(.2);obj.verify()
        finally:obj.close()
        result['checks']['normal']='PASS'
        with (out/'child.log').open('w') as log:
            subprocess.run([sys.executable,__file__,'--child',str(out/'abrupt')],stdout=log,stderr=subprocess.STDOUT,timeout=45,check=True)
        deadline=time.monotonic()+40
        while not (out/'abrupt/watch-recovery.json').exists():
            if time.monotonic()>=deadline:raise TimeoutError('watch recovery receipt missing')
            time.sleep(.1)
        receipt=json.loads((out/'abrupt/watch-recovery.json').read_text())
        assert receipt['status']=='PASS' and receipt['reason']=='owner_gone',receipt
        result['checks']['abrupt_owner_exit']='PASS'
        result['status']='PASS'
    except Exception as exc:
        result.update(status='FAIL',error=repr(exc))
    finally:
        # A separate parent can recover a failed test without discarding its result.
        for directory in (out/'normal',out/'abrupt'):
            if (directory/'backup.json').exists():
                receipt=restore(directory,'validation_parent_finally')
                if receipt['status']!='PASS':result['status']='FAIL'
        result['after']={str(c):read_policy(Path(f'/sys/devices/system/cpu/cpu{c}/cpufreq')) for c in CPUS}
        if result['after']!=before:result.update(status='FAIL',restoration_mismatch=True)
        save(out/'result.json',result)
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
