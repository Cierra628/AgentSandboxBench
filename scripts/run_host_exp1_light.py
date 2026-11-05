"""One isolated original Exp1 task; guest meminfo and host current only."""
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

from exp1_light import ROOT,configuration


def run(config):
    configuration(config)
    from isolated_storage import available,capacity_budget,GIB
    budget=capacity_budget(available(ROOT),20*GIB)
    if not budget['ready']:raise RuntimeError('Exp1 requires >46 GiB available: '+json.dumps(budget))
    from host_file_monitor import measurement_group,HostFileMonitor
    group,_=measurement_group()
    out=ROOT/'.artifacts/host-exp1-light'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_host_exp1_light.py','run_exp1_checkpoint.py','exp1_light.py','guest_exp1_actions.py',
                 'sandbox_runtime_monitor.py','report_task_overview.py','report_host_file.py','run_host_file_workflow.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    shutil.copy2(config,out/'config.json')
    state=dict(status='FAIL',task_id='prettier__prettier-14400',budget=budget)
    cache=ROOT/'.artifacts/prettier6604'/('exp1-light-seed-'+out.name)
    collector=None
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        collector=HostFileMonitor(out/'host-file',group,include_pss=False)
        with (out/'prepare.log').open('x') as log:
            subprocess.run([sys.executable,'-B',str(ROOT/'scripts/prepare_prettier6604_cache.py'),str(cache)],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=300,check=True)
        command=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window',
            '--platform-config',str(Path(config).resolve()),'--exp1-light','--light-monitor','--host-file-monitor','--host-current-only',
            '--immutable-cache-seed',str(cache)]
        (out/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        with (out/'runtime.log').open('x') as log:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1100)
        children={Path(p) for p in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)',(out/'runtime.log').read_text())
                  if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(children)!=1:raise ValueError('expected one isolated Exp1 result')
        storage=children.pop();state['storage']=str(storage)
        assert result.returncode==0,'isolated workflow exit '+str(result.returncode)
        outer=json.loads((storage/'result.json').read_text())
        assert outer['status']=='PASS' and outer['default_runtime_report']['status']=='PASS'
        assert outer['checks']['actual_writable_files_inside_volume']=='PASS'
        assert outer['checks']['full_exp1_and_official_tests']=='PASS'
        assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed',
            'successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged'))
        assert outer['host_file_residency']['status']=='NOT_REQUESTED'
        raw=storage/'exp1'/outer['exp1_light']['raw_relative']
        task=json.loads((raw/'result.json').read_text())
        assert task['status']=='PASS' and task['official_task_status']=='PASS' and all(task['cleanup'].values())
        monitor=json.loads((raw/'runtime-monitor.json').read_text())
        assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==1
        assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 for s in monitor['sessions'])
        state.update(status='PASS',raw=str(raw),actual_actions=26)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if collector:
            try:state['collector']=collector.stop()
            except Exception as error:state.update(status='FAIL',collector_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                with (out/'release.log').open('x') as log:
                    subprocess.run([sys.executable,'-B',str(ROOT/'scripts/prepare_prettier6604_cache.py'),str(cache),'--release'],
                        cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=90,check=True)
                state['cache_released']=json.loads((cache/'resources.json').read_text())['released']
                assert state['cache_released']
            except Exception as error:state.update(status='FAIL',cache_release_error=repr(error))
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    if state['status']=='PASS':
        try:
            from report_host_file import report
            shutil.copytree(storage/'report',out/'task-report')
            report(out/'host-file',None,out/'host-report',creation_path=storage/'result.json')
        except Exception as error:
            state.update(status='FAIL',report_error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    args=parser.parse_args();raise SystemExit(run(args.config))
