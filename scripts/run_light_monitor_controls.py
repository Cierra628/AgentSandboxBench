"""Three fixed paired controls per task; existing isolated runners own task resources."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time
import urllib.request

from platform_tasks import ROOT, configuration, preflight
from isolated_storage import available, capacity_budget, GIB

SOURCE='asb-source-rebuilt-20261018T160925Z-2726362'
SOURCE_ID='8635246521cb148c41f0d97ae13dbd0f6b470883c856bb3d47a5e9fb872aaa3e'
TASKS=('sphinx7590','prettier6604','xarray6992')


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')
def rows(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def inspect_run(workflow,task,mode):
    workflow=Path(workflow);work=read(workflow/'result.json')
    assert work['status']=='PASS' and work['cache_released']
    storage=Path(work['storage_result_dir']);outer=read(storage/'result.json')
    raw=storage/('prettier6604' if task=='prettier6604' else 'python-backbone')
    result=read(raw/'result.json');summary=read(Path(work['report_dir'])/'summary.json')
    assert result['status']==outer['status']==summary['status']=='PASS'
    assert all(outer['cleanup'].values()) and all(result['cleanup'].values())
    assert all(v=='PASS' for k,v in result['checks'].items() if k!='official_task_tests')
    assert result['official_task_status']=='FAIL'
    if task=='prettier6604':
        official=read(raw/'official-task-result.json')['parser_results']
        outcome=dict(passed=official['num_tests_passed'],failed=official['num_tests_failed'])
        assert outcome==dict(passed=0,failed=2)
        patch=(raw/'rollouts/backbone/backbone_finish/patch.diff').read_text()
    else:
        outcome={k:result['official'][k] for k in ('passed','failed','skipped')}
        expected=(24,1) if task=='sphinx7590' else (945,12)
        assert outcome==dict(passed=expected[0],failed=expected[1],skipped=0)
        assert result['diagnostic_status']=='PASS' and result['strict_replay_status']=='FAIL'
        patch=(raw/'final.patch').read_text()
    tools=rows(Path(work['report_dir'])/'tools.tsv')
    assert len(tools)==(29 if task=='prettier6604' else 35)
    total=sum(int(r['duration_ns']) for r in tools)
    assert total==summary['tool_duration_sum_ns']
    samples=[];cpu=0;max_gap=None
    if mode=='on':
        manifest=read(raw/'runtime-monitor.json')
        assert manifest['profile']=='light' and manifest['interval_seconds']==.1
        assert not manifest['host_vmm_requested'] and manifest['guest_performance_version'] is None
        assert len(manifest['sessions'])==1
        session=manifest['sessions'][0];receipt=session['receipt'];directory=raw/session['raw_directory']
        assert session['collector_reaped'] and receipt['status']=='PASS' and receipt['profile']=='light'
        assert receipt['interval_seconds']==.1
        samples=rows(directory/'guest-memory.tsv');assert len(samples)==receipt['samples']>2
        gaps=[(int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(samples,samples[1:])]
        assert .09<statistics.median(gaps)<.2
        max_gap=max(gaps);cpu=receipt['cpu_user_seconds']+receipt['cpu_system_seconds']
        assert not any((directory/name).exists() for name in ('guest-vmstat.tsv','guest-cpu.tsv','guest-diskstats.tsv'))
    else:
        assert result['monitor_mode']=='off' and result['cleanup']['monitoring_disabled']
        assert not (raw/'runtime-monitor.json').exists() and summary['memory']=='NOT_MEASURED'
        for segment in rows(raw/'segments.tsv'):
            directory=raw/'rollouts'/segment['rollout']/segment['segment']
            assert not rows(directory/'guest-memory.tsv') and not rows(directory/'guest-vmstat.tsv')
    assert not any((raw/name).exists() for name in ('host-vmm','host-monitor.log','host/memory-samples.tsv','physical-cache','live-cache'))
    assert not (storage/'volume.ext4').exists()
    assert not (storage/'mount').is_mount() and not list((storage/'mount').iterdir())
    return dict(status='PASS',workflow=str(workflow),raw=str(raw),report=work['report_dir'],
        task=task,mode=mode,tool_count=len(tools),tool_seconds=total/1e9,
        frozen_replay_seconds=result['frozen_replay_elapsed_ns']/1e9,
        controller_seconds=result['controller_elapsed_ns']/1e9,
        sample_count=len(samples),collector_cpu_seconds=cpu,max_sample_gap_seconds=max_gap,
        memory_peak_mib=max((int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 for r in samples) if samples else None,
        cached_peak_mib=max(int(r['Cached_kib'])/1024 for r in samples) if samples else None,
        minimum_host_available_gib=outer['observed_host_available_min_bytes']/GIB,
        original_tests=outcome,patch=patch,owned_resources_removed=True)


def summarize(out,tasks=TASKS):
    runs=read(out/'runs.json');pairs=[];summaries={}
    assert len(runs)==6*len(tasks) and {r['task'] for r in runs}==set(tasks)
    for task in tasks:
        selected=[]
        for round_number in (1,2,3):
            pair=[r for r in runs if r['task']==task and r['round']==round_number]
            assert len(pair)==2 and {r['mode'] for r in pair}=={'on','off'}
            on=next(r for r in pair if r['mode']=='on');off=next(r for r in pair if r['mode']=='off')
            assert on['patch']==off['patch'] and on['original_tests']==off['original_tests']
            data=dict(task=task,round=round_number,order=[r['mode'] for r in pair])
            for field in ('tool_seconds','frozen_replay_seconds','controller_seconds'):
                data[field]=dict(on=on[field],off=off[field],change_percent=(on[field]/off[field]-1)*100)
            pairs.append(data);selected.append(data)
        summaries[task]={field:dict(
            on_median_seconds=statistics.median(p[field]['on'] for p in selected),
            off_median_seconds=statistics.median(p[field]['off'] for p in selected),
            paired_change_median_percent=statistics.median(p[field]['change_percent'] for p in selected),
            paired_change_range_percent=[min(p[field]['change_percent'] for p in selected),max(p[field]['change_percent'] for p in selected)])
            for field in ('tool_seconds','frozen_replay_seconds','controller_seconds')}
    value=dict(status='PASS',run_count=len(runs),pairs=pairs,tasks=summaries,
        definition='paired (on / off - 1) * 100; medians of three paired changes, signed values retained',
        scope='same original tasks and checks, fresh guests, .1 s guest meminfo plus existing boundary instrumentation vs all sampling off; no host cache reset; controller includes setup, tests and monitoring control')
    write(out/'comparison.json',value);return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report-only',action='store_true')
    parser.add_argument('--tasks',nargs='+',choices=TASKS,default=TASKS,help='Tasks to run, three pairs each; default keeps the original three-task experiment')
    args=parser.parse_args();out=args.output.resolve();tasks=tuple(args.tasks)
    if len(tasks)!=len(set(tasks)):parser.error('tasks must not repeat')
    if args.report_only:
        summarize(out,tasks);print('status=PASS result_dir='+str(out));return
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'configs').mkdir()
    (out/'code').mkdir()
    for name in ('run_light_monitor_controls.py','run-light-monitor-controls.sh','report_task_overview.py','sandbox_runtime_monitor.py','analyze_light_monitor_overhead.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    os.environ['ASB_AGENTENV_SOURCE_SERVICE']=SOURCE
    plan=[]
    for number,order in enumerate((tasks,tuple(reversed(tasks)),tasks),1):
        for task in order:
            modes=('on','off') if (number+(task=='prettier6604'))%2==0 else ('off','on')
            for mode in modes:plan.append(dict(round=number,task=task,mode=mode))
    write(out/'plan.json',dict(runs=plan,tasks=tasks,interval_seconds=.1,pairs_per_task=3))
    runs=[];write(out/'runs.json',runs);started=False;result=dict(status='FAIL',stage='precheck')
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running']
        assert source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        checks={}
        for task in tasks:
            for mode in ('on','off'):
                cfg=read(ROOT/f'configs/platform-{task}.json')
                if mode=='off':cfg.update(monitor_mode='off',light_monitor=False)
                path=out/'configs'/f'{task}-{mode}.json';write(path,cfg)
                budget=capacity_budget(available(ROOT),8*GIB);assert budget['ready']
                checks[task+'-'+mode]=dict(budget=budget,inputs=preflight(configuration(path),offline=True))
        write(out/'precheck.json',dict(checks=checks,load_average=os.getloadavg()))
        result['stage']='source-start'
        subprocess.run(['timeout','30s','docker','start',SOURCE],check=True,capture_output=True);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source service not ready')
        for item in plan:
            name=f"r{item['round']:02}-{item['task']}-{item['mode']}";result['stage']=name
            write(out/'result.json',dict(result,status='RUNNING',completed=len(runs)))
            print('status=RUNNING run='+name+' result_dir='+str(out),flush=True)
            begin=time.monotonic_ns();load=os.getloadavg()
            with (out/(name+'.log')).open('x') as log:
                proc=subprocess.run(['timeout','-k','120s','1500s','bash','scripts/use-platform.sh','run',
                    str(out/'configs'/f"{item['task']}-{item['mode']}.json")],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            write(out/(name+'-exit.json'),dict(exit_code=proc.returncode,elapsed_ns=time.monotonic_ns()-begin,load_average=load))
            assert proc.returncode==0,name+' failed; keep results and diagnose before further runs'
            paths=re.findall(r'status=PASS result_dir=(\S+)',(out/(name+'.log')).read_text())
            workflows=[p for p in paths if Path(p).parent==ROOT/'.artifacts/platform-tasks']
            assert len(set(workflows))==1
            verified=inspect_run(workflows[0],item['task'],item['mode'])
            if item['task']=='prettier6604' and item['mode']=='on':
                raw=Path(verified['raw']);events=read(raw/'events.json')
                blocking=[e['stage'] for e in events if e['stage'].startswith('monitor-') and not e['stage'].startswith('monitor-collect-')]
                assert sorted(blocking)==sorted(['monitor-upload-bundle','monitor-ready','monitor-phase','monitor-finish','monitor-download'])
                assert (raw/'runtime-code/sandbox_runtime_monitor.py').read_bytes()==(ROOT/'scripts/sandbox_runtime_monitor.py').read_bytes()
                verified['optimized_light_control']='PASS_FIVE_BLOCKING_CALLS'
            runs.append(dict(item,**{k:v for k,v in verified.items() if k not in item},load_average=load))
            write(out/'runs.json',runs)
            print('status=PASS run='+name+' tool_seconds='+str(verified['tool_seconds']),flush=True)
        summarize(out,tasks);result.update(status='PASS',stage='complete',completed=len(runs))
    except BaseException as error:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());result.update(error=repr(error),completed=len(runs))
        raise
    finally:
        if started:
            with (out/'source-stop.log').open('w') as log:
                stop=subprocess.run(['timeout','60s','docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT)
            state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=20))
            write(out/'source-after.json',state)
            result['source_returned_to_stopped']=stop.returncode==0 and not state['Running']
            if not result['source_returned_to_stopped']:result['status']='FAIL'
        write(out/'result.json',result)
        print('status='+result['status']+' result_dir='+str(out),flush=True)
    if result['status']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
