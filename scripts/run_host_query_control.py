"""One shared GRPO pair: P0/P1 residency off then on, all other sampling unchanged."""
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

from run_host_full_rootfs import ROOT,read,write,require_budget,daemon_file
from host_full_rootfs_control import validate_observation
from isolated_storage import available,GIB
from run_light_monitor_controls import SOURCE,SOURCE_ID,rows
from run_grpo_light_pair import interval_union


def inspect_group(directory,residency):
    saved=read(directory/'result.json');storage=Path(saved['storage']);outer=read(storage/'result.json');raw=storage/'prettier6604'
    task=read(raw/'result.json');report=read(storage/'report/runtime-metrics.json');obs=read(raw/'host-full-rootfs.json')
    assert saved['status']==outer['status']==task['status']==report['status']=='PASS'
    assert saved['seed_released'] and task['actual_action_count']==report['tool_count']==189
    assert report['schedule_audit']['status']=='PASS' and report['schedule_audit']['actions']==189
    assert all(v=='PASS' for v in task['checks'].values()) and all(outer['cleanup'].values())
    validate_observation(obs,residency);assert obs['mode']=='shared' and obs['schedule']=='grpo'
    assert task.get('host_full_rootfs_residency','on')==residency
    targets=[]
    for phase in ('P0','P1'):
        data=obs[phase];objects=data['objects']
        assert len(objects)==len({(r['identity']['device'],r['identity']['inode']) for r in objects})==19
        assert all(len(r['users'])==7 for r in objects)
        targets.append(sorted((r['relative'],r['identity']['size']) for r in objects))
        assert data['elapsed_ns']==data['end_monotonic_ns']-data['start_monotonic_ns']>0
        if residency=='off':assert all(r['residency']==dict(status='NOT_MEASURED',resident_bytes=None) for r in objects)
        else:assert all(r['residency']['stable'] for r in objects)
    assert targets[0]==targets[1]
    assert obs['P0']['end_monotonic_ns']<task['rollout_start_monotonic_ns']<=obs['paths_end_monotonic_ns']<=obs['P1']['start_monotonic_ns']<obs['P1']['end_monotonic_ns']<task['rollout_end_monotonic_ns']
    assert outer['host_shared_preparation_sync']['end_monotonic_ns']<outer['host_file_creation']['verified_before_guest_creation_ns']
    assert outer['host_file_residency']['status']=='NOT_REQUESTED' and obs['pss']=='NOT_REQUESTED'
    monitor=read(raw/'runtime-monitor.json');assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==7
    sessions=[]
    for s in monitor['sessions']:
        r=s['receipt'];assert s['status']=='PASS' and s['collector_reaped'] and r['interval_seconds']==.1
        data=rows(raw/s['raw_directory']/'guest-memory.tsv');assert len(data)==r['samples']>2
        sessions.append(dict(role=s['role'],samples=r['samples'],cpu_seconds=r['cpu_user_seconds']+r['cpu_system_seconds'],
            peak_rss_mib=r['maxrss_kib']/1024,max_gap_seconds=max((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(data,data[1:]))))
    collector=saved['collector'];assert collector['status']=='PASS' and collector['interval_seconds']==.1 and collector['pss_interval_seconds'] is None
    assert not (directory/'host-file/pss.jsonl').exists()
    assert len(rows(directory/'host-file/memory.tsv'))==collector['samples']>2
    cfg=read(raw/'effective-config.json');patches={};official={}
    for role,path in cfg['executed_paths'].items():
        p=task['paths'][role];assert p['official_task_status']=='FAIL' and (p['official_passed'],p['official_failed'])==(0,2)
        patches[role]=(raw/'rollouts'/role/path[-1]/'patch.diff').read_text();official[role]=dict(passed=0,failed=2)
    events=read(raw/'events.json');calls=[e for e in events if e['stage'].endswith('-controller-call')]
    cold=[e for e in events if '-cold-' in e['stage']];control=[e for e in events if re.sub(r'^\d+-','',e['stage']).startswith('monitor-') and '-monitor-collect-' not in e['stage']]
    assert len(control)==35 and len(cold)==7 and all(e['exit_code']==0 for e in events)
    span=lambda values:max(e['start_monotonic_ns']+e['duration_ns'] for e in values)-min(e['start_monotonic_ns'] for e in values)
    assert obs['P0']['end_monotonic_ns']<min(e['start_monotonic_ns'] for e in calls) and max(e['start_monotonic_ns']+e['duration_ns'] for e in calls)<obs['P1']['start_monotonic_ns']
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount() and not list((storage/'mount').iterdir())
    return dict(status='PASS',residency=residency,group=str(directory),storage=str(storage),raw=str(raw),report=str(storage/'report'),
        paths=cfg['executed_paths'],patches=patches,official=official,targets=targets[0],sessions=sessions,
        host_periodic={k:collector[k] for k in ('samples','thread_cpu_ns','counter_read_total_ns','interval_seconds','pss_interval_seconds')},
        endpoints={p:dict(status=obs[p]['status'],resident_bytes=obs[p]['resident_bytes'],elapsed_ns=obs[p]['elapsed_ns'],cpu_ns=obs[p]['cpu_ns']) for p in ('P0','P1')},
        metadata={p:{k:obs[p][k] for k in ('start_monotonic_ns','end_monotonic_ns','cpu_ns')} for p in ('P0_metadata','P1_metadata','P1_postcheck')},
        timings_ns=dict(tool_sum=sum(p['guest_action_sum_ns'] for p in task['paths'].values()),tools_and_downloads_span=span(calls),cold_request_span=span(cold),
            monitor_control_call_sum=sum(e['duration_ns'] for e in control),monitor_control_union=interval_union(control),
            controller=task['controller_elapsed_ns'],rollout=task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns']),
        minimum_free_disk_gib=outer['observed_host_available_min_bytes']/GIB)


def summarize(out):
    runs=read(out/'runs.json');assert [r['residency'] for r in runs]==['off','on']
    off,on=runs
    assert all(off[k]==on[k] for k in ('paths','patches','official','targets'))
    comparison={k:dict(off_ns=off['timings_ns'][k],on_ns=on['timings_ns'][k],difference_ns=on['timings_ns'][k]-off['timings_ns'][k],
        change_percent=(on['timings_ns'][k]/off['timings_ns'][k]-1)*100) for k in off['timings_ns']}
    public=[]
    for r in runs:
        row={k:v for k,v in r.items() if k not in ('patches','paths')}
        for k in ('group','storage','raw','report'):row[k]=str(Path(row[k]).relative_to(ROOT))
        public.append(row)
    value=dict(status='PASS_HOST_QUERY_PAIR',actual_actions=378,guest_samples=sum(s['samples'] for r in runs for s in r['sessions']),
        scope='One shared seven-guest GRPO pair, P0/P1 residency only off then on; guest and host periodic sampling both on; PSS off',
        comparison=comparison,runs=public,repeats=1,definition='Signed (on/off-1)*100 observed difference; direct query cost is not the whole-run effect')
    write(out/'comparison.json',value);return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--copy-daemon',type=Path);parser.add_argument('--report-only',action='store_true');args=parser.parse_args();out=args.output.resolve()
    if args.report_only:summarize(out);print('status=PASS result_dir='+str(out));return
    if args.copy_daemon is None:parser.error('live run requires copy-daemon')
    require_budget(available(ROOT));daemon=daemon_file(args.copy_daemon)
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists();os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_host_query_control.py','run_host_full_rootfs.py','run_host_file_workflow.py','host_full_rootfs_control.py','host_private_files.py','run_prettier6604_grpo.py','validate_isolated_agentenv.py','report_task_overview.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    write(out/'plan.json',dict(order=['off','on'],planned_actions=378,guest_sampling=True,host_periodic_sampling=True,pss=False,volume_gib=48))
    runs=[];write(out/'runs.json',runs);state=dict(status='FAIL',stage='precheck');started=False
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'precheck.json',dict(budget=require_budget(available(ROOT)),load_average=os.getloadavg(),source_state=source['State']))
        started=True;subprocess.run(['docker','start',SOURCE],capture_output=True,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source service not ready')
        for mode in ('off','on'):
            require_budget(available(ROOT));state.update(status='RUNNING',stage=mode,completed=len(runs));write(out/'result.json',state)
            command=['timeout','-k','120s','1500s','bash','scripts/run-host-file-monitor.sh','configs/platform-prettier6604-host-file.json',
                '--full-rootfs-group','shared','--copy-daemon',str(daemon),'--rootfs-residency',mode]
            write(out/(mode+'-command.json'),command);print('status=RUNNING residency='+mode+' result_dir='+str(out),flush=True)
            with (out/(mode+'.log')).open('x') as stream:proc=subprocess.run(command,cwd=ROOT,env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE),stdout=stream,stderr=subprocess.STDOUT)
            write(out/(mode+'-exit.json'),dict(exit_code=proc.returncode,load_average=os.getloadavg()));assert proc.returncode==0,'stop after first failure; preserve outputs'
            groups={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(mode+'.log')).read_text()) if Path(p).parent==ROOT/'.artifacts/host-full-rootfs'}
            assert len(groups)==1;runs.append(inspect_group(groups.pop(),mode));write(out/'runs.json',runs)
            print('status=PASS residency='+mode+' actions=189',flush=True)
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
