"""Bounded persistent guest/host sampler controls on owned TrEnv-X instances."""
import argparse
import json
import os
from pathlib import Path
import random
import re
import shutil
import time
import traceback

from archive_trenvx_run_data import allocated_bytes, sha256
from audit_exp1_metrics import audit_run
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from report_memory_metrics import report
from run_persistent_metrics_comparison import summarize
from run_trenvx_monitor_balanced import IDENTITY, SOURCES as OLD_SOURCES

ROOT=Path(__file__).resolve().parents[1]
METRICS=('task_window_ns','guest_action_sum_ns','controller_end_to_end_ns')
SOURCES=tuple(dict.fromkeys(OLD_SOURCES+('run_trenvx_persistent_comparison.py',
    'run_persistent_metrics_comparison.py','guest_memory_collector.py','persistent_guest_sampling.py',
    'trenvx_diagnostic_metrics.py','report_memory_metrics.py','memory_composition.py',
    'audit_exp1_metrics.py','exp1_official_task.py')))


def orders(stage,groups,seed):
    if stage=='smoke' and groups!=1:raise ValueError('smoke requires one group')
    rng=random.Random(seed);result=[]
    for _ in range(groups):
        labels=['off','host','guest','both'] if stage=='smoke' else ['control_A','control_B','off','host','guest','both']
        if stage=='balanced':rng.shuffle(labels)
        result.append(labels+labels[::-1])
    return result


def inspect_run(directory,label):
    guest=label in ('guest','both');host=label in ('host','both')
    cfg=json.loads((directory/'config.json').read_text());run=json.loads((directory/'result.json').read_text())
    assert cfg['checkpoint_after'] is None and cfg['discard_success_images']
    assert not cfg['diagnostic_metrics'] and not cfg['file_physical_diagnostic'] and cfg['record_phases']
    assert cfg['guest_memory_sampling']==guest and cfg['guest_collector']==('persistent' if guest else 'original')
    assert cfg['host_sample_interval_seconds']==(.1 if host else 0)
    assert cfg['guest_sample_interval_seconds']==.1
    assert run['status']=='PASS' and all(v=='PASS' for v in run['checks'].values())
    checked=audit_run(dict(backend='trenvx',mode='full-light' if host else 'full',raw=str(directory)))
    assert checked['final_checks']['status']=='PASS' and checked['cleanup']['status']=='PASS'
    official=json.loads((directory/'official-task-result.json').read_text())
    assert official['status']=='PASS' and official['parser_results']['num_tests_passed']==4
    assert checked['guest_memory']['sample_count']>=2 if guest else checked['guest_memory']['sample_count']==0
    assert checked['host_memory']['sample_count']>=2 if host else checked['host_memory']['sample_count']==0
    memory=report(dict(backend='trenvx',mode='full-light' if host else 'full',raw=str(directory)))
    if guest:assert memory['guest_composition']['status']=='PASS'
    manifest=json.loads((directory/'image-cleanup.json').read_text())
    assert manifest['source_removed'] and manifest['mode']=='successful-exp1-images-discarded'
    assert manifest['seed_sha256']==cfg['seed_sha256'] and not (directory/'data').exists()
    guards=[json.loads(line) for line in (directory/'disk-guard.jsonl').read_text().splitlines()]
    assert len(guards)==1 and guards[0]['status']=='PASS'
    retained=allocated_bytes(directory);assert retained<=128*2**20
    timing=checked['timing_ns']
    row=dict(label=label,run_id=directory.name,raw_dir=str(directory),
        task_window_ns=timing['controller_runner_call_ns'],guest_action_sum_ns=timing['guest_runner_action_sum_ns'],
        controller_end_to_end_ns=timing['controller_end_to_end_ns'],
        guest_sample_count=checked['guest_memory']['sample_count'],host_sample_count=checked['host_memory']['sample_count'],
        guest_peak_nonfree_bytes=memory['guest_composition']['observed_peak_nonfree_bytes'],
        host_peak_current_bytes=checked['host_memory']['observed_peak_current_bytes'],
        action_manifest_sha256=checked['action_manifest_sha256'],patch_sha256=checked['final_patch_sha256'],
        retained_allocated_bytes=retained,observed_min_free_bytes=guards[0]['observed_min_free_bytes'])
    if guest:row['guest_collector']=memory['guest_collector']
    return row,{key:cfg[key] for key in IDENTITY}


def audit(directory):
    saved=json.loads((directory/'result.json').read_text());assert saved['status']=='PASS'
    protocol=json.loads((directory/'protocol.json').read_text());assert protocol==saved['protocol']
    assert protocol['orders']==orders(protocol['stage'],protocol['group_count'],protocol['seed'])
    assert len(saved['groups'])==protocol['group_count']
    for number,(group,order) in enumerate(zip(saved['groups'],protocol['orders']),1):
        assert len(group)==len(order)
        for position,(row,label) in enumerate(zip(group,order),1):
            assert row['group']==number and row['position']==position and row['label']==label
            checked,identity=inspect_run(Path(row['raw_dir']),label)
            assert identity==saved['identity']
            for key,value in checked.items():
                if key!='retained_allocated_bytes':assert row[key]==value,key
    sources=json.loads((directory/'code/sha256.json').read_text())
    assert sources=={name:sha256(directory/'code'/name) for name in sources}
    analysis=summarize(saved['groups'],METRICS);assert analysis==saved['analysis']
    return dict(status='PASS',run_count=sum(map(len,saved['groups'])),protocol=protocol,analysis=analysis)


def main(args):
    os.umask(0o077)
    out=ROOT/'.artifacts/trenvx-persistent-comparison'/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    out.mkdir(parents=True,exist_ok=False);(out/'code').mkdir()
    schedule=orders(args.stage,args.groups,args.seed)
    protocol=dict(stage=args.stage,group_count=args.groups,seed=args.seed,orders=schedule,
        target_sample_interval_seconds=.1,threshold=None,
        primary_scope='controller frozen runner call including Git preparation and guest collector start/stop',
        end_to_end_scope='outer controller start through tests, cleanup and private image hash inventory/removal',
        host_scope='run-owned sandbox parent cgroup; excludes orchestrator',
        guest_scope='one persistent collector per runner; includes preparation; short steps may have no samples',
        cpu_cache_policy='original frequency/affinity and guest defaults; fresh private VM; host cache preserved; no cache drops',
        official_tests='four task assertions in every run; independent properties and final patch also verified',
        stop_policy='first failure, source/environment change, oversized raw results or disk guard; no automatic retries',
        inference='five independent group log ratios with approximate t95 intervals; no PPT performance threshold',
        checkpoint_restore='NOT_MEASURED',physical_scans=False)
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    hashes={name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    result=dict(status='FAIL',protocol=protocol,groups=[])
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        total=sum(map(len,schedule));done=0
        for group,order in enumerate(schedule,1):
            rows=[];result['groups'].append(rows)
            for position,label in enumerate(order,1):
                assert hashes=={name:sha256(ROOT/'scripts'/name) for name in SOURCES},'source changed'
                directory=out/f'group-{group}-run-{position}-{label}';directory.mkdir()
                plan=successful_image_cleanup_budget(shutil.disk_usage(out).free,total-done)
                (directory/'storage-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
                assert plan['status']=='READY',plan
                argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images','--record-phases',
                    '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                    '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
                if label in ('guest','both'):argv+=['--guest-memory-sampling','--guest-collector','persistent','--guest-sample-interval','0.1']
                if label in ('host','both'):argv+=['--host-sample-interval','0.1']
                with (directory/'driver.log').open('w') as log:guarded_run(argv,directory=directory,log=log,timeout=600)
                found=re.findall(r'result_dir=(\S+)',(directory/'driver.log').read_text());assert len(found)==1
                raw=Path(found[0]).resolve(strict=True);assert raw.parent==(ROOT/'.artifacts/trenvx-service-smoke').resolve()
                row,identity=inspect_run(raw,label)
                if 'identity' not in result:result['identity']=identity
                assert identity==result['identity'],'effective environment changed'
                row.update(group=group,position=position);rows.append(row);done+=1
                (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'status=REPLAY_PASS group={group}/{args.groups} position={position}/{len(order)} case={label} result_dir={out}',flush=True)
        result.update(status='PASS',analysis=summarize(result['groups'],METRICS))
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('smoke','balanced'),default='smoke')
    parser.add_argument('--groups',type=int,choices=(1,5),default=1)
    parser.add_argument('--seed',type=int,default=20261002)
    parser.add_argument('--audit',type=Path)
    args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit.resolve())))
    else:raise SystemExit(main(args))
