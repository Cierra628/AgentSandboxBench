"""Fixed density runs on fresh cgroups/services; pinned local Prettier/C4 input."""
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

from host_private_files import read,write
from host_density import ROOT,COUNTS,request
from isolated_storage import GIB,capacity_budget
from prettier_no_proxy import clean_environment
from run_light_monitor_controls import SOURCE,SOURCE_ID
SEED_PREFIX='host-persistent-density-'


def planned_runs(c4_timing_pair=False,pressure_comparison=False,pressure_extension=False):
    if sum((c4_timing_pair,pressure_comparison,pressure_extension))>1:raise ValueError('select only one comparison')
    if pressure_extension:
        from host_pressure import extension_plan
        return extension_plan()
    if pressure_comparison:
        from host_pressure import plan
        return plan()
    if c4_timing_pair:return [dict(task='c4',count=7,mode=mode) for mode in ('shared','independent')]
    return [dict(task='prettier6604',count=n,mode=mode) for i,n in enumerate(COUNTS)
        for mode in (('shared','independent') if i%2==0 else ('independent','shared'))]+[
        dict(task='c4',count=7,mode=mode) for mode in ('shared','independent')]


def inside(args):
    from host_file_monitor import measurement_group,HostFileMonitor
    from host_persistent_input import run_phase
    from platform_tasks import configuration,commands
    out=args.member.resolve();seed=args.seed.resolve() if args.seed else None;value=request(args.request);c4=value['task']=='c4'
    group,_=measurement_group();collector=None;state=dict(status='FAIL',request=value)
    def save():write(out/'result.json',state)
    try:
        extra={}
        if 'pressure_context' in value:
            from host_pressure import EXTRA_STATS
            extra['extra_stat_keys']=EXTRA_STATS
        collector=HostFileMonitor(out/'host-file',group,include_pss=False,**extra)
        if not c4:run_phase('import',seed,360)
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-current.json')
        _,command=commands(cfg,seed) if not c4 else (None,[])
        command=[str(v) for v in command]
        if not c4:command[command.index('--immutable-cache-seed')]='--persistent-cache-seed'
        if c4:
            command=[sys.executable,'-B',str(ROOT/'scripts/validate_isolated_agentenv.py'),'--automatic-window',
                '--external-workload-config',value['config'],'--external-workload-upstream',value['upstream'],
                '--light-monitor','--host-file-monitor','--host-current-only']
        command+=['--density-request',str(args.request.resolve())]
        write(out/'command.json',command)
        with (out/'native.log').open('x') as log:
            result=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1800 if 'pressure_context' in value else 1500 if c4 else 1080)
        matches={Path(v) for v in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)',(out/'native.log').read_text()) if Path(v).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(matches)==1:state['storage']=str(matches.pop())
        if result.returncode:raise RuntimeError('density native workflow failed; preserve this attempt')
        storage=Path(state['storage']);native=read(storage/'result.json');task=read(storage/'density/result.json')
        assert native['status']==task['status']=='PASS' and task['actions']==(36 if c4 else 29)*value['count']
        assert task['P0']['resident_bytes']==0 and task['identity']['status']=='PASS'
        assert len(task['P1']['objects'])==(9 if c4 else 19)*(value['count'] if value['mode']=='independent' else 1)
        assert all(native['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged'))
        assert all(native['checks'][k]=='PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation'))
        for child in task['results']:
            monitor=read(storage/'density'/child['raw']/'runtime-monitor.json')
            assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==1
            s=monitor['sessions'][0];assert s['status']=='PASS' and s['collector_reaped'] and s['receipt']['interval_seconds']==.1
        if not c4:run_phase('release-input',seed,120)
        state.update(status='PASS',actions=task['actions'],cgroup=str(group),cgroup_id=group.stat().st_ino)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if collector:
            try:state['collector']=collector.stop()
            except Exception as error:state.update(status='FAIL',collector_error=repr(error))
        save()
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def main(args):
    if args.inside_group:return inside(args)
    if os.geteuid()!=0:raise RuntimeError('density lifecycle requires root')
    out=args.output.resolve()
    if not out.is_relative_to(ROOT/'.artifacts') or out.exists()!=args.resume:raise ValueError('new output required, or explicit resume of this batch')
    if not args.resume:out.mkdir(parents=True)
    elif (out/'failure.txt').exists():
        shutil.copy2(out/'failure.txt',out/('failure-before-resume-'+str(time.monotonic_ns())+'.txt'))
    env=clean_environment(os.environ);env.update(ASB_AGENTENV_SOURCE_SERVICE=SOURCE,ASB_LOCAL_NO_PROXY='on',NO_PROXY='*',no_proxy='*')
    timing_pair=getattr(args,'c4_timing_pair',False)
    pressure_comparison=getattr(args,'pressure_comparison',False)
    pressure_extension=getattr(args,'pressure_extension',False)
    pressure_mode=pressure_comparison or pressure_extension
    plan=planned_runs(timing_pair,pressure_comparison,pressure_extension)
    if pressure_mode and not args.pressure_binary:raise ValueError('pressure comparison requires its owned allocator binary')
    if pressure_extension:
        from host_pressure import host_snapshot
        if host_snapshot()['SwapTotal']:raise ValueError('pressure extension requires swap disabled; do not change host swap configuration')
    if timing_pair and not args.c4_input:raise ValueError('C4 timing pair requires fixed local input and upstream')
    state=read(out/'result.json') if args.resume else dict(status='RUNNING',plan=plan,runs=[],attempts=[],C4=dict(status='BLOCKED',reason='No complete fixed local image; direct registry TLS timed out, no proxy fallback'),
        concurrency=dict(api='No numeric creation/admission cap in fixed standalone route and OrchestratorConfig',batch_policy='Submit all N asynchronously in one batch; requested maximum 10',
            old_copy_profile='Seven slots is an experiment-only old copy limit, not a service cap',new_copy_profile='Explicit density count 1/2/4/7/10; existing profiles retain seven'))
    assert state['plan']==plan
    if timing_pair:state.update(experiment='c4-call-timing-pair-v2',C4=dict(status='READY_LOCAL_INPUT',local_input=str(args.c4_input.resolve())))
    if pressure_comparison:state.update(experiment='prettier-density-pressure-v1',control_policy='Fresh A/B; host cache and load changed since previous density batch')
    if pressure_extension:state.update(experiment='prettier-density-pressure-extension-v1',control_policy='Reuse separately reported original N=10 C/D and A/B; new N=4/7 curve plus two N=10 pairs; original allocator protocol',C4=dict(status='NOT_REQUESTED',reason='Prettier-only pressure extension'))
    state['status']='RUNNING';state.pop('error',None);state.pop('pause',None);state['source_restored_stopped']=False
    started=False
    def save():write(out/'result.json',state)
    def command(argv,timeout=30):return subprocess.check_output(argv,env=env,text=True,stderr=subprocess.PIPE,timeout=timeout)
    try:
        source=json.loads(command(['docker','inspect',SOURCE]))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running']
        assert not command(['docker','ps','-q']).strip()
        from probe_storage_boundary import identities
        assert not identities()
        started=True;command(['docker','start',SOURCE]);deadline=time.monotonic()+90
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=3) as response:
                    if response.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source service health')
            time.sleep(1)
        for index,item in enumerate(state['plan'] if args.c4_input else state['plan'][:10],1):
            keys=('task','count','mode','condition') if pressure_mode else ('task','count','mode')
            if any(all(r[k]==item[k] for k in keys) for r in state['runs']):continue
            budget=capacity_budget(shutil.disk_usage(ROOT).free,80*GIB)
            if not budget['ready']:raise RuntimeError('density 80 GiB volume requires >106 GiB available')
            mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
            assert mem>64*GIB and not identities()
            name=f"{index:02}-{item['task']}-N{item['count']}-{item['mode']}";member=out/name
            attempt=1
            while member.exists():
                attempt+=1;member=out/(name+'-attempt-'+str(attempt))
            member.mkdir()
            req=dict(profile='host-density-v1',**item,daemon=str(args.daemon.resolve()))
            if pressure_mode:req.update(pressure_context=str(member/'pressure'),pressure_enabled=item['pressure'])
            if pressure_extension:req['pressure_extension']=True
            if item['task']=='c4':
                req.update(oci_input=str(args.c4_input.resolve()),upstream=str(args.upstream.resolve()),config=str(ROOT/'configs/external-c4-light.json'))
            write(member/'request.json',req);request(member/'request.json')
            seed=ROOT/'.artifacts/prettier6604'/(SEED_PREFIX+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
            if item['task']!='c4':seed.mkdir()
            from host_persistent_input import run_phase
            state.update(stage='prepare-'+name);save();print('status=RUNNING '+name,flush=True)
            from prettier_no_proxy import prepare_metadata
            if item['task']!='c4':
                prepare_metadata(seed/'local-no-proxy-metadata')
                run_phase('prepare',seed,420)
            unit='asbhost-density'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid())
            slice_name=unit+'.slice'
            argv=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,'--slice='+slice_name,
                '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120','--working-directory='+str(ROOT),
                '--setenv=ASB_HOST_FILE_SLICE='+slice_name,'--setenv=ASB_HOST_FILE_SAMPLING=on','--setenv=ASB_AGENTENV_SOURCE_SERVICE='+SOURCE,
                '--setenv=ASB_LOCAL_NO_PROXY='+('off' if item['task']=='c4' else 'on'),'--setenv=NO_PROXY=*','--setenv=no_proxy=*',
                sys.executable,'-B',str(Path(__file__).resolve()),'--inside-group','--member',str(member),'--seed',str(seed),'--request',str(member/'request.json')]
            from prettier_no_proxy import PROXY_KEYS
            argv[argv.index(sys.executable):argv.index(sys.executable)]=['--setenv='+key+'=' for key in PROXY_KEYS]
            state.update(stage='run-'+name);save()
            try:
                if pressure_mode:
                    from host_pressure import run_group
                    p=subprocess.CompletedProcess(argv,run_group(argv,env,member,item['pressure'],args.pressure_binary.resolve()))
                else:
                    with (member/'group.log').open('x') as log:p=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1500)
            finally:subprocess.run(['systemctl','stop',unit+'.service',slice_name],capture_output=True,timeout=150)
            saved=read(member/'result.json') if (member/'result.json').exists() else dict(status='FAIL',error='group returned no result')
            state['attempts'].append(dict(item,result_dir=str(member),result=saved));save()
            if p.returncode or saved['status']!='PASS':raise RuntimeError('density member failed: '+name+'; diagnose and rerun only this member')
            state['runs'].append(dict(item,result_dir=str(member),storage=saved['storage']));save()
            print('status=PASS '+name,flush=True)
        if pressure_extension:state.update(status='PASS',stage='eight-pressure-extension-runs-complete')
        elif pressure_comparison:state.update(status='PASS',stage='four-pressure-conditions-complete')
        elif args.c4_input:state.update(status='PASS',stage='c4-timing-pair-complete' if timing_pair else 'all-twelve-complete',C4=dict(status='PASS',local_input=str(args.c4_input.resolve())))
        else:state.update(status='PRETTIER_COMPLETE_C4_BLOCKED',stage='waiting-for-C4-local-image')
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:command(['docker','stop','-t','10',SOURCE],40);state['source_restored_stopped']=not json.loads(command(['docker','inspect',SOURCE]))[0]['State']['Running']
            except BaseException as error:state.update(status='FAIL',source_cleanup_error=repr(error))
        state['disk_available_after_bytes']=shutil.disk_usage(ROOT).free;save()
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 2 if state['status']=='PRETTIER_COMPLETE_C4_BLOCKED' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path);p.add_argument('--daemon',type=Path)
    p.add_argument('--c4-input',type=Path);p.add_argument('--upstream',type=Path)
    p.add_argument('--c4-timing-pair',action='store_true',help='Only C4 N=7 shared/independent in a new batch; keep the ordinary twelve-run plan unchanged')
    p.add_argument('--pressure-comparison',action='store_true',help='Fresh Prettier N=10 A/B controls and C/D external-anon-pressure groups')
    p.add_argument('--pressure-extension',action='store_true',help='Only the agreed eight pressure runs: N=4/7 curve, then two more N=10 pairs')
    p.add_argument('--pressure-binary',type=Path)
    p.add_argument('--resume',action='store_true',help='Keep successful members; retry only the failed/missing member and continue')
    p.add_argument('--inside-group',action='store_true')
    for name in ('member','seed','request'):p.add_argument('--'+name,type=Path)
    args=p.parse_args()
    if bool(args.c4_input)!=bool(args.upstream):p.error('C4 local input and pinned upstream required together')
    if not args.inside_group and not all((args.output,args.daemon)):p.error('output and experimental daemon required')
    raise SystemExit(main(args))
