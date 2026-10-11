"""Fixed-frequency Exp1 monitoring: four smoke replays or one balanced eight-replay group."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
from cpu_frequency_lease import CPUS,read_policy,save
from diagnose_exp1_cpu_variance import cgroup
from experiment_disk_guard import writer_lock
from run_exp1_monitor_balanced import run_one, schedule

ROOT=Path(__file__).resolve().parents[1]


def instrument(text,helper,runtime,service_group,checker,*,host_wait=False,host_kvm_faults=False):
    anchor='task_window_start_ns="$(date +%s%N)"'
    assert text.count(anchor)==1
    before='''policy_pid=''
stop_frequency() {
  if [[ -n $policy_pid ]]; then
    kill -TERM "$policy_pid" 2>/dev/null || true
    wait "$policy_pid" 2>/dev/null || true
    policy_pid=''
  fi
}
trap 'stop_frequency; cleanup_runtime' EXIT
python3 HELPER "$sandbox_id" "$raw_dir/fixed-frequency" RUNTIME SERVICE_GROUP > "$raw_dir/frequency-window.log" 2>&1 &
policy_pid=$!
for attempt in $(seq 1 600); do
  [[ -f $raw_dir/fixed-frequency/ready.json ]] && break
  kill -0 "$policy_pid" 2>/dev/null || { wait "$policy_pid"; exit 1; }
  sleep 0.1
done
test -f "$raw_dir/fixed-frequency/ready.json"
'''
    import shlex
    before=before.replace('HELPER',shlex.quote(str(helper))).replace('RUNTIME',shlex.quote(str(runtime))).replace('SERVICE_GROUP',shlex.quote(service_group))
    if host_wait:
        before=before.replace(' > "$raw_dir/frequency-window.log"',' --host-wait > "$raw_dir/frequency-window.log"')
        before='aenv exec "$sandbox_id" sha256sum /workspace/guest-runner.sh > "$raw_dir/guest-runner-sha256.txt"\n'+before
    assert not host_kvm_faults or host_wait
    if host_kvm_faults:before=before.replace(' --host-wait >',' --host-wait --host-kvm-faults >')
    marker="python3 -c 'import time; print(time.monotonic_ns())'"
    if host_wait:before+=marker+' > "$raw_dir/host-wait-task-start-monotonic-ns.txt"\n'
    text=text.replace(anchor,before+'\n'+anchor)
    anchor='printf \'%s\\n\' "$exec_status" > "$raw_dir/replay-command-exit-code.txt"'
    assert text.count(anchor)==1
    after='\n'
    if host_wait:after+=marker+' > "$raw_dir/host-wait-task-end-monotonic-ns.txt"\n'
    text=text.replace(anchor,anchor+after+'''touch "$raw_dir/fixed-frequency/stop"
wait "$policy_pid"
policy_pid=''
''')
    anchor='# Delete before rebuilding the report so the summary can confirm cleanup.'
    assert text.count(anchor)==1
    text=text.replace(anchor,'aenv upload "$sandbox_id" '+shlex.quote(str(checker))+''' /workspace/independent.cjs
aenv exec "$sandbox_id" node /workspace/independent.cjs > "$raw_dir/independent.json"
\n'''+anchor)
    return text


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--balanced',action='store_true',help='one eight-replay mirrored control/comparison group')
    parser.add_argument('--seed',type=int,default=20260930)
    parser.add_argument('--monitor-damon-mode',choices=('default','damon_disabled'),help='fixed guest DAMON policy for an original-runner off/light smoke or balanced group')
    parser.add_argument('--git-diagnostic',action='store_true')
    parser.add_argument('--git-clock',action='store_true')
    parser.add_argument('--git-clock-only',action='store_true',help='only a dual-clock wrapper at step 025; requires --host-wait; no native Git tracing')
    parser.add_argument('--git-command-clocks',action='store_true',help='also time git add and git diff separately; requires --git-clock-only')
    parser.add_argument('--git-command-times',action='store_true',help='use Bash builtin time for git add/diff; requires --git-clock-only')
    parser.add_argument('--git-add-performance',action='store_true',help='native performance log only for git add; requires --git-command-times')
    parser.add_argument('--git-add-trace2',action='store_true',help='one Trace2 EVENT log for add directory boundaries; requires --git-add-performance')
    parser.add_argument('--git-open-only',action='store_true',help='add-only opendir libc timing; requires --git-add-trace2')
    parser.add_argument('--directory-cache-probe',action='store_true',help='eight off-only control/prewarm replays; not monitoring acceptance')
    parser.add_argument('--directory-prewarm-smoke',action='store_true',help='one prewarm off-only replay before directory-cache-probe')
    parser.add_argument('--guest-diskstats',action='store_true',help='guest block counters before/after step 025 command; requires add Trace2')
    parser.add_argument('--guest-file-cache-cold',action='store_true',help='private guest drop_caches=1 before step 025; requires cache probe and guest diskstats; separate diagnostic only')
    parser.add_argument('--git-syscalls',action='store_true')
    parser.add_argument('--host-wait',action='store_true',help='owned vCPU runnable-wait upper bound over the whole task window')
    parser.add_argument('--syscall-scope',choices=('all-files','reads'),default='all-files')
    parser.add_argument('--diagnostic-runs',type=int,choices=(1,4),default=1)
    parser.add_argument('--host-kvm-faults',action='store_true',help='incremental owned vCPU KVM attempt and async setup counters; requires host wait')
    parser.add_argument('--guest-cache-state',action='store_true',help='guest vmstat/meminfo at step 001 and before/after step 025; requires add Trace2')
    parser.add_argument('--guest-damon-state',action='store_true',help='DAMON region statistics at cache boundaries; requires guest cache state')
    parser.add_argument('--damon-control',choices=('smoke','comparison','comparison-reverse'),help='approved new-guest DAMON default/disabled comparison, all monitoring off')
    parser.add_argument('--runner-phases',action='store_true',help='calibrated timestamps outside frozen actions plus whole-runner dual clock')
    parser.add_argument('--baseline-git-commands',action='store_true',help='calibrated initial Git command wall/CPU times; requires runner phases')
    parser.add_argument('--baseline-add-trace',action='store_true',help='initial add native Git logs and block counters; requires baseline Git commands')
    args=parser.parse_args()
    if args.monitor_damon_mode:
        assert not any((args.git_diagnostic,args.git_clock,args.git_clock_only,args.host_wait,args.host_kvm_faults,
                        args.runner_phases,args.damon_control,args.directory_cache_probe,args.directory_prewarm_smoke,
                        args.guest_file_cache_cold,args.guest_cache_state,args.guest_damon_state))
    assert not args.baseline_add_trace or args.baseline_git_commands
    from guest_damon_control import SMOKE,COMPARISON,REVERSE_COMPARISON,instrument as instrument_damon,audit as audit_damon
    damon_modes={'smoke':SMOKE,'comparison':COMPARISON,'comparison-reverse':REVERSE_COMPARISON}.get(args.damon_control,[])
    assert not args.runner_phases or (damon_modes and args.git_clock_only)
    assert not args.baseline_git_commands or args.runner_phases
    if damon_modes:
        assert args.guest_cache_state and args.guest_diskstats and args.host_kvm_faults
        assert not any((args.guest_damon_state,args.balanced,args.directory_cache_probe,args.directory_prewarm_smoke,args.guest_file_cache_cold,args.git_open_only))
    assert not args.guest_damon_state or args.guest_cache_state
    assert not args.guest_cache_state or (args.git_add_trace2 and not (args.directory_cache_probe or args.directory_prewarm_smoke))
    assert not args.host_kvm_faults or args.host_wait
    cache_probe=args.directory_cache_probe or args.directory_prewarm_smoke
    assert not cache_probe or (args.git_add_trace2 and args.host_wait and not args.balanced and not args.git_diagnostic)
    assert not args.guest_file_cache_cold or (cache_probe and args.guest_diskstats)
    cache_modes=['prewarm'] if args.directory_prewarm_smoke else (['control','prewarm','prewarm','control','prewarm','control','control','prewarm'] if args.directory_cache_probe else [])
    assert not args.git_command_clocks or args.git_clock_only
    assert not args.git_command_times or (args.git_clock_only and not args.git_command_clocks)
    assert not args.git_add_performance or args.git_command_times
    assert not args.git_add_trace2 or args.git_add_performance
    assert not args.git_open_only or args.git_add_trace2
    assert not args.guest_diskstats or args.git_add_trace2
    assert not args.git_clock_only or (args.host_wait and not args.git_diagnostic and not args.git_clock and not args.git_syscalls)
    assert not args.git_syscalls or (args.git_diagnostic and args.git_clock)
    assert not args.host_wait or (not args.git_syscalls and (not args.git_diagnostic or args.git_clock))
    assert not args.git_clock or args.git_diagnostic
    assert not (args.balanced and args.git_diagnostic) or args.git_clock
    order=schedule(args.seed,1)[0] if args.balanced else (['off']*args.diagnostic_runs if args.git_diagnostic or args.host_wait else ['off','light','light','off'])
    if cache_probe:order=['off']*len(cache_modes)
    if damon_modes:order=['off']*len(damon_modes)
    checked_damon_modes=damon_modes or ([args.monitor_damon_mode]*len(order) if args.monitor_damon_mode else [])
    os.umask(0o077)
    prefix='.artifacts/exp1-git-add-performance' if args.git_add_performance else ('.artifacts/exp1-git-command-times' if args.git_command_times else ('.artifacts/exp1-git-command-clocks' if args.git_command_clocks else ('.artifacts/exp1-git-diagnostic' if args.git_diagnostic else ('.artifacts/exp1-git-clock-only' if args.git_clock_only else ('.artifacts/exp1-host-wait-original' if args.host_wait else ('.artifacts/exp1-monitor-fixed-balanced' if args.balanced else '.artifacts/exp1-monitor-fixed-smoke'))))))
    if args.git_add_trace2:prefix='.artifacts/exp1-git-add-trace2'
    if args.git_open_only:prefix='.artifacts/exp1-git-open-only'
    if cache_probe:prefix='.artifacts/exp1-directory-cache-probe'
    if args.guest_diskstats:prefix+='-diskstats'
    if args.guest_file_cache_cold:prefix+='-guest-cold'
    if args.guest_cache_state:prefix+='-cache-state'
    if args.guest_damon_state:prefix+='-damon'
    if args.host_kvm_faults:prefix+='-kvm'
    if damon_modes:prefix='.artifacts/exp1-damon-control-'+args.damon_control
    if args.runner_phases:prefix+='-runner-phases'
    if args.baseline_git_commands:prefix+='-baseline-git'
    if args.baseline_add_trace:prefix+='-add-trace'
    if args.monitor_damon_mode:prefix+='-'+args.monitor_damon_mode
    out=ROOT/prefix/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}');out.mkdir(parents=True)
    result=dict(status='FAIL',runs=[],order=order,balanced=args.balanced,seed=args.seed,verdict='INCONCLUSIVE_SMOKE_ONLY',scope='host service cgroup sampling at 1 second; guest sampling off; fixed vCPU 2/4 and four-policy 2GHz requests')
    result['git_diagnostic']=args.git_diagnostic
    result['git_clock']=args.git_clock
    result['git_clock_only']=args.git_clock_only
    result['git_command_clocks']=args.git_command_clocks
    result['git_command_times']=args.git_command_times
    result['git_add_performance']=args.git_add_performance
    result['git_add_trace2']=args.git_add_trace2
    result['git_open_only']=args.git_open_only
    result['guest_diskstats']=args.guest_diskstats
    result['guest_cache_state']=args.guest_cache_state
    result['guest_damon_state']=args.guest_damon_state
    result['runner_phases']=args.runner_phases
    result['baseline_git_commands']=args.baseline_git_commands
    result['baseline_add_trace']=args.baseline_add_trace
    result['damon_control_modes']=damon_modes
    result['monitor_damon_mode']=args.monitor_damon_mode
    result['guest_damon_format']='vmstat' if args.guest_damon_state else None
    result['guest_file_cache_cold']=args.guest_file_cache_cold
    result['directory_cache_probe']=cache_probe
    result['directory_cache_modes']=cache_modes
    result['git_syscalls']=args.git_syscalls
    result['host_wait']=args.host_wait
    result['host_kvm_faults']=args.host_kvm_faults
    result['syscall_scope']=args.syscall_scope
    if args.git_diagnostic:result['scope']='step 025 Git tracing and CPU/I/O diagnosis; no monitoring acceptance'
    if args.host_wait and not args.git_diagnostic:
        result.update(verdict='DIAGNOSTIC_ONLY',scope='original guest runner; owned-vCPU whole-window runnable wait; host memory sampling only for light; no monitoring acceptance')
    if args.git_clock_only:
        result['scope']='only step 025 command dual clocks and rusage plus owned-vCPU wait; no native Git trace or guest /proc samples; no monitoring acceptance'
    if args.git_command_clocks:
        result['scope']+='; separate git add/diff wrappers; measured incremental cost on warm fixture only'
    if args.git_command_times:
        result['scope']+='; separate Bash builtin git add/diff timing; milliseconds precision; segment clocks not independently validated'
    if args.git_add_performance:
        result['scope']=result['scope'].replace('no native Git trace','no Trace2')+'; native performance timers for add only; nested/omitted intervals possible'
    if args.git_add_trace2:result['scope']=result['scope'].replace('no Trace2','add-only Trace2 EVENT')+'; paired read_directory boundaries and counts'
    if args.git_open_only:result['scope']+='; add-only opendir libc timing; closedir, directory reads and stat not measured'
    if args.guest_cache_state:result['scope']+='; guest-wide cache/reclaim endpoints at steps 001 and 025, no guest parsing during measurement'
    if cache_probe:result['scope']='off-only directory prewarm intervention before frozen step 025; prewarm inside step/task timing but outside command clocks; guest/host cache effects not distinguished; not monitoring acceptance'
    save(out/'protocol.json',result)
    shutil.copy2(__file__,out/'driver.py')
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        with writer_lock():
            bundle=out/'workspace'
            with (out/'prepare.log').open('w') as log:subprocess.run(['python3',str(ROOT/'scripts/prepare_exp1_clean_directory.py'),str(bundle)],stdout=log,stderr=subprocess.STDOUT,timeout=150,check=True)
            cfg=json.loads((bundle/'configs/exp1-agentenv.json').read_text());cfg['repository']=str(bundle/'IncrementalDAX_moti')
            for name in ('fixed_frequency_window.py','cpu_frequency_lease.py','diagnose_exp1_cpu_variance.py'):
                shutil.copy2(ROOT/'scripts'/name,bundle/'scripts'/name)
            if args.host_wait:
                for name in ('owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt'):
                    shutil.copy2(ROOT/'scripts'/name,bundle/'scripts'/name)
            if args.guest_cache_state:
                shutil.copy2(ROOT/'scripts/guest_cache_state.py',out/'guest_cache_state.py')
                result['guest_cache_parser_sha256']=hashlib.sha256((out/'guest_cache_state.py').read_bytes()).hexdigest()
            if args.host_kvm_faults:
                save(out/'host-probe-inputs.json',{name:hashlib.sha256((bundle/'scripts'/name).read_bytes()).hexdigest() for name in ('owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt','fixed_frequency_window.py')})
            cid=(Path(cfg['server_result'])/'container-id.txt').read_text().strip()
            service_pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',cid],text=True,timeout=10).strip()
            service_group=cgroup(service_pid)
            runner=Path(cfg['repository'])/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/run.sh'
            (out/'runner-original.sh').write_text(runner.read_text())
            if args.git_clock or args.git_clock_only:
                shutil.copy2(ROOT/'scripts/command_clock.c',out/'command_clock.c')
                subprocess.run(['gcc','-O2','-static','-Wall','-Wextra','-Werror',str(out/'command_clock.c'),'-o',str(out/'command-clock')],check=True,timeout=30)
                import shlex
                upload='aenv upload "$sandbox_id" '+shlex.quote(str(out/'command-clock'))+' /workspace/command-clock\naenv exec "$sandbox_id" chmod +x /workspace/command-clock\n'
                original=runner.read_text();anchor='task_window_start_ns="$(date +%s%N)"';assert original.count(anchor)==1
                runner.write_text(original.replace(anchor,upload+anchor))
            if args.git_open_only:
                shutil.copy2(ROOT/'scripts/directory_profile.c',out/'directory_profile.c')
                subprocess.run(['gcc','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-DDIRECTORY_OPEN_ONLY',str(out/'directory_profile.c'),'-ldl','-pthread','-o',str(out/'directory-profile.so')],check=True,timeout=30)
                save(out/'directory-profile-inputs.json',{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('directory_profile.c','directory-profile.so')})
                import shlex
                upload='aenv upload "$sandbox_id" '+shlex.quote(str(out/'directory-profile.so'))+' /workspace/directory-profile.so\naenv exec "$sandbox_id" sha256sum /workspace/directory-profile.so > "$raw_dir/directory-profile-sha256.txt"\n'
                original=runner.read_text();anchor='task_window_start_ns="$(date +%s%N)"';assert original.count(anchor)==1
                runner.write_text(original.replace(anchor,upload+anchor))
            if args.guest_diskstats:
                original=runner.read_text();anchor='task_window_start_ns="$(date +%s%N)"';assert original.count(anchor)==1
                runner.write_text(original.replace(anchor,'aenv exec "$sandbox_id" cat /proc/mounts > "$raw_dir/guest-mounts.txt"\n'+anchor))
            if cache_probe:
                shutil.copy2(ROOT/'scripts/prewarm_directories.py',out/'prewarm_directories.py')
                result['directory_prewarm_source_sha256']=hashlib.sha256((out/'prewarm_directories.py').read_bytes()).hexdigest()
                import shlex
                upload='aenv upload "$sandbox_id" '+shlex.quote(str(out/'prewarm_directories.py'))+' /workspace/prewarm_directories.py\naenv exec "$sandbox_id" sha256sum /workspace/prewarm_directories.py > "$raw_dir/directory-prewarm-source-sha256.txt"\n'
                original=runner.read_text();anchor='task_window_start_ns="$(date +%s%N)"';assert original.count(anchor)==1
                runner.write_text(original.replace(anchor,upload+anchor))
            if args.git_syscalls:
                from guest_syscall_bundle import prepare
                prepare(out/'syscall-tools')
                shutil.copy2(ROOT/'scripts/guest_syscall_bundle.py',out/'guest_syscall_bundle.py')
                import shlex
                upload='aenv upload "$sandbox_id" '+shlex.quote(str(out/'syscall-tools'))+' /workspace/syscall-tools\naenv exec "$sandbox_id" chmod +x /workspace/syscall-tools/run-strace /workspace/syscall-tools/ld-linux-x86-64.so.2 /workspace/syscall-tools/strace\naenv exec "$sandbox_id" /workspace/syscall-tools/run-strace -qq -f -T -o /workspace/syscall-probe.log /bin/true > "$raw_dir/syscall-probe.stdout" 2> "$raw_dir/syscall-probe.stderr"\n'
                if args.syscall_scope=='reads':upload=upload.replace('run-strace -qq -f -T','run-strace -d --seccomp-bpf -e trace=read,pread64,getdents64 -qq -f -T')
                original=runner.read_text();anchor='task_window_start_ns="$(date +%s%N)"';assert original.count(anchor)==1
                runner.write_text(original.replace(anchor,upload+anchor))
            runner.write_text(instrument(runner.read_text(),bundle/'scripts/fixed_frequency_window.py',ROOT/'runtime',service_group,ROOT/'scripts/exp1_independent_check.cjs',host_wait=args.host_wait,host_kvm_faults=args.host_kvm_faults))
            if args.monitor_damon_mode:
                anchor='task_window_start_ns="$(date +%s%N)"'
                runner.write_text(runner.read_text().replace(anchor,'aenv exec "$sandbox_id" sha256sum /workspace/guest-runner.sh > "$raw_dir/guest-runner-sha256.txt"\n'+anchor))
            if args.baseline_add_trace:
                for name in ('git_step_diagnostic.py','guest_diskstats.py'):
                    shutil.copy2(ROOT/'scripts'/name,out/name)
                result['baseline_add_parser_sha256']={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('git_step_diagnostic.py','guest_diskstats.py')}
            if args.runner_phases:
                from guest_runner_phases import instrument_host
                runner.write_text(instrument_host(runner.read_text()))
                shutil.copy2(ROOT/'scripts/guest_runner_phases.py',out/'guest_runner_phases.py')
                result['runner_phase_source_sha256']=hashlib.sha256((out/'guest_runner_phases.py').read_bytes()).hexdigest()
            subprocess.run(['bash','-n',str(runner)],check=True,timeout=10)
            shutil.copy2(runner,out/'runner-fixed.sh')
            if checked_damon_modes:
                assert cfg['sandbox_memory_mib']==4096 and cfg['sandbox_cpu']==2 and cfg['drop_guest_caches']==0
                shutil.copy2(ROOT/'scripts/guest_damon_control.py',out/'guest_damon_control.py')
                result['damon_control_source_sha256']=hashlib.sha256((out/'guest_damon_control.py').read_bytes()).hexdigest()
                result['damon_host_runner_sha256']={}
                for damon_mode in ('default','damon_disabled'):
                    path=out/f'runner-{damon_mode}.sh'
                    path.write_text(instrument_damon((out/'runner-fixed.sh').read_text(),out/'guest_damon_control.py',damon_mode))
                    subprocess.run(['bash','-n',str(path)],check=True,timeout=10)
                    result['damon_host_runner_sha256'][damon_mode]=hashlib.sha256(path.read_bytes()).hexdigest()
            guest=runner.parent/'guest-runner.sh'
            (out/'guest-runner-original.sh').write_text(guest.read_text())
            if args.git_diagnostic:
                from git_step_diagnostic import instrument as instrument_git
                guest.write_text(instrument_git(guest.read_text(),clock_helper='/workspace/command-clock' if args.git_clock else None,syscall_helper='/workspace/syscall-tools/run-strace' if args.git_syscalls else None,syscall_scope=args.syscall_scope))
                subprocess.run(['bash','-n',str(guest)],check=True,timeout=10)
                shutil.copy2(guest,out/'guest-runner-diagnostic.sh')
                shutil.copy2(ROOT/'scripts/git_step_diagnostic.py',out/'git_step_diagnostic.py')
            if args.git_clock_only:
                from git_step_diagnostic import instrument_clock_only
                guest.write_text(instrument_clock_only(guest.read_text(),'/workspace/command-clock',split_commands='bash' if args.git_command_times else args.git_command_clocks,add_performance=args.git_add_performance,add_trace2=args.git_add_trace2,open_close_library='/workspace/directory-profile.so' if args.git_open_only else None,guest_diskstats=args.guest_diskstats,guest_drop_file_cache=args.guest_file_cache_cold,guest_cache_state=args.guest_cache_state,guest_damon_state=args.guest_damon_state))
                subprocess.run(['bash','-n',str(guest)],check=True,timeout=10)
                shutil.copy2(ROOT/'scripts/git_step_diagnostic.py',out/'git_step_diagnostic.py')
            if args.runner_phases:
                from guest_runner_phases import instrument as instrument_phases
                guest.write_text(instrument_phases(guest.read_text(),baseline_commands=args.baseline_git_commands,baseline_add_trace=args.baseline_add_trace))
                subprocess.run(['bash','-n',str(guest)],check=True,timeout=10)
            shutil.copy2(guest,out/'guest-runner-effective.sh')
            result['guest_runner_sha256']={name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in ('guest-runner-original.sh','guest-runner-effective.sh')}
            if args.monitor_damon_mode:
                assert set(result['guest_runner_sha256'].values())=={'bb3f50aa03b7424855556920d3808be5b6b8c3046e9bc0687c4229fc344efd9a'}
            if cache_probe:
                result['directory_cache_runner_sha256']={}
                from git_step_diagnostic import instrument_clock_only
                for mode in ('control','prewarm'):
                    source=instrument_clock_only((out/'guest-runner-original.sh').read_text(),'/workspace/command-clock',split_commands='bash',add_performance=args.git_add_performance,add_trace2=args.git_add_trace2,open_close_library='/workspace/directory-profile.so' if args.git_open_only else None,directory_prewarm_helper='/workspace/prewarm_directories.py' if mode=='prewarm' else None,guest_diskstats=args.guest_diskstats,guest_drop_file_cache=args.guest_file_cache_cold)
                    path=out/f'guest-runner-{mode}.sh';path.write_text(source)
                    subprocess.run(['bash','-n',str(path)],check=True,timeout=10)
                    result['directory_cache_runner_sha256'][mode]=hashlib.sha256(path.read_bytes()).hexdigest()
            if args.host_wait and not args.git_diagnostic and not args.git_clock_only:
                assert len(set(result['guest_runner_sha256'].values()))==1
            save(out/'protocol.json',result)
            fixed=None
            for i,label in enumerate(result['order'],1):
                mode=cache_modes[i-1] if cache_probe else None
                if cache_probe:shutil.copy2(out/f'guest-runner-{mode}.sh',guest)
                if checked_damon_modes:
                    damon_mode=checked_damon_modes[i-1]
                    shutil.copy2(out/f'runner-{damon_mode}.sh',runner)
                    cfg['damon_control_mode']=damon_mode
                row=run_one(out,cfg,i,label);raw=Path(row['raw_dir'])
                if args.runner_phases:
                    from guest_runner_phases import collect as collect_phases
                    row['runner_phases']=collect_phases(raw/'replay',row['guest_action_sum_ns'],baseline_commands=args.baseline_git_commands,baseline_add_trace=args.baseline_add_trace)
                if checked_damon_modes:
                    row['damon_control_mode']=damon_mode
                    row['damon_control']=audit_damon(raw,damon_mode)
                    assert (raw/'damon-host-runner-sha256.txt').read_text().split()[0]==result['damon_host_runner_sha256'][damon_mode]
                if args.monitor_damon_mode:
                    assert (raw/'guest-runner-sha256.txt').read_text().split()[0]==result['guest_runner_sha256']['guest-runner-effective.sh']
                freq=json.loads((raw/'fixed-frequency/result.json').read_text())
                assert freq['status']=='PASS' and freq['restoration']['status']=='PASS'
                if args.host_wait:
                    wait=freq['host_wait'];assert wait['status']=='PASS'
                    assert {x['tid'] for x in wait['threads']}=={x['tid'] for x in freq['binding']}
                    assert (raw/'guest-runner-sha256.txt').read_text().split()[0]==(result['directory_cache_runner_sha256'][mode] if cache_probe else result['guest_runner_sha256']['guest-runner-effective.sh'])
                if args.guest_cache_state:
                    from guest_cache_state import collect
                    collect(raw/'replay',include_start=True,include_damon=args.guest_damon_state)
                independent=json.loads((raw/'independent.json').read_text())
                assert set(independent)=={'svg','nested_svg','html_control'}
                assert all(v['javascript_formatted'] and v['idempotent'] for v in independent.values())
                applied=json.loads((raw/'fixed-frequency/frequency-policy/applied.json').read_text())
                assert all(v==dict(scaling_min_freq='2000000',scaling_max_freq='2000000',scaling_governor='schedutil') for v in applied['policies'].values())
                assert {str(c):read_policy(Path(f'/sys/devices/system/cpu/cpu{c}/cpufreq')) for c in CPUS}==freq['restoration']['after']
                identity={k:row['metadata'][k] for k in ('workload_image','sandbox_cpu','sandbox_memory_mib','action_manifest_sha256','aenv_version','host_kernel','agentenv_container_image','agentenv_container_id','overlaybd_io_engine','overlaybd_direct_io')}
                if fixed is None:fixed=identity
                assert identity==fixed
                row.update(position=i,frequency=freq,independent=independent)
                if cache_probe:
                    row['directory_cache_mode']=mode
                    row['guest_runner_sha256']=result['directory_cache_runner_sha256'][mode]
                if args.balanced:row['group']=1
                result['runs'].append(row);save(out/'progress.json',result)
                print(f'position={i}/{len(order)} mode={label} status=PASS result_dir={out}',flush=True)
            result['status']='PASS'
    except Exception:
        (out/'failure.txt').write_text(traceback.format_exc())
    save(out/'result.json',result)
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
