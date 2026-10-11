"""Resume the five-group plan after its audited smoke and first group."""
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
from report_monitor_damon_followup import audit

ROOT=Path(__file__).resolve().parents[1]


def main(section='monitoring_return_to_original_replay'):
    import os
    os.umask(0o077)
    plan=ROOT/'configs/exp1-damon-control-plan.json'
    assert section in ('monitoring_return_to_original_replay','monitoring_default_policy')
    config=json.loads(plan.read_text());state=config[section]
    groups=state['groups'];assert 1<=len(groups)<state['maximum_balanced_groups']==5
    assert all(g['status']==g['offline_audit']=='PASS' for g in state['smoke']+groups)
    directories=[ROOT/g['result_dir'] for g in state['smoke']+groups]
    mode=state.get('balanced_mode','damon_disabled');assert mode in ('damon_disabled','default')
    previous=audit(directories,balanced_mode=mode)
    assert abs(previous['analysis']['control']['group_percent'][0])<=5
    prefix='exp1-monitor-default-followup' if mode=='default' else 'exp1-monitor-damon-followup'
    out=ROOT/'.artifacts'/prefix/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True);result=dict(status='FAIL',groups=[],scope='remaining balanced groups; preserve completed raw data')
    print(f'status=RUNNING result_dir={out}',flush=True)
    def save():
        plan.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    try:
        for number in range(len(groups)+1,6):
            seed=state['balanced_seed_base']+number-1
            command=[sys.executable,str(ROOT/'scripts/run_fixed_frequency_monitor_smoke.py'),
                     '--balanced','--seed',str(seed),'--monitor-damon-mode',mode]
            (out/f'group-{number}-command.json').write_text(json.dumps(command)+'\n')
            log=out/f'group-{number}.log'
            with log.open('w') as stream:
                completed=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT)
            paths=[line.split('result_dir=',1)[1] for line in log.read_text().splitlines() if line.startswith('status=RUNNING result_dir=')]
            assert len(paths)==1
            directory=Path(paths[0]);row=dict(group=number,seed=seed,result_dir=str(directory.relative_to(ROOT)),status='FAIL')
            result['groups'].append(row)
            assert completed.returncode==0,f'group {number} failed, see {log}'
            value=audit(directories+[directory],balanced_mode=mode)
            row.update(status='PASS',offline_audit='PASS',differences=value['groups'][-1]['differences']['task_window_seconds'])
            directories.append(directory);groups.append(dict(row))
            result['analysis']=value['analysis'];state.update(status=f'BALANCED_GROUP_{number}_AUDITED',result_dir=str(out.relative_to(ROOT)))
            (out/'summary.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');save()
            print(f"status=GROUP_PASS group={number}/5 verdict={value['monitoring_acceptance']} result_dir={out}",flush=True)
        result['status']='PASS';state.update(status='COMPLETED',monitoring_acceptance=result['analysis']['verdict'])
    except Exception:
        result['error']=traceback.format_exc();(out/'failure.txt').write_text(result['error'])
        state.update(status='FOLLOWUP_FAILED',failed_result_dir=str(out.relative_to(ROOT)))
    save();print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-section',choices=('monitoring_return_to_original_replay','monitoring_default_policy'),default='monitoring_return_to_original_replay')
    raise SystemExit(main(parser.parse_args().plan_section))
