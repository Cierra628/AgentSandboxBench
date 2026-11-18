"""Two fixed external-task sampling pairs; isolated workflows own service and volume cleanup."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback
import urllib.request

from external_workload import C4,CYTHON,load,guest_runner,validate_timings,validate_c4_outputs,validate_cython_outputs
from isolated_storage import GIB,available,capacity_budget
from run_light_monitor_controls import SOURCE,SOURCE_ID

ROOT=Path(__file__).resolve().parents[1]
ORDER=((1,'off'),(1,'on'),(2,'on'),(2,'off'))
PROFILES={
    'c4':dict(config='external-c4-light.json',task_id=C4,actions=36,prior='external-c4-live-20261030'),
    'django16819':dict(config='external-django16819-light.json',task_id='django__django-16819',actions=28,prior='external-django-live-20261030-clock'),
    'cython':dict(config='external-cython-light.json',task_id=CYTHON,actions=83,prior='external-cython-live-20261030-nft'),
}


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def rows(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def cython_identity(raw):
    logs=raw/'input/logs';extension=(raw/'extension-validation.txt').read_text()
    assert 'compiled extensions import successfully' in extension
    commit=re.findall(r'(?m)^source_repo_commit: ([0-9a-f]{40})$',extension);assert len(commit)==1
    identity=dict(source_commit=commit[0])
    for step,pattern in [(24,r'(?m)^gcc .+$'),(24,r'(?m)^Cython .+$'),(25,r'(?m)^vispy .+$'),
                         (26,r'(?m)^(?:networkx|peewee|appdirs|requests|tqdm|matplotlib|planarity) .+$'),(36,r'(?m)^setuptools .+$')]:
        matches=re.findall(pattern,(logs/f'{step:03}.stdout').read_text())
        assert len(matches)==(7 if step==26 else 1),'missing original version output'
        for line in matches:
            name,version=line.split(' ',1);assert name not in identity
            identity[name]=version
    return identity


def inspect_run(workflow,mode,upstream,profile='c4'):
    workflow=Path(workflow);work=read(workflow/'result.json');assert work['status']=='PASS' and work['sampling']==mode
    storage=Path(work['storage']);outer=read(storage/'result.json');raw=Path(work['raw']);task=read(raw/'result.json')
    cfg,replay,expected=load(ROOT/'configs'/PROFILES[profile]['config'],upstream)
    c4=cfg['task_id']==C4;cython=cfg['task_id']==CYTHON;count=cfg['actions'];bundle=raw/('input' if c4 or cython else 'asb-external-tools')
    assert outer['status']==task['status']=='PASS' and task['task_id']==cfg['task_id'] and task['actions']==count and task['image']==cfg['image']
    assert task['sampling']==mode and task['strict_exit_match'] and task['source_baseline']=='PASS'
    assert task['syntax_validation']==('NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK' if cython else 'PASS')
    if c4:assert task['roundtrip_validation']=='PASS'
    assert all(task['cleanup'][k] for k in ('sandbox_removed','service_sandbox_list_empty'))
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    check='external_fixed_tools_and_extensions_and_tests' if cython else 'external_frozen_tools_and_original_syntax_check'
    assert all(outer['checks'][k]=='PASS' for k in (check,'actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation'))
    assert outer['host_file_residency']['status']=='NOT_REQUESTED' and outer['host_file_creation']['host_sampling']==mode
    tools=rows(raw/'tools.tsv');assert len(tools)==count;validate_timings(tools)
    assert [int(r['exit_code']) for r in tools]==[int(r['exit_code']) for r in expected]
    if c4:
        validate_c4_outputs(bundle/'logs')
        assert int((bundle/'logs/002.stdout').read_text().splitlines()[0])==9898
    elif cython:
        validate_cython_outputs(bundle/'logs')
        assert all(task[k]=='PASS' for k in ('functional_validation','test_validation','extension_validation','guest_network_preflight'))
        assert task['official_tests']=='NOT_RUN_ORIGINAL_18_TESTS_TWICE_AND_EXTENSION_CHECK'
        assert work['owned_guest_proxy_stopped']
        assert read(workflow/'guest-proxy-rule.json')['status']==read(workflow/'guest-proxy-address.json')['status']=='REMOVED'
        before=read(workflow/'guest-proxy-counters-before-head.json');after=read(workflow/'guest-proxy-counters-after-head.json')
        assert 0<=before['allowed_packets']<after['allowed_packets']
        identity=cython_identity(raw)
    else:assert task['official_tests']=='NOT_RUN_ORIGIN_SYNTAX_ONLY'
    assert (bundle/'runner.sh').read_text()==guest_runner(count,task_id=cfg['task_id'])
    names=set(replay['files']) if c4 else {f'{i:03}.sh' for i in range(1,count+1)}
    if cython:
        names|={f"{s['step']:03}-read.py" for s in replay['steps'] if s['tool']=='read'}
        names|={f"{s['step']:03}.payload" for s in replay['steps'] if s['tool']=='write'}
    for name in names:assert (bundle/name).read_text()==replay['files'][name]
    events=read(raw/'events.json');assert all(e['exit_code']==0 for e in events)
    cold=[e for e in events if e['stage']=='cold-create'];assert len(cold)==1
    controls=[e for e in events if e['stage'].startswith('monitor-') and not e['stage'].startswith('monitor-collect-')]
    managed=[e for e in events if e['stage'].startswith('monitor-collect-')]
    if cython:
        network=[e for e in events if e['stage']=='guest-network'];assert len(network)==1
        network_end=network[0]['start_monotonic_ns']+network[0]['duration_ns']
        assert network_end<=task['replay_start_monotonic_ns']
        assert all(e['start_monotonic_ns']>=network_end for e in controls+managed)
    costs=None;memory='NOT_MEASURED'
    if mode=='off':
        assert not controls and not managed and task['memory']=='NOT_MEASURED' and task['cleanup']['memory_sampling_disabled']
        assert work['collector']['status']=='NOT_REQUESTED' and work['memory']=='NOT_MEASURED'
        assert outer['host_file_creation']['before_guest_counters'] is None
        assert not (workflow/'host-file').exists() and not (workflow/'report').exists()
        assert not any((raw/n).exists() for n in ('runtime-monitor.json','runtime-code','runtime','host-vmm'))
    else:
        assert len(controls)==4 and len(managed)==1
        manifest=read(raw/'runtime-monitor.json');assert manifest['status']=='PASS' and manifest['profile']=='light' and not manifest['host_vmm_requested']
        assert manifest['host_pss_interval_seconds'] is None and len(manifest['sessions'])==1
        session=manifest['sessions'][0];receipt=session['receipt'];collector=work['collector'];host=work['host']
        assert session['status']=='PASS' and session['collector_reaped'] and receipt['status']=='PASS'
        assert receipt['interval_seconds']==collector['interval_seconds']==.1 and collector['status']=='PASS' and collector['pss_interval_seconds'] is None
        assert host['primary_metric']=='memory.current' and host['pss_samples']==0 and host['mincore']['status']=='NOT_REQUESTED'
        guest=rows(raw/session['raw_directory']/'guest-memory.tsv');assert len(guest)==receipt['samples']>1
        assert all(int(b['monotonic_ns'])>int(a['monotonic_ns']) for a,b in zip(guest,guest[1:]))
        assert int(guest[0]['monotonic_ns'])<=int(tools[0]['start_monotonic_ns']) and int(guest[-1]['monotonic_ns'])>=int(tools[-1]['end_monotonic_ns'])
        hostrows=rows(workflow/'host-file/memory.tsv');assert len(hostrows)==collector['samples']>1
        hostspan=int(hostrows[-1]['monotonic_ns'])-int(hostrows[0]['monotonic_ns'])
        guestcpu=receipt['cpu_user_seconds']+receipt['cpu_system_seconds']
        costs=dict(host_samples=len(hostrows),guest_samples=len(guest),host_cpu_seconds=collector['thread_cpu_ns']/1e9,
            guest_cpu_seconds=guestcpu,host_single_core_cpu_percent=100*collector['thread_cpu_ns']/hostspan,
            guest_single_core_cpu_percent=100*guestcpu/(receipt['elapsed_ns']/1e9),host_counter_read_seconds=collector['counter_read_total_ns']/1e9,
            host_max_gap_seconds=host['maximum_sampling_gap_ns']/1e9,
            guest_max_gap_seconds=max(int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in zip(guest,guest[1:]))/1e9)
        memory=dict(host_current_peak_mib=host['current']['observed_peak_bytes']/2**20,guest_used_peak_mib=work['guest']['guest_used_peak_mib'],guest_cached_peak_mib=work['guest']['guest_cached_peak_mib'])
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
    return dict(status='PASS',mode=mode,workflow=str(workflow),storage=str(storage),raw=str(raw),actions=count,
        cgroup=outer['host_file_creation']['cgroup'],guest_id=task['sandbox_id'],memory=memory,sampling_cost=costs,
        **(dict(source_identity=identity,network_preflight_seconds=network[0]['duration_ns']/1e9) if cython else {}),
        timings_seconds=dict(tool_sum=sum(int(r['duration_ns']) for r in tools)/1e9,
            tool_span=(int(tools[-1]['end_monotonic_ns'])-int(tools[0]['start_monotonic_ns']))/1e9,
            replay_cli=(task['replay_end_monotonic_ns']-task['replay_start_monotonic_ns'])/1e9,
            cold_create=cold[0]['duration_ns']/1e9,controller=task['controller_elapsed_ns']/1e9,
            monitor_blocking=sum(e['duration_ns'] for e in controls)/1e9 if mode=='on' else None,
            managed_collector_request=managed[0]['duration_ns']/1e9 if mode=='on' else None),
        **(dict(call_timing=task['call_timing']) if 'call_timing' in task else {}),
        minimum_available_gib=outer['observed_host_available_min_bytes']/GIB,cleanup='PASS')


def summarize(out):
    runs=read(out/'runs.json');assert [(r['pair'],r['mode']) for r in runs]==list(ORDER)
    # Saved C4 plans predate the selector; their existing meaning stays C4.
    profile=read(out/'plan.json').get('task','c4') if (out/'plan.json').exists() else 'c4'
    assert all(r['status']=='PASS' for r in runs) and len({r['cgroup'] for r in runs})==len({r['guest_id'] for r in runs})==4
    fields=('tool_sum','tool_span','replay_cli','cold_create','controller');pairs=[]
    for number in (1,2):
        pair=[r for r in runs if r['pair']==number];on=next(r for r in pair if r['mode']=='on');off=next(r for r in pair if r['mode']=='off')
        changed=[]
        if profile=='cython':changed=[k for k in on['source_identity'].keys()|off['source_identity'].keys() if on['source_identity'].get(k)!=off['source_identity'].get(k)]
        pairs.append(dict(pair=number,order=[r['mode'] for r in pair],
            **(dict(comparability=dict(status='DIFFERENT' if changed else 'MATCH_RECORDED_FIELDS',changed_fields=sorted(changed))) if profile=='cython' else {}),
            timings={field:dict(on=on['timings_seconds'][field],off=off['timings_seconds'][field],change_percent=None if changed else (on['timings_seconds'][field]/off['timings_seconds'][field]-1)*100) for field in fields}))
    comparable=[p for p in pairs if p['timings']['tool_sum']['change_percent'] is not None]
    value=dict(status='PASS',task_id=PROFILES[profile]['task_id'],runs=4,pairs=pairs,summary={field:dict(
        on_mean_seconds=statistics.mean(p['timings'][field]['on'] for p in comparable) if comparable else None,
        off_mean_seconds=statistics.mean(p['timings'][field]['off'] for p in comparable) if comparable else None,
        paired_change_median_percent=statistics.median(p['timings'][field]['change_percent'] for p in comparable) if comparable else None,
        paired_change_range_percent=[min(p['timings'][field]['change_percent'] for p in comparable),max(p['timings'][field]['change_percent'] for p in comparable)] if comparable else None) for field in fields},
        definition='Per pair: (on / off - 1) * 100; signed values retained. Both memory samplers change together; clocks, checks, cgroup accounting and disk guards retained.',
        scope='Two fresh-service pairs only; no population confidence bound, host/guest slowdown decomposition or sharing savings.')
    if profile=='cython':value['comparable_pairs']=len(comparable)
    write(out/'comparison.json',value);return value


def source_state():
    value=json.loads(subprocess.check_output(['docker','inspect','--format','{"id":"{{.Id}}","running":{{.State.Running}}}',SOURCE],text=True,timeout=15))
    assert value['id']==SOURCE_ID
    return value


def image_evidence(out,profile,cfg):
    prior=ROOT/'.artifacts'/PROFILES[profile]['prior'];previous=read(prior/'result.json');assert previous['status']=='PASS'
    if profile=='c4':
        assert previous['image_preflight']['image']==cfg['image']
        for name in ('manifest.json','image-config.json'):shutil.copy2(prior/name,out/name)
        assert read(out/'image-config.json')['architecture']=='amd64' and read(out/'image-config.json')['os']=='linux'
        manifest=read(out/'manifest.json');assert len(manifest['layers'])==9 and sum(l['size'] for l in manifest['layers'])==1432498021
    else:
        work=read(Path(previous['workflow'])/'result.json');task=read(Path(work['raw'])/'result.json')
        assert work['status']==task['status']=='PASS' and task['image']==cfg['image'] and task['task_id']==cfg['task_id']
        assert task['source_baseline']=='PASS'
        if profile=='cython':
            assert task['functional_validation']==task['test_validation']==task['extension_validation']=='PASS'
            assert task['syntax_validation']=='NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK'
            image=previous['image_preflight']
            assert image['status']=='PASS' and image['image']==cfg['image'] and image['platform']=='linux/amd64'
            assert image['layers']==7 and image['compressed_bytes']==303214315
        else:assert task['syntax_validation']=='PASS'
    write(out/'image-evidence.json',dict(status='PASS',image=cfg['image'],prior=str(prior),basis='Existing verified fixed-image creation; no new registry query'))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--upstream',type=Path,required=True)
    parser.add_argument('--task',choices=tuple(PROFILES),default='c4',help='Explicit Django or Cython profile; existing C4 default unchanged')
    parser.add_argument('--report-only',action='store_true');args=parser.parse_args();out=args.output.resolve()
    if args.report_only:
        summarize(out);print('status=PASS result_dir='+str(out));return 0
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);state=dict(status='FAIL',stage='precheck');started=False;runs=[];launches=[]
    selected=PROFILES[args.task];config=ROOT/'configs'/selected['config']
    write(out/'plan.json',dict(order=[dict(pair=p,mode=m) for p,m in ORDER],actions_per_run=selected['actions'],total_actions=4*selected['actions'],volume_gib=32,interval_seconds=.1,**({'task':args.task} if args.task!='c4' else {})))
    write(out/'runs.json',runs);write(out/'launches.json',launches)
    try:
        cfg,_,_=load(config,args.upstream);assert cfg['task_id']==selected['task_id'] and cfg['actions']==selected['actions']
        state['source_before']=source_state();assert not state['source_before']['running']
        assert not subprocess.check_output(['docker','ps','-q'],text=True,timeout=10).strip()
        assert subprocess.run(['pgrep','-x','firecracker'],capture_output=True,timeout=10).returncode==1
        image_evidence(out,args.task,cfg)
        code=out/'code';code.mkdir()
        for name in ('run_c4_sampling_controls.py','run_host_external_workload.py','run_external_workload.py','validate_isolated_agentenv.py','external_workload.py','sandbox_runtime_monitor.py','host_file_monitor.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
        assert capacity_budget(available(ROOT),32*GIB)['ready']
        started=True
        with (out/'source-start.log').open('x') as log:subprocess.run(['docker','start',SOURCE],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+90
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=3) as r:
                    if r.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source health')
            time.sleep(1)
        for index,(number,mode) in enumerate(ORDER,1):
            budget=capacity_budget(available(ROOT),32*GIB);assert budget['ready'],budget
            mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024;assert mem>64*GIB
            state.update(status='RUNNING',stage=f'run-{index}-{mode}');write(out/'result.json',state)
            launch=dict(index=index,pair=number,mode=mode,budget=budget,mem_available_bytes=mem,load_average=os.getloadavg());launches.append(launch);write(out/'launches.json',launches)
            command=['bash',str(ROOT/'scripts/run-external-workload-light.sh'),str(config),str(args.upstream.resolve()),'--sampling',mode]
            launch['command']=command;begin=time.monotonic_ns();logpath=out/f'{index:02}-{mode}.log'
            print(f'status=RUNNING run={index}/4 mode={mode} result_dir={out}',flush=True)
            with logpath.open('x') as log:result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE),stdout=log,stderr=subprocess.STDOUT,timeout=1500)
            launch.update(exit_code=result.returncode,elapsed_ns=time.monotonic_ns()-begin)
            found={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',logpath.read_text()) if Path(p).parent==ROOT/'.artifacts/host-external-workload'}
            if len(found)==1:launch['workflow']=str(found.pop())
            write(out/'launches.json',launches)
            assert result.returncode==0,'workflow failed; retain fixture and diagnose first boundary'
            accepted=inspect_run(launch['workflow'],mode,args.upstream,args.task);accepted.update(index=index,pair=number);runs.append(accepted);write(out/'runs.json',runs)
            print(f'status=PASS run={index}/4 mode={mode} tool_seconds={accepted["timings_seconds"]["tool_sum"]:.6f}',flush=True)
        summarize(out);state.update(status='PASS',stage='complete',runs_completed=len(runs))
    except BaseException as error:
        state.update(status='FAIL',error=repr(error),runs_completed=len(runs));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            source_state()
            with (out/'source-stop.log').open('x') as log:subprocess.run(['docker','stop','-t','10',SOURCE],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
        state['source_after']=source_state();state['available_after_bytes']=available(ROOT)
        if state['source_after']['running']:state['status']='FAIL'
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
