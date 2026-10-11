"""Calibrate host/guest counter filtering against a fixed instruction loop in an owned VM."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time
import traceback
from diagnose_exp1_cpu_variance import cgroup,matching_vm,proc_stat,instrument
from run_exp1_monitor_balanced import PATCH
from experiment_disk_guard import writer_lock
from owned_thread_wait import WaitTrace
from cpu_frequency_lease import PolicyLease, CPUS
ROOT=Path(__file__).resolve().parents[1]


def thread_runtime(tids):
    return {str(t):dict(runtime_ns=int((Path('/proc')/str(t)/'schedstat').read_text().split()[0]),
                       **proc_stat((Path('/proc')/str(t)/'stat').read_text())) for t in tids}


def measure(binary,tids,operation,directory,trace_wait=False):
    with (directory.with_suffix('.stderr')).open('w') as log:
        tracer=WaitTrace(tids,directory.parent/(directory.name+'-wait')) if trace_wait else None
        p=None
        try:
            p=subprocess.Popen([str(binary),*map(str,tids)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
            with selectors.DefaultSelector() as sel:
                sel.register(p.stdout,selectors.EVENT_READ)
                assert sel.select(5),'counter ready timeout'
            assert p.stdout.readline().strip()=='READY','counter setup failed'
            before=thread_runtime(tids)
            start=time.monotonic_ns()
            operation()
            duration=time.monotonic_ns()-start
            after=thread_runtime(tids)
            assert all(after[str(t)]['start_ticks']==before[str(t)]['start_ticks'] for t in tids)
            output,_=p.communicate('S\n',timeout=5)
            assert p.returncode==0,'counter read failed'
            rows=json.loads(output)
            assert all(r['enabled_ns']>0 and r['running_ns']/r['enabled_ns']>=.95 for r in rows),'counter multiplexing exceeds 5%'
            result=dict(duration_ns=duration,counters=rows,thread_before=before,thread_after=after,
                        host_runtime_delta_ns={str(t):after[str(t)]['runtime_ns']-before[str(t)]['runtime_ns'] for t in tids})
            if tracer:
                result['scheduler_wait']=tracer.stop();tracer=None
            directory.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
            return result
        finally:
            if p is not None and p.poll() is None:p.kill();p.wait(timeout=5)
            if tracer:tracer.stop()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frequency-variant',choices=('A','B'))
    parser.add_argument('--trace-wait',action='store_true')
    parser.add_argument('--calibration-result',type=Path)
    parser.add_argument('--exp1',action='store_true',help='diagnostic trajectory after validated counter calibration')
    parser.add_argument('--settle-seconds',type=int,choices=(0,10),default=0)
    args=parser.parse_args()
    assert not args.exp1 or args.settle_seconds==10, 'Exp1 diagnostic requires the validated 10-second settling variant'
    assert not args.exp1 or args.calibration_result is not None, '--exp1 requires --calibration-result pointing to a successful scope result.json'
    assert not args.frequency_variant or (args.exp1 and args.trace_wait), 'frequency diagnostic requires full replay and scheduler trace'
    os.umask(0o077)
    out=ROOT/'.artifacts/guest-perf-scope'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    result=dict(status='FAIL',checks={},phases=[],settle_seconds=args.settle_seconds,exp1=args.exp1,trace_wait=args.trace_wait,frequency_variant=args.frequency_variant,counter_window_version=2,scope='diagnostic only; VM-wide counters over tool calls; not monitoring overhead acceptance')
    cfg=json.loads((ROOT/'configs/exp1-monitor-calibration.json').read_text())
    base=json.loads((ROOT/'configs/exp1-agentenv.json').read_text())
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(ROOT/'runtime/config'))
    result['configuration']=dict(workload_image=cfg['workload_image'],cpu=2,memory_mib=4096,host_kernel=os.uname().release,guest_memory_sampling=False,host_memory_sampling=False)
    sid=None
    policy=None
    def call(label,args,timeout=30):
        if policy and label not in ('delete','list-after'):policy.verify()
        if label not in ('delete','list-after'):assert shutil.disk_usage(out).free>20*1024**3, 'disk reserve reached'
        p=subprocess.run(args,env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr)
        p.check_returncode();return p.stdout.decode().strip()
    def interrupted(signum,frame):raise RuntimeError('interrupted')
    signal.signal(signal.SIGTERM,interrupted)
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        with writer_lock():
            assert shutil.disk_usage(out).free>32*1024**3
            for name,static in [('count_vcpu_perf',False),('perf_guest_loop',True)]:
                source=ROOT/'scripts'/(name+'.c');shutil.copy2(source,out/source.name)
                call('build-'+name,['gcc','-O2','-Wall','-Wextra','-Werror',*(['-static'] if static else []),str(source),'-o',str(out/name)])
            shutil.copy2(__file__,out/'driver.py')
            for dependency in ('owned_thread_wait.py','trace_owned_vcpu_wait.bt','diagnose_exp1_cpu_variance.py','cpu_frequency_lease.py'):
                shutil.copy2(ROOT/'scripts'/dependency,out/dependency)
            # Negative control: guest-only events must not count a host-only process.
            native=measure(out/'count_vcpu_perf',[os.getpid()],lambda:sum(i*i for i in range(200000)),out/'native')
            assert sum(r['instructions'] for r in native['counters'] if r['scope']=='guest')==0
            assert sum(r['instructions'] for r in native['counters'] if r['scope']=='host')>0
            result['checks']['native_guest_exclusion']='PASS'
            assert json.loads(call('list-before',['aenv','list','--output','json']))==[]
            cid=(ROOT/base['server_result']/'container-id.txt').read_text().strip()
            service=json.loads(call('service',['docker','inspect','--format','{{json .State}}',cid]))
            assert service['Running']
            sid=call('create',['aenv','start','--cold',cfg['workload_image'],'--detach','--timeout','300','--cpu','2','--memory','4096'],180)
            (out/'sandbox-id.txt').write_text(sid+'\n')
            for attempt in range(30):
                try:call('ready',['aenv','exec',sid,'true'],5);break
                except subprocess.CalledProcessError:time.sleep(.2)
            else:raise RuntimeError('guest not ready')
            pid=matching_vm(sid,cgroup(service['Pid']))
            assert pid,'no identity-checked VMM'
            tids=[int(t.name) for t in (Path('/proc')/str(pid)/'task').iterdir() if 'vcpu' in (t/'comm').read_text()]
            assert len(tids)==2
            result['identity']=dict(pid=pid,tids=tids,sandbox_id=sid,method='MMDS instanceID and service cgroup',start_ticks=proc_stat((Path('/proc')/str(pid)/'stat').read_text())['start_ticks'])
            if args.frequency_variant:
                plan=json.loads((ROOT/'configs/exp1-cpu-frequency-plan.json').read_text())
                for cpu in CPUS:
                    cpu_path=Path(f'/sys/devices/system/cpu/cpu{cpu}')
                    old=plan['original_policies'][str(cpu)]
                    for name in ('related_cpus','scaling_driver'):
                        assert (cpu_path/'cpufreq'/name).read_text().strip()==old[name], 'topology or driver changed'
                    assert (cpu_path/'topology/thread_siblings_list').read_text().strip()==old['siblings']
                bindings=[]
                for index,cpu in enumerate((2,4)):
                    selected=[t for t in tids if (Path('/proc')/str(t)/'comm').read_text().strip()==f'fc_vcpu {index}']
                    assert len(selected)==1
                    tid=selected[0];before=sorted(os.sched_getaffinity(tid))
                    os.sched_setaffinity(tid,{cpu});assert os.sched_getaffinity(tid)=={cpu}
                    bindings.append(dict(tid=tid,cpu=cpu,original_affinity=before))
                result['vcpu_binding']=bindings
                (out/'vmm-numa-maps.txt').write_text((Path('/proc')/str(pid)/'numa_maps').read_text())
                policy=PolicyLease(out/'frequency-policy',args.frequency_variant,sandbox=dict(id=sid,runtime=str(ROOT/'runtime')))
            call('upload',['aenv','upload',sid,str(out/'perf_guest_loop'),'/workspace/perf-loop'])
            call('chmod',['aenv','exec',sid,'chmod','+x','/workspace/perf-loop'])
            if args.settle_seconds:
                def settle():
                    call('settle-processes',['aenv','exec',sid,'bash','-lc',
                        'ps -e -o pid,comm,time --sort=-time; sleep 10; ps -e -o pid,comm,time --sort=-time'],20)
                result['settling']=measure(out/'count_vcpu_perf',tids,settle,out/'settling')
            if not args.exp1:
                for i,n in enumerate([0,1000000000,2000000000,1000000000,0],1):
                    assert matching_vm(sid,cgroup(service['Pid']))==pid
                    def operation():
                        assert call(f'loop-{i}',['aenv','exec',sid,'/workspace/perf-loop',str(n)])=='LOOP_PASS'
                    measured=measure(out/'count_vcpu_perf',tids,operation,out/f'counts-{i}')
                    measured['iterations']=n
                    measured['guest_instructions']=sum(r['instructions'] for r in measured['counters'] if r['scope']=='guest')
                    measured['host_instructions']=sum(r['instructions'] for r in measured['counters'] if r['scope']=='host')
                    result['phases'].append(measured)
                    (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                baseline=sum(result['phases'][i]['guest_instructions'] for i in (0,4))/2
                errors=[]
                for phase in result['phases'][1:4]:
                    phase['baseline_corrected_instruction_error_percent']=100*((phase['guest_instructions']-baseline)/(2*phase['iterations'])-1)
                    errors.append(phase['baseline_corrected_instruction_error_percent'])
                result['calibration']=dict(idle_tool_call_guest_instructions=baseline,errors_percent=errors,
                                           interpretation='VM-wide instruction increments; tool call and other guest work included; not process-only counters')
                assert all(abs(e)<10 for e in errors),'instruction increments differ by >10% after idle controls'
                result['checks']['known_guest_instruction_loop']='PASS'
            else:
                calibration=args.calibration_result.resolve().parent
                proof=json.loads((calibration/'result.json').read_text())
                assert proof['status']=='PASS' and proof['checks']['known_guest_instruction_loop']=='PASS'
                assert (calibration/'count_vcpu_perf.c').read_bytes()==(out/'count_vcpu_perf.c').read_bytes()
                result['calibration_reference']=dict(path=str(calibration),sha256=hashlib.sha256((calibration/'result.json').read_bytes()).hexdigest())
                workload=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
                runner=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
                manifest=[line.split('\t',2) for line in (workload/'actions.tsv').read_text().splitlines()]
                for step,digest,_ in manifest:
                    assert hashlib.sha256((workload/'actions'/f'{step}.sh').read_bytes()).hexdigest()==digest
                (out/'guest-runner.sh').write_text(instrument(runner.read_text()))
                for name,path,dest in [('actions',workload/'actions','/workspace/replay-actions'),
                    ('manifest',workload/'actions.tsv','/workspace/replay-actions.tsv'),
                    ('runner',out/'guest-runner.sh','/workspace/guest-runner.sh')]:
                    call('upload-'+name,['aenv','upload',sid,str(path),dest],60)
                def replay(first,last,label):
                    return call(label,['aenv','exec',sid,'bash','-lc',
                        f'START_STEP={first} END_STEP={last} GUEST_MEMORY_SAMPLING=0 DROP_GUEST_CACHES=0 ACTION_TIMEOUT_SECONDS=60 bash /workspace/guest-runner.sh'],90)
                replay('001','022','prefix')
                for step in ('023','024'):
                    assert matching_vm(sid,cgroup(service['Pid']))==pid
                    if args.frequency_variant:
                        assert all(os.sched_getaffinity(b['tid'])=={b['cpu']} for b in result['vcpu_binding'])
                    measured=measure(out/'count_vcpu_perf',tids,lambda:replay(step,step,'step-'+step),out/('step-'+step+'-counters'),trace_wait=args.trace_wait)
                    measured.update(step=step,guest_instructions=sum(r['instructions'] for r in measured['counters'] if r['scope']=='guest'),
                                    guest_cycles=sum(r['cycles'] for r in measured['counters'] if r['scope']=='guest'),
                                    host_instructions=sum(r['instructions'] for r in measured['counters'] if r['scope']=='host'))
                    result['phases'].append(measured)
                replay('025','026','suffix')
                call('download',['aenv','download',sid,'/workspace/artifacts/replay',str(out)],60)
                replay_dir=out/'replay'
                with (replay_dir/'steps.tsv').open() as f: steps=list(csv.DictReader(f,delimiter='\t'))
                assert [r['step'] for r in steps]==[f'{i:03}' for i in range(1,27)]
                assert {r['step']:int(r['exit_code']) for r in steps if int(r['exit_code'])}=={'008':127,'016':127}
                assert (replay_dir/'actions.tsv').read_bytes()==(workload/'actions.tsv').read_bytes()
                assert hashlib.sha256((replay_dir/'patch.diff').read_bytes()).hexdigest()==PATCH
                stdout=(replay_dir/'stdout/024.log').read_text()
                assert 'document.addEventListener("DOMContentLoaded", () => {' in stdout
                assert 'const node = document.getElementById("lastStroke");' in stdout
                for phase in result['phases']:
                    phase['cpu_time']=dict(zip(('wall_seconds','user_seconds','system_seconds'),map(float,(replay_dir/f"cpu-{phase['step']}-time.tsv").read_text().split())))
                checker=ROOT/'scripts/exp1_independent_check.cjs'
                call('upload-independent',['aenv','upload',sid,str(checker),'/workspace/independent.cjs'])
                independent=json.loads(call('independent',['aenv','exec',sid,'node','/workspace/independent.cjs']))
                assert set(independent)=={'svg','nested_svg','html_control'}
                assert all(x['javascript_formatted'] and x['idempotent'] for x in independent.values())
                result['input_sha256']=hashlib.sha256((workload/'actions.tsv').read_bytes()).hexdigest()
                result['patch_sha256']=PATCH
                result['checks'].update(full_trajectory='PASS',independent_svg='PASS')
            result['status']='PASS'
    except BaseException:
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if policy:
            try:
                result['frequency_restoration']=policy.close()
            except BaseException:
                result['status']='FAIL'
                (out/'frequency-restoration-failure.txt').write_text(traceback.format_exc())
        if sid:
            try:
                call('delete',['aenv','delete',sid],90)
                assert not any(x['sandboxID']==sid for x in json.loads(call('list-after',['aenv','list','--output','json'])))
                result['checks']['cleanup']='PASS'
            except BaseException:
                result['checks']['cleanup']='FAIL';result['status']='FAIL'
                (out/'cleanup-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
