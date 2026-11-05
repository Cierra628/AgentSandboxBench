"""One frozen tool task with guest meminfo and host current; no PSS or mincore."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

from platform_tasks import ROOT,configuration,preflight


def check_inputs(config,spec_path):
    cfg=configuration(config)
    if not (cfg['task_id']=='sphinx-doc__sphinx-7590' and cfg['rollout']=='single' and
            cfg['checkpoint_after_step'] is None and cfg.get('host_file_monitor')):
        raise ValueError('tool replay currently requires the explicit single Sphinx host image config')
    spec=json.loads(Path(spec_path).read_text())
    if set(spec)!={'task_config','replay_source'}:raise ValueError('require task_config and replay_source')
    spec={key:(ROOT/value).resolve() for key,value in spec.items()}
    from online_task import load
    task=load(spec['task_config'])
    if task['profile']!='sphinx7590':raise ValueError('this replay entry uses the prepared Sphinx image')
    check=[sys.executable,'-B',str(ROOT/'scripts/run_tool_capture.py'),'--inputs',cfg['inputs'],
           '--task-config',str(spec['task_config']),'--replay-source',str(spec['replay_source']),
           '--light-monitor','--check-only']
    subprocess.run(check,cwd=ROOT,capture_output=True,text=True,timeout=60,check=True)
    return cfg,spec,task


def run(config,spec_path):
    cfg,spec,task=check_inputs(config,spec_path)
    preflight(cfg)
    from host_file_monitor import measurement_group,HostFileMonitor
    group,_=measurement_group()
    out=ROOT/'.artifacts/host-tool-replay'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True)
    (out/'code').mkdir()
    for name in ('run_host_tool_replay.py','run_tool_capture.py','report_task_overview.py',
                 'host_file_monitor.py','report_host_file.py','run_host_file_workflow.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    shutil.copy2(spec_path,out/'replay-config.json')
    cache=ROOT/'.artifacts/sphinx-backbone'/('tool-replay-cache-'+out.name)
    state=dict(status='FAIL',task_id=task['task_id'],memory_mib=task['memory_mib'],cpu=2,
               source=str(spec['replay_source']),measurement='guest meminfo + host current; PSS/mincore off')
    collector=None
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,check=True)
    print('status=RUNNING result_dir='+str(out),flush=True)
    stage='prepare'
    try:
        collector=HostFileMonitor(out/'host-file',group,include_pss=False)
        call(stage,[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,
            '--source-backup-directory',cfg['cache_backup_directory'],'--task-profile','sphinx7590'],300)
        command=['bash',ROOT/'scripts/validate-isolated-storage.sh','--automatic-window',
            '--immutable-cache-seed',cache,'--python-task-profile','sphinx7590',
            '--python-backbone-inputs',cfg['inputs'],'--python-readonly-replay','--python-persistent-sampling',
            '--python-cache-reference',cfg['reference'],'--python-tool-capture-demo',
            '--python-tool-task-config',spec['task_config'],'--python-tool-replay-source',spec['replay_source'],
            '--light-monitor','--host-file-monitor','--host-current-only']
        (out/'command.json').write_text(json.dumps(list(map(str,command)),indent=2)+'\n')
        stage='runtime';call(stage,command,1100)
        children={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/'runtime.log').read_text())
                  if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(children)!=1:raise ValueError('expected one isolated result')
        storage=children.pop();outer=json.loads((storage/'result.json').read_text())
        raw=storage/'python-backbone';result=json.loads((raw/'result.json').read_text())
        assert outer['status']==result['status']=='PASS' and all(result['cleanup'].values())
        assert outer['host_file_residency']['status']=='NOT_REQUESTED'
        assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed',
            'successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged'))
        assert result['checks']['task_outcome_preserved']=='PASS'
        assert result['checks']['frozen_replay_matches_calls_and_final_file']=='PASS'
        monitor=json.loads((raw/'runtime-monitor.json').read_text())
        assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==1
        assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 for s in monitor['sessions'])
        state.update(status='PASS',storage=str(storage),task_outcome=result['task_outcome'],
                     actual_actions=result['replay']['action_count'])
    except BaseException as error:
        state.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
        if (out/'runtime.log').exists():
            children={Path(p) for p in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)',(out/'runtime.log').read_text())
                      if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
            if len(children)==1:state['storage']=str(children.pop())
    finally:
        if collector:
            try:state['collector']=collector.stop()
            except Exception as error:state.update(status='FAIL',collector_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call('release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                state['cache_released']=json.loads((cache/'resources.json').read_text())['released']
                assert state['cache_released']
            except Exception as error:state.update(status='FAIL',release_error=repr(error))
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    if state['status']=='PASS':
        try:
            from tool_trajectory import report as task_report
            from report_host_file import report as host_report
            task_report(raw,out/'task-report')
            host_report(out/'host-file',None,out/'host-report',creation_path=storage/'result.json')
        except Exception as error:
            state.update(status='FAIL',failed_stage='report',error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path);parser.add_argument('spec',type=Path)
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    if args.check_only:
        check_inputs(args.config,args.spec);print('status=VALIDATED');raise SystemExit(0)
    raise SystemExit(run(args.config,args.spec))
