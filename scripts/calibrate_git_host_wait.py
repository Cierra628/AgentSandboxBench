"""Calibrate owned-vCPU tracing or per-Git timing on a warm fixture before Exp1."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def guest(base,phase,clock_calibration=False,split_commands=False,add_performance=False,add_trace2=False,open_close=False,open_only=False,guest_diskstats=False,guest_cache_state=False,guest_damon_state=False):
    repo=base/'fixture'
    if phase=='prepare':
        repo.mkdir()
        for i in range(256):
            d=repo/f'd{i:03d}';d.mkdir()
            for j in range(32):(d/f'f{j:03d}').write_text('original\n')
        for argv in (['git','init','-q'],['git','config','user.name','fixture'],['git','config','user.email','fixture@localhost'],['git','add','-A'],['git','commit','-qm','fixture']):
            subprocess.run(argv,cwd=repo,check=True,capture_output=True,timeout=30)
        (repo/'d000/f000').write_text('updated\n')
        return
    out=base/phase;out.mkdir()
    rows=[];started=time.monotonic_ns()
    env=dict(os.environ)
    runner=None
    if clock_calibration:
        from git_step_diagnostic import instrument_clock_only
        original='''#!/bin/bash
set -eu
testbed="$1"; action="$2"; out="$3"; timeout_seconds=10; step=025
mkdir -p "$out/stdout" "$out/stderr"
set +e
  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
exit "$status"
'''
        runner=out/'runner.sh'
        runner.write_text(instrument_clock_only(original,base/'command-clock',split_commands=split_commands,add_performance=add_performance,add_trace2=add_trace2,open_close_library=base/'directory-profile.so' if open_close else None,guest_diskstats=guest_diskstats,guest_cache_state=guest_cache_state,guest_damon_state=guest_damon_state).replace('cd /testbed','cd "$testbed"'))
    else:
        env.update(GIT_TRACE2_EVENT=str(out/'events.jsonl'),GIT_TRACE2_PERF=str(out/'perf.txt'),GIT_TRACE_PERFORMANCE=str(out/'performance.txt'))
    for i in range(64):
        subprocess.run(['git','reset','--mixed','--quiet','HEAD'],cwd=repo,check=True,capture_output=True,timeout=10)
        path=out/f'clock-{i:03d}.json'
        if clock_calibration:
            iteration=out/f'iteration-{i:03d}'
            p=subprocess.run(['bash',str(runner),str(repo),str(base/'025.sh'),str(iteration)],cwd=repo,env=env,capture_output=True,timeout=15)
            assert not p.stdout and not p.stderr and p.returncode==0
            p.stdout=(iteration/'stdout/025.log').read_bytes();p.stderr=(iteration/'stderr/025.log').read_bytes()
            shutil.copy2(iteration/'git-025-clocks.json',path)
        else:
            p=subprocess.run([str(base/'command-clock'),str(path),'timeout','--signal=TERM','--kill-after=5s','10s','bash',str(base/'025.sh')],cwd=repo,env=env,capture_output=True,timeout=15)
        assert p.returncode==0 and not p.stderr,(p.returncode,p.stderr)
        clock=json.loads(path.read_text());assert clock['exit_code']==0
        rows.append(dict(iteration=i,clock=clock,stdout_sha256=hashlib.sha256(p.stdout).hexdigest(),exit_code=p.returncode,stderr_bytes=len(p.stderr)))
        if clock_calibration and split_commands:
            if split_commands=='bash':
                rows[-1]['segments']={name:[float(v) for v in (iteration/f'git-025-{name}-time.tsv').read_text().split()] for name in ('add','diff')}
                assert all(len(v)==3 and all(n>=0 for n in v) for v in rows[-1]['segments'].values())
            else:
                rows[-1]['segments']={name:json.loads((iteration/f'git-025-{name}-clocks.json').read_text()) for name in ('add','diff')}
                assert all(c['exit_code']==0 for c in rows[-1]['segments'].values())
        if add_performance:
            from git_step_diagnostic import add_performance_records
            rows[-1]['add_performance']=add_performance_records((iteration/'git-025-add-performance.txt').read_text())
        if add_trace2:
            from git_step_diagnostic import add_trace2_records
            rows[-1]['add_trace2']=add_trace2_records((iteration/'git-025-add-events.jsonl').read_text(),rows[-1]['add_performance'])
        if open_close:
            from git_step_diagnostic import open_close_records
            rows[-1]['open_close']=open_close_records(iteration,open_only=open_only)
        if i==0:(out/'stdout').write_bytes(p.stdout)
        assert rows[-1]['stdout_sha256']==rows[0]['stdout_sha256']
    result=dict(status='PASS',phase=phase,iterations=rows,phase_ns=time.monotonic_ns()-started,command_total_ns=sum(x['clock']['monotonic_raw_ns'] for x in rows))
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')


def host(clock_calibration=False,bash_command_times=False,add_performance=False,add_trace2=False,open_close=False,open_only=False,guest_diskstats=False,host_kvm_faults=False,guest_cache_state=False,guest_damon_state=False):
    from cpu_frequency_lease import save
    from diagnose_exp1_cpu_variance import cgroup,matching_vm,proc_stat
    from experiment_disk_guard import writer_lock
    from owned_thread_wait import WaitTrace
    prefix='.artifacts/git-add-trace2-calibration' if add_trace2 else ('.artifacts/git-add-performance-calibration' if add_performance else ('.artifacts/git-command-time-calibration' if bash_command_times else ('.artifacts/git-command-clock-calibration' if clock_calibration else '.artifacts/git-host-wait-calibration')))
    if open_close:prefix='.artifacts/git-open-close-calibration'
    if open_only:prefix='.artifacts/git-open-only-calibration'
    if guest_diskstats:prefix='.artifacts/guest-diskstats-calibration'
    if guest_cache_state:prefix='.artifacts/guest-cache-state-calibration'
    if guest_damon_state:prefix='.artifacts/guest-damon-state-calibration'
    if host_kvm_faults:prefix='.artifacts/owned-kvm-fault-calibration'
    out=ROOT/prefix/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}');out.mkdir(parents=True)
    result=dict(status='FAIL',runs=[],cleanup='NOT_NEEDED',diagnostic_gate='NOT_RUN')
    # One short mirrored group, not a formal monitoring acceptance experiment.
    order=[('aa','A',False),('aa','B',False),('aa','B',False),('aa','A',False),('ab','A',False),('ab','B',True),('ab','B',True),('ab','A',False)]
    scope='incremental per-Git command wrappers; whole-command clocks enabled in all phases; no native Git trace or BPF; warm fixture only' if clock_calibration else 'incremental owned-vCPU BPF overhead; native Git traces enabled in all phases; warm fixture only'
    if bash_command_times:scope='incremental Bash builtin per-Git time; whole-command clocks enabled in all phases; no native Git trace or BPF; warm fixture only'
    if add_performance:scope='incremental native performance log for add only; whole clocks and Bash command time in all phases; no BPF; warm fixture only'
    if add_trace2:scope='incremental add-only Trace2 EVENT; native performance, whole clocks and Bash time in all phases; no BPF; warm fixture only'
    if open_close:scope='incremental add-only opendir/closedir timing; add Trace2, native performance and Bash timing in all phases; no directory reads or stat measured'
    if open_only:scope=scope.replace('opendir/closedir','opendir').replace('no directory reads or stat measured','closedir, directory reads and stat NOT_MEASURED')
    if guest_diskstats:scope='incremental before/after guest diskstats using Bash builtins; clocks, Bash timing, add native and Trace2 in all phases; not pure I/O wait'
    if guest_cache_state:scope='incremental guest vmstat/meminfo endpoints via Bash builtin file reads; clocks, Bash timing, add native and Trace2 in all phases; parsing offline only'
    if guest_damon_state:scope='incremental guest DAMON endpoints; vmstat/meminfo and native Git traces in all phases; offline parsing'
    if host_kvm_faults:scope='incremental owned-vCPU KVM TRY and async setup counters; owned queue wait and native Git traces in all phases; warm fixture only'
    save(out/'protocol.json',dict(guest_damon_format='vmstat' if guest_damon_state else None,guest_damon_state=guest_damon_state,guest_cache_state=guest_cache_state,host_kvm_faults=host_kvm_faults,order=order,clock_calibration=clock_calibration,bash_command_times=bash_command_times,add_performance=add_performance,add_trace2=add_trace2,open_close=open_close,open_only=open_only,guest_diskstats=guest_diskstats,diskstats_validation='offline' if guest_diskstats else None,iterations_per_phase=64,directories=256,files_per_directory=32,gate='AA +/-5% and AB <=5% for both command and phase elapsed',scope=scope))
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(ROOT/'runtime/config'))
    def call(label,argv,timeout=30):
        p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
        (out/(label+'.stdout')).write_text(p.stdout);(out/(label+'.stderr')).write_text(p.stderr)
        p.check_returncode();return p.stdout.strip()
    sid=None;helper=None;helper_log=None;tracer=None
    print(f'status=RUNNING result_dir={out}',flush=True)
    with writer_lock():
        try:
            assert shutil.disk_usage(ROOT).free>32*1024**3
            assert json.loads(call('list-before',['aenv','list','--output','json']))==[]
            bundle=out/'tools';bundle.mkdir()
            shutil.copy2(__file__,bundle/'calibrate_git_host_wait.py')
            if guest_cache_state:shutil.copy2(ROOT/'scripts/guest_cache_state.py',bundle/'guest_cache_state.py')
            if guest_diskstats:shutil.copy2(ROOT/'scripts/guest_diskstats.py',bundle/'guest_diskstats.py')
            if clock_calibration:shutil.copy2(ROOT/'scripts/git_step_diagnostic.py',bundle/'git_step_diagnostic.py')
            action=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400/actions/025.sh'
            shutil.copy2(action,bundle/'025.sh')
            shutil.copy2(ROOT/'scripts/command_clock.c',bundle/'command_clock.c')
            call('build-clock',['gcc','-O2','-static','-Wall','-Wextra','-Werror',str(bundle/'command_clock.c'),'-o',str(bundle/'command-clock')])
            if open_close:
                shutil.copy2(ROOT/'scripts/directory_profile.c',bundle/'directory_profile.c')
                call('build-profile',['gcc','-O2','-Wall','-Wextra','-Werror','-shared','-fPIC','-DDIRECTORY_OPEN_ONLY' if open_only else '-DDIRECTORY_OPEN_CLOSE_ONLY',str(bundle/'directory_profile.c'),'-ldl','-pthread','-o',str(bundle/'directory-profile.so')])
            save(out/'inputs.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bundle.iterdir()})
            cfg=json.loads((ROOT/'configs/exp1-agentenv.json').read_text())
            cfg['workload_image']=json.loads((ROOT/'configs/exp1-monitor-calibration.json').read_text())['workload_image']
            host_sources=out/'host-sources';host_sources.mkdir()
            for name in ('owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt','fixed_frequency_window.py','cpu_frequency_lease.py','diagnose_exp1_cpu_variance.py'):
                shutil.copy2(ROOT/'scripts'/name,host_sources/name)
            save(out/'host-inputs.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in host_sources.iterdir()})
            cid=(Path(cfg['server_result'])/'container-id.txt').read_text().strip()
            service_pid=call('service-pid',['docker','inspect','--format','{{.State.Pid}}',cid]);group=cgroup(service_pid)
            result['image']=cfg['workload_image']
            sid=call('create',['aenv','start','--cold',cfg['workload_image'],'--detach','--timeout','300','--cpu','2','--memory','4096'],90)
            result['sandbox_id']=sid;save(out/'progress.json',result)
            call('upload',['aenv','upload',sid,str(bundle),'/workspace/git-wait'],45)
            call('chmod',['aenv','exec',sid,'chmod','+x','/workspace/git-wait/command-clock'])
            if not host_kvm_faults:call('prepare',['aenv','exec',sid,'python3','/workspace/git-wait/calibrate_git_host_wait.py','--guest','/workspace/git-wait','--phase','prepare'],60)
            helper_log=(out/'frequency.log').open('w')
            helper=subprocess.Popen([sys.executable,str(ROOT/'scripts/fixed_frequency_window.py'),sid,str(out/'fixed-frequency'),str(ROOT/'runtime'),group],stdout=helper_log,stderr=subprocess.STDOUT,env=env)
            deadline=time.monotonic()+45
            while not (out/'fixed-frequency/ready.json').exists():
                assert helper.poll() is None and time.monotonic()<deadline,'frequency helper did not become ready'
                time.sleep(.05)
            identity=json.loads((out/'fixed-frequency/ready.json').read_text());result['identity']=identity
            tids=[x['tid'] for x in identity['binding']]
            if host_kvm_faults:
                tracer=WaitTrace(tids,out/'functional',max_seconds=60,kvm_faults=True)
            if host_kvm_faults:call('prepare',['aenv','exec',sid,'python3','/workspace/git-wait/calibrate_git_host_wait.py','--guest','/workspace/git-wait','--phase','prepare'],60)
            if tracer:
                owned=tracer;tracer=None;result['functional']=owned.stop()
                counts=result['functional']['kvm_faults']['threads']
                result['functional_validation']=dict(try_event='PASS' if sum(x['try_async_get_page'] for x in counts)>0 else 'NOT_VALIDATED',async_setup='PASS' if sum(x['successful_async_setup'] for x in counts)>0 else 'NOT_VALIDATED')
                save(out/'progress.json',result)
            flags=['--clock-calibration'] if clock_calibration else []
            if add_performance:flags+=['--bash-command-times']
            if add_trace2:flags+=['--add-performance']
            if open_close or guest_diskstats or guest_cache_state:flags+=['--add-trace2']
            if guest_damon_state:flags+=['--guest-cache-state']
            call('warmup',['aenv','exec',sid,'python3','/workspace/git-wait/calibrate_git_host_wait.py','--guest','/workspace/git-wait','--phase','warmup']+flags,30)
            for i,(comparison,label,enabled) in enumerate(order):
                phase=f'{comparison}-{i}-{label}'
                assert matching_vm(sid,group)==identity['pid']
                assert proc_stat((Path('/proc')/str(identity['pid'])/'stat').read_text())['start_ticks']==identity['start_ticks']
                if not clock_calibration and (enabled or host_kvm_faults):tracer=WaitTrace(tids,out/phase,max_seconds=35,kvm_faults=host_kvm_faults and enabled)
                split_flags=['--add-trace2'] if add_trace2 else (['--add-performance'] if add_performance else (['--bash-command-times'] if bash_command_times else ['--split-commands']))
                if guest_cache_state:split_flags=['--guest-cache-state']
                if guest_damon_state:split_flags=['--guest-damon-state']
                if guest_diskstats:split_flags=['--guest-diskstats']
                if open_close:split_flags=['--open-only'] if open_only else ['--open-close']
                call(phase,['aenv','exec',sid,'python3','/workspace/git-wait/calibrate_git_host_wait.py','--guest','/workspace/git-wait','--phase',phase]+flags+(split_flags if clock_calibration and enabled else []),30)
                wait=None
                if tracer:
                    owned=tracer;tracer=None;wait=owned.stop()
                output=call(phase+'-result',['aenv','exec',sid,'cat',f'/workspace/git-wait/{phase}/result.json'])
                measured=json.loads(output)
                result['runs'].append(dict(comparison=comparison,label=label,enabled=enabled,phase=phase,measurement=measured,host_wait=wait))
                save(out/'progress.json',result)
                print(f'phase={phase} status=PASS',flush=True)
            differences={}
            for metric in ('command_total_ns','phase_ns'):
                values={}
                for comparison in ('aa','ab'):
                    selected=[r for r in result['runs'] if r['comparison']==comparison]
                    a=sum(r['measurement'][metric] for r in selected if r['label']=='A')/2
                    b=sum(r['measurement'][metric] for r in selected if r['label']=='B')/2
                    values[comparison]=100*(b/a-1)
                differences[metric]=values
            result.update(status='PASS',differences_percent=differences,diagnostic_gate='PASS' if all(abs(x['aa'])<=5 and x['ab']<=5 for x in differences.values()) else 'NOT_ACCEPTED')
        except Exception as exc:result['error']=repr(exc)
        finally:
            if tracer:
                try:tracer.stop()
                except Exception as exc:result.update(status='FAIL',trace_cleanup_error=repr(exc))
            if helper:
                (out/'fixed-frequency/stop').touch()
                try:
                    assert helper.wait(timeout=55)==0
                    result['frequency']=json.loads((out/'fixed-frequency/result.json').read_text())
                    assert result['frequency']['restoration']['status']=='PASS'
                except Exception as exc:result.update(status='FAIL',frequency_error=repr(exc))
                helper_log.close()
            if sid:
                try:
                    call('download',['aenv','download',sid,'/workspace/git-wait',str(out/'guest')],60)
                except Exception as exc:result.update(status='FAIL',download_error=repr(exc))
                try:
                    call('delete',['aenv','delete',sid],90)
                    assert not any(x['sandboxID']==sid for x in json.loads(call('list-after',['aenv','list','--output','json'])))
                    result['cleanup']='PASS'
                except Exception as exc:result.update(status='FAIL',cleanup='FAIL',cleanup_error=repr(exc))
            result['free_bytes_after']=shutil.disk_usage(ROOT).free
            save(out/'result.json',result)
    print(f"status={result['status']} gate={result['diagnostic_gate']} cleanup={result['cleanup']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--guest',type=Path);parser.add_argument('--phase');parser.add_argument('--clock-calibration',action='store_true');parser.add_argument('--split-commands',action='store_true');parser.add_argument('--bash-command-times',action='store_true');parser.add_argument('--add-performance',action='store_true');parser.add_argument('--add-trace2',action='store_true');parser.add_argument('--open-close',action='store_true');parser.add_argument('--open-only',action='store_true');parser.add_argument('--guest-diskstats',action='store_true');parser.add_argument('--host-kvm-faults',action='store_true');parser.add_argument('--guest-cache-state',action='store_true');parser.add_argument('--guest-damon-state',action='store_true');args=parser.parse_args()
    if args.guest_damon_state:args.guest_cache_state=True
    if args.guest_cache_state:
        assert not args.guest_diskstats and not args.host_kvm_faults
        args.add_trace2=True
    if args.guest_diskstats:args.add_trace2=True
    if args.open_only:args.open_close=True
    if args.open_close:args.add_trace2=True
    if args.add_trace2:args.add_performance=True
    if args.add_performance:args.bash_command_times=True
    assert not (args.split_commands and args.bash_command_times)
    if args.bash_command_times:args.clock_calibration=True
    assert not args.split_commands or args.clock_calibration
    assert not args.host_kvm_faults or (not args.clock_calibration and not args.guest)
    if args.guest:guest(args.guest,args.phase,args.clock_calibration,'bash' if args.bash_command_times else args.split_commands,args.add_performance,args.add_trace2,args.open_close,args.open_only,args.guest_diskstats,args.guest_cache_state,args.guest_damon_state)
    else:raise SystemExit(host(args.clock_calibration,args.bash_command_times,args.add_performance,args.add_trace2,args.open_close,args.open_only,args.guest_diskstats,args.host_kvm_faults,args.guest_cache_state,args.guest_damon_state))
