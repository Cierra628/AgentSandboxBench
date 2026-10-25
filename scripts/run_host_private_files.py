"""One real shared/private 19-file pair; existing isolated runners own each service."""
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
import urllib.request

from host_private_files import ROOT,read,write,prepared_objects,query_objects,verify_live,compare
from isolated_storage import GIB,available,capacity_budget,rootfs_network_pools
from platform_tasks import configuration,preflight,commands


def wait_event(path,workers,timeout=600):
    start=time.monotonic()
    while not path.exists():
        if any(p.poll() is not None for p in workers):raise RuntimeError('member exited before '+str(path))
        if time.monotonic()-start>timeout:raise TimeoutError('member event timeout: '+str(path))
        time.sleep(.1)
    return read(path)


def release(directory,event,success=True):
    (Path(directory)/(event+'.release')).write_text('continue\n' if success else 'abort\n')


def member_command(cfg,cache):
    from run_host_file_restore import EXPORT
    _,live=commands(cfg,cache)
    live=[v for v in live if v not in ('--prettier6604-readonly-replay','--light-monitor')]
    return [*live,'--prettier6604-export',str(EXPORT),'--prettier6604-schedule','grpo',
        '--prettier6604-grpo-capacity-gib','16','--light-monitor','--host-prefix-count','1']


def audit_member(service):
    from host_private_files import audit_prefix_task
    storage=Path(service['storage']);outer=read(storage/'result.json');raw=storage/'prettier6604'
    task=read(raw/'result.json')
    assert outer['status']==task['status']=='PASS' and task['actual_action_count']==11
    assert task['official_evaluation']=='NOT_RUN_PREFIX_ONLY' and task['cleanup']['service_sandbox_list_empty']
    prefix=audit_prefix_task(raw)
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed',
        'successful_fixture_removed','original_service_unchanged','original_source_firecracker_identities_unchanged'))
    assert not (storage/'volume.ext4').exists()
    monitor=read(raw/'runtime-monitor.json')
    assert monitor['profile']=='light' and monitor['interval_seconds']==.1 and not monitor['host_vmm_requested']
    assert all(s['receipt']['status']=='PASS' and s['collector_reaped'] for s in monitor['sessions'])
    return dict(status='PASS',storage=str(storage),actual_actions=11,
        tool_ns=prefix['tool_ns'],
        official_evaluation='NOT_RUN_PREFIX_ONLY',cleanup=outer['cleanup'])


def group(config,mode):
    from experiment_disk_guard import writer_lock
    from host_file_monitor import measurement_group,HostFileMonitor
    cfg=configuration(config)
    if not (cfg['task_id']=='prettier__prettier-6604' and cfg['rollout']=='single' and cfg['checkpoint_after_step'] is None):
        raise ValueError('private-file pair requires ordinary single Prettier config')
    if cfg.get('damon_disable') or cfg.get('damon_observe') or cfg.get('monitor_mode')=='off':raise ValueError('default reclamation and guest sampling required')
    preflight(cfg);group_path,_=measurement_group()
    budget=capacity_budget(available(ROOT),32*GIB)
    if not budget['ready']:raise RuntimeError('combined 32 GiB volume budget not met')
    os.umask(0o077)
    out=ROOT/'.artifacts/host-private-files'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+mode+'-'+str(os.getpid()))
    out.mkdir(parents=True);write(out/'budget.json',budget)
    cache=ROOT/'.artifacts/prettier6604'/('private-file-seed-'+out.name)
    state=dict(status='RUNNING',mode=mode,services=[],stages={},pss='NOT_REQUESTED',planned_actions=22)
    code=out/'code';code.mkdir()
    for name in ('host_private_files.py','run_host_private_files.py','validate_isolated_agentenv.py',
        'run_prettier6604_grpo.py','report_task_overview.py','host_rootfs_layers.py','host_file_monitor.py','experiment_disk_guard.py','run_host_file_workflow.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    workers=[];logs=[];collector=None
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        with writer_lock() as lock_fd:
            try:
                collector=HostFileMonitor(out/'host-file',group_path,include_pss=False)
                with (out/'prepare.log').open('x') as log:
                    subprocess.run([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
                routes=json.loads(subprocess.check_output(['ip','-j','route','show','table','all'],text=True,timeout=10))
                for slot in range(2):
                    directory=out/('member-'+str(slot));directory.mkdir()
                    pools=rootfs_network_pools(json.dumps(routes));routes.extend(dict(dst=p) for p in pools)
                    value=dict(mode=mode,slot=slot,network_pools=pools)
                    if mode=='shared' and slot==1:
                        value['reuse_targets_from']=str(Path(state['services'][0]['mount'])/'env/image-cache/commits')
                    write(directory/'member.json',value)
                    env=dict(os.environ,ASB_HOST_PRIVATE_MEMBER=str(directory/'member.json'),ASB_PRIVATE_WRITER_FD=str(lock_fd))
                    log=(directory/'run.log').open('x');logs.append(log)
                    command=member_command(cfg,cache);write(directory/'command.json',command)
                    worker=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,pass_fds=(lock_fd,))
                    workers.append(worker)
                    ready=wait_event(directory/'service-ready.json',workers)
                    state['services'].append(dict(ready,directory=str(directory)))
                    write(out/'result.json',state)
                sync_start=time.monotonic_ns()
                for service in state['services']:
                    subprocess.run(['sync','-f',service['mount']],check=True,timeout=60)
                state['sync']=dict(start_monotonic_ns=sync_start,end_monotonic_ns=time.monotonic_ns())
                preparation_start=time.monotonic_ns();preparation_cpu=time.thread_time_ns()
                objects=prepared_objects(state['services'],mode)
                state['preparation_metadata']=dict(start_monotonic_ns=preparation_start,
                    end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-preparation_cpu)
                state['stages']['P0']=query_objects(objects,group_path,'prepared-before-guests')
                write(out/'result.json',state)
                state['guest_creation_begin_monotonic_ns']=time.monotonic_ns()
                for service in state['services']:
                    release(service['directory'],'service-ready')
                    wait_event(Path(service['directory'])/'guest-ready.json',workers)
                # Both guests exist before the same original prefixes are dispatched.
                for service in state['services']:release(service['directory'],'guest-ready')
                finished=[wait_event(Path(service['directory'])/'paths-finished.json',workers) for service in state['services']]
                state['paths_end_monotonic_ns']=max(row['task_end_monotonic_ns'] for row in finished)
                state['creation_and_prefix_ns']=state['paths_end_monotonic_ns']-state['guest_creation_begin_monotonic_ns']
                metadata_start=time.monotonic_ns();metadata_cpu=time.thread_time_ns()
                roots,resolvers=verify_live(state['services'],objects,group_path,out)
                state['metadata']=dict(start_monotonic_ns=metadata_start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-metadata_cpu)
                state['stages']['P1']=query_objects(objects,group_path,'prefixes-finished-before-delete')
                from host_rootfs_layers import validate_rootfs_inventory
                validate_rootfs_inventory(roots)
                saved=read(out/'live-targets.json')['bindings']
                for role,(resolver,sid) in resolvers.items():assert resolver.resolve(sid)==saved[role]
                write(out/'result.json',state)
                state['status']='OBSERVED'
            finally:
                # Member 1's binds may hold member 0's filesystem. Release it first.
                for slot in reversed(range(len(workers))):
                    service_dir=out/('member-'+str(slot))
                    for event in ('service-ready','guest-ready','paths-finished'):
                        if not (service_dir/(event+'.release')).exists():release(service_dir,event,success=state['status']=='OBSERVED')
                    try:workers[slot].wait(timeout=240)
                    except subprocess.TimeoutExpired:
                        workers[slot].terminate()
                        try:workers[slot].wait(timeout=150)
                        except subprocess.TimeoutExpired:
                            state['cleanup_pending_member']=slot
                            raise RuntimeError('owned member has not stopped; preserve its source volume and seed')
                state['member_exit_codes']=[worker.returncode for worker in workers]
            assert state['status']=='OBSERVED' and state['member_exit_codes']==[0,0]
            state['audits']=[audit_member(service) for service in state['services']]
            state['actual_actions']=sum(row['actual_actions'] for row in state['audits'])
            assert state['actual_actions']==22
            state['tool_ns']=sum(row['tool_ns'] for row in state['audits'])
            state['query_total_ns']=sum(row['elapsed_ns'] for row in state['stages'].values())
            state['status']='PASS'
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for log in logs:log.close()
        if collector:
            state['collector']=collector.stop()
            if state['collector']['status']!='PASS':state['status']='FAIL'
        if any(worker.poll() is None for worker in workers):
            state['seed_release_pending']='An owned member is still running; do not unmount its source'
            state['status']='FAIL'
        elif (cache/'resources.json').exists():
            with (out/'release-seed.log').open('x') as log:
                result=subprocess.run([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],stdout=log,stderr=subprocess.STDOUT,timeout=90)
            state['seed_released']=result.returncode==0 and read(cache/'resources.json')['released']
            if not state['seed_released']:state['status']='FAIL'
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def recovered_shared(path):
    """Consume separate recovery evidence; never rewrite an original FAIL receipt."""
    path=Path(path).resolve();state=read(path/'result.json');recovery=read(path/'report-recovery.json')
    assert state['mode']=='shared' and state['status']=='FAIL'
    assert recovery['status']=='PASS_TARGET_MEASUREMENT_WITH_RECOVERED_REPORTS'
    assert recovery['actual_actions']==22 and recovery['original_group']==str(path)
    from host_private_files import audit_prefix_task
    for service in state['services']:
        storage=Path(service['storage']);outer=read(storage/'result.json')
        assert outer['status']=='FAIL' and outer['failed_stage']=='default-runtime-report'
        assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed','original_service_unchanged','original_source_firecracker_identities_unchanged'))
        assert audit_prefix_task(storage/'prettier6604')['status']=='PASS'
    assert read(path/'live-targets.json')['status']=='PASS'
    assert state['collector']['status']=='PASS' and state['seed_released']
    assert all(stage['status']=='OBSERVED' and len(stage['objects'])==19 for stage in state['stages'].values())
    return dict(mode='shared',status=recovery['status'],result_dir=str(path),stages=state['stages'],
        reused_actions=22,original_group_status='FAIL',post_report_write_boundary_audit='NOT_RUN')


def execution_modes(order='shared-first',resume_shared=None):
    if order not in ('shared-first','private-first'):raise ValueError('unknown private-file execution order')
    if resume_shared:
        if order!='shared-first':raise ValueError('shared recovery requires the original shared-first order')
        return ('private',)
    return ('shared','private') if order=='shared-first' else ('private','shared')


def comparison_for_groups(groups):
    by_mode={row['mode']:row for row in groups}
    if len(groups)!=2 or set(by_mode)!={'shared','private'}:raise ValueError('one shared and one private group required')
    return compare(by_mode['shared']['stages'],by_mode['private']['stages'])


def pair(config,resume_shared=None,order='shared-first'):
    from run_light_monitor_controls import SOURCE,SOURCE_ID
    modes=execution_modes(order,resume_shared)
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
    out=ROOT/'.artifacts/host-private-file-pairs'/stamp;out.mkdir(parents=True)
    state=dict(status='RUNNING',groups=[],planned_actions=44,requested_order=order,executed_group_order=list(modes));started=False
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        if resume_shared:state['groups'].append(recovered_shared(resume_shared))
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        assert capacity_budget(available(ROOT),32*GIB)['ready']
        write(out/'resources-before.json',dict(loadavg=Path('/proc/loadavg').read_text().strip(),
            available_bytes=available(ROOT),mem_available_line=next(line for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')),
            docker_ps=subprocess.check_output(['docker','ps','--format','{{.Names}} {{.Status}}'],text=True,timeout=10)))
        write(out/'source-before.json',source['State'])
        env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE)
        started=True;subprocess.run(['docker','start',SOURCE],capture_output=True,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+60
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source health timeout')
            time.sleep(1)
        for mode in modes:
            command=['bash',str(ROOT/'scripts/run-host-file-monitor.sh'),str(Path(config).resolve()),'--private-file-group',mode]
            write(out/(mode+'-command.json'),command)
            with (out/(mode+'.log')).open('x') as log:
                completed=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1700)
            found={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',(out/(mode+'.log')).read_text()) if Path(p).parent==ROOT/'.artifacts/host-private-files'}
            row=dict(mode=mode,returncode=completed.returncode,results=list(map(str,found)));state['groups'].append(row);write(out/'result.json',state)
            if completed.returncode or len(found)!=1:raise RuntimeError(mode+' control failed; preserve and stop')
            result=read(next(iter(found))/'result.json');assert result['status']=='PASS'
            row.update(result_dir=str(next(iter(found))),status='PASS',stages=result['stages'])
        state['comparison']=comparison_for_groups(state['groups'])
        state.update(status='PASS_TARGET_MEASUREMENT_WITH_REPORT_RECOVERY' if resume_shared else 'PASS',
            actual_actions=44,new_actions=22 if resume_shared else 44,reused_actions=22 if resume_shared else 0)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            with (out/'source-stop.log').open('x') as log:stopped=subprocess.run(['docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT,timeout=60)
            after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=20))
            write(out/'source-after.json',after)
            state['source_returned_to_stopped']=stopped.returncode==0 and not after['Running']
            if not state['source_returned_to_stopped']:state['status']='FAIL'
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status'] in ('PASS','PASS_TARGET_MEASUREMENT_WITH_REPORT_RECOVERY') else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('pair','group'));parser.add_argument('config',type=Path)
    parser.add_argument('--mode',choices=('shared','private'))
    parser.add_argument('--resume-shared',type=Path,help='Reuse an observed shared group with explicit offline report-recovery evidence; execute only the private group')
    parser.add_argument('--order',choices=('shared-first','private-first'),default='shared-first',help='Actual pair execution order; default preserves the original entry')
    args=parser.parse_args()
    if (args.action=='group')!=(args.mode is not None):parser.error('group requires mode; pair has no mode')
    if args.resume_shared and args.action!='pair':parser.error('resume-shared is only for pair')
    if args.order!='shared-first' and args.action!='pair':parser.error('order is only for pair')
    if args.resume_shared and args.order!='shared-first':parser.error('shared recovery requires shared-first')
    raise SystemExit(group(args.config,args.mode) if args.action=='group' else pair(args.config,args.resume_shared,args.order))
