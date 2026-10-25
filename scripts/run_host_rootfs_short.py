"""Reuse one exported checkpoint for two original short suffixes and shared files."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from host_file_monitor import HostFileMonitor,measurement_group
from isolated_storage import available,capacity_budget,GIB,rootfs_network_pools
from platform_tasks import ROOT,configuration,preflight
from run_host_independent_pair import phase_command,resumed_capture
from run_light_monitor_controls import SOURCE,SOURCE_ID
from native_checkpoint_bundle import IMAGE


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def short_command(cfg,cache,source):
    source=Path(source)
    return [*phase_command(cfg,cache,'dual',source/'checkpoint-bundle',source/'prettier6604'),'--host-rootfs-short']


def check_source(state):
    if state['Id']!=SOURCE_ID:raise ValueError('source container identity differs from verified source')
    if state['Image']!=IMAGE:raise ValueError('source image differs from the checkpoint server image')


def task_action_receipt(task):
    return dict(actual_actions=task.get('actual_action_count'),
        actual_actions_status='REPORTED' if 'actual_action_count' in task else 'NOT_REPORTED')


def inspect_result(storage):
    storage=Path(storage);outer=read(storage/'result.json');raw=storage/'prettier6604'
    task=read(raw/'result.json');matched=read(raw/'host-matched.json');sessions=read(raw/'runtime-monitor.json')
    assert outer['status']==task['status']==matched['status']==sessions['status']=='PASS'
    assert task['actual_action_count']==20 and task['reused_action_count']==11
    assert len(matched['groups'])==1 and matched['groups'][0]['sandboxes']==2
    phase=matched['groups'][0];value=phase['shared_files'];query=value['query'];roots=query['rootfs_inventory']
    assert value['status']=='OBSERVED' and phase['functional_checks']=='PASS'
    assert phase['end_monotonic_ns']<=query['start_monotonic_ns']<query['end_monotonic_ns']<phase['device_released_ns']
    assert value['pss']=='NOT_REQUESTED' and 'endpoint_pss' not in phase and 'ordinary_residency' not in phase
    roles={g['role'] for g in phase['guests']};assert roles=={'N2-a','N2-b'}
    paths={r:task['paths'][r] for r in roles}
    assert all(p['action_count']==10 and p['source_patch_check']==p['independent_check']=='PASS' and
        (p['official_passed'],p['official_failed'])==(0,2) for p in paths.values())
    assert sessions['profile']=='light' and not sessions['host_vmm_requested'] and len(sessions['sessions'])==2
    assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 for s in sessions['sessions'])
    assert all(outer['cleanup'].get(k) for k in ('owned_container_removed','mount_and_loop_removed',
        'successful_fixture_removed','original_service_unchanged','original_source_firecracker_identities_unchanged'))
    assert outer['host_shared_preparation_sync']['end_monotonic_ns']<outer['host_file_creation']['verified_before_guest_creation_ns']
    assert len({tuple(o['object_key']) for o in query['objects']})==len(query['objects'])
    selected=[o for o in query['objects'] if o['category']=='shared_rootfs_layer']
    assert all(set(o['users'])==roles and o['residency']['stable'] for o in selected)
    return dict(status='PASS' if roots['status']=='IDENTIFIED' and selected else 'INCOMPLETE_MEASUREMENT',
        task_validation='PASS',rootfs_coverage=roots['status'],actual_actions=20,reused_prefix_actions=11,
        shared_rootfs_file_count=len(selected),shared_rootfs_resident_bytes=sum(o['residency']['resident_bytes'] for o in selected) if selected else None,
        rootfs_metadata_elapsed_ns=roots['end_monotonic_ns']-roots['start_monotonic_ns'],rootfs_metadata_cpu_ns=roots['cpu_ns'],
        rootfs_query_elapsed_ns=sum(o['residency']['end_monotonic_ns']-o['residency']['start_monotonic_ns'] for o in selected),
        rootfs_query_cpu_ns=sum(o['residency']['cpu_ns'] for o in selected),
        all_selected_query_elapsed_ns=query['end_monotonic_ns']-query['start_monotonic_ns'],
        suffix_wall_ns=phase['end_monotonic_ns']-phase['start_monotonic_ns'],
        tool_ns=sum(p['guest_action_sum_ns'] for p in paths.values()),
        official_results={r:{k:p[k] for k in ('official_task_status','official_passed','official_failed')} for r,p in paths.items()},
        query=query,scope='Two imported restores, original suffixes; shared rootfs files at final query, not total RAM or a no-sharing comparison.')


def workflow(config,resume_prefix):
    cfg=configuration(config)
    if not (cfg['task_id']=='prettier__prettier-6604' and cfg['rollout']=='single' and cfg.get('host_file_monitor') and cfg['checkpoint_after_step'] is None):
        raise ValueError('rootfs short requires the ordinary explicit Prettier host config')
    group,_=measurement_group();os.umask(0o077)
    out=ROOT/'.artifacts/host-rootfs-short'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False);write(out/'effective-config.json',cfg)
    result=dict(status='FAIL',stage='disk-budget',actual_actions=0);monitor=None;started=False
    source=os.environ.get('ASB_AGENTENV_SOURCE_SERVICE',SOURCE)
    cache=ROOT/'.artifacts/prettier6604'/('host-rootfs-short-cache-'+out.name)
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        budget=capacity_budget(available(ROOT),24*GIB);write(out/'disk-budget.json',budget)
        if not budget['ready']:raise RuntimeError('24 GiB volume requires over 50 GiB available')
        result['stage']='network-precheck'
        routes=subprocess.check_output(['ip','-j','route','show','table','all'],text=True,timeout=10)
        write(out/'network-precheck.json',dict(candidate_pools=rootfs_network_pools(routes),scope='Metadata precheck before source/volume creation; isolated service reselects from current routes'))
        capture=resumed_capture(resume_prefix);write(out/'prefix-reuse.json',capture)
        result['stage']='source-identity'
        state=json.loads(subprocess.check_output(['docker','inspect',source],timeout=20))[0]
        check_source(state)
        write(out/'source-before.json',{k:state['State'][k] for k in ('Running','Pid','StartedAt')})
        os.environ['ASB_AGENTENV_SOURCE_SERVICE']=source
        if not state['State']['Running']:
            result['stage']='start-source';started=True;call('source-start',['docker','start',source],60)
        result['stage']='preflight';write(out/'preflight.json',preflight(cfg))
        (out/'code').mkdir()
        for name in ('run_host_rootfs_short.py','run_host_file_workflow.py','host_file_matched_task.py','run_prettier6604_grpo.py',
            'validate_isolated_agentenv.py','isolated_storage.py','host_rollout_shared.py','host_rootfs_layers.py','host_prefix_compare.py','host_scaling.py','run_host_file_restore.py'):
            shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
        monitor=HostFileMonitor(out/'host-file',group,include_pss=False)
        result['stage']='cache-prepare';call('cache-prepare',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        budget=capacity_budget(available(ROOT),24*GIB)
        if not budget['ready']:raise RuntimeError('disk budget no longer sufficient after preparation')
        result['stage']='dual-suffix';command=short_command(cfg,cache,resume_prefix);write(out/'command.json',command)
        try:call('dual-suffix',command,1000)
        finally:
            candidates={Path(p) for p in re.findall(r'status=(?:RUNNING|PASS|FAIL) result_dir=(\S+)',(out/'dual-suffix.log').read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
            if len(candidates)==1:
                result['storage_result_dir']=str(next(iter(candidates)))
                task=Path(result['storage_result_dir'])/'prettier6604/result.json'
                if task.exists():result.update(task_action_receipt(read(task)))
        if len(candidates)!=1:raise ValueError('expected exactly one isolated short result')
        storage=candidates.pop();result.update(storage_result_dir=str(storage),actual_actions=20,stage='saved-result-validation')
        result['summary']=inspect_result(storage);write(out/'summary.json',result['summary'])
        result.update(status=result['summary']['status'],stage='complete')
    except BaseException as error:
        import traceback
        result['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if monitor:
            try:
                result['collector']=monitor.stop()
                if result['collector']['status']!='PASS':result['status']='FAIL'
            except Exception as error:result.update(status='FAIL',collector_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                result['cache_released']=read(cache/'resources.json')['released']
                if not result['cache_released']:result['status']='FAIL'
            except Exception as error:result.update(status='FAIL',cache_release_error=repr(error))
        if started:
            try:
                call('source-stop',['docker','stop','-t','30',source],60)
                after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',source],timeout=20))
                write(out/'source-after.json',{k:after[k] for k in ('Running','Pid','StartedAt')})
                result['source_returned_to_stopped']=not after['Running']
                if after['Running']:result['status']='FAIL'
            except Exception as error:result.update(status='FAIL',source_stop_error=repr(error))
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    parser.add_argument('--resume-prefix',type=Path,required=True)
    args=parser.parse_args();raise SystemExit(workflow(args.config,args.resume_prefix))
