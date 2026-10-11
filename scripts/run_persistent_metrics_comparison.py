"""Bounded factorial sampler comparison with matched off/off controls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import time
import traceback
from experiment_disk_guard import writer_lock
from run_exp1_monitor_balanced import run_one,estimate
from report_memory_metrics import report,write_report

ROOT=Path(__file__).resolve().parents[1]
METRICS=('task_window_ns','controller_exec_ns','guest_action_sum_ns','controller_end_to_end_ns')


def summarize(groups,metrics=METRICS):
    import statistics
    result={}
    for metric in metrics:
        contrasts={case:[] for case in ('host','guest','both','control')}
        for rows in groups:
            means={case:statistics.mean(row[metric] for row in rows if row['label']==case)
                   for case in {row['label'] for row in rows}}
            for case in ('host','guest','both'):contrasts[case].append(100*(means[case]/means['off']-1))
            if 'control_A' in means:contrasts['control'].append(100*(means['control_B']/means['control_A']-1))
        result[metric]={case:estimate(values) for case,values in contrasts.items() if values}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--stage',choices=('smoke','balanced'),required=True)
    parser.add_argument('--groups',type=int,choices=(1,5),default=1)
    parser.add_argument('--seed',type=int,default=20261011)
    args=parser.parse_args();assert args.stage=='balanced' or args.groups==1
    cfg=json.loads(args.config.read_text());repo=Path(cfg['repository'])
    assert repo.is_absolute() and cfg['fixed_frequency_window'] and cfg['damon_control_mode']=='damon_disabled'
    assert cfg['sandbox_cpu']==2 and cfg['sandbox_memory_mib']==4096 and cfg['drop_guest_caches']==0
    host=repo/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh'
    collector=Path(cfg['persistent_guest_collector'])
    sources=[Path(__file__),ROOT/'scripts/run_exp1.py',ROOT/'scripts/run_exp1_monitor_balanced.py',
             ROOT/'scripts/report_memory_metrics.py',ROOT/'scripts/memory_composition.py',
             host,host.with_name('guest-runner.sh'),collector,repo/'motivation/experiments/lib/host-memory-monitor.sh']
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    os.umask(0o077)
    out=ROOT/'.artifacts/persistent-metrics'/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    out.mkdir(parents=True,exist_ok=False);(out/'code').mkdir()
    for i,path in enumerate(sources):shutil.copy2(path,out/'code'/f'{i:02}-{path.name}')
    (out/'source-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
    rng=random.Random(args.seed);orders=[]
    for group in range(args.groups):
        first=['off','host','guest','both'] if args.stage=='smoke' else ['control_A','control_B','off','host','guest','both']
        if args.stage=='balanced':rng.shuffle(first)
        orders.append(first if args.stage=='smoke' else first+first[::-1])
    protocol=dict(stage=args.stage,seed=args.seed,orders=orders,interval_seconds=.1,
        guest_sampler='one persistent Python process per runner call, includes preparation; asynchronous phase markers',
        host_sampler='service cgroup only',physical_scans=False,threshold=None,
        scope='task window includes collector start/stop; short actions may have no sample; fresh VM, host cache preserved')
    result=dict(status='FAIL',protocol=protocol,groups=[])
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    print(f'status=RUNNING result_dir={out}',flush=True)
    identity=None;counter=0
    try:
        with writer_lock():
            for group,order in enumerate(orders,1):
                rows=[];result['groups'].append(rows)
                for position,case in enumerate(order,1):
                    assert hashes=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
                    counter+=1;on=case in ('guest','both');host_on=case in ('host','both')
                    row=run_one(out,cfg,counter,'light' if on or host_on else 'off',
                                sample_guest=on,sample_host=host_on,sample_interval_seconds=.1)
                    row.update(label=case,group=group,position=position)
                    fixed={key:row['metadata'][key] for key in ('workload_image','sandbox_cpu','sandbox_memory_mib',
                        'action_manifest_sha256','aenv_version','host_kernel','agentenv_container_image','agentenv_container_id',
                        'overlaybd_io_engine','overlaybd_direct_io')}
                    if identity is None:identity=fixed
                    assert fixed==identity
                    raw=Path(row['raw_dir'])
                    assert json.loads((raw/'fixed-frequency/result.json').read_text())['status']=='PASS'
                    assert json.loads((raw/'fixed-frequency/frequency-policy/restored.json').read_text())['status']=='PASS'
                    for phase in ('prepare','before_task','after_task'):
                        assert json.loads((raw/f'damon-{phase}.json').read_text())['status']=='PASS'
                    if on:
                        receipt=json.loads((raw/'replay/collector-status.json').read_text())
                        assert receipt['status']=='PASS' and receipt['samples']==row['guest_sample_count']>=2
                        row['guest_collector']=receipt
                    else:assert row['guest_sample_count']==0 and not (raw/'replay/collector-status.json').exists()
                    value=report(dict(backend='agentenv',mode='full',raw=row['raw_dir'],audit=row['audit_dir']))
                    if on:assert value['guest_composition']['status']=='PASS'
                    write_report(value,out/f'memory-report-{counter:02}')
                    rows.append(row)
                    (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                    print(f'status=REPLAY_PASS group={group}/{args.groups} position={position}/{len(order)} case={case} result_dir={out}',flush=True)
        result.update(status='PASS',analysis=summarize(result['groups']),identity=identity)
    except BaseException:(out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
