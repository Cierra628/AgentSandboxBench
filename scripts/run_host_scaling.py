"""Use the existing isolated service lifecycle for one cumulative guest sequence."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from host_scaling import arguments,report
from platform_tasks import ROOT,configuration,preflight,commands


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def scaling_command(cfg,cache,mode,bundle=None,source=None):
    from run_host_file_restore import EXPORT
    _,live=commands(cfg,cache)
    # The existing GRPO adapter supplies start/run/cleanup, not its old schedule.
    live=[v for v in live if v not in ('--prettier6604-readonly-replay','--light-monitor')]
    return [*live,'--prettier6604-export',str(EXPORT),
        '--prettier6604-schedule','grpo','--light-monitor',*arguments(mode,bundle,source)]


def workflow(config,mode,resume_prefix=None):
    started=time.monotonic_ns()
    cfg=configuration(config)
    if not (cfg['task_id']=='prettier__prettier-6604' and cfg.get('host_file_monitor') and cfg['rollout']=='single' and cfg['checkpoint_after_step'] is None):
        raise ValueError('scaling requires the ordinary explicit Prettier host config')
    if cfg.get('damon_observe') or cfg.get('damon_disable') or cfg.get('monitor_mode')=='off':
        raise ValueError('scaling preserves guest sampling and default DAMON')
    if mode not in ('cold','restore') or (mode=='restore')!=(resume_prefix is not None):
        raise ValueError('only restore scaling requires an existing exported prefix')
    from host_file_monitor import measurement_group,HostFileMonitor
    from isolated_storage import available,capacity_budget,GIB
    group,_=measurement_group();ready=preflight(cfg)
    bundle=source=None
    if mode=='restore':
        from run_host_independent_pair import resumed_capture
        capture=resumed_capture(resume_prefix)
        source=Path(capture['storage'])/'prettier6604';bundle=source.parent/'checkpoint-bundle'
    capacity=16 if mode=='cold' else 24
    budget=capacity_budget(available(ROOT),capacity*GIB)
    if not budget['ready']:raise RuntimeError('insufficient scaling disk budget: '+json.dumps(budget))
    sampling=os.environ.get('ASB_HOST_FILE_SAMPLING','on')
    if sampling not in ('on','off'):raise ValueError('invalid periodic host sampling mode')
    os.umask(0o077)
    out=ROOT/'.artifacts/host-scaling'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False);write(out/'effective-config.json',cfg);write(out/'preflight.json',dict(ready,scaling_disk_budget=budget))
    code=out/'code';code.mkdir()
    for name in ('run_host_scaling.py','host_scaling.py','run_host_file_workflow.py','run_prettier6604_grpo.py',
                 'validate_isolated_agentenv.py','host_independent_task.py','host_prefix_compare.py','run_host_file_restore.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    if os.environ.get('ASB_HOST_ROOTFS_REFERENCE')=='on':
        for name in ('host_scaling_reference.py','host_rollout_shared.py','host_rootfs_layers.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
    cache=ROOT/'.artifacts/prettier6604'/('host-scaling-cache-'+out.name)
    state=dict(status='FAIL',mode=mode,host_periodic_sampling=sampling,whole_physical_savings='NOT_MEASURED')
    collector=None;stage='prepare'
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        if sampling=='on':collector=HostFileMonitor(out/'host-file',group,include_pss=False)
        call('cache-prepare',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        stage='isolated-runtime';call(stage,scaling_command(cfg,cache,mode,bundle,source),1300)
        found={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/(stage+'.log')).read_text())
            if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(found)!=1:raise ValueError('expected one isolated scaling result')
        storage=found.pop();outer=read(storage/'result.json')
        assert outer['status']=='PASS' and outer['host_scaling_preparation_sync']['status']=='PASS'
        assert all(outer['cleanup'].get(k) for k in ('owned_container_removed','mount_and_loop_removed',
            'successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
        assert outer['host_scaling_preparation_sync']['end_monotonic_ns']<outer['host_file_creation']['verified_before_guest_creation_ns']
        state.update(storage_result_dir=str(storage),preparation_sync=outer['host_scaling_preparation_sync'],status='PASS')
    except BaseException as error:
        import traceback
        state.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if collector:
            try:
                state['collector']=collector.stop()
                if state['collector']['status']!='PASS':state['status']='FAIL'
            except Exception as error:state.update(status='FAIL',collector_error=repr(error))
        else:state['collector']=dict(status='NOT_REQUESTED',scope='Periodic sampling off; fixed phase observations retained')
        if (cache/'resources.json').exists():
            try:
                call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                state['cache_released']=read(cache/'resources.json')['released']
                if not state['cache_released']:state['status']='FAIL'
            except Exception as error:state.update(status='FAIL',cache_release_error=repr(error))
    write(out/'result.json',state)
    if state['status']=='PASS':
        try:state['scaling_summary']=report(Path(state['storage_result_dir'])/'prettier6604',out/'report')
        except BaseException as error:
            import traceback
            state.update(status='FAIL',failed_stage='offline-report',error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
    state['workflow_elapsed_ns']=time.monotonic_ns()-started
    state['workflow_timing_scope']='Preflight, preparation, isolated runtime, owned cleanup and offline report; not task wall time'
    write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path)
    p.add_argument('--mode',choices=('cold','restore'),required=True);p.add_argument('--resume-prefix',type=Path)
    args=p.parse_args();raise SystemExit(workflow(args.config,args.mode,args.resume_prefix))
