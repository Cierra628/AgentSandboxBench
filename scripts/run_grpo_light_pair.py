"""One seven-guest GRPO sampling pair; the existing workflow owns VM/storage cleanup."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback
import urllib.request

from platform_tasks import ROOT,configuration,preflight
from isolated_storage import available,capacity_budget,GIB
from run_light_monitor_controls import SOURCE,SOURCE_ID,read,write,rows


def pair_configs(out,create_before_setup=False,*,skip_boundary_vmstat=False):
    if skip_boundary_vmstat and not create_before_setup:raise ValueError('skip boundary vmstat requires create-before-setup')
    paths={mode:ROOT/f'configs/platform-prettier6604-grpo-{name}.json' for mode,name in [('off','off'),('on','light')]}
    if create_before_setup:
        (out/'configs').mkdir()
        for mode,path in paths.items():
            cfg=read(path);cfg['grpo_create_before_setup']=True
            if mode=='on' and skip_boundary_vmstat:cfg['light_skip_boundary_vmstat']=True
            paths[mode]=out/'configs'/(mode+'.json');write(paths[mode],cfg)
    return paths


def interval_union(events):
    end=None;total=0
    for e in sorted(events,key=lambda e:e['start_monotonic_ns']):
        a=e['start_monotonic_ns'];b=a+e['duration_ns']
        total+=max(0,b-max(a,end if end is not None else a));end=max(end if end is not None else b,b)
    return total


def inspect_run(workflow,mode):
    work=read(Path(workflow)/'result.json');assert work['status']=='PASS' and work['cache_released']
    storage=Path(work['storage_result_dir']);outer=read(storage/'result.json');raw=storage/'prettier6604'
    task=read(raw/'result.json');summary=read(Path(work['report_dir'])/'summary.json')
    assert outer['status']==task['status']==summary['status']=='PASS'
    assert all(outer['cleanup'].values()) and all(task['cleanup'].values())
    assert all(v=='PASS' for v in task['checks'].values())
    assert task['schedule']=='grpo' and task['actual_action_count']==summary['tool_count']==189
    assert summary['schedule_audit']['actions']==189 and len(summary['schedule_audit']['roles'])==7
    assert not any((raw/name).exists() for name in ('host-vmm','host-monitor.log','host/memory-samples.tsv','physical-cache','live-cache','host-full-rootfs.json','host-shared-files.json'))
    cfg=read(raw/'effective-config.json');patches={};official={}
    for role,path in cfg['executed_paths'].items():
        p=task['paths'][role];assert p['official_task_status']=='FAIL' and (p['official_passed'],p['official_failed'])==(0,2)
        patches[role]=(raw/'rollouts'/role/path[-1]/'patch.diff').read_text();official[role]=dict(passed=0,failed=2)
    events=read(raw/'events.json');assert all(e['exit_code']==0 for e in events)
    setup_schedule=task.get('grpo_setup_schedule','per-guest-setup')
    assert setup_schedule==cfg.get('grpo_setup_schedule','per-guest-setup')==summary.get('grpo_setup_schedule','per-guest-setup')
    assert setup_schedule in ('per-guest-setup','create-all-before-setup')
    skip_boundary_vmstat=task.get('light_skip_boundary_vmstat',False)
    assert skip_boundary_vmstat==cfg.get('light_skip_boundary_vmstat',False)==summary.get('light_skip_boundary_vmstat',False)
    variant={}
    if skip_boundary_vmstat:
        from run_prettier6604_grpo import local_current_grpo_record
        assert mode=='on' and (setup_schedule=='create-all-before-setup' or local_current_grpo_record(task))
        assert task['boundary_vmstat']==summary['boundary_vmstat']==dict(status='NOT_MEASURED',reason='explicitly skipped boundary vmstat',row_count=0,boundary_memory_rows=448)
        variant=dict(light_skip_boundary_vmstat=True,boundary_vmstat=task['boundary_vmstat'])
    merge_boundary_meminfo=task.get('light_merge_boundary_meminfo',False)
    assert merge_boundary_meminfo==cfg.get('light_merge_boundary_meminfo',False)==summary.get('light_merge_boundary_meminfo',False)
    if merge_boundary_meminfo:
        from run_prettier6604_grpo import local_current_grpo_record
        from sandbox_runtime_monitor import boundary_meminfo_metadata
        assert mode=='on' and skip_boundary_vmstat and local_current_grpo_record(task)
        metadata=boundary_meminfo_metadata(task['action_count'],task['segment_count'])
        assert task['boundary_meminfo']==summary['boundary_meminfo']==metadata
        variant.update(light_merge_boundary_meminfo=True,boundary_meminfo=metadata)
    label=lambda e:re.sub(r'^\d+-','',e['stage'])
    control=[e for e in events if label(e).startswith('monitor-') and not label(e).startswith('monitor-collect-')]
    cold=[e for e in events if label(e).startswith('cold-')];assert len(cold)==7
    tools=[e for e in events if label(e)=='controller-call'];assert len(tools)==2*task['segment_count']
    span=lambda values:max(e['start_monotonic_ns']+e['duration_ns'] for e in values)-min(e['start_monotonic_ns'] for e in values)
    extra_timings={}
    if setup_schedule=='create-all-before-setup':
        from report_task_overview import grpo_setup_timing
        assert summary['grpo_setup_timing']==grpo_setup_timing(task)
        created=task['all_created_monotonic_ns'];prepared=task['all_prepared_monotonic_ns']
        assert all(e['start_monotonic_ns']+e['duration_ns']<=created for e in cold)
        preparation=[e for e in events if label(e) in {prefix+role for prefix in ('source-','upload-check-','baseline-','upload-workload-') for role in task['roles']}]
        assert len(preparation)==28
        assert all(created<=e['start_monotonic_ns']<=e['start_monotonic_ns']+e['duration_ns']<=prepared for e in preparation)
        extra_timings={k:task[k] for k in ('created_to_controller_end_ns','create_to_setup_ns')}
    sessions=[]
    if mode=='off':
        assert task['monitor_mode']=='off' and task['cleanup']['monitoring_disabled'] and not control
        assert summary['memory']=='NOT_MEASURED' and not (raw/'runtime-monitor.json').exists()
        for row in rows(raw/'segments.tsv'):
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):assert not rows(raw/'rollouts'/row['rollout']/row['segment']/name)
    else:
        m=read(raw/'runtime-monitor.json');assert m['profile']=='light' and not m['host_vmm_requested'] and m['guest_performance_version'] is None
        assert len(m['sessions'])==7
        for s in m['sessions']:
            r=s['receipt'];assert s['status']=='PASS' and s['collector_reaped'] and r['profile']=='light' and r['interval_seconds']==.1
            if setup_schedule=='create-all-before-setup':assert created<=s['start_host_monotonic_ns']<=prepared
            data=rows(raw/s['raw_directory']/'guest-memory.tsv');assert len(data)==r['samples']>2
            assert not any((raw/s['raw_directory']/name).exists() for name in ('guest-vmstat.tsv','guest-cpu.tsv','guest-diskstats.tsv'))
            sessions.append(dict(role=s['role'],samples=r['samples'],cpu_seconds=r['cpu_user_seconds']+r['cpu_system_seconds'],
                peak_rss_mib=r['maxrss_kib']/1024,max_gap_seconds=max((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(data,data[1:]))))
        assert len(control)==35  # Five blocking calls per guest, excluding each managed collector command.
    cleanup=task['light_cleanup_timing'];assert len(cleanup['guests'])==7
    expected_stopped=True if mode=='on' else None
    assert all(r['deleted'] and not r['errors'] and r['stopped'] is expected_stopped for r in cleanup['guests'])
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount() and not list((storage/'mount').iterdir())
    return dict(status='PASS',mode=mode,grpo_setup_schedule=setup_schedule,workflow=str(workflow),storage=str(storage),raw=str(raw),report=work['report_dir'],
        paths=cfg['executed_paths'],patches=patches,official=official,actual_actions=189,sessions=sessions,
        timings_ns=dict(tool_sum=sum(p['guest_action_sum_ns'] for p in task['paths'].values()),
            frozen_tools_and_downloads_span=span(tools),cold_request_span=span(cold),
            monitor_control_call_sum=sum(e['duration_ns'] for e in control),monitor_control_union=interval_union(control),
            cleanup=cleanup['end_monotonic_ns']-cleanup['start_monotonic_ns'],controller=task['controller_elapsed_ns'],
            rollout=task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns'],**extra_timings),
        minimum_free_disk_gib=outer['observed_host_available_min_bytes']/GIB,**variant)


def summarize(out):
    runs=read(out/'runs.json')
    assert not any(r.get('light_merge_boundary_meminfo',False) for r in runs), 'this pair retains separate boundary meminfo'
    order=read(out/'plan.json')['order'] if (out/'plan.json').exists() else ['off','on']
    assert order in (['off','on'],['on','off']) and len(runs)==2 and [r['mode'] for r in runs]==order
    off=next(r for r in runs if r['mode']=='off');on=next(r for r in runs if r['mode']=='on')
    setup_schedule=read(out/'plan.json').get('grpo_setup_schedule','per-guest-setup') if (out/'plan.json').exists() else 'per-guest-setup'
    assert setup_schedule in ('per-guest-setup','create-all-before-setup')
    assert off.get('grpo_setup_schedule','per-guest-setup')==on.get('grpo_setup_schedule','per-guest-setup')==setup_schedule
    skip_boundary_vmstat=read(out/'plan.json').get('light_skip_boundary_vmstat',False) if (out/'plan.json').exists() else False
    assert not off.get('light_skip_boundary_vmstat',False)
    assert on.get('light_skip_boundary_vmstat',False)==skip_boundary_vmstat
    if skip_boundary_vmstat:assert setup_schedule=='create-all-before-setup'
    assert off['timings_ns'].keys()==on['timings_ns'].keys()
    if setup_schedule=='create-all-before-setup':
        assert all(k in off['timings_ns'] for k in ('created_to_controller_end_ns','create_to_setup_ns'))
    assert off['paths']==on['paths'] and off['patches']==on['patches'] and off['official']==on['official']
    comparison={k:dict(off_ns=off['timings_ns'][k],on_ns=on['timings_ns'][k],
        change_percent=(on['timings_ns'][k]/off['timings_ns'][k]-1)*100 if off['timings_ns'][k] else None) for k in off['timings_ns']}
    result=dict(status='PASS_GRPO_LIGHT_PAIR',order=order,grpo_setup_schedule=setup_schedule,actual_actions=378,sample_count=sum(s['samples'] for s in on['sessions']),comparison=comparison,
        scope='One guest-only seven-guest GRPO pair; no host/PSS/physical queries; signed observed differences, no repeat statistics')
    if skip_boundary_vmstat:result['light_skip_boundary_vmstat']=True
    write(out/'comparison.json',result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--order',choices=('off-on','on-off'),default='off-on',help='Execution order for a new pair; report-only uses its saved plan')
    parser.add_argument('--grpo-create-before-setup',action='store_true',help='Both groups: create all seven guests before concurrent setup; old default unchanged')
    parser.add_argument('--light-skip-boundary-vmstat',action='store_true',help='On group only: skip boundary vmstat; requires create-before-setup')
    args=parser.parse_args();out=args.output.resolve();order=args.order.split('-')
    if args.report_only:summarize(out);print('status=PASS result_dir='+str(out));return
    if args.light_skip_boundary_vmstat and not args.grpo_create_before_setup:parser.error('--light-skip-boundary-vmstat requires --grpo-create-before-setup')
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_grpo_light_pair.py','run_prettier6604_grpo.py','sandbox_runtime_monitor.py','report_prettier6604_grpo.py','report_task_overview.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    configs=pair_configs(out,args.grpo_create_before_setup,skip_boundary_vmstat=args.light_skip_boundary_vmstat)
    setup_schedule='create-all-before-setup' if args.grpo_create_before_setup else 'per-guest-setup'
    write(out/'plan.json',dict(order=order,planned_actions=378,guest_count_per_run=7,host_sampling=False,pss=False,
        grpo_setup_schedule=setup_schedule,**({'light_skip_boundary_vmstat':True} if args.light_skip_boundary_vmstat else {})))
    runs=[];write(out/'runs.json',runs);state=dict(status='FAIL',stage='precheck');started=False
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        checks={}
        for mode in order:
            budget=capacity_budget(available(ROOT),16*GIB);assert budget['ready']
            cfg=configuration(configs[mode])
            checks[mode]=dict(budget=budget,inputs=preflight(cfg,offline=True))
        write(out/'precheck.json',dict(checks=checks,load_average=os.getloadavg(),source_state=source['State']))
        started=True;subprocess.run(['docker','start',SOURCE],capture_output=True,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source service not ready')
        for mode in order:
            state.update(status='RUNNING',stage=mode,completed=len(runs));write(out/'result.json',state)
            print('status=RUNNING mode='+mode+' result_dir='+str(out),flush=True)
            command=['timeout','-k','120s','1500s','bash','scripts/use-platform.sh','run',str(configs[mode])]
            with (out/(mode+'.log')).open('x') as stream:
                proc=subprocess.run(command,cwd=ROOT,env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE),stdout=stream,stderr=subprocess.STDOUT)
            write(out/(mode+'-exit.json'),dict(exit_code=proc.returncode,load_average=os.getloadavg()));assert proc.returncode==0,'stop after first failure; preserve outputs'
            workflows={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(mode+'.log')).read_text()) if Path(p).parent==ROOT/'.artifacts/platform-tasks'}
            assert len(workflows)==1;verified=inspect_run(workflows.pop(),mode)
            assert verified['grpo_setup_schedule']==setup_schedule, 'executed setup schedule differs from the requested pair'
            assert verified.get('light_skip_boundary_vmstat',False)==(mode=='on' and args.light_skip_boundary_vmstat), 'executed boundary vmstat option differs from requested pair'
            runs.append(verified);write(out/'runs.json',runs)
            print('status=PASS mode='+mode+' actions=189',flush=True)
        summarize(out);state.update(status='PASS',stage='complete',completed=2)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc());raise
    finally:
        if started:
            with (out/'source-stop.log').open('w') as stream:stop=subprocess.run(['timeout','60s','docker','stop','-t','30',SOURCE],stdout=stream,stderr=subprocess.STDOUT)
            after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=20));write(out/'source-after.json',after)
            state['source_returned_to_stopped']=stop.returncode==0 and not after['Running']
            if not state['source_returned_to_stopped']:state['status']='FAIL'
        write(out/'result.json',state);print('status='+state['status']+' result_dir='+str(out),flush=True)
    if state['status']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
