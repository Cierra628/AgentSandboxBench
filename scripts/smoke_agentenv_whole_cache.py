"""One isolated AgentENV VM: whole-cache boundaries, replay and post-scan checks."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import threading
import time
import traceback

from smoke_vm_pfn import ROOT,SERVER,ENV,CLI
from experiment_disk_guard import writer_lock
from agentenv_whole_cache import bind_vm,layer_inventory,ram_snapshot,recount
from trenvx_whole_cache import dump,scan_backing


def post_checks_after_analysis_error(checks,pause,verify):
    if checks.get('frozen_exp1')=='PASS' and checks.get('official_before')=='PASS':
        if pause and pause.paused:pause.resume();pause.save(False)
        verify()


def main(exp1,analysis=True,paused_boundary=False,file_inventory=None,kernel_file_types=False,calibration_scope='interval',evict_fixture=False,*,task=None,output=None,sampling=True):
    os.umask(0o077)
    out=output if output is not None else ROOT/'.artifacts/agentenv-whole-cache'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    env=dict(ENV,XDG_CONFIG_HOME=str(task['config_home'])) if task else ENV
    out.mkdir(parents=True);cache=out/'whole-cache';cache.mkdir();(out/'code').mkdir()
    sources=['smoke_agentenv_whole_cache.py','agentenv_whole_cache.py','guest_whole_cache_probe.py',
             'guest_cache_marker.py','trenvx_whole_cache.py','exp1_physical_workload.py',
             'persistent_guest_sampling.py','guest_memory_collector.py','exp1_official_task.py']
    if paused_boundary:sources.append('paused_agentenv_boundary.py')
    if file_inventory:sources += ['guest_cached_file_inventory.py','cached_file_inventory.py','guest_named_file_probe.py','guest_file_type_check.py']
    if kernel_file_types:sources.append('kernel_file_cache.py')
    if task:sources+=['task_metrics_prefix.py','xarray6992_persistent_sampling.py','sphinx7590_inputs.py','sphinx7590_check.py','agentenv_cold_start.py','report_file_inventory.py']
    for name in sources:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    sid=None;tracer=None;sample=None;pause=None;stop=threading.Event();checks={};stage='preflight';started=time.monotonic_ns()
    result=dict(status='FAIL',scope='one private cold VM; boundary analysis; not a matched performance comparison')
    def interrupted(*_):raise RuntimeError('owned experiment interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    def call(label,argv,timeout=30):
        before=time.monotonic_ns();p=subprocess.run(argv,env=env,cwd=ROOT,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr)
        event=dict(phase=label,start_monotonic_ns=before,end_monotonic_ns=time.monotonic_ns(),exit_code=p.returncode)
        with (out/'events.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
        p.check_returncode();return p.stdout
    def aenv(label,*args,timeout=30):return call(label,[CLI,*args],timeout)
    def download(name):aenv('download-'+name,'download',sid,guest+'/'+name,str(cache/name))
    def wait(label):
        aenv('wait-'+label,'exec',sid,'bash','-lc',f'for i in $(seq 1 200); do test -f {guest}/{label} && exit 0; test -f {guest}/failure.txt && break; sleep 0.1; done; cat {guest}/failure.txt /dev/shm/asb-cache.log; exit 1',timeout=25)
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        assert os.geteuid()==0 and (task or shutil.disk_usage(out).free>32*2**30)
        cid=task['container'] if task else (SERVER/'container-id.txt').read_text().strip();info=json.loads(call('server',['docker','inspect',cid]))[0]
        assert info['State']['Running'] and info['Config']['Labels']['agentenv.experiment']==('isolated-storage-check' if task else 'initial-smoke')
        if task:
            owned=next(p for p in out.parents if p.parent==ROOT/'.artifacts/storage-isolated-service')
            assert info['Config']['Labels']['agentsandboxbench.run_id']==owned.name
            assert out.is_relative_to(owned/'mount/controller/python-backbone')
        cg=Path(f'/proc/{info["State"]["Pid"]}/cgroup').read_text().strip().split('::')[1]
        assert json.loads(aenv('list-before','list','--output','json'))==[]
        cgpath=Path('/sys/fs/cgroup')/cg.lstrip('/');assert not list(cgpath.glob('*/cgroup.procs'))
        image=json.loads((task['inputs']/'config.json').read_text())['source_image'] if task else json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())['workload_image']
        config=dict(image=image,cpu=2,memory_mib=4096,exp1=exp1,whole_cache_analysis=analysis,host_kernel=os.uname().release,
            server_image=info['Image'],cgroup=cg,cache_policy='preserve; no guest or host drop',
            source_sha256={name:hashlib.sha256((out/'code'/name).read_bytes()).hexdigest() for name in sources})
        if paused_boundary:config['paused_boundary']=True
        if file_inventory:config['file_inventory']=file_inventory
        if kernel_file_types:config['kernel_file_types']=True
        if kernel_file_types:config['calibration_scope']=calibration_scope
        if evict_fixture:config['controlled_fixture_eviction']=True
        if task:config.update(task_id='sphinx-doc__sphinx-7590',trajectory_scope='read_repo steps 1-6',periodic_sampling=sampling,
                              cache_policy='fresh VM; converted immutable RAM layers shared between sequential cases; no cache drop')
        dump(out/'config.json',config)
        before_pids=set(cgpath.joinpath('cgroup.procs').read_text().split())
        stage='capture'
        program='''struct region { unsigned int slot; unsigned int flags; unsigned long guest; unsigned long size; unsigned long host; };
BEGIN { printf("READY\\n"); }
tracepoint:syscalls:sys_enter_ioctl /cgroup == CGID && args->cmd == 0x4020ae46/ {
 $r=(struct region *)uptr(args->arg); @active[tid]=1; @slot[tid]=$r->slot; @guest[tid]=$r->guest; @size[tid]=$r->size; @host[tid]=$r->host;
}
tracepoint:syscalls:sys_exit_ioctl /@active[tid]/ {
 printf("%d\\t%lu\\t%lx\\t%lx\\t%lx\\t%ld\\n",pid,@slot[tid],@guest[tid],@size[tid],@host[tid],args->ret);
 delete(@active[tid]);delete(@slot[tid]);delete(@guest[tid]);delete(@size[tid]);delete(@host[tid]);
}
interval:s:240 { exit(); }
END { clear(@active);clear(@slot);clear(@guest);clear(@size);clear(@host); }
'''.replace('CGID',str(cgpath.stat().st_ino))
        (cache/'capture.bt').write_text(program)
        with (cache/'kvm-slots.tsv').open('w') as stdout,(cache/'capture.stderr').open('w') as stderr:
            tracer=subprocess.Popen(['bpftrace','-q','-B','line',str(cache/'capture.bt')],stdout=stdout,stderr=stderr,env=dict(env,BPFTRACE_PERF_RB_PAGES='8'))
        deadline=time.monotonic()+20
        while 'READY' not in (cache/'kvm-slots.tsv').read_text():
            assert tracer.poll() is None and time.monotonic()<deadline;time.sleep(.1)
        checks['capture_attach']='PASS';stage='cold_start'
        sid=(call('start',[__import__('sys').executable,'-B',ROOT/'scripts/agentenv_cold_start.py','--config-home',task['config_home'],
                         '--service-url',task['service_url'],'--image',image],timeout=180) if task else
             aenv('start','start','--cold',image,'--detach','--cpu','2','--memory','4096','--timeout','600',timeout=180)).decode().strip()
        assert __import__('re').fullmatch('[a-f0-9-]{36}',sid);dump(out/'sandbox.json',dict(id=sid))
        aenv('guest-environment','exec',sid,'bash','-lc','uname -a; getconf PAGESIZE; cat /proc/iomem; cat /proc/meminfo')
        tracer.send_signal(signal.SIGINT);tracer.wait(timeout=10);assert tracer.returncode==0
        assert not __import__('re').search(r'lost\s+[1-9]|ERROR',(cache/'capture.stderr').read_text(),__import__('re').I)
        checks['capture_detach']='PASS'
        new=[];daemons=[]
        for pid in cgpath.joinpath('cgroup.procs').read_text().split():
            name=Path(f'/proc/{pid}/comm').read_text().strip()
            if name=='firecracker' and pid not in before_pids:new.append(int(pid))
            if name=='uvm-ublk-daemon':daemons.append(int(pid))
        assert len(new)==len(daemons)==1
        vm_pid=new[0]
        if kernel_file_types:
            from paused_agentenv_boundary import PausedBoundary
            proc=Path('/proc')/str(vm_pid);api=PausedBoundary.__new__(PausedBoundary)
            api.vm=dict(pid=vm_pid,start_time_ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19]),
                        executable=os.readlink(proc/'exe'),cgroup=cg,sandbox_id=sid)
            argv=(proc/'cmdline').read_bytes().split(b'\0');target=argv[argv.index(b'--api-sock')+1].decode()
            assert __import__('re').fullmatch('/workspace/env/firecracker-work/agentenv-fc-[A-Za-z0-9]+/firecracker.socket',target)
            api.socket=proc/'root'/target.lstrip('/')
            dump(out/'vmm-launch-config.json',api.api('GET','/vm/config'))
        def sample_host():
            try:
                with (out/'host-vmm.tsv').open('w',buffering=1) as f:
                    f.write('timestamp_ns\tpss_bytes\trss_bytes\tservice_cgroup_current_bytes\n')
                    while not stop.is_set():
                        text=Path(f'/proc/{vm_pid}/smaps_rollup').read_text()
                        values={key:int(v)*1024 for key,v in __import__('re').findall(r'^(Pss|Rss):\s+(\d+) kB$',text,__import__('re').M)}
                        f.write(f'{time.time_ns()}\t{values["Pss"]}\t{values["Rss"]}\t{cgpath.joinpath("memory.current").read_text().strip()}\n')
                        if not sampling:break
                        stop.wait(.1)
            except BaseException:(out/'host-sampling-error.txt').write_text(traceback.format_exc())
        sample=threading.Thread(target=sample_host);sample.start()
        if task:
            stage='frozen_task_prefix'
            from task_metrics_prefix import run_prefix
            verify_after,replay=run_prefix(aenv,sid,out,task,sampling)
            checks['frozen_task_prefix']='PASS';result['prefix']=replay
        if exp1:
            stage='frozen_exp1'
            from exp1_physical_workload import run_exp1
            run_exp1(aenv,sid,out,persistent_sampling=True);checks['frozen_exp1']='PASS'
            from exp1_official_task import guest_command,grade
            task=ROOT/'.artifacts/v1-closeout-inputs/official-task'
            aenv('upload-official','upload',sid,str(task/'test.patch'),'/workspace/asb-official.patch')
            graded=grade(task,aenv('official-before-scan','exec',sid,'bash','-lc',guest_command(task,'/workspace/asb-official.patch'),timeout=145).decode())
            dump(out/'official-before.json',graded);checks['official_before']='PASS'
            code='sha256sum /testbed/src/language-html/utils/index.js /testbed/src/language-html/parser-html.js /testbed/package.json; cd /testbed; git diff --binary | sha256sum'
            source_before=aenv('source-before','exec',sid,'bash','-lc',code)
            def verify_after():
                assert source_before==aenv('source-after','exec',sid,'bash','-lc',code)
                props=json.loads(aenv('independent-post-scan','exec',sid,'node','/workspace/asb-exp1-physical/check.cjs'))
                assert all(v['javascript_formatted'] and v['idempotent'] for v in props.values())
                graded=grade(task,aenv('official-post-scan','exec',sid,'bash','-lc',guest_command(task,'/workspace/asb-official.patch',apply_test_patch=False),timeout=145).decode())
                dump(out/'official-after.json',graded);checks['post_scan_correctness']='PASS';checks['official_after']='PASS'
        stage='whole_cache';tools=out/'probe-tools';tools.mkdir()
        tool_names=['guest_whole_cache_probe.py','guest_cache_marker.py']
        if file_inventory:tool_names += ['guest_cached_file_inventory.py','guest_named_file_probe.py']
        for name in tool_names:shutil.copy2(out/'code'/name,tools/name)
        aenv('upload-probe','upload',sid,str(tools),'/workspace/asb-cache-tools');guest='/dev/shm/asb-cache-'+sid
        if analysis:
            analysis_started=time.monotonic_ns();usage=resource.getrusage(resource.RUSAGE_SELF)
            suffix=' --heartbeat' if paused_boundary else ''
            probe_timeout=600 if file_inventory else 210
            probe_env='ASB_CACHE_WAIT_SECONDS=300 ' if file_inventory else ''
            aenv('probe-start','exec',sid,'bash','-lc',f'nohup timeout {probe_timeout} env {probe_env}PYTHONDONTWRITEBYTECODE=1 python3 /workspace/asb-cache-tools/guest_cache_marker.py {guest} {sid}{suffix} >/dev/shm/asb-cache.log 2>&1 </dev/null &')
            wait('marker.json')
            for name in ('before.json','before-flags.bin','iomem.txt','marker.json'):download(name)
            exe=os.readlink(f'/proc/{vm_pid}/exe');vm,proof=bind_vm(cache,new,cg,exe,sid)
            inventory=layer_inventory(Path('/proc')/str(vm_pid),Path('/proc')/str(daemons[0]),image,evidence=cache)
            dump(cache/'layer-inventory.json',inventory)
            if file_inventory:
                from cached_file_inventory import start as inventory_start,host_capture,finish as inventory_finish
                file_dir,file_guest=inventory_start(aenv,sid,out,file_inventory,kernel_file_types)
            if paused_boundary:
                from paused_agentenv_boundary import PausedBoundary
                pause=PausedBoundary(cache,vm,proof,inventory['workdir']);pause.pause()
            if file_inventory:host_capture(file_dir,vm,proof)
            if kernel_file_types:
                from kernel_file_cache import classify
                classify(file_dir,vm,proof,pause);checks['kernel_file_types']='PASS'
            ram_snapshot(cache,vm,proof,'before')
            scans=[scan_backing(Path(row['path']),cache,index,allow_unaligned=True) for index,row in enumerate(inventory['files'])]
            dump(cache/'backing-scans.json',scans)
            if pause:
                pause.snapshot_recheck();pause.resume();pause.save(True);checks['paused_boundary']='PASS'
            if file_inventory:
                if evict_fixture:
                    aenv('controlled-fixture-eviction','exec',sid,'python3','/workspace/asb-cache-tools/guest_cached_file_inventory.py','--evict-fixture',file_guest)
                attributed=inventory_finish(aenv,sid,file_dir,file_guest,calibration_scope)
                checks['file_inventory']='PASS'
            aenv('request-after','exec',sid,'bash','-lc',f'touch {guest}/capture-after');wait('after-ready')
            for name in ('after.json','after-flags.bin'):download(name)
            ram_snapshot(cache,vm,proof,'after');measured=recount(cache)
            after_usage=resource.getrusage(resource.RUSAGE_SELF)
            aenv('probe-release','exec',sid,'bash','-lc',f'touch {guest}/release');wait('done')
            measured.update(controller_elapsed_ns=time.monotonic_ns()-analysis_started,
                collector_cpu_user_seconds=after_usage.ru_utime-usage.ru_utime,
                collector_cpu_system_seconds=after_usage.ru_stime-usage.ru_stime,
                collector_process_maxrss_kib=after_usage.ru_maxrss,
                collector_scope='controller process, includes async host sampler; peak RSS is not private memory')
            dump(cache/'result.json',measured);checks['whole_cache']='PASS'
        else:checks['whole_cache']='DISABLED'
        if exp1 or task:
            stage='post_scan_correctness'
            verify_after()
            if task:checks['post_scan_correctness']='PASS'
        result['status']='PASS'
    except BaseException as exc:
        checks[stage]='FAIL';result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
        if (exp1 or task) and stage=='whole_cache':
            try:
                if task:
                    if pause and pause.paused:pause.resume();pause.save(False)
                    verify_after();checks['post_scan_correctness']='PASS'
                else:post_checks_after_analysis_error(checks,pause,verify_after)
            except BaseException as post_exc:
                checks['post_scan_correctness']='FAIL';result['post_scan_error']=repr(post_exc)
                (out/'post-scan-failure.txt').write_text(traceback.format_exc())
    finally:
        if pause and pause.paused:
            try:pause.resume();pause.save(False)
            except BaseException as exc:checks['resume_after_error']='FAIL';result.update(status='FAIL',resume_error=repr(exc))
        stop.set()
        if sample:sample.join(timeout=5)
        if (out/'host-sampling-error.txt').exists():checks['host_samples']='FAIL';result['status']='FAIL'
        elif sample:checks['host_samples']='PASS'
        if tracer and tracer.poll() is None:
            tracer.send_signal(signal.SIGINT)
            try:tracer.wait(timeout=10)
            except subprocess.TimeoutExpired:tracer.kill();tracer.wait(timeout=5)
        if sid:
            try:
                aenv('delete','delete',sid,timeout=90)
                assert not any(row.get('sandboxID')==sid for row in json.loads(aenv('list-after','list','--output','json')))
                checks['cleanup']='PASS'
            except BaseException as exc:checks['cleanup']='FAIL';result.update(status='FAIL',cleanup_error=repr(exc))
        result.update(checks=checks,last_stage=stage,controller_elapsed_ns=time.monotonic_ns()-started)
        dump(out/'result.json',result)
    print(f'status={result["status"]} stage={stage} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exp1',action='store_true')
    parser.add_argument('--analysis',choices=('on','off'),default='on')
    parser.add_argument('--paused-boundary',action='store_true',help='in-place pause of this owned VMM during backing scan')
    parser.add_argument('--file-inventory',choices=('fixture','task'),help='visible regular-file resident-only ownership calibration or all-root task analysis')
    parser.add_argument('--kernel-file-types',action='store_true',help='exact ELF and known-file verified guest inode types while paused')
    parser.add_argument('--calibration-scope',choices=('interval','frozen'),default='interval')
    parser.add_argument('--evict-calibration-fixture',action='store_true',help='controlled short validation: evict owned fixture pages after resume')
    args=parser.parse_args()
    if args.analysis=='off' and not args.exp1:parser.error('analysis controls require the frozen Exp1 workload')
    if args.paused_boundary and args.analysis!='on' and not args.kernel_file_types:parser.error('paused boundary requires analysis on or dormant kernel protocol options')
    if args.kernel_file_types and args.file_inventory!='fixture':parser.error('kernel file-type calibration requires fixture inventory')
    if args.file_inventory and not args.paused_boundary:parser.error('file inventory requires paused boundary')
    if args.file_inventory=='task' and not args.exp1:parser.error('task file inventory requires full frozen Exp1')
    if args.file_inventory=='fixture' and args.exp1 and not args.kernel_file_types:parser.error('fixture calibration must be separate from full task')
    if args.calibration_scope=='frozen' and not args.kernel_file_types:parser.error('frozen calibration requires exact-kernel proof')
    if args.evict_calibration_fixture and not (args.kernel_file_types and args.analysis=='on'):parser.error('controlled eviction requires fixture kernel analysis')
    with writer_lock():raise SystemExit(main(args.exp1,args.analysis=='on',args.paused_boundary,args.file_inventory,args.kernel_file_types,args.calibration_scope,args.evict_calibration_fixture))
