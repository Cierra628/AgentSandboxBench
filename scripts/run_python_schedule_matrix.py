"""Run the agreed two-task, three-schedule, three-repeat matrix; stop on failure."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from prepare_python_schedule_matrix import ROOT, plan
from prettier_no_proxy import clean_environment
from isolated_storage import GIB, available, capacity_budget
from run_light_monitor_controls import SOURCE, SOURCE_ID
from report_python_schedule_matrix import collect, plot_run, report


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def capacity_after_cleanup(path, capacity, previous=None, *, margin=None):
    """Allow up to five seconds for deleted successful-volume space to appear."""
    checks = []
    may_wait = bool(previous and previous['status'] == 'PASS' and
                    not (Path(previous['storage']) / 'volume.ext4').exists())
    for attempt in range(6 if may_wait else 1):
        if attempt:time.sleep(1)
        budget = capacity_budget(available(path), capacity, **({} if margin is None else dict(margin=margin)))
        checks.append(dict(monotonic_ns=time.monotonic_ns(), budget=budget))
        if budget['ready']:break
    return budget, checks


def continuation(proposed, previous, *, retry_failed=False):
    """Retain successful logical slots; failed attempts never enter repeat statistics."""
    assert previous['status']=='FAIL' and previous['source_returned_to_stopped']
    planned={(r['task'],r['round'],r['schedule']) for r in proposed['runs']}
    keys=[(r['task'],r['round'],r['schedule']) for r in previous['runs']]
    assert len(keys)==len(set(keys)) and set(keys)<=planned
    failed=[r for r in previous['runs'] if r['status']=='FAIL']
    assert retry_failed or not failed,'retrying a failed group requires explicit --retry-failed authorization'
    completed=[dict(r) for r in previous['runs'] if r['status']=='PASS']
    done={(r['task'],r['round'],r['schedule']) for r in completed}
    pending=[dict(r) for r in proposed['runs'] if (r['task'],r['round'],r['schedule']) not in done]
    return completed,pending,[*previous.get('previous_failures',[]),*failed]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume-from',type=Path,help='Stopped failed batch; reuse successful slots in a new output directory')
    parser.add_argument('--retry-failed',action='store_true',help='Only after user approval of the diagnosed failed group')
    parser.add_argument('--sphinx-branch-policy',choices=['original','early2-dual-output-v1'],default='original')
    parser.add_argument('--sphinx-find-exit-diagnostic',action='store_true')
    parser.add_argument('--xarray-find-exit-diagnostic',action='store_true')
    args=parser.parse_args();out=args.output.resolve()
    assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True)
    proposed=plan();proposed.update(volume_gib_by_task_schedule={
        'sphinx7590':dict(grpo=40,bpo=56,tvcache=56),
        'xarray6992':dict(grpo=40,bpo=64,tvcache=64)},
        disk_rule='volume capacity + 20 GiB reserve + 6 GiB margin; GRPO has no snapshot allocation')
    completed=[];pending=proposed['runs'];previous_failures=[]
    if args.resume_from:
        previous_path=args.resume_from.resolve()
        assert previous_path.is_relative_to(ROOT/'.artifacts') and previous_path!=out
        previous=read(previous_path/'result.json');old_plan=read(previous_path/'plan.json')
        assert [(t['profile'],t['image'],t['expected']) for t in old_plan['tasks']]==[(t['profile'],t['image'],t['expected']) for t in proposed['tasks']]
        completed,pending,previous_failures=continuation(proposed,previous,retry_failed=args.retry_failed)
        for cache_path in previous.get('released_caches',[]):assert read(Path(cache_path)/'resources.json')['released']
        for row in completed:
            assert read(Path(row['raw'])/'result.json')['status']=='PASS'
            row['reused_from']=str(previous_path)
            row.setdefault('branch_pipeline_policy','original')
        proposed.update(resume_from=str(previous_path),retry_failed_authorized=args.retry_failed)
    elif args.retry_failed:parser.error('--retry-failed requires --resume-from')
    proposed.update(pending_runs=pending,reused_completed_slots=len(completed),sphinx_branch_policy=args.sphinx_branch_policy,
                    sphinx_find_exit_diagnostic=args.sphinx_find_exit_diagnostic,
                    xarray_find_exit_diagnostic=args.xarray_find_exit_diagnostic)
    write(out/'plan.json',proposed)
    if not args.execute:
        print('status=PLAN_SAVED result_dir='+str(out));return 0
    state=dict(status='RUNNING',stage='preflight',batch=str(out),expected_runs=18,runs=completed,
               expected_new_runs=len(pending),reused_completed_slots=len(completed),
               resume_from=proposed.get('resume_from'),previous_failures=previous_failures,
               sphinx_branch_policy=args.sphinx_branch_policy,
               sphinx_find_exit_diagnostic=args.sphinx_find_exit_diagnostic,
               xarray_find_exit_diagnostic=args.xarray_find_exit_diagnostic,
               started_utc=datetime.now(timezone.utc).isoformat(),business_retries=0)
    published=ROOT/'results'/out.name;assert not published.exists()
    env=clean_environment(dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE,
        ASB_ISOLATED_UNUSED_NETWORK_POOLS='on',PYTHONDONTWRITEBYTECODE='1'))
    env.pop('ASB_LOCAL_NO_PROXY',None);env.pop('ASB_LOCAL_NO_PROXY_CHECK',None)
    code=out/'code';code.mkdir()
    for name in ('run_python_schedule_matrix.py','run_python_schedule.py','prepare_python_schedule_matrix.py',
                 'report_python_schedule_matrix.py','validate_isolated_agentenv.py','schedule_smoke_metrics.py',
                 'host_file_monitor.py','experiment_memory.py','host_schedule_files.py','host_schedule_cache_layers.py',
                 'sandbox_runtime_monitor.py','sphinx7590_inputs.py','xarray6992_inputs.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    started_source=False;cache=None
    def call(argv,label,timeout):
        write(out/(label+'-command.json'),list(map(str,argv)))
        with (out/(label+'.log')).open('x') as log:
            p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        if p.returncode:raise RuntimeError(label+': exit '+str(p.returncode)+'; '+str(out/(label+'.log')))
    def resources(label, profile='sphinx7590', schedule='grpo'):
        state['stage']=label+'-resource-check';write(out/'result.json',state)
        budget,checks=capacity_after_cleanup(ROOT,proposed['volume_gib_by_task_schedule'][profile][schedule]*GIB,
            state['runs'][-1] if state['runs'] else None)
        memory=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
        write(out/(label+'-resources.json'),dict(budget=budget,disk_capacity_checks=checks,mem_available_bytes=memory,load=os.getloadavg(),time_utc=datetime.now(timezone.utc).isoformat()))
        assert budget['ready'] and memory>64*GIB,'resource budget insufficient; no automatic deletion'
    def release_cache():
        nonlocal cache
        if cache and (cache/'resources.json').exists():
            label=cache.name+'-release'
            call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],label,90)
            assert read(cache/'resources.json')['released']
            state.setdefault('released_caches',[]).append(str(cache))
        cache=None
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=15))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        assert pending,'no unfinished slots; do not start a new service'
        resources('before-preparation',pending[0]['task'],pending[0]['schedule'])
        for task in proposed['tasks']:
            if not any(r['task']==task['profile'] for r in pending):continue
            command=[sys.executable,'-B',ROOT/'scripts/run_python_schedule.py','--inputs',ROOT/Path(task['input_config']).parent,
                '--task-profile',task['profile'],'--schedule','grpo','--check-only']
            if task['profile']=='sphinx7590' and args.sphinx_branch_policy!='original':command+=['--branch-pipeline-policy',args.sphinx_branch_policy]
            if task['profile']=='sphinx7590' and args.sphinx_find_exit_diagnostic:command+=['--find-exit-diagnostic']
            if task['profile']=='xarray6992' and args.xarray_find_exit_diagnostic:command+=['--xarray-find-exit-diagnostic']
            call(command,task['profile']+'-offline',60)
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started_source=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        for task in proposed['tasks']:
            task_runs=[r for r in pending if r['task']==task['profile']]
            if not task_runs:continue
            profile=task['profile'];state['stage']=profile+'-cache-prepare';write(out/'result.json',state)
            resources(profile+'-before-cache',profile,task_runs[0]['schedule'])
            config=read(ROOT/f'configs/platform-{profile}-endpoint-metrics.json')
            cache=ROOT/'.artifacts'/('sphinx-backbone' if profile=='sphinx7590' else 'python-backbone')/('schedule-cache-'+out.name+'-'+profile)
            call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,
                '--task-profile',profile,'--source-backup-directory',ROOT/config['cache_backup_directory']],profile+'-cache-prepare',360)
            for item in task_runs:
                label=profile+'-r'+str(item['round'])+'-'+item['schedule'];resources(label,profile,item['schedule'])
                row={k:item[k] for k in ('task','round','schedule')};row.update(status='RUNNING',log=str(out/(label+'.log')))
                retry=next((r for r in reversed(previous_failures) if (r['task'],r['round'],r['schedule'])==(item['task'],item['round'],item['schedule'])),None)
                if retry:
                    row['retry_of']=retry.get('raw',retry.get('log'));state['business_retries']+=1
                state['runs'].append(row);state['stage']=label;write(out/'result.json',state)
                command=[sys.executable,'-B',ROOT/'scripts/validate_isolated_agentenv.py','--automatic-window',
                    '--python-backbone-inputs',ROOT/config['inputs'],'--python-task-profile',profile,
                    '--python-readonly-replay','--python-persistent-sampling','--immutable-cache-seed',cache,
                    '--python-schedule',item['schedule']]
                command+=['--python-sphinx-pipeline-diagnostic','--python-sphinx-pipeline-policy-version','2'] if profile=='sphinx7590' else ['--python-diagnostic-continuation']
                if profile=='sphinx7590' and args.sphinx_branch_policy!='original':command+=['--python-branch-pipeline-policy',args.sphinx_branch_policy]
                if profile=='sphinx7590' and args.sphinx_find_exit_diagnostic:command+=['--python-schedule-find-exit-diagnostic']
                if profile=='xarray6992' and args.xarray_find_exit_diagnostic:command+=['--python-schedule-xarray-find-exit-diagnostic']
                print('status=RUNNING run='+label+' result_dir='+str(out),flush=True)
                try:
                    begin=time.monotonic_ns();call(command,label,1500)
                    text=(out/(label+'.log')).read_text()
                    storage={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',text) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
                    assert len(storage)==1
                    verified=collect(storage.pop(),{k:item[k] for k in ('task','round','schedule')})
                    row.update(verified,driver_seconds=(time.monotonic_ns()-begin)/1e9)
                    write(out/'result.json',state)
                    print('status=PASS run='+label,flush=True)
                except BaseException as error:
                    text=(out/(label+'.log')).read_text() if (out/(label+'.log')).exists() else ''
                    storage={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',text) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
                    row.update(status='FAIL',error=repr(error))
                    if len(storage)==1:
                        directory=storage.pop();row.update(storage=str(directory),raw=str(directory/'python-backbone'))
                        if (directory/'result.json').exists():
                            outer=read(directory/'result.json');row.update(failed_stage=outer.get('failed_stage'),original_error=outer.get('error'))
                        if (directory/'python-backbone/result.json').exists():
                            child=read(directory/'python-backbone/result.json');row.update(child_status=child['status'],child_error=child.get('error'),child_failed_stage=child.get('failed_stage'))
                    raise
            release_cache()
        assert len(state['runs'])==18 and all(r['status']=='PASS' for r in state['runs'])
        state.update(status='PASS',stage='complete')
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        try:release_cache()
        except Exception as error:state.update(status='FAIL',cache_release_error=repr(error))
        if started_source:
            try:
                call(['docker','stop','-t','30',SOURCE],'source-stop',60)
                after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=15))
                assert not after['Running'];write(out/'source-after.json',after);state['source_returned_to_stopped']=True
            except Exception as error:state.update(status='FAIL',source_cleanup_error=repr(error))
        state.update(finished_utc=datetime.now(timezone.utc).isoformat(),available_after_bytes=available(ROOT))
        # Plot only saved successful runs, after all task and service operations have ended.
        images=[]
        for row in state['runs']:
            if row['status']=='PASS':
                label=row['task']+'-r'+str(row['round'])+'-'+row['schedule']
                try:
                    image=plot_run(row,out/'plots'/label)
                    row['curve']=str(published/label/'curves.png');images.append((Path(image).parent,published/label))
                except Exception as error:
                    row['plot_error']=repr(error)
                    state.update(status='FAIL',report_status='PARTIAL',report_error='Saved task succeeded but a required curve was not generated')
        write(out/'result.json',state)
        report(state,published)
        for source,destination in images:shutil.copytree(source,destination)
        print('status='+state['status']+' result_dir='+str(out)+' report='+str(published/'REPORT.md'),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
