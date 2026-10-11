"""Bounded frozen Exp1 comparison of original guest/host sampling, no new threshold."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import time
import traceback

from experiment_disk_guard import writer_lock
from run_exp1_monitor_balanced import run_one, schedule
from report_memory_metrics import report, write_report

ROOT=Path(__file__).resolve().parents[1]


def comparisons(rows):
    result={}
    for metric in ['task_window_ns','controller_exec_ns','guest_action_sum_ns','controller_end_to_end_ns']:
        means={label:statistics.mean(row[metric] for row in rows if row['label']==label)
               for label in sorted({row['label'] for row in rows})}
        result[metric]={'means_ns':means,'light_vs_off_percent':100*(means['light']/means['off']-1)}
        if 'control_A' in means:
            result[metric]['control_B_vs_A_percent']=100*(means['control_B']/means['control_A']-1)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--stage',choices=('smoke','balanced'),required=True)
    parser.add_argument('--seed',type=int,default=20261001)
    args=parser.parse_args()
    cfg=json.loads(args.config.read_text())
    assert cfg['fixed_frequency_window'] and cfg['damon_control_mode']=='damon_disabled'
    assert cfg['sandbox_cpu']==2 and cfg['sandbox_memory_mib']==4096 and cfg['drop_guest_caches']==0
    repo=Path(cfg['repository']);repo=repo if repo.is_absolute() else ROOT/repo
    host=repo/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh'
    text=host.read_text()
    assert 'fixed-frequency' in text and ('damon-disabled' in text or 'damon_disabled' in text)
    out=ROOT/'.artifacts/exp1-guest-metrics'/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    os.umask(0o077);out.mkdir(parents=True,exist_ok=False)
    order=['off','light'] if args.stage=='smoke' else schedule(args.seed,1)[0]
    protocol=dict(stage=args.stage,seed=args.seed,order=order,guest_sample_interval_seconds=.1,
        host_sample_interval_seconds=.1,guest_sampler='original per-step before/running/after collector',
        control='both control labels off; off/light actual comparison',cache_policy='preserve; DAMON disabled',
        timing_scope='task window includes sampling start/stop; full controller lifetime separately recorded',
        threshold=None,statistical_scope='one descriptive group; no confidence interval or acceptance threshold',
        physical_pages='NOT_MEASURED; fine scans excluded',reference='frozen 26 actions, fixed original runner')
    sources=[Path(__file__),ROOT/'scripts/run_exp1.py',ROOT/'scripts/run_exp1_monitor_balanced.py',
             host,host.with_name('guest-runner.sh'),repo/'motivation/experiments/lib/host-memory-monitor.sh']
    sources += [ROOT/'scripts'/name for name in ('memory_composition.py','report_memory_metrics.py')]
    snapshot=out/'code';snapshot.mkdir()
    hashes={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    for index,path in enumerate(sources):shutil.copy2(path,snapshot/f'{index:02}-{path.name}')
    (out/'source-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    rows=[];result=dict(status='FAIL',protocol=protocol,runs=rows)
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        with writer_lock():
            for number,label in enumerate(order,1):
                assert hashes=={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
                row=run_one(out,cfg,number,label,sample_guest=True,sample_interval_seconds=.1)
                assert row['guest_sample_count']>=55 if label=='light' else row['guest_sample_count']==0
                value=report(dict(backend='agentenv',mode='full',raw=row['raw_dir'],audit=row['audit_dir']))
                if label=='light':assert value['guest_composition']['status']=='PASS'
                write_report(value,out/f'memory-report-{number:02}')
                row['memory_report']=str(out/f'memory-report-{number:02}')
                rows.append(row)
                (out/'progress.json').write_text(json.dumps(rows,indent=2)+'\n')
                print(f'status=REPLAY_PASS position={number}/{len(order)} guest_samples={row["guest_sample_count"]} result_dir={out}',flush=True)
        result.update(status='PASS',comparison=comparisons(rows))
    except BaseException:
        (out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
