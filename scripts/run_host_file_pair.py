"""Bounded host checks: a cold pair, or one checkpoint with N=1/N=2 restores."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
GUEST_CHECK="""import json,sys
from pathlib import Path
p=Path(sys.argv[3])
if sys.argv[1]=='write':
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(sys.argv[2])
assert p.read_text()==sys.argv[2]
m={line.split(':',1)[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if len(line.split())==3 and line.split()[2]=='kB'}
print(json.dumps(dict(marker=p.read_text(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),MemTotal=m['MemTotal'],MemAvailable=m['MemAvailable'],Cached=m['Cached'],Shmem=m['Shmem'])))
"""


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def paired_pss(rows, bindings, start, end):
    expected={(item['pid'],item['start_ticks']) for item in bindings}
    if len(expected)!=2:raise ValueError('two distinct VMM identities required')
    found=[]
    for row in rows:
        if not start<=row['start_monotonic_ns']<=row['end_monotonic_ns']<=end:continue
        if row['status']!='OBSERVED':continue
        vm=[item for item in row['processes'] if item['comm']=='firecracker']
        if len(vm)!=2 or {(item['pid'],item['start_ticks']) for item in vm}!=expected:
            raise ValueError('periodic PSS did not cover exactly the two bound VMMs')
        if any(item['pss']<=0 for item in vm):raise ValueError('VMM PSS missing')
        if found and row['start_monotonic_ns']<=found[-1]['start_monotonic_ns']:
            raise ValueError('duplicate or unordered paired PSS observations')
        found.append(dict(start_monotonic_ns=row['start_monotonic_ns'],
                          end_monotonic_ns=row['end_monotonic_ns'],vmm_pss_bytes=sum(item['pss'] for item in vm),
                          vmm_pss=[dict(pid=item['pid'],start_ticks=item['start_ticks'],pss_bytes=item['pss']) for item in vm]))
    if len(found)<2:raise ValueError('fewer than two complete paired PSS samples')
    return found


def guests(args):
    from agentenv_cold_start import create
    from host_sandbox_monitor import Binding
    from host_file_monitor import measurement_group, pids, pss_snapshot
    group,_=measurement_group()
    out=args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    out.mkdir(exist_ok=False)
    shutil.copy2(Path(__file__),out/'executed-runner.py')
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    events=[];live=[];success=False;result=dict(status='FAIL',scope='two fresh 2 CPU/4096 MiB cold sandboxes; synthetic markers and meminfo only; no Prettier task, snapshot, cache fill, reclaim or guest kernel change',checks={},cleanup={})
    def cli(label,*argv):
        begin=time.monotonic_ns()
        with (out/(label+'.stderr')).open('x') as log:
            value=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=log,timeout=30)
        (out/(label+'.stdout')).write_text(value.stdout)
        events.append(dict(stage=label,start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),exit_code=value.returncode))
        write(out/'events.json',events)
        if value.returncode:raise RuntimeError(label+': command failed')
        return value.stdout.strip()
    # No /testbed accesses: one tiny file in each guest verifies isolation and continued usability.
    # API readiness can precede the guest's volatile-directory initialization.
    # Keep the probe in its own non-temporary directory, not /tmp.
    marker='/root/.asb-host-file-pair/marker'
    def cancel(signum,frame):raise RuntimeError('owned short pair interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    try:
        binding=Binding(args.container,out)
        image=read(ROOT/'configs/prettier6604-backbone.json')['source_image']
        result['guests']=[]
        for role in ('a','b'):
            began=time.monotonic_ns()
            sid=create(args.config_home,args.service_url,image,request_timeout=90,memory_mib=4096)
            live.append(sid)
            vm=binding.resolve(sid)
            assert binding.owned(vm['pid'])
            result['guests'].append(dict(role=role,sandbox_id=sid,binding=vm,created_start_ns=began,created_end_ns=time.monotonic_ns()))
            write(out/'running.json',result)
        assert len({g['sandbox_id'] for g in result['guests']})==2
        result['both_live_start_ns']=time.monotonic_ns()
        for g in result['guests']:
            value=json.loads(cli('write-'+g['role'],'exec',g['sandbox_id'],'python3','-c',GUEST_CHECK,'write','host-pair-'+g['role'],marker))
            g['before']=value
        assert len({g['before']['boot_id'] for g in result['guests']})==2
        direct=pss_snapshot(group)
        write(out/'both-live-pss.json',direct)
        identities=[g['binding'] for g in result['guests']]
        direct_vms=[item for item in direct['processes'] if item['comm']=='firecracker']
        assert direct['status']=='OBSERVED' and {(item['pid'],item['start_ticks']) for item in direct_vms}=={(item['pid'],item['start_ticks']) for item in identities}
        assert all(item['pss']>0 for item in direct_vms)
        # Five seconds of ordinary idle time give periodic PSS at least two observations of both VMMs.
        # No requests are held or delayed, and no memory pages are pinned.
        time.sleep(5)
        for g in result['guests']:
            value=json.loads(cli('read-'+g['role'],'exec',g['sandbox_id'],'python3','-c',GUEST_CHECK,'read','host-pair-'+g['role'],marker))
            assert value['boot_id']==g['before']['boot_id'] and value['marker']==g['before']['marker']
            g['after']=value
            assert binding.resolve(g['sandbox_id'])==g['binding']
        result['both_live_end_ns']=time.monotonic_ns()
        assert len([pid for pid in pids(group) if Path('/proc',str(pid),'comm').read_text().strip()=='firecracker'])==2
        result['checks']=dict(two_fresh_distinct_guests='PASS',markers_isolated_and_retained='PASS',two_vmm_bindings='PASS',direct_pss_two_vm_coverage='PASS')
        success=True
    except BaseException as error:
        import traceback
        result['error']=repr(error);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        for index,sid in enumerate(reversed(live)):
            try:cli('delete-'+str(index),'delete',sid);result['cleanup'][sid]='REMOVED'
            except Exception as error:result['cleanup'][sid]=repr(error);success=False
        try:
            assert json.loads(cli('list-after','list','--output','json'))==[]
            result['checks']['service_sandbox_list_empty']='PASS'
        except Exception as error:result['list_after_error']=repr(error);success=False
        result['status']='PASS' if success else 'FAIL';write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


def workflow(config, *, restore=False, matched=False, trace_dual=False):
    matched=matched or trace_dual
    restore=restore or matched
    sampling=os.environ.get('ASB_HOST_FILE_SAMPLING','on')
    if sampling not in ('on','off') or sampling=='off' and (not matched or trace_dual):
        raise ValueError('host sampling control requires matched-task without tracing')
    from platform_tasks import configuration,preflight,commands
    from host_file_monitor import measurement_group,HostFileMonitor
    from report_host_file import report
    cfg=configuration(config)
    if not (cfg.get('host_file_monitor') and cfg['task_id']=='prettier__prettier-6604' and cfg['rollout']=='single' and cfg['checkpoint_after_step'] is None):
        raise ValueError('short pair uses the explicit ordinary Prettier-image host config only')
    if cfg.get('damon_observe') or cfg.get('damon_disable') or cfg.get('monitor_mode')=='off':
        raise ValueError('short host pair does not change DAMON or run a guest sampling control')
    group,_=measurement_group();ready=preflight(cfg)
    os.umask(0o077)
    out=ROOT/'.artifacts'/('host-file-traced-dual-task' if trace_dual else 'host-file-matched-task' if matched else 'host-file-restore' if restore else 'host-file-pair-smoke')/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True,exist_ok=False)
    write(out/'effective-config.json',cfg);write(out/'preflight.json',ready)
    code=out/'code';code.mkdir()
    for name in ('run_host_file_pair.py','host_file_monitor.py','report_host_file.py','run_host_file_workflow.py','validate_isolated_agentenv.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    if restore:shutil.copy2(ROOT/'scripts/run_host_file_restore.py',code/'run_host_file_restore.py')
    if matched:shutil.copy2(ROOT/'scripts/host_file_matched_task.py',code/'host_file_matched_task.py')
    cache=ROOT/'.artifacts/prettier6604'/('host-pair-cache-'+out.name)
    result=dict(status='FAIL',workflow='short-host-file-pair',full_task_executions=0,scope='two cold 4 GiB guests; host instrumentation validation only; no A/N savings calculation')
    if sampling=='off':result.update(host_sampling='off',host_measurements='NOT_REQUESTED',collector=dict(status='NOT_REQUESTED'))
    def call(label,argv,timeout):
        with (out/(label+'.log')).open('x') as log:
            subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    if restore:result.update(workflow='short-host-file-restore',scope='one step-11 snapshot; N=1 then N=2 idle observations; no suffix or savings ratio')
    if matched:result.update(workflow='host-file-matched-task',scope='one step-11 snapshot; identical early_1 suffix N=1/N=2; 41 actual actions')
    if trace_dual:result.update(workflow='host-file-traced-dual-task',scope='one step-11 snapshot; two identical early_1 suffixes only; 31 actual actions; targeted metadata trace')
    monitor=None;stage='host-sampling'
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        if sampling=='on':monitor=HostFileMonitor(out/'host-file',group)
        stage='cache-prepare';call(stage,[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache],300)
        stage='isolated-runtime';_,live=commands(cfg,cache)
        if restore:
            from run_host_file_restore import restore_command
            live=restore_command(live,matched=matched)
            if trace_dual:live+=['--host-trace-dual']
        else:live+=["--host-file-pair-smoke"]
        call(stage,live,900)
        paths=re.findall(r'status=PASS result_dir=(\S+)',(out/'isolated-runtime.log').read_text())
        children={Path(path) for path in paths if Path(path).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(children)!=1:raise ValueError('expected one owned isolated result')
        child=children.pop();outer=read(child/'result.json');pair=read(child/('prettier6604' if restore else 'host-file-pair')/'result.json')
        assert outer['status']==pair['status']=='PASS' and all(outer['cleanup'].values())
        result.update(status='PASS',storage_result_dir=str(child),pair=pair,creation=outer['host_file_creation'])
    except BaseException as error:
        import traceback
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if monitor:
            try:result['collector']=monitor.stop()
            except Exception as error:result.update(status='FAIL',host_stop_error=repr(error))
        if (cache/'resources.json').exists():
            try:
                call('cache-release',[sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],90)
                result['cache_released']=read(cache/'resources.json')['released']
            except Exception as error:result.update(status='FAIL',cache_release_error=repr(error))
    if result['status']=='PASS' and sampling=='off':
        try:
            stage='offline-timing-report'
            from host_file_matched_task import report_matched_off
            result['timing_summary']=report_matched_off(Path(result['storage_result_dir'])/'prettier6604',out/'timing-report')
        except BaseException as error:
            import traceback
            result.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
    elif result['status']=='PASS':
        try:
            stage='offline-report';child=Path(result['storage_result_dir'])
            summary=report(out/'host-file',child/'host-file-residency.json',out/'report')
            proof=read(child/'result.json')['host_file_creation']
            assert proof['before_guest_counters'] and summary['creation_baseline']['synchronous_counters']==proof['before_guest_counters']
            inventory=read(child/'host-file-residency.json')
            required=[child/'volume.ext4',cache/'memory/volume.ext4']
            assert all(str(path) in {item['path'] for item in inventory['files']} for path in required)
            assert inventory['status']=='OBSERVED' and not inventory['errors']
            rows=[json.loads(line) for line in (out/'host-file/pss.jsonl').read_text().splitlines()]
            if matched:
                from host_file_matched_task import report_matched
                result['matched_summary']=report_matched(out/'host-file',child/'prettier6604',out/'matched-report')
                coverage=[]
            elif restore:
                from run_host_file_restore import report_restore
                result['restore_summary']=report_restore(out/'host-file',child/'prettier6604',out/'restore-report')
                coverage=[]
            else:coverage=paired_pss(rows,[g['binding'] for g in pair['guests']],pair['both_live_start_ns'],pair['both_live_end_ns'])
            result.update(host_summary=summary,periodic_pair_coverage=coverage,report=str(out/'report'),
                backing_loop_images=[item for item in inventory['files'] if item['path'] in set(map(str,required))])
        except BaseException as error:
            import traceback
            result.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
    write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    run=sub.add_parser('run');run.add_argument('config',type=Path);run_modes=run.add_mutually_exclusive_group();run_modes.add_argument('--restore-pair',action='store_true');run_modes.add_argument('--matched-task',action='store_true')
    run.add_argument('--trace-dual-task',action='store_true')
    guest=sub.add_parser('guests')
    for name in ('config-home','output'):guest.add_argument('--'+name,type=Path,required=True)
    for name in ('service-url','container'):guest.add_argument('--'+name,required=True)
    args=parser.parse_args()
    raise SystemExit(workflow(args.config,restore=args.restore_pair,matched=args.matched_task,trace_dual=args.trace_dual_task) if args.mode=='run' else guests(args))
