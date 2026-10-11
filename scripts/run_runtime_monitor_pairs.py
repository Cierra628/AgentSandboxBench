"""Matched complete Sphinx runs; quantify lightweight guest + host memory monitoring."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import statistics
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from isolated_storage import GIB,capacity_budget,available
from sphinx7590_inputs import validate,sha,grade_xml,verify_final_state,verify_pipeline_replay
from xarray6992_inputs import normalize_patch
from run_prettier6604_grpo import verify_path


def inspect_run(raw,mode):
    if raw.name=='prettier6604':
        from prettier6604_monitor_pairs import inspect_run as inspect_prettier
        return inspect_prettier(raw,mode)
    cfg,_,samples,names=validate(raw/'prepared-input')
    result=json.loads((raw/'result.json').read_text());outer=json.loads((raw.parent/'result.json').read_text())
    assert outer['status']==result['status']=='PASS' and result['monitor_mode']==mode
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
        'original_firecracker_identities_unchanged','original_service_unchanged'))
    pipeline=result.get('sphinx_pipeline_diagnostic',False)
    pipeline_version=result.get('sphinx_pipeline_policy_version',1)
    if pipeline_version==2:assert outer['sphinx_pipeline_policy_version']==2
    if pipeline:assert result['diagnostic_status']==outer['diagnostic_status']=='PASS' and result['strict_replay_status']==outer['strict_replay_status']=='FAIL'
    replay=verify_pipeline_replay(raw,cfg,cfg['path'],samples,names,sampling=mode=='continuous',policy_version=pipeline_version) if pipeline else verify_path(raw,'backbone',cfg['path'],raw/'frozen-input',samples,names,sampling=mode=='continuous')
    assert replay==result['replay'] and replay['action_count']==35
    assert all(result['checks'][k]=='PASS' for k in ('first_segment_smoke','key_outputs','frozen_actions_order_and_diagnostic_exit_policy' if pipeline else 'actions_hash_order_expected_failures',
        'final_patch_and_source_hashes','complete_benchmark_results_and_source_unchanged'))
    original=raw/'frozen-input/guest-runner.original.sh';executed=raw/'frozen-input/guest-runner.sh'
    from sandbox_runtime_monitor import boundary_runner
    from task_metrics_prefix import without_sampling
    assert sha(original)==cfg['input_sha256']['guest-runner.sh']==result['sampling_variant']['original_runner_sha256']
    expected_runner=boundary_runner(original.read_text(),guest_phases=result['sampling_variant'].get('phase_updates')=='guest runner') if mode=='continuous' else without_sampling(original.read_text())
    assert executed.read_text()==expected_runner
    assert sha(executed)==result['sampling_variant']['executed_runner_sha256']
    for segment,step,fragment in cfg['key_outputs']:
        assert fragment in (raw/'rollouts/backbone'/segment/'stdout'/f'{step:03}.log').read_text()
    assert normalize_patch((raw/'final.patch').read_text())==normalize_patch((raw/'prepared-input/reference.patch').read_text())
    assert verify_final_state(json.loads((raw/'source-before.json').read_text()),json.loads((raw/'final-files.json').read_text()),cfg)==result['image_background']
    task=json.loads((raw/'prepared-input/swebench-task.json').read_text());f2p=json.loads(task['FAIL_TO_PASS']);p2p=json.loads(task['PASS_TO_PASS'])
    xmls=list((raw/'evaluation').rglob('official.xml'));assert len(xmls)==1
    evaluation=json.loads((raw/'evaluation-receipt.json').read_text())
    assert grade_xml(xmls[0].read_text(),f2p+p2p,f2p,p2p,evaluation['exit_code'])==result['official']
    if mode=='off':
        assert not (raw/'runtime-monitor.json').exists() and not (raw/'host/memory-samples.tsv').exists()
        assert result['cleanup']['monitoring_disabled']
        for segment in cfg['path']:
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):
                with (raw/'rollouts/backbone'/segment/name).open() as f:assert not list(csv.DictReader(f,delimiter='\t'))
    else:
        from report_sandbox_runtime import samples as monitored_samples
        groups,_=monitored_samples(raw);assert len(groups)==1
        assert result['cleanup']['monitor_stopped']
        if pipeline:
            from report_sandbox_performance import audit
            performance=audit(raw,groups)
            assert all(d['guest'] and d['host'] for d in performance.values())
    return dict(**(dict(sphinx_pipeline_policy_version=2) if pipeline_version==2 else {}),**(dict(sphinx_pipeline_diagnostic=True,strict_replay_status=result['strict_replay_status'],exit_differences=replay['exit_differences']) if pipeline else {}),raw=str(raw),mode=mode,configuration=cfg,official=result['official'],final_patch_sha256=sha(raw/'final.patch'),
        final_files=json.loads((raw/'final-files.json').read_text()),source_before=json.loads((raw/'source-before.json').read_text()),
        guest_action_seconds=replay['guest_action_sum_ns']/1e9,frozen_replay_seconds=result['frozen_replay_elapsed_ns']/1e9,
        lifecycle_seconds=result['controller_elapsed_ns']/1e9,
        observed_volume_used_peak_bytes=outer['observed_volume_used_peak_bytes'])


def comparison(pairs):
    assert pairs
    summary={}
    rng=random.Random(6604)
    for metric in ('guest_action_seconds','frozen_replay_seconds','lifecycle_seconds'):
        a=[p['off'][metric] for p in pairs];b=[p['continuous'][metric] for p in pairs]
        effects=[100*(y/x-1) for x,y in zip(a,b)]
        boots=sorted(statistics.mean(rng.choices(effects,k=len(effects))) for _ in range(10000))
        summary[metric]=dict(off_seconds=a,continuous_seconds=b,paired_percent=effects,
            mean_paired_percent=statistics.mean(effects),median_paired_percent=statistics.median(effects),
            min_paired_percent=min(effects),max_paired_percent=max(effects),
            bootstrap_mean_95_percent_interval=[boots[249],boots[9749]] if len(pairs)>1 else None,
            off_cv_percent=statistics.stdev(a)/statistics.mean(a)*100 if len(a)>1 else None,
            continuous_cv_percent=statistics.stdev(b)/statistics.mean(b)*100 if len(b)>1 else None)
    return summary


def tool_variability(pairs):
    """Keep every pair and show which original step has the largest timing range."""
    values={};order=None
    for pair in pairs:
        current={}
        for mode in ('off','continuous'):
            run=pair[mode];raw=Path(run['raw']);keys=[]
            for segment in run['configuration']['path']:
                directory=raw/'rollouts/backbone'/segment
                with (directory/'actions.tsv').open() as f:actions=list(csv.DictReader(f,delimiter='\t'))
                with (directory/'steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
                assert [r['step'] for r in steps]==[r['step'] for r in actions]
                for action,step in zip(actions,steps):
                    key=(segment,step['step'],action['sha256']);keys.append(key)
                    duration=int(step['duration_ns'])/1e9;assert duration>=0
                    entry=values.setdefault(key,dict(off=[],continuous=[]))
                    entry[mode].append(duration)
            current[mode]=keys
        assert current['off']==current['continuous'],'paired frozen action identities differ'
        if order is None:order=current['off']
        assert current['off']==order,'frozen action order changed across pairs'
    rows=[]
    for key in order or []:
        data=values[key];row=dict(segment=key[0],step=key[1],command_sha256=key[2])
        for mode,series in data.items():
            mean=statistics.mean(series)
            row[mode]=dict(seconds=series,mean_seconds=mean,min_seconds=min(series),max_seconds=max(series),
                cv_percent=100*statistics.stdev(series)/mean if len(series)>1 and mean>0 else None)
        combined=data['off']+data['continuous'];row['pooled_range_seconds']=max(combined)-min(combined)
        row['paired_delta_seconds']=[b-a for a,b in zip(data['off'],data['continuous'])]
        rows.append(row)
    return dict(unit='seconds',steps=rows,largest_range_steps=sorted(rows,key=lambda r:r['pooled_range_seconds'],reverse=True)[:5],
        scope='original per-step runner intervals; all pairs retained, no threshold or automatic outlier exclusion; ranking does not establish cause')


def report(directory,output):
    value=json.loads((directory/'result.json').read_text())
    assert value['status']=='PASS' and value['completed_pairs']==value['plan']['pairs'] and value['cache_released']
    raw_roots=[Path(p[m]['raw']) for p in value['pairs'] for m in ('off','continuous')]
    assert not output.is_relative_to(directory) and all(not output.is_relative_to(raw) for raw in raw_roots),'report must be outside original data'
    def hashes():
        result={}
        for raw in raw_roots:
            for p in sorted(raw.rglob('*')):
                if p.is_file():
                    with p.open('rb') as f:result[str(p.relative_to(ROOT))]=hashlib.file_digest(f,'sha256').hexdigest()
        return result
    before=hashes();verified=[]
    for p in value['pairs']:
        row=dict(pair=p['pair'],order=p['order'])
        for mode in ('off','continuous'):
            row[mode]=inspect_run(Path(p[mode]['raw']),mode)
            assert row[mode]==p[mode]
            if value['plan'].get('sphinx_pipeline_policy_version')==2:
                assert row[mode]['sphinx_pipeline_policy_version']==2,'paired policy version differs from plan'
        for key in ('configuration','official','final_patch_sha256','final_files','source_before',*(['tool_output_signatures'] if value['plan'].get('task_profile')=='prettier6604' else [])):
            assert row['off'][key]==row['continuous'][key]
        verified.append(row)
    assert comparison(verified)==value['metrics'] and hashes()==before
    output.mkdir(parents=True,exist_ok=False)
    (output/'input-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    public=dict(status='PASS',scope=value['plan']['scope'],plan=value['plan'],metrics=value['metrics'],
        completed_pairs=value['completed_pairs'],source_directory=str(directory.relative_to(ROOT)),raw_files=len(before),raw_hashes_unchanged=True,
        semantic_checks='35 frozen actions, expected exits, key outputs, source/patch, all 25 independent test outcomes, zero unexpected cleanup changes',
        runs=[dict(pair=p['pair'],order=p['order'],**{mode:{k:v for k,v in p[mode].items() if k not in ('configuration','source_before','final_files')} for mode in ('off','continuous')}) for p in verified])
    if 'replay_variant' in value['plan'] and value['plan'].get('task_profile')!='prettier6604':
        public.update(strict_replay_status='FAIL',diagnostic_status='PASS',
            semantic_checks='35 frozen actions; approved exact read_repo/001 output/patch permits 0 or 141; all other exits strict; key outputs, final source/patch, all 25 independent task outcomes and cleanup checked')
    if value['plan'].get('sphinx_pipeline_policy_version')==2:
        public['semantic_checks']='35 frozen actions; approved exact read_repo/001 and core_patch/001 output/patch permits 0 or 141; all other exits strict; key outputs, final source/patch, all 25 independent task outcomes and cleanup checked'
    prettier=value['plan'].get('task_profile')=='prettier6604'
    if prettier:
        public['strict_replay_status']='PASS'
        public['semantic_checks']='29 unchanged frozen actions; all exits strict; source/patch, independent properties and original two official failures preserved; original output hashes retained, only known Jest timing and valid tail PASS paths separated'
        public['original_output_differences']=[dict(pair=p['pair'],keys=[k for k,v in p['off']['original_output_hashes'].items() if v!=p['continuous']['original_output_hashes'][k]]) for p in verified]
    costs=[]
    for p in verified:
        raw=Path(p['continuous']['raw']);events=json.loads((raw/'events.json').read_text())
        begin=next(e['start_monotonic_ns']+e['duration_ns'] for e in events if e['stage']==('upload-frozen-workload' if prettier else 'upload-frozen'))
        end=next(e['start_monotonic_ns'] for e in events if e['stage']==('task-final-state' if prettier else 'final-files'))
        phases=[e for e in events if e['stage']=='monitor-phase' and begin<=e['start_monotonic_ns']<end]
        variant=json.loads((raw/'sampling-variant.json').read_text())
        assert len(phases)==(0 if variant.get('phase_updates')=='guest runner' else 10),'unexpected segment phase update calls'
        monitor_session=json.loads((raw/'runtime-monitor.json').read_text())['sessions'][0]
        session=monitor_session['receipt']
        with (raw/monitor_session['raw_directory']/'guest-memory.tsv').open() as f:memory=list(csv.DictReader(f,delimiter='\t'))
        benchmark='official-task-tests' if prettier else 'benchmark-tests'
        assert any(r['phase']=='waiting' for r in memory) and any(r['phase']==benchmark for r in memory),'waiting or task-test sampling missing'
        costs.append(dict(pair=p['pair'],replay_phase_update_calls=len(phases),replay_phase_update_seconds=sum(e['duration_ns'] for e in phases)/1e9,
            guest_collector_cpu_seconds=session['cpu_user_seconds']+session['cpu_system_seconds'],guest_collector_maxrss_kib=session['maxrss_kib'],
            sample_count=len(memory),waiting_samples=sum(r['phase']=='waiting' for r in memory),benchmark_samples=sum(r['phase']==benchmark for r in memory),
            max_recorded_interval_ms=max((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e6 for a,b in zip(memory,memory[1:])),
            scope='directly measured calls and collector CPU; remaining paired difference not solely attributed to sampler CPU'))
        if prettier or p['continuous'].get('sphinx_pipeline_diagnostic'):
            host=monitor_session['host_receipt']
            with (raw/monitor_session['host_directory']/'host-vmm.tsv').open() as f:host_rows=list(csv.DictReader(f,delimiter='\t'))
            scans=[int(r['pss_scan_duration_ns']) for r in host_rows if r['pss_kib']!='']
            costs[-1].update(host_vmm_collector_cpu_seconds=host['cpu_user_seconds']+host['cpu_system_seconds'],
                pss_samples=len(scans),pss_read_sum_seconds=sum(scans)/1e9,pss_read_max_seconds=max(scans)/1e9,
                cost_limits='host CPU covers owned-VMM sampler thread only; service cgroup monitor and host orchestration CPU not separately measured; guest and host CPU seconds are separate')
    public['measured_monitoring_costs']=costs
    # One sequential VM: subtract the original command intervals from the
    # controller replay span. This is a remainder, not pure monitoring time.
    remainders=[dict(pair=p['pair'],off_seconds=p['off']['frozen_replay_seconds']-p['off']['guest_action_seconds'],
        continuous_seconds=p['continuous']['frozen_replay_seconds']-p['continuous']['guest_action_seconds']) for p in verified]
    deltas=[r['continuous_seconds']-r['off_seconds'] for r in remainders]
    public['replay_remainder']=dict(unit='seconds',pairs=remainders,paired_delta_seconds=deltas,
        mean_paired_delta_seconds=statistics.mean(deltas),min_paired_delta_seconds=min(deltas),max_paired_delta_seconds=max(deltas),
        scope='controller replay span minus sum of guest command intervals; includes runner bookkeeping, sampling boundaries, transfers, checks and scheduling; not solely monitoring; no parallel duration subtraction')
    public['statistics_limits']='small paired study, both orders reported; bootstrap interval is exploratory; host frequency/background load not fixed; no formal performance threshold or cross-task generalization'
    if prettier:
        public['order_groups']={order:dict(pairs=[p['pair'] for p in verified if p['order'][0]==order],metrics=comparison([p for p in verified if p['order'][0]==order])) for order in ('off','continuous') if any(p['order'][0]==order for p in verified)}
        if value['plan'].get('continued_first_on') and len(verified)>1:
            public['continuation_groups']=dict(first_pair=dict(pairs=[1],metrics=comparison(verified[:1])),later_pairs=dict(pairs=[p['pair'] for p in verified[1:]],metrics=comparison(verified[1:])),limits='descriptive strata only; all five retained; first pair and later pairs differ in date and host/cache state, not a causal comparison')
    public['tool_timing_variability']=tool_variability(verified)
    for row in public['runs']:
        for mode in ('off','continuous'):row[mode]['raw']=str(Path(row[mode]['raw']).relative_to(ROOT))
    (output/'summary.json').write_text(json.dumps(public,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='runtime-monitor-pairs-v1'
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(12,4),constrained_layout=True)
    for ax,key,title in zip(axes,('guest_action_seconds','frozen_replay_seconds','lifecycle_seconds'),
            ('29 guest command intervals' if prettier else '35 guest command intervals','Complete frozen replay','Controller lifecycle')):
        row=public['metrics'][key]
        ax.plot(range(1,len(verified)+1),row['paired_percent'],'o-')
        ax.axhline(0,color='black',linewidth=.8);ax.set(title=title,xlabel='Pair (alternating order)',ylabel='Monitoring change (%)')
        ax.set_xticks(range(1,len(verified)+1));ax.grid(alpha=.25)
    fig.suptitle(('Prettier' if prettier else 'Sphinx')+': same frozen task, new VM per run; no performance threshold')
    fig.savefig(output/'paired-overhead.svg',metadata={'Date':None});plt.close(fig)
    assert hashes()==before,'raw inputs changed during report export'
    print('status=PASS result_dir='+str(output),flush=True)


def run(args):
    prettier=getattr(args,'task_profile','sphinx7590')=='prettier6604'
    out=ROOT/'.artifacts/runtime-monitor-pairs'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False)
    cache=ROOT/'.artifacts'/('prettier6604' if prettier else 'sphinx-backbone')/('paired-cache-'+out.name)
    plan=dict(pairs=args.pairs,orders=[['off','continuous'] if i%2==0 else ['continuous','off'] for i in range(args.pairs)],
        scope='100 ms guest memory/CPU/block I/O + boundary reads, host service cgroup and per-owned-VMM CPU/I/O/RSS; VMM PSS 1 s; off removes those reads; tool timing, safety disk sampler and correctness checks retained in both',
        cache_state='fresh 2 CPU/4096 MiB VM each run; same verified RAM immutable seed; no host/guest drop_caches; warm image preparation; not cold-download performance',
        thresholds='none; a small paired study does not prove an arbitrary performance threshold',
        timing=dict(guest_action_seconds='sum of original 35 command intervals',frozen_replay_seconds='first segment through all five segments and replay validation; includes phase updates/downloads',
            lifecycle_seconds='controller preflight through owned VM/monitor cleanup; excludes cache preparation, outer service create/delete and offline plotting'),
        order_limitation='odd pair count leaves one extra off-first pair; both orders reported' if args.pairs%2 else 'equal numbers of off-first and on-first pairs')
    if args.sphinx_pipeline_diagnostic:plan['replay_variant']='Sphinx read_repo/001 exact command/stdout/stderr/prefix patch; expected 141, permits actual 0 or 141; all other steps strict; historical strict FAIL retained'
    if prettier:
        plan.update(task_profile='prettier6604',replay_variant='original 29-step backbone; no exit-code exceptions; same original official failures',
            first_pair_is_smoke=True,previous_incomplete_study='.artifacts/runtime-monitor-pairs/20261010T052318Z-140347; Sphinx retained separately, no reused pairs',
            output_comparison='all raw stdout/stderr hashes retained; only three fixed Jest stdout streams separate timing numbers and valid tail PASS paths; all other bytes strict')
        plan['timing']['guest_action_seconds']='sum of original 29 command intervals'
        if args.first_off_raw:
            from prettier6604_monitor_pairs import inspect_first_off
            first_off,versions=inspect_first_off(args.first_off_raw)
            plan['continued_first_off']=dict(raw=first_off['raw'],execution_code_sha256=versions,
                reason='original live run PASS; previous batch stopped in offline snapshot-summary audit; original FAIL retained; no live run excluded')
        if args.first_on_raw:
            from prettier6604_monitor_pairs import inspect_continued_run
            first_on,on_versions=inspect_continued_run(args.first_on_raw,'continuous')
            assert versions==on_versions
            for key in ('configuration','official','final_patch_sha256','final_files','source_before','tool_output_signatures'):
                assert first_off[key]==first_on[key], 'continued pair differs: '+key
            plan['continued_first_on']=dict(raw=first_on['raw'],execution_code_sha256=on_versions,
                reason='live PASS; prior output audit stopped on unchanged warning order and Node PID; original FAIL retained')
            plan['continuation_limits']='first pair from 2026-10-10; remaining pairs run later after interruption; host state uncontrolled; report first pair separately and retain all five'
        plan['output_comparison']+='; custom test milliseconds also separated, snapshot remains exactly 1 written'
        plan['output_comparison']+='; only read_repo/004 exact DEP0128 warning PID and core_verify/005 exact three known warning blocks order separated; unknown text strict'
    if args.sphinx_pipeline_diagnostic and args.sphinx_pipeline_policy_version==2:
        plan.update(sphinx_pipeline_policy_version=2,replay_variant='Sphinx read_repo/001 and core_patch/001 exact command/stdout/stderr/segment patch; expected 141, permits actual 0 or 141; all other steps strict; historical strict FAIL retained',previous_incomplete_study='.artifacts/runtime-monitor-pairs/20261010T050511Z-124782; retained separately, no reused pairs')
    (out/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    (out/'driver-source.py').write_bytes(Path(__file__).read_bytes())
    if prettier:(out/'audit-source.py').write_bytes((ROOT/'scripts/prettier6604_monitor_pairs.py').read_bytes())
    pairs=[];status='FAIL';error=None;released=False
    def interrupted(signum,frame):raise RuntimeError('paired run interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    def call(argv,log,timeout):
        with (out/log).open('x') as stream:
            proc=subprocess.Popen(list(map(str,argv)),cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:proc.wait(timeout=timeout)
            except BaseException:
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGTERM)
                    try:proc.wait(timeout=150)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=10)
                raise
        if proc.returncode:raise RuntimeError(log+': exit '+str(proc.returncode))
    try:
        budget=capacity_budget(available(ROOT),8*GIB)
        (out/'capacity-check.json').write_text(json.dumps(budget,indent=2)+'\n')
        assert budget['ready'],'8 GiB run requires >34 GiB host space'
        call([sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache,*([] if prettier else ['--source-backup-directory',
            '.artifacts/storage-isolated-service/20261009T050703Z-3934019','--task-profile','sphinx7590'])],'cache-prepare.log',300)
        for i,order in enumerate(plan['orders'],1):
            pair=dict(pair=i,order=order)
            for mode in order:
                label=f'{i:02}-{mode}.log'
                if prettier and args.first_off_raw and i==1 and mode=='off':
                    pair[mode],_=inspect_first_off(args.first_off_raw)
                    (out/f'{i:02}-{mode}.json').write_text(json.dumps(pair[mode],indent=2)+'\n')
                    print(f'status=RUN_REUSED pair={i} mode={mode} result_dir={out}',flush=True)
                    continue
                if prettier and args.first_on_raw and i==1 and mode=='continuous':
                    pair[mode],_=inspect_continued_run(args.first_on_raw,mode)
                    (out/f'{i:02}-{mode}.json').write_text(json.dumps(pair[mode],indent=2)+'\n')
                    print(f'status=RUN_REUSED pair={i} mode={mode} result_dir={out}',flush=True)
                    continue
                command=['bash','scripts/validate-isolated-storage.sh','--automatic-window','--prettier6604-task',
                    '.artifacts/prettier6604/20261008T082850Z/shared-prepare-check','--prettier6604-readonly-replay',
                    '--prettier6604-monitor-mode',mode,'--immutable-cache-seed',cache,'--expected-official-audit',
                    '.artifacts/prettier6604/20261008T102942Z-retry/official-audit-recheck.json'] if prettier else ['bash','scripts/validate-isolated-storage.sh','--automatic-window','--python-task-profile','sphinx7590',
                    '--python-backbone-inputs','.artifacts/sphinx-backbone/prepare-20261009T041720Z',
                    '--python-readonly-replay','--python-persistent-sampling','--immutable-cache-seed',cache,
                    '--python-monitor-mode',mode,*(['--python-sphinx-pipeline-diagnostic','--python-sphinx-pipeline-policy-version',str(args.sphinx_pipeline_policy_version)] if args.sphinx_pipeline_diagnostic else [])]
                call(command,label,1050)
                match=re.findall(r'status=PASS result_dir=(\S+)',(out/label).read_text())
                assert match,'missing actual validator PASS receipt'
                pair[mode]=inspect_run(Path(match[-1])/('prettier6604' if prettier else 'python-backbone'),mode)
                if args.sphinx_pipeline_policy_version==2:
                    assert pair[mode]['sphinx_pipeline_policy_version']==2
                (out/f'{i:02}-{mode}.json').write_text(json.dumps(pair[mode],indent=2)+'\n')
                print(f'status=RUN_COMPLETE pair={i} mode={mode} result_dir={out}',flush=True)
            for key in ('configuration','official','final_patch_sha256','final_files','source_before',*(['tool_output_signatures'] if prettier else [])):
                assert pair['off'][key]==pair['continuous'][key], 'paired task semantics differ: '+key
            pairs.append(pair)
            (out/'pairs.json').write_text(json.dumps(pairs,indent=2)+'\n')
        status='PASS'
    except BaseException as exc:
        error=repr(exc)
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        if (cache/'resources.json').exists():
            try:
                call([sys.executable,'-B','scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release.log',60)
                released=json.loads((cache/'resources.json').read_text()).get('released',False)
            except BaseException as exc:error=str(error)+'; cache cleanup '+repr(exc);status='FAIL'
        result=dict(status=status,error=error,completed_pairs=len(pairs),cache_released=released,plan=plan,
            metrics=comparison(pairs) if pairs else {},pairs=pairs)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+status+' result_dir='+str(out),flush=True)
    return 0 if status=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-profile',choices=('sphinx7590','prettier6604'),default='sphinx7590')
    parser.add_argument('--sphinx-pipeline-policy-version',type=int,choices=(1,2),default=1)
    parser.add_argument('--sphinx-pipeline-diagnostic',action='store_true')
    parser.add_argument('--pairs',type=int,choices=range(1,6),default=5)
    parser.add_argument('--first-off-raw',type=Path,help='Continue a Prettier live PASS after offline audit failure; code must match')
    parser.add_argument('--first-on-raw',type=Path,help='Continue the matching first monitored Prettier run, requires --first-off-raw')
    parser.add_argument('--report',type=Path,help='Offline only: verify finished paired raw data')
    parser.add_argument('--output',type=Path,help='New offline report directory')
    args=parser.parse_args()
    if args.report:
        if not args.output:parser.error('--report requires --output')
        report(args.report.resolve(),args.output.resolve())
    else:
        if args.first_on_raw and not args.first_off_raw:parser.error('--first-on-raw requires --first-off-raw')
        if args.first_off_raw and args.task_profile!='prettier6604':parser.error('--first-off-raw requires Prettier')
        if args.task_profile=='prettier6604' and (args.sphinx_pipeline_diagnostic or args.sphinx_pipeline_policy_version!=1):parser.error('Prettier never accepts Sphinx exit-code exceptions')
        if args.sphinx_pipeline_policy_version!=1 and not args.sphinx_pipeline_diagnostic:parser.error('policy version requires --sphinx-pipeline-diagnostic')
        if args.output:parser.error('--output requires --report')
        raise SystemExit(run(args))
