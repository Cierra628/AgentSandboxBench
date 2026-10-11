"""One functional smoke, then at most four ABBA sampler controls; no retries."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback
from statistics import mean

from archive_trenvx_run_data import sha256
from experiment_disk_guard import guarded_run
from plan_trenvx_storage import successful_image_cleanup_budget
from run_trenvx_persistent_comparison import inspect_run, SOURCES as OLD_SOURCES, METRICS

ROOT=Path(__file__).resolve().parents[1]
SOURCES=tuple(dict.fromkeys(OLD_SOURCES+('run_trenvx_fixed_identity.py','fixed_runner_git_identity.py','guest_runner_phases.py',
    'guest_runner_lifecycle.py','command_clock.c')))
ORDER=('off','both','both','off')


def cpu_policies():
    return {p.name:{name:(p/name).read_text().strip() for name in
        ('scaling_driver','scaling_governor','scaling_min_freq','scaling_max_freq')}
        for p in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*'))}


def inspect(raw,label):
    row,identity=inspect_run(raw,label)
    cfg=json.loads((raw/'config.json').read_text())
    replay=json.loads((raw/'replay-config.json').read_text())
    assert cfg['fixed_git_identity'] and not cfg['runner_phases'] and not cfg['git_wait_diagnostic']
    assert not cfg['git_identity_diagnostic'] and not cfg['runner_phase_calibration']
    assert cfg['fixed_git_identity_values']=={'user.name':'asb-fixture','user.email':'asb-fixture@localhost'}
    assert replay['fixed_git_identity']==cfg['fixed_git_identity_values']
    assert not any(k.startswith('GIT_') for k in replay['environment'])
    assert replay['runner_sha256']==sha256(raw/'inputs/runner.sh')
    assert cfg['fixed_git_identity_sources']=={name:sha256(raw/name) for name in cfg['fixed_git_identity_sources']}
    identity.update(fixed_git_identity_values=cfg['fixed_git_identity_values'],fixed_git_identity_sources=cfg['fixed_git_identity_sources'])
    row['runner_sha256']=replay['runner_sha256']
    if cfg.get('runner_lifecycle_diagnostic',False):
        from guest_runner_lifecycle import collect
        measured=collect(raw/'replay',label=='both',row['guest_action_sum_ns'],row['task_window_ns'])
        assert measured==json.loads((raw/'runner-lifecycle.json').read_text())
        assert cfg['runner_lifecycle_sources']=={name:sha256(raw/name) for name in cfg['runner_lifecycle_sources']}
        identity['runner_lifecycle_sources']=cfg['runner_lifecycle_sources']
        row['runner_lifecycle']=measured
    return row,identity


def analysis(rows):
    assert [r['label'] for r in rows]==list(ORDER)
    return {metric:dict(off_mean_seconds=mean(r[metric] for r in rows if r['label']=='off')/1e9,
        both_mean_seconds=mean(r[metric] for r in rows if r['label']=='both')/1e9,
        both_vs_off_percent=100*(mean(r[metric] for r in rows if r['label']=='both')/mean(r[metric] for r in rows if r['label']=='off')-1),
        last_vs_first_off_percent=100*(rows[-1][metric]/rows[0][metric]-1)) for metric in METRICS}


def lifecycle_analysis(rows):
    metrics={}
    for row in rows:
        life=row['runner_lifecycle']
        values=dict(life['durations_ns'],guest_envelope_ns=life['clock']['monotonic_raw_ns'],
                    controller_minus_guest_ns=life['controller_minus_guest_envelope_ns'])
        for key,value in values.items():metrics.setdefault(key,{'off':[],'both':[]})[row['label']].append(value)
    return {key:dict(off_mean_ns=mean(v['off']),both_mean_ns=mean(v['both']),difference_ns=mean(v['both'])-mean(v['off']),
        last_minus_first_off_ns=v['off'][-1]-v['off'][0]) for key,v in metrics.items()}


def audit(out):
    saved=json.loads((out/'result.json').read_text());assert saved['status']=='PASS'
    assert saved['protocol']==json.loads((out/'protocol.json').read_text())
    assert len(saved['runs'])==4 and saved['protocol']['order']==list(ORDER)
    for row in [saved['smoke'],*saved['runs']]:
        checked,identity=inspect(Path(row['raw_dir']),row['label'])
        assert identity==saved['identity']
        for key,value in checked.items():
            if key!='retained_allocated_bytes':assert value==row[key],key
    assert analysis(saved['runs'])==saved['analysis']
    if saved['protocol'].get('runner_lifecycle_diagnostic',False):
        from guest_runner_lifecycle import audit_calibration
        assert lifecycle_analysis(saved['runs'])==saved['lifecycle_analysis']
        assert audit_calibration(Path(saved['smoke']['raw_dir'])/'lifecycle-calibration')['status']=='PASS'
    hashes=json.loads((out/'code/sha256.json').read_text())
    assert hashes=={name:sha256(out/'code'/name) for name in hashes}
    assert json.loads((out/'cpu-before.json').read_text())==json.loads((out/'cpu-after.json').read_text())
    return dict(status='PASS',run_count=5,analysis=saved['analysis'])


def main(lifecycle=False):
    os.umask(0o077)
    out=ROOT/('.artifacts/trenvx-runner-lifecycle' if lifecycle else '.artifacts/trenvx-fixed-identity')/f"{time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())}-{os.getpid()}"
    out.mkdir(parents=True);(out/'code').mkdir()
    protocol=dict(order=list(ORDER),functional_smoke='off; excluded from contrasts',
        task='Prettier 14400, frozen 26 actions',fixed_identity_scope='Git setup/finalization commands only; frozen actions and environment preserved',
        guest_sampling='persistent, 100 ms',host_sampling='owned sandbox parent cgroup, 100 ms; excludes orchestrator',
        cpu_cache_policy='existing CPU policies; fresh private VM each run; host/guest caches preserved; no drops',
        inference='one ABBA group, two runs per condition; descriptive only, no confidence interval or performance threshold',
        runner_lifecycle_diagnostic=lifecycle,
        lifecycle_scope='guest outer dual clock and collector startup/stop; diagnostic instrumentation in both conditions' if lifecycle else None,
        stop_policy='first failure/source change/disk guard; no retry or automatic extension',
        checkpoint_restore='NOT_MEASURED',file_physical_memory='NOT_MEASURED',retention='successful owned images discarded; raw retained')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    hashes={name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    result=dict(status='FAIL',protocol=protocol,runs=[])
    cpu_before=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(cpu_before,indent=2)+'\n')
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        for position,label in enumerate(('off',*ORDER)):
            assert hashes=={name:sha256(ROOT/'scripts'/name) for name in SOURCES},'source changed'
            assert cpu_policies()==cpu_before,'CPU policy changed'
            directory=out/('smoke' if position==0 else f'run-{position}-{label}');directory.mkdir()
            plan=successful_image_cleanup_budget(shutil.disk_usage(out).free,5-position)
            (directory/'storage-plan.json').write_text(json.dumps(plan,indent=2)+'\n');assert plan['status']=='READY',plan
            argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--discard-success-images','--record-phases','--fixed-git-identity',
                  '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                  '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
            if label=='both':argv+=['--guest-memory-sampling','--guest-collector','persistent','--guest-sample-interval','0.1','--host-sample-interval','0.1']
            if lifecycle:
                argv+=['--runner-lifecycle-diagnostic']
                if position==0:argv+=['--lifecycle-calibration']
            with (directory/'driver.log').open('w') as log:guarded_run(argv,directory=directory,log=log,timeout=600)
            found=re.findall(r'result_dir=(\S+)',(directory/'driver.log').read_text());assert len(found)==1
            raw=Path(found[0]).resolve(strict=True);assert raw.parent==(ROOT/'.artifacts/trenvx-service-smoke').resolve()
            row,identity=inspect(raw,label)
            if 'identity' not in result:result['identity']=identity
            assert identity==result['identity'],'effective environment changed'
            row['position']=position
            if position==0:result['smoke']=row
            else:result['runs'].append(row)
            (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
            print(f'status=REPLAY_PASS position={position}/4 case={label} result_dir={out}',flush=True)
        assert cpu_policies()==cpu_before,'CPU policy changed'
        result.update(status='PASS',analysis=analysis(result['runs']))
        if lifecycle:result['lifecycle_analysis']=lifecycle_analysis(result['runs'])
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    if result['status']=='PASS':(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--audit',type=Path)
    parser.add_argument('--lifecycle-diagnostic',action='store_true')
    args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main(args.lifecycle_diagnostic))
