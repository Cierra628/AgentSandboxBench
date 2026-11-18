"""Three alternating startup-only sampling controls; existing isolation owns each volume."""
import argparse
import calendar
from datetime import datetime
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

from cold_start_sampling import ROLES
from host_private_files import read,write
from isolated_storage import available,capacity_budget,GIB
from platform_tasks import ROOT,configuration,commands
from run_light_monitor_controls import SOURCE,SOURCE_ID


def utc_ns(text):
    m=re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\.(\d+)Z',text)
    if not m:raise ValueError('server timestamp unavailable')
    return calendar.timegm(datetime.strptime(m[1],'%Y-%m-%dT%H:%M:%S').timetuple())*10**9+int(m[2].ljust(9,'0'))


def inspect_run(storage,mode):
    outer=read(storage/'result.json');raw=storage/'prettier6604';value=read(raw/'result.json');events=read(raw/'events.json')
    assert outer['status']==value['status']=='PASS' and value['mode']==mode and value['actual_action_count']==0
    assert value['official_evaluation']=='NOT_RUN_STARTUP_ONLY' and set(value['guests'])==set(ROLES)
    assert all(e['exit_code']==0 for e in events)
    assert not any((raw/p).exists() for p in ['rollouts','frozen-input','host-vmm','host-full-rootfs.json','host-shared-files.json'])
    cold=[e for e in events if '-cold-' in e['stage']];assert len(cold)==7
    start=min(e['start_monotonic_ns'] for e in cold);end=max(e['start_monotonic_ns']+e['duration_ns'] for e in cold)
    assert max(e['start_monotonic_ns'] for e in cold)<min(e['start_monotonic_ns']+e['duration_ns'] for e in cold)
    assert value['cold_span_ns']==end-start and value['postcheck_begin_monotonic_ns']>=end
    assert all(e['start_monotonic_ns']>=end for e in events if '-postcheck-' in e['stage'] or '-list-created' in e['stage'])
    assert value['cleanup']['service_sandbox_list_empty'] and all(r['deleted'] and not r['errors'] for r in value['cleanup']['guests'])
    monitor=[e for e in events if '-monitor-' in e['stage']]
    samples=0;ready_before=None;upload_lead=None
    if mode=='off':
        assert not monitor and not (raw/'runtime-monitor.json').exists() and value['cleanup']['monitoring_disabled']
    else:
        m=read(raw/'runtime-monitor.json');assert m['profile']=='light' and not m['host_vmm_requested'] and len(m['sessions'])==7
        assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 and s['receipt']['samples']>=1 for s in m['sessions'])
        samples=sum(s['receipt']['samples'] for s in m['sessions'])
        uploads=[e for e in events if '-monitor-upload-bundle' in e['stage']];ready=[e for e in events if '-monitor-ready' in e['stage']]
        assert len(uploads)==len(ready)==7
        upload_lead=end-min(e['start_monotonic_ns'] for e in uploads)
        ready_before=end-min(e['start_monotonic_ns']+e['duration_ns'] for e in ready)
    for k in ['owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged']:
        assert outer['cleanup'][k]
    assert outer['checks']['actual_writable_files_inside_volume']=='PASS'
    assert outer['default_runtime_report']['status']=='NOT_REQUESTED'
    lines=[re.sub(r'\x1b\[[0-9;]*m','',s) for s in (storage/'service-logs/server.log').read_text().splitlines()]
    indexed={}
    for n,line in enumerate(lines,1):
        sid=re.search(r'\bsandbox_id=([a-f0-9-]{36})\b',line)
        phase='create' if 'creating sandbox timeout=' in line else 'launch' if 'sandbox launch completed ' in line else None
        if sid and phase:
            key=(sid[1],phase);assert key not in indexed;indexed[key]=(utc_ns(line),n)
    rows=[]
    for event in cold:
        role=event['stage'].split('-cold-')[1];sid=value['guests'][role]
        created,cl=indexed[(sid,'create')];launched,ll=indexed[(sid,'launch')]
        a=event['timestamp_ns'];b=a+event['duration_ns'];assert a<created<launched<b
        phases=dict(before_create_log=created-a,create_to_launch_log=launched-created,launch_to_client_return=b-launched)
        assert sum(phases.values())==event['duration_ns']
        rows.append(dict(role=role,start_monotonic_ns=event['start_monotonic_ns'],duration_ns=event['duration_ns'],
            phases_ns=phases,server_log_lines=dict(create=cl,launch=ll)))
    offsets=[e['timestamp_ns']-e['start_monotonic_ns'] for e in events]
    source_matches={name:(raw/'code'/name).read_bytes()==(ROOT/'scripts'/name).read_bytes() for name in
        ['cold_start_sampling.py','agentenv_cold_start.py','sandbox_runtime_monitor.py','guest_memory_collector.py']}
    service_metrics=None
    if 'server_metrics' in value:
        from cold_start_metrics import audit
        service_metrics=audit(raw,value)
        source_matches['cold_start_metrics.py']=(raw/'code/cold_start_metrics.py').read_bytes()==(ROOT/'scripts/cold_start_metrics.py').read_bytes()
    assert all(source_matches.values())
    result=dict(status='PASS',mode=mode,storage=str(storage.relative_to(ROOT)),actual_actions=0,created_guests=7,
        cold_span_ns=end-start,cold_dispatch_span_ns=value['cold_dispatch_span_ns'],
        controller_elapsed_ns=value['controller_elapsed_ns'],sample_count=samples,
        upload_command_before_last_cold_return_ns=upload_lead,collector_ready_before_last_cold_return_ns=ready_before,
        event_wall_minus_monotonic_offset_range_ns=max(offsets)-min(offsets),requests=rows,source_matches=source_matches,
        minimum_free_bytes=outer['observed_host_available_min_bytes'],available_after_cleanup_bytes=outer['host_available_after_cleanup_bytes'],
        checks=dict(parallel_seven='PASS',no_task_work_during_creation='PASS',sampler_profile='PASS',identities='PASS',cleanup='PASS'))
    if service_metrics is not None:result['server_metrics']=service_metrics
    return result


def experiment_orders(server_metrics=False):
    return [['off','on']] if server_metrics else [['off','on'],['on','off'],['off','on']]


def summarize_metric_pair(runs):
    if len(runs)!=2 or [r['mode'] for r in runs]!=['off','on']:raise ValueError('one off/on metric pair required')
    if any(r['status']!='PASS' or r['actual_actions']!=0 or r['created_guests']!=7 or r['server_metrics']['status']!='PASS' for r in runs):
        raise ValueError('startup metric pair incomplete')
    off,on=runs;a,b=off['cold_span_ns'],on['cold_span_ns']
    return dict(status='PASS_STARTUP_METRICS_PAIR',created_guests=14,actual_actions=0,runs=runs,
        cold_span=dict(off_ns=a,on_ns=b,difference_ns=b-a,change_percent=(b/a-1)*100),
        scope='One startup-only pair with two metric reads per group; cumulative service stages are distinct from batch wall time; no causal slowdown estimate')


def summarize(runs):
    if len(runs)!=6:raise ValueError('six completed runs required')
    pairs=[]
    for n,order in enumerate([['off','on'],['on','off'],['off','on']],1):
        selected=[r for r in runs if r['pair']==n]
        if len(selected)!=2 or [r['mode'] for r in selected]!=order:raise ValueError('paired order differs')
        if any(r['status']!='PASS' or r['actual_actions']!=0 or r['created_guests']!=7 for r in selected):raise ValueError('startup group incomplete')
        off=next(r for r in selected if r['mode']=='off');on=next(r for r in selected if r['mode']=='on')
        a,b=off['cold_span_ns'],on['cold_span_ns']
        pairs.append(dict(pair=n,order=order,off_ns=a,on_ns=b,difference_ns=b-a,change_percent=(b/a-1)*100))
    diffs=[p['difference_ns'] for p in pairs];changes=[p['change_percent'] for p in pairs]
    return dict(status='PASS_STARTUP_SAMPLING_THREE_PAIRS',actual_actions=0,created_guests=42,runs=runs,pairs=pairs,
        difference_ns=dict(mean=statistics.mean(diffs),median=statistics.median(diffs),minimum=min(diffs),maximum=max(diffs)),
        change_percent=dict(mean=statistics.mean(changes),median=statistics.median(changes),minimum=min(changes),maximum=max(changes)),
        scope='Startup-only immediate sampler deployment/operation interference, not full task slowdown; signed paired changes, no subtraction from previous full-GRPO results')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--server-metrics',action='store_true',help='Only one off/on pair, with two service metric reads per run')
    args=parser.parse_args();orders=experiment_orders(args.server_metrics)
    out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ['run_startup_sampling_controls.py','cold_start_sampling.py','validate_isolated_agentenv.py','sandbox_runtime_monitor.py','agentenv_cold_start.py']:
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if args.server_metrics:shutil.copy2(ROOT/'scripts/cold_start_metrics.py',out/'code/cold_start_metrics.py')
    state=dict(status='FAIL',stage='precheck',runs=[]);started=False;seed=None;index=0
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout,
            env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE))
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        assert capacity_budget(available(ROOT),16*GIB)['ready']
        write(out/'preflight.json',dict(load_average=os.getloadavg(),available_bytes=available(ROOT),required_strict_gib=42,orders=orders))
        started=True;subprocess.run(['docker','start',SOURCE],capture_output=True,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+60
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source health timeout')
            time.sleep(1)
        for pair,order in enumerate(orders,1):
            for mode in order:
                index+=1;label=f'r{pair}-{mode}';state.update(status='RUNNING',stage=label);write(out/'result.json',state)
                assert capacity_budget(available(ROOT),16*GIB)['ready']
                cfg=configuration(ROOT/f'configs/platform-prettier6604-grpo-{"light" if mode=="on" else "off"}.json')
                seed=ROOT/'.artifacts/prettier6604'/('startup-'+out.name+'-'+label)
                call(label+'-prepare',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',seed],300)
                _,command=commands(cfg,seed);command+=['--startup-sampling',mode];write(out/(label+'-command.json'),command)
                if args.server_metrics:
                    command+=['--startup-server-metrics'];write(out/(label+'-command.json'),command)
                call(label+'-runtime',command,1000)
                found={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(label+'-runtime.log')).read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
                assert len(found)==1
                value=inspect_run(found.pop(),mode);value['pair']=pair;state['runs'].append(value)
                if args.server_metrics:assert value['server_metrics']['status']=='PASS'
                call(label+'-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',seed,'--release'],90)
                assert read(seed/'resources.json')['released'];seed=None
                write(out/'result.json',state)
                print('status=PASS run='+label+' cold_span_seconds='+str(value['cold_span_ns']/1e9),flush=True)
        summary=(summarize_metric_pair if args.server_metrics else summarize)(state['runs'])
        write(out/'summary.json',summary);state.update(status='PASS',stage='complete')
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if seed and (seed/'resources.json').exists():
            try:call('failure-seed-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',seed,'--release'],90)
            except Exception as error:state['seed_release_error']=repr(error)
        if started:
            try:
                subprocess.run(['docker','stop','-t','30',SOURCE],capture_output=True,check=True,timeout=60)
                after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],text=True,timeout=10))
                state['source_returned_to_stopped']=not after['Running']
                if after['Running']:state['status']='FAIL'
            except Exception as error:state.update(status='FAIL',source_stop_error=repr(error))
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
