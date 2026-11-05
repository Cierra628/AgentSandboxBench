"""Explicit complete GRPO/BPO pair, using the existing private-service lifecycle."""
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

from host_private_files import ROOT,read,write,compare
from isolated_storage import GIB,available,capacity_budget
from platform_tasks import configuration,preflight,commands


def require_budget(free,schedule='grpo'):
    if schedule not in ('grpo','bpo'):raise ValueError('unsupported full copy schedule')
    capacity=48 if schedule=='grpo' else 64
    value=capacity_budget(free,capacity*GIB)
    if not value['ready']:raise RuntimeError(f'full {schedule} copy control requires >{capacity+26} GiB free: '+json.dumps(value))
    return value


def daemon_file(path):
    path=Path(path).resolve()
    with path.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':raise ValueError('explicit experiment daemon is not ELF')
    return path


def full_comparison(shared,private):
    if shared.get('schedule','grpo')!=private.get('schedule','grpo'):raise ValueError('compare within one schedule, not GRPO versus BPO')
    if bool(shared.get('cold_read_requested'))!=bool(private.get('cold_read_requested')):
        raise ValueError('compare within one initial cache policy, not cold versus prepared-warm')
    result=compare(shared,private)
    result['scope']='Direct initial-lower file residency comparison: one private service and seven guests per group; preparation included, not whole RAM'
    if shared.get('cold_read_requested'):
        from host_full_rootfs_control import validate_observation
        for value in (shared,private):validate_observation(value)
        result['scope']='Direct initial-lower file residency comparison: seven guests per group, confirmed zero P0 and original complete GRPO; not whole RAM'
    return result


def member_command(cfg,cache,mode,daemon,schedule='grpo',*,residency='on',cold_read=False):
    from host_full_rootfs_control import validate_residency,validate_cold_read
    validate_residency(mode,schedule,residency)
    validate_cold_read(mode,schedule,residency,cold_read)
    from run_host_file_restore import EXPORT
    _,command=commands(cfg,cache)
    command=[v for v in command if v not in ('--prettier6604-readonly-replay','--light-monitor')]
    return command+['--prettier6604-export',str(EXPORT),'--prettier6604-schedule',schedule,'--light-monitor',
        '--host-full-rootfs-mode',mode,'--host-full-rootfs-daemon',str(daemon),
        *(['--host-full-rootfs-residency','off'] if residency=='off' else []),
        *(['--host-full-rootfs-cold-read'] if cold_read else [])]


def group(config,mode,daemon,schedule='grpo',*,residency='on',cold_read=False):
    from host_full_rootfs_control import validate_residency,validate_observation,validate_cold_read
    validate_residency(mode,schedule,residency)
    validate_cold_read(mode,schedule,residency,cold_read)
    require_budget(available(ROOT),schedule);daemon=daemon_file(daemon)
    cfg=configuration(config)
    if not (cfg['task_id']=='prettier__prettier-6604' and cfg['rollout']=='single' and cfg.get('host_file_monitor') and cfg['checkpoint_after_step'] is None):
        raise ValueError('full copy entry requires the ordinary explicit single Prettier host config')
    preflight(cfg)
    from host_file_monitor import measurement_group,HostFileMonitor
    group_path,_=measurement_group()
    out=ROOT/'.artifacts/host-full-rootfs'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+mode+'-'+str(os.getpid()))
    out.mkdir(parents=True);cache=ROOT/'.artifacts/prettier6604'/('full-rootfs-seed-'+out.name)
    (out/'code').mkdir()
    for name in ('run_host_full_rootfs.py','run_host_file_workflow.py','host_full_rootfs_control.py',*(['host_grpo_cold_read.py','host_cold_file_read.py'] if cold_read else [])):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    shutil.copy2(config,out/'config.json')
    write(out/'budget.json',require_budget(available(ROOT),schedule))
    collector=None;state=dict(status='FAIL',mode=mode,schedule=schedule,actual_actions=None)
    def call(name,argv,timeout):
        with (out/(name+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        collector=HostFileMonitor(out/'host-file',group_path,include_pss=False)
        call('prepare',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        command=member_command(cfg,cache,mode,daemon,schedule,residency=residency,cold_read=cold_read)
        write(out/'command.json',command);call('runtime',command,1300)
        found={Path(p) for p in re.findall(r'status=PASS result_dir=(\S+)',(out/'runtime.log').read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        assert len(found)==1;storage=found.pop();outer=read(storage/'result.json');raw=storage/'prettier6604'
        task=read(raw/'result.json')
        state.update(storage=str(storage),actual_actions=task.get('actual_action_count'),task_status=task['status'])
        observation=read(raw/'host-full-rootfs.json')
        assert task['status']==outer['status']=='PASS' and task['actual_action_count']==(189 if schedule=='grpo' else 84)
        validate_observation(observation,residency);assert observation['schedule']==schedule
        assert bool(observation.get('cold_read_requested'))==cold_read
        monitor=read(raw/'runtime-monitor.json')
        assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==(7 if schedule=='grpo' else 9)
        assert all(s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1 for s in monitor['sessions'])
        assert all(p['official_passed']==0 and p['official_failed']==2 for p in task['paths'].values())
        assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed',
            'successful_fixture_removed','original_source_firecracker_identities_unchanged','original_service_unchanged'))
        assert outer['checks']['actual_writable_files_inside_volume']=='PASS'
        assert len(observation['P1']['objects'])==(19 if mode=='shared' else 133)
        assert observation['P0']['end_monotonic_ns']<task['rollout_start_monotonic_ns']<=observation['paths_end_monotonic_ns']<=observation['P1']['start_monotonic_ns']<task['rollout_end_monotonic_ns']
        assert outer['host_shared_preparation_sync']['end_monotonic_ns']<outer['host_file_creation']['verified_before_guest_creation_ns']
        if schedule=='bpo':
            assert task['checks']['restored_files_progress_and_process']=='PASS'
            assert read(storage/'report/runtime-metrics.json')['schedule_audit']['checkpoint_count']==2
            assert sorted(len(r['users']) for r in observation['generated_layers']['P1']['objects'])==[4,7]
            assert len(observation['roots']['objects'])==(21 if mode=='shared' else 135)
        state.update(status='PASS',storage=str(storage),actual_actions=task['actual_action_count'],observation=observation)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if collector:
            try:state['collector']=collector.stop()
            except Exception as error:state.update(status='FAIL',collector_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call('release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                state['seed_released']=read(cache/'resources.json')['released']
                if not state['seed_released']:state['status']='FAIL'
            except Exception as error:state.update(status='FAIL',seed_release_error=repr(error))
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def pair(config,daemon,schedule='grpo',*,cold_read=False,order='shared-first'):
    if order not in ('shared-first','private-first'):raise ValueError('invalid shared/private pair order')
    from host_full_rootfs_control import validate_cold_read
    validate_cold_read('shared',schedule,'on',cold_read)
    # Budget rejection precedes source startup or any other service changes.
    require_budget(available(ROOT),schedule);daemon=daemon_file(daemon)
    from run_light_monitor_controls import SOURCE,SOURCE_ID
    out=ROOT/'.artifacts/host-full-rootfs-pairs'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True);state=dict(status='RUNNING',schedule=schedule,groups=[],planned_actions=378 if schedule=='grpo' else 168,order=order);started=False
    observations={}
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running']
        env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE)
        started=True;subprocess.run(['docker','start',SOURCE],capture_output=True,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+60
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source health timeout')
            time.sleep(1)
        for mode in (('shared','private') if order=='shared-first' else ('private','shared')):
            require_budget(available(ROOT),schedule)
            command=['bash',str(ROOT/'scripts/run-host-file-monitor.sh'),str(Path(config).resolve()),
                '--full-rootfs-group',mode,'--copy-daemon',str(daemon),*(['--copy-schedule',schedule] if schedule!='grpo' else []),
                *(['--rootfs-cold-read'] if cold_read else [])]
            with (out/(mode+'.log')).open('x') as log:
                result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1700)
            found={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',(out/(mode+'.log')).read_text()) if Path(p).parent==ROOT/'.artifacts/host-full-rootfs'}
            assert result.returncode==0 and len(found)==1, 'group failed; preserve and stop'
            path=found.pop();saved=read(path/'result.json');assert saved['status']=='PASS'
            observations[mode]=saved['observation']
            state['groups'].append(dict(saved,result_dir=str(path)));write(out/'result.json',state)
        state.update(status='PASS',actual_actions=state['planned_actions'],comparison=full_comparison(observations['shared'],observations['private']))
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                with (out/'source-stop.log').open('x') as log:stopped=subprocess.run(['docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT,timeout=60)
                after=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],text=True,timeout=10))
                state['source_returned_to_stopped']=stopped.returncode==0 and not after['Running']
                if not state['source_returned_to_stopped']:state['status']='FAIL'
            except Exception as error:state.update(status='FAIL',source_stop_error=repr(error))
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('group','pair'));parser.add_argument('config',type=Path)
    parser.add_argument('--schedule',choices=('grpo','bpo'),default='grpo');parser.add_argument('--mode',choices=('shared','private'));parser.add_argument('--copy-daemon',type=Path,required=True)
    parser.add_argument('--rootfs-residency',choices=('on','off'),default='on')
    parser.add_argument('--rootfs-cold-read',action='store_true',help='Full GRPO only: owned initial files cold, serial background residency queries')
    parser.add_argument('--order',choices=('shared-first','private-first'),help='Pair only; default shared-first preserves the original order')
    args=parser.parse_args()
    if (args.action=='group')!=(args.mode is not None):parser.error('group requires mode; pair has no group mode')
    if args.rootfs_residency=='off' and args.action!='group':parser.error('residency off is a group timing control, not a shared/private memory pair')
    if args.order is not None and args.action!='pair':parser.error('order requires pair')
    raise SystemExit(group(args.config,args.mode,args.copy_daemon,args.schedule,residency=args.rootfs_residency,cold_read=args.rootfs_cold_read) if args.action=='group' else pair(args.config,args.copy_daemon,args.schedule,cold_read=args.rootfs_cold_read,order=args.order or 'shared-first'))
