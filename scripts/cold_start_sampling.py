"""Seven cold requests; only immediate sampler deployment differs, no task setup."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from host_private_files import read,write
from run_prettier6604_grpo import joined,parallel_guest_cleanup
from sandbox_runtime_monitor import RuntimeMonitor

ROOT=Path(__file__).resolve().parents[1]
ROLES=('backbone','early_1','early_2','early_3','late_1','late_2','late_3')


def options(args):
    mode=getattr(args,'startup_sampling',None)
    if mode is None:return None
    expected=(getattr(args,'light_monitor',False),getattr(args,'prettier6604_monitor_mode','continuous'))
    if mode not in ('on','off') or expected!=((True,'continuous') if mode=='on' else (False,'off')):
        raise ValueError('startup sampling mode must match the ordinary guest-only on/off profile')
    if not (getattr(args,'prettier6604_task',None) and getattr(args,'prettier6604_export',None)
            and getattr(args,'immutable_cache_seed',None) and getattr(args,'prettier6604_schedule','grpo')=='grpo'
            and getattr(args,'prettier6604_grpo_capacity_gib',32)==16):
        raise ValueError('startup sampling requires the ordinary seeded 16 GiB GRPO environment')
    if any(getattr(args,k,None) for k in ('prettier6604_role','prettier6604_checkpoint','prettier6604_readonly_replay',
        'host_file_monitor','host_full_rootfs_mode','host_shared_files','host_prefix_count','host_scaling_mode',
        'host_restore_short','host_matched_task','host_independent_phase','snapshot_cache_pair',
        'prettier6604_snapshot_cache_pair','diagnostic_guest_kernel','damon_disable','damon_observe','online_task_config',
        'prettier6604_shared_cache','prettier6604_live_cache','prettier6604_physical_cache_after')):
        raise ValueError('startup sampling cannot combine with another experiment')
    return mode


async def create_batch(create,monitor,live):
    async def one(role):
        sid=await asyncio.to_thread(create,role)
        if not re.fullmatch(r'[a-f0-9-]{36}',sid) or sid in live.values():raise ValueError('invalid or duplicate cold guest')
        live[role]=sid
        if monitor is not None:await asyncio.to_thread(monitor,sid,role,'startup-only')
        return sid
    await joined(one(role) for role in ROLES)
    return dict(live)


def run(args):
    metrics_requested=getattr(args,'server_metrics',False)
    out=args.output.resolve()
    if not (out.parent.name=='controller' and out.parent.parent.name=='mount' and out.parents[3]==ROOT/'.artifacts/storage-isolated-service'):
        raise ValueError('owned isolated output required')
    out.mkdir(exist_ok=False);(out/'code').mkdir()
    for name in ('cold_start_sampling.py','agentenv_cold_start.py','sandbox_runtime_monitor.py','guest_memory_collector.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    if metrics_requested:shutil.copy2(ROOT/'scripts/cold_start_metrics.py',out/'code/cold_start_metrics.py')
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    lock=threading.Lock();events=[];counter=0;live={};runtime=None;success=False
    state=dict(status='FAIL',mode=args.mode,actual_action_count=0,official_evaluation='NOT_RUN_STARTUP_ONLY',checks={},
        scope='Seven fresh 2 CPU/4096 MiB guests; immediate .1 s guest meminfo vs off; no repository checks, workload upload, task actions, snapshots, host/PSS queries or cache control')
    def call(label,argv,timeout=60):
        nonlocal counter
        begin=time.monotonic_ns();wall=time.time_ns()
        with lock:index=counter;counter+=1
        label=f'{index:04}-{label}';code=None
        try:
            with (out/(label+'.stderr')).open('x') as log:
                result=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
            (out/(label+'.stdout')).write_text(result.stdout);code=result.returncode
            if code:raise RuntimeError(label+': nonzero command exit')
            return result.stdout.strip()
        finally:
            with lock:
                events.append(dict(stage=label,start_monotonic_ns=begin,timestamp_ns=wall,duration_ns=time.monotonic_ns()-begin,exit_code=code))
                write(out/'events.json',sorted(events,key=lambda r:r['stage']))
    def cli(label,*argv,timeout=60):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    image=read(ROOT/'configs/prettier6604-backbone.json')['source_image']
    def create(role):return call('cold-'+role,[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
        '--config-home',args.config_home,'--service-url',args.service_url,'--image',image],610)
    def cancel(signum,frame):raise RuntimeError('owned startup experiment interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    started=time.monotonic_ns()
    try:
        assert json.loads(cli('list-before','list','--output','json'))==[]
        if args.mode=='on':runtime=RuntimeMonitor(out,cli,light=True)
        if metrics_requested:
            from cold_start_metrics import capture,evaluate
            before=capture(args.service_url,args.container,out,'before')
            state['server_metrics']=dict(status='FAIL',snapshots=[before])
            if before['status']!='PASS':raise RuntimeError('pre-create service metrics unavailable')
        state['creation_begin_monotonic_ns']=time.monotonic_ns()
        asyncio.run(create_batch(create,runtime.start if runtime else None,live))
        state['all_creation_and_sampler_setup_returned_ns']=time.monotonic_ns()
        if metrics_requested:
            after=capture(args.service_url,args.container,out,'after')
            try:state['server_metrics']=evaluate(out,before,after)
            except Exception as error:state['server_metrics']=dict(status='FAIL',snapshots=[before,after],error=repr(error))
        cold=[e for e in events if '-cold-' in e['stage']]
        assert len(cold)==7 and len(live)==len(set(live.values()))==7
        first=min(e['start_monotonic_ns'] for e in cold);end=max(e['start_monotonic_ns']+e['duration_ns'] for e in cold)
        state.update(cold_span_ns=end-first,cold_dispatch_span_ns=max(e['start_monotonic_ns'] for e in cold)-first,
            cold_begin_monotonic_ns=first,cold_end_monotonic_ns=end)
        # Common verification is deliberately outside all cold request intervals.
        state['postcheck_begin_monotonic_ns']=time.monotonic_ns()
        assert state['postcheck_begin_monotonic_ns']>=end
        assert len(json.loads(cli('list-created','list','--output','json')))==7
        from host_sandbox_monitor import Binding
        binding=Binding(args.container,out);vms={role:binding.resolve(sid) for role,sid in live.items()}
        boots={role:cli('postcheck-boot-'+role,'exec',sid,'cat','/proc/sys/kernel/random/boot_id') for role,sid in live.items()}
        assert len(set(boots.values()))==7 and all(re.fullmatch(r'[a-f0-9-]{36}',b) for b in boots.values())
        assert len({(vm['pid'],vm['start_ticks']) for vm in vms.values()})==7
        state.update(guests=live,bindings=vms,boot_ids=boots,checks=dict(seven_distinct_live_guests='PASS',postchecks_after_all_creates='PASS',no_task_actions='PASS'))
        state['functional_status']='PASS'
        success=not metrics_requested or state['server_metrics']['status']=='PASS'
    except BaseException as error:
        import traceback
        state['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        rows=parallel_guest_cleanup(list(live.values()),runtime.stop if runtime else None,lambda sid:cli('delete-owned','delete',sid,timeout=60))
        state['cleanup']=dict(guests=rows)
        if any(not row['deleted'] or row['errors'] or row['stopped'] is not (True if runtime else None) for row in rows):success=False
        try:
            assert json.loads(cli('list-after','list','--output','json'))==[]
            state['cleanup']['service_sandbox_list_empty']=True
            if runtime:
                manifest=read(out/'runtime-monitor.json')
                assert manifest['status']=='PASS' and len(manifest['sessions'])==7
                assert all(s['collector_reaped'] and s['receipt']['samples']>=1 for s in manifest['sessions'])
                state['cleanup']['collectors_reaped']=True
            else:state['cleanup']['monitoring_disabled']=True
        except Exception as error:state['cleanup']['error']=repr(error);success=False
        state.update(status='PASS' if success else 'FAIL',controller_elapsed_ns=time.monotonic_ns()-started)
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('container','service-url'):parser.add_argument('--'+name,required=True)
    for name in ('config-home','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--mode',choices=('on','off'),required=True)
    parser.add_argument('--server-metrics',action='store_true',help='Two service metric snapshots outside cold request intervals')
    raise SystemExit(run(parser.parse_args()))
