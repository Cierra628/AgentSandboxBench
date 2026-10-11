"""Run the authorized four-replay ABBA diagnostic, stopping on any failed check."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from cpu_frequency_lease import CPUS, read_policy, save
from report_guest_perf_diagnostics import audit

ROOT=Path(__file__).resolve().parents[1]


def policies():
    return {str(c):read_policy(Path(f'/sys/devices/system/cpu/cpu{c}/cpufreq')) for c in CPUS}


def main():
    out=ROOT/'.artifacts/cpu-frequency-abba'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    plan=json.loads((ROOT/'configs/exp1-cpu-frequency-plan.json').read_text())
    assert plan['maximum_new_replays']==7 and plan['diagnostic_order']==['A','B','B','A']
    save(out/'plan.json',plan)
    shutil.copy2(__file__,out/'driver.py')
    result=dict(status='RUNNING',runs=[],before=policies(),scope='frequency diagnostic, not monitoring overhead acceptance')
    baseline={str(c):dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil') for c in CPUS}
    save(out/'result.json',result)
    try:
        assert result['before']==baseline
        for i,variant in enumerate(plan['diagnostic_order'],1):
            assert policies()==baseline
            assert shutil.disk_usage(out).free>32*1024**3
            log=out/f'{i}-{variant}.log'
            command=[sys.executable,str(ROOT/'scripts/validate_guest_perf_scope.py'),'--settle-seconds','10','--exp1','--trace-wait','--frequency-variant',variant,'--calibration-result',str(ROOT/'.artifacts/guest-perf-scope/20260929T133112Z-2455891/result.json')]
            with log.open('w') as stream:
                process=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=300)
            paths=re.findall(r'result_dir=(\S+)',log.read_text());assert paths,'missing result path'
            directory=Path(paths[-1]);raw=json.loads((directory/'result.json').read_text())
            row=dict(order=i,variant=variant,directory=str(directory.relative_to(ROOT)),exit_code=process.returncode,status=raw['status'])
            result['runs'].append(row);save(out/'result.json',result)
            assert process.returncode==0 and raw['status']=='PASS'
            row['audit']=audit(directory)
            assert raw['frequency_variant']==variant
            assert [r['cpu'] for r in raw['vcpu_binding']]==[2,4]
            assert {r['tid'] for r in raw['vcpu_binding']}==set(raw['identity']['tids'])
            applied=json.loads((directory/'frequency-policy/applied.json').read_text())
            expected=baseline if variant=='A' else {str(c):dict(scaling_min_freq='2000000',scaling_max_freq='2000000',scaling_governor='schedutil') for c in CPUS}
            assert applied['policies']==expected
            receipt=json.loads((directory/'frequency-policy/restored.json').read_text())
            assert receipt==raw['frequency_restoration'] and receipt['status']=='PASS' and receipt['after']==baseline
            assert policies()==baseline
            row['policy_checks']='PASS'
            row['result_sha256']=hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()
            save(out/'result.json',result)
            print(f'order={i} variant={variant} status=PASS result_dir={directory}',flush=True)
        result['status']='PASS'
    except Exception as exc:
        result.update(status='FAIL',error=repr(exc))
    finally:
        result['after']=policies()
        if result['after']!=baseline:result['status']='FAIL'
        save(out/'result.json',result)
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
