"""Audit and recompute fixed-frequency monitoring smoke and one balanced group."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
from cpu_frequency_lease import CPUS
from run_exp1_monitor_balanced import PATCH, schedule, summarize
from owned_thread_wait import host_wait_bound
from git_step_diagnostic import instrument_clock_only,add_performance_records,performance_marker_gaps,add_trace2_records,open_close_records

ORIGINAL_GUEST_SHA256='bb3f50aa03b7424855556920d3808be5b6b8c3046e9bc0687c4229fc344efd9a'
STEP_025_STDOUT_SHA256='9a64ed707c224734715f7af4fea2d84f0b51d128e5c8d05535fc9de7d5b6162f'

def report(directory):
    source=directory/'result.json'
    result=json.loads(source.read_text())
    assert result['status']=='PASS'
    balanced=result.get('balanced',False)
    cache_probe=result.get('directory_cache_probe',False)
    cache_modes=result.get('directory_cache_modes',[])
    from guest_damon_control import SMOKE,COMPARISON,REVERSE_COMPARISON,instrument as instrument_damon,audit as audit_damon
    damon_modes=result.get('damon_control_modes',[])
    monitor_damon_mode=result.get('monitor_damon_mode')
    checked_damon_modes=damon_modes or ([monitor_damon_mode]*len(result['runs']) if monitor_damon_mode else [])
    if monitor_damon_mode:
        assert monitor_damon_mode in ('default','damon_disabled') and not damon_modes
        assert not any(result.get(k,False) for k in ('git_diagnostic','git_clock','git_clock_only','host_wait',
                       'host_kvm_faults','runner_phases','directory_cache_probe','guest_file_cache_cold',
                       'guest_cache_state','guest_damon_state'))
        hashes=result['guest_runner_sha256']
        assert set(hashes)=={'guest-runner-original.sh','guest-runner-effective.sh'}
        assert set(hashes.values())=={ORIGINAL_GUEST_SHA256}
        for name,digest in hashes.items():assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
    runner_phases=result.get('runner_phases',False)
    baseline_commands=result.get('baseline_git_commands',False)
    baseline_add_trace=result.get('baseline_add_trace',False)
    assert not baseline_add_trace or baseline_commands
    if baseline_add_trace:
        assert set(result['baseline_add_parser_sha256'])=={'git_step_diagnostic.py','guest_diskstats.py'}
        for name,digest in result['baseline_add_parser_sha256'].items():
            assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
    assert not baseline_commands or runner_phases
    if runner_phases:
        assert damon_modes and result.get('git_clock_only')
        assert hashlib.sha256((directory/'guest_runner_phases.py').read_bytes()).hexdigest()==result['runner_phase_source_sha256']
        assert (directory/'runner-fixed.sh').read_text().count('/workspace/command-clock /workspace/artifacts/replay/runner-envelope.json bash /workspace/guest-runner.sh')==1
    if damon_modes:
        assert damon_modes in (SMOKE,COMPARISON,REVERSE_COMPARISON) and len(result['runs'])==len(damon_modes)
        assert len({r['raw_dir'] for r in result['runs']})==len(damon_modes)
        assert len({(Path(r['raw_dir'])/'sandbox-id.txt').read_text().strip() for r in result['runs']})==len(damon_modes)
        assert hashlib.sha256((directory/'guest-runner-original.sh').read_bytes()).hexdigest()=='bb3f50aa03b7424855556920d3808be5b6b8c3046e9bc0687c4229fc344efd9a'
        assert not balanced and not cache_probe and not result.get('guest_damon_state',False)
        assert result.get('guest_cache_state') and result.get('guest_diskstats') and result.get('host_kvm_faults')
        assert not result.get('guest_file_cache_cold') and not result.get('git_open_only')
    if checked_damon_modes:
        assert len({r['raw_dir'] for r in result['runs']})==len(checked_damon_modes)
        assert len({(Path(r['raw_dir'])/'sandbox-id.txt').read_text().strip() for r in result['runs']})==len(checked_damon_modes)
        assert hashlib.sha256((directory/'guest_damon_control.py').read_bytes()).hexdigest()==result['damon_control_source_sha256']
        assert set(result['damon_host_runner_sha256'])=={'default','damon_disabled'}
        for mode,digest in result['damon_host_runner_sha256'].items():
            path=directory/f'runner-{mode}.sh'
            assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
            assert path.read_text()==instrument_damon((directory/'runner-fixed.sh').read_text(),directory/'guest_damon_control.py',mode)
    if cache_probe:
        assert not balanced and cache_modes in (['prewarm'],['control','prewarm','prewarm','control','prewarm','control','control','prewarm'])
        assert hashlib.sha256((directory/'prewarm_directories.py').read_bytes()).hexdigest()==result['directory_prewarm_source_sha256']
        assert len(result['runs'])==len(cache_modes)
    git_diagnostic=result.get('git_diagnostic',False)
    clock_only=result.get('git_clock_only',False)
    split_commands=result.get('git_command_clocks',False)
    bash_times=result.get('git_command_times',False)
    add_performance=result.get('git_add_performance',False)
    add_trace2=result.get('git_add_trace2',False)
    open_only=result.get('git_open_only',False)
    diskstats=result.get('guest_diskstats',False)
    cache_state=result.get('guest_cache_state',False)
    damon_state=result.get('guest_damon_state',False)
    assert not damon_state or cache_state
    damon_format=result.get('guest_damon_format','separate')
    assert not damon_state or damon_format in ('vmstat','separate')
    assert not cache_state or (add_trace2 and not cache_probe)
    if cache_state:assert hashlib.sha256((directory/'guest_cache_state.py').read_bytes()).hexdigest()==result['guest_cache_parser_sha256']
    kvm=result.get('host_kvm_faults',False)
    if kvm:
        assert result.get('host_wait')
        inputs=json.loads((directory/'host-probe-inputs.json').read_text())
        assert set(inputs)=={'owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt','fixed_frequency_window.py'}
        for name,digest in inputs.items():assert hashlib.sha256((directory/'workspace/scripts'/name).read_bytes()).hexdigest()==digest
    guest_cold=result.get('guest_file_cache_cold',False)
    assert not guest_cold or (cache_probe and diskstats)
    assert not open_only or add_trace2
    if open_only:
        profile_inputs=json.loads((directory/'directory-profile-inputs.json').read_text())
        assert set(profile_inputs)=={'directory_profile.c','directory-profile.so'}
        for name,digest in profile_inputs.items():assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
    assert not split_commands or clock_only
    assert not bash_times or (clock_only and not split_commands)
    assert not add_performance or bash_times
    assert not add_trace2 or add_performance
    plain_wait=result.get('host_wait',False) and not git_diagnostic
    original_wait=plain_wait and not clock_only
    if git_diagnostic:assert (balanced and result.get('git_clock') and len(result['runs'])==8) or (len(result['runs']) in (1,4) and not balanced)
    expected=schedule(result['seed'],1)[0] if balanced else (['off']*len(result['runs']) if git_diagnostic or plain_wait else ['off','light','light','off'])
    if plain_wait and not balanced and not cache_probe and not damon_modes:assert len(result['runs']) in (1,4)
    if plain_wait:
        hashes=result['guest_runner_sha256']
        assert set(hashes)=={'guest-runner-original.sh','guest-runner-effective.sh'}
        if original_wait:assert len(set(hashes.values()))==1
        for name,digest in hashes.items():assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest
        if clock_only:
            assert result['host_wait'] and not result['git_diagnostic'] and not result['git_clock'] and not result['git_syscalls']
            expected_guest=instrument_clock_only((directory/'guest-runner-original.sh').read_text(),'/workspace/command-clock',split_commands='bash' if bash_times else split_commands,add_performance=add_performance,add_trace2=add_trace2,open_close_library='/workspace/directory-profile.so' if open_only else None,guest_diskstats=diskstats,guest_drop_file_cache=guest_cold,guest_cache_state=cache_state,guest_damon_state=damon_state,guest_damon_format=damon_format)
            if runner_phases:
                from guest_runner_phases import instrument as instrument_phases
                expected_guest=instrument_phases(expected_guest,baseline_commands=baseline_commands,baseline_add_trace=baseline_add_trace)
            assert (directory/'guest-runner-effective.sh').read_text()==expected_guest
    if cache_probe:
        assert plain_wait and clock_only and bash_times and add_trace2 and result['order']==['off']*len(cache_modes)
        assert set(result['directory_cache_runner_sha256'])=={'control','prewarm'}
        for mode,digest in result['directory_cache_runner_sha256'].items():
            path=directory/f'guest-runner-{mode}.sh'
            assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
            assert path.read_text()==instrument_clock_only((directory/'guest-runner-original.sh').read_text(),'/workspace/command-clock',split_commands='bash',add_performance=add_performance,add_trace2=add_trace2,open_close_library='/workspace/directory-profile.so' if open_only else None,directory_prewarm_helper='/workspace/prewarm_directories.py' if mode=='prewarm' else None,guest_diskstats=diskstats,guest_drop_file_cache=guest_cold,guest_cache_state=cache_state,guest_damon_state=damon_state,guest_damon_format=damon_format)
    assert result['order']==expected
    rows=result['runs'];assert len(rows)==len(expected)
    baseline={str(c):dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil') for c in CPUS}
    observations=[]
    for number,row in enumerate(rows,1):
        assert row['position']==number and row['label']==result['order'][number-1]
        raw=Path(row['raw_dir']);audit=Path(row['audit_dir'])
        checks=json.loads((audit/'checks.json').read_text());assert checks==row['checks']
        assert all(checks[k]=='PASS' for k in ('input_hashes','runner','replay_correctness','cleanup','task_window'))
        assert checks['host_sampling']==('PASS' if row['label']=='light' else 'DISABLED')
        assert checks['guest_sampling']=='DISABLED'
        with (raw/'replay/steps.tsv').open() as stream:steps=list(csv.DictReader(stream,delimiter='\t'))
        assert [v['step'] for v in steps]==[f'{i:03}' for i in range(1,27)]
        assert {v['step']:int(v['exit_code']) for v in steps if int(v['exit_code'])}=={'008':127,'016':127}
        assert hashlib.sha256((raw/'replay/patch.diff').read_bytes()).hexdigest()==row['patch_sha256']==PATCH
        assert int((raw/'task-window-duration-ns.txt').read_text())==row['task_window_ns']
        assert int((raw/'controller-exec-duration-ns.txt').read_text())==row['controller_exec_ns']
        assert sum(int(v['duration_ns']) for v in steps)==row['guest_action_sum_ns']
        freq=json.loads((raw/'fixed-frequency/result.json').read_text());assert freq==row['frequency'] and freq['status']=='PASS'
        receipt=json.loads((raw/'fixed-frequency/frequency-policy/restored.json').read_text())
        assert receipt==freq['restoration'] and receipt['status']=='PASS' and receipt['reason']=='controller_finally' and receipt['after']==baseline
        assert [v['cpu'] for v in freq['binding']]==[2,4]
        independent=json.loads((raw/'independent.json').read_text());assert independent==row['independent']
        assert all(v['javascript_formatted'] and v['idempotent'] for v in independent.values())
        host=raw/'host/memory-samples.tsv'
        samples=list(csv.DictReader(host.open(),delimiter='\t')) if host.exists() else []
        assert len(samples)==row['sample_count']
        assert len(samples)>=2 if row['label']=='light' else not samples
        observations.append(dict(position=number,mode=row['label'],task_window_seconds=row['task_window_ns']/1e9,controller_exec_seconds=row['controller_exec_ns']/1e9,guest_action_sum_seconds=row['guest_action_sum_ns']/1e9,sample_count=len(samples),steps_ms={s['step']:int(s['duration_ns'])/1e6 for s in steps if s['step'] in ('023','024','025')},observed_min_free_gib=row['observed_min_free_bytes']/1024**3))
        phase_files={p.name for p in (raw/'replay').glob('runner-*')}
        assert phase_files==({'runner-phases.tsv','runner-envelope.json'} if runner_phases else set())
        if runner_phases:
            from guest_runner_phases import collect as collect_phases
            phases=collect_phases(raw/'replay',row['guest_action_sum_ns'],baseline_commands=baseline_commands,baseline_add_trace=baseline_add_trace);assert phases==row['runner_phases']
            assert row['controller_exec_ns']>=phases['clock']['monotonic_raw_ns']-2_000_000
            observations[-1]['runner_phases']=phases
            observations[-1]['controller_minus_guest_envelope_ms']=(row['controller_exec_ns']-phases['clock']['monotonic_raw_ns'])/1e6
        if checked_damon_modes:
            mode=checked_damon_modes[number-1]
            assert (row['label']=='off' or monitor_damon_mode) and row['damon_control_mode']==mode
            assert hashlib.sha256((raw/'replay/stdout/025.log').read_bytes()).hexdigest()==STEP_025_STDOUT_SHA256
            receipts=audit_damon(raw,mode);assert receipts==row['damon_control']
            effective=json.loads((audit/'effective-config.json').read_text())
            assert effective['damon_control_mode']==mode and effective['sandbox_memory_mib']==4096
            assert effective['sandbox_cpu']==2 and effective['drop_guest_caches']==0
            assert (raw/'damon-host-runner-sha256.txt').read_text().split()[0]==result['damon_host_runner_sha256'][mode]
            observations[-1].update(damon_control_mode=mode,damon_control=receipts)
            if monitor_damon_mode:
                assert (raw/'guest-runner-sha256.txt').read_text().split()[0]==hashes['guest-runner-effective.sh']
        cold_policy=raw/'replay/guest-file-cache-policy.txt'
        if guest_cold:assert cold_policy.read_text()=='drop_caches=1 no_sync guest_only\n'
        else:assert not cold_policy.exists()
        if cache_probe:
            mode=cache_modes[number-1]
            assert row['directory_cache_mode']==mode and row['guest_runner_sha256']==result['directory_cache_runner_sha256'][mode]
            assert (raw/'directory-prewarm-source-sha256.txt').read_text().split()==[result['directory_prewarm_source_sha256'],'/workspace/prewarm_directories.py']
            observations[-1]['directory_cache_mode']=mode
            path=raw/'replay/prewarm-directories.json'
            if mode=='prewarm':
                prewarm=json.loads(path.read_text())
                assert prewarm['status']=='PASS' and prewarm['schema']==1 and prewarm['root']=='/testbed'
                assert type(prewarm['directories']) is int and prewarm['directories']>0 and prewarm['entries']>0
                assert type(prewarm['monotonic_raw_ns']) is int and 0<prewarm['monotonic_raw_ns']<25*10**9
                assert len(prewarm['directory_names_sha256'])==64
                observations[-1]['directory_prewarm']=prewarm
                if diskstats:
                    from guest_diskstats import parse,delta
                    observations[-1]['prewarm_diskstats']=delta(parse(prewarm['diskstats_before']),parse(prewarm['diskstats_after']))
            else:assert not path.exists()
        if open_only:
            assert (raw/'directory-profile-sha256.txt').read_text().split()==[profile_inputs['directory-profile.so'],'/workspace/directory-profile.so']
            observations[-1]['git_opendir']=open_close_records(raw/'replay',open_only=True)
        else:assert not list((raw/'replay').glob('directory-*.json'))
        if result.get('host_wait'):
            observations[-1]['host_wait_bound']=host_wait_bound(raw,freq)
            assert ('kvm_faults' in freq['host_wait'])==kvm
            if kvm:
                from owned_thread_wait import parse_kvm_faults,combined_wait_script
                parsed=parse_kvm_faults((raw/'fixed-frequency/host-wait.stdout').read_text(),[x['tid'] for x in freq['binding']])
                assert parsed==freq['host_wait']['kvm_faults']
                script=(raw/'fixed-frequency/host-wait.bt').read_text()
                assert script==combined_wait_script((directory/'workspace/scripts/trace_owned_vcpu_wait.bt').read_text(),(directory/'workspace/scripts/trace_owned_vcpu_faults.bt').read_text())
                observations[-1]['host_kvm_faults']=parsed
        if plain_wait:
            actual_hash=result['directory_cache_runner_sha256'][cache_modes[number-1]] if cache_probe else hashes['guest-runner-effective.sh']
            assert (raw/'guest-runner-sha256.txt').read_text().split()==[actual_hash,'/workspace/guest-runner.sh']
            files={p.name for p in (raw/'replay').glob('git-025-*')}
            expected_files={'git-025-clocks.json'} if clock_only else set()
            if split_commands:expected_files.update({'git-025-add-clocks.json','git-025-diff-clocks.json'})
            if bash_times:expected_files.update({'git-025-add-time.tsv','git-025-diff-time.tsv'})
            if add_performance:expected_files.add('git-025-add-performance.txt')
            if add_trace2:expected_files.add('git-025-add-events.jsonl')
            if diskstats:expected_files.update({'git-025-before.diskstats','git-025-after.diskstats'})
            if cache_state:expected_files.update({f'git-025-{phase}.{kind}' for phase in ('before','after') for kind in ('vmstat','meminfo')})
            if damon_state and damon_format=='separate':expected_files.update({f'git-025-{phase}.damon' for phase in ('before','after')})
            assert files==expected_files
        if diskstats:
            from guest_diskstats import parse,delta
            observations[-1]['guest_diskstats']=delta(parse((raw/'replay/git-025-before.diskstats').read_text()),parse((raw/'replay/git-025-after.diskstats').read_text()))
        cache_files={p.name for p in (raw/'replay').glob('cache-start.*')}
        assert cache_files==(({'cache-start.vmstat','cache-start.meminfo'}|({'cache-start.damon'} if damon_state and damon_format=='separate' else set())) if cache_state else set())
        if cache_state:
            from guest_cache_state import collect
            observations[-1]['guest_cache_state']=collect(raw/'replay',include_start=True,include_damon=damon_state)
        if clock_only:
            clock=json.loads((raw/'replay/git-025-clocks.json').read_text())
            assert clock['exit_code']==0 and clock['monotonic_raw_ns']>0
            assert all(clock[k]>=0 for k in ('user_ns','system_ns','minor_faults','major_faults','voluntary_switches','involuntary_switches'))
            observations[-1]['command_clock']=clock
            observations[-1]['realtime_minus_monotonic_ms']=(clock['realtime_ns']-clock['monotonic_raw_ns'])/1e6
            observations[-1]['outer_step_minus_command_ms']=observations[-1]['steps_ms']['025']-clock['monotonic_raw_ns']/1e6
        if split_commands:
            segments={name:json.loads((raw/f'replay/git-025-{name}-clocks.json').read_text()) for name in ('add','diff')}
            for segment in segments.values():
                assert segment['exit_code']==0 and segment['monotonic_raw_ns']>0
                assert all(segment[k]>=0 for k in ('user_ns','system_ns','minor_faults','major_faults','voluntary_switches','involuntary_switches'))
            total=sum(c['monotonic_raw_ns'] for c in segments.values())
            assert total<=clock['monotonic_raw_ns']
            observations[-1]['git_command_clocks']=segments
            observations[-1]['command_minus_segments_ms']=(clock['monotonic_raw_ns']-total)/1e6
        if bash_times:
            segments={}
            for name in ('add','diff'):
                values=[float(v) for v in (raw/f'replay/git-025-{name}-time.tsv').read_text().split()]
                assert len(values)==3 and all(math.isfinite(v) and v>=0 for v in values)
                segments[name]=dict(zip(('real_seconds','user_seconds','system_seconds'),values))
            observations[-1]['git_command_times']=segments
            observations[-1]['command_minus_segments_ms']=clock['monotonic_raw_ns']/1e6-sum(c['real_seconds']*1000 for c in segments.values())
            assert observations[-1]['command_minus_segments_ms']>=-2,'segment sum exceeds whole-command time beyond rounding tolerance'
        if add_performance:
            records=add_performance_records((raw/'replay/git-025-add-performance.txt').read_text())
            assert records['command_seconds']<=segments['add']['real_seconds']+.003
            observations[-1]['git_add_performance']=records
            observations[-1]['git_add_marker_gaps']=performance_marker_gaps(records)
        if add_trace2:
            trace=add_trace2_records((raw/'replay/git-025-add-events.jsonl').read_text(),records)
            assert trace['command_seconds']<=segments['add']['real_seconds']+.003
            observations[-1]['git_add_trace2']=trace
    differences={}
    compare=not git_diagnostic and (balanced or not plain_wait)
    for metric in (('task_window_seconds','controller_exec_seconds','guest_action_sum_seconds') if compare else ()):
        means={label:statistics.mean(x[metric] for x in observations if x['mode']==label) for label in (('control_A','control_B','off','light') if balanced else ('off','light'))}
        differences[metric]=dict(means=means,light_vs_off_percent=100*(means['light']/means['off']-1))
        if balanced:differences[metric]['control_B_vs_A_percent']=100*(means['control_B']/means['control_A']-1)
    summary=dict(status='PASS',verdict='INCONCLUSIVE_SMOKE_ONLY',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),observations=observations,differences=differences,limitations=['Four-run smoke has no same-batch off/off control or statistical confidence interval','Host service cgroup sampling only; no guest memory or VMM PSS sampling','No hardware counter or BPF diagnosis in this comparison','Policy and affinity checked at window boundaries; transient external changes cannot be excluded','Shared CPUs; symmetric positions do not eliminate arbitrary background load','Full official task tests NOT_RUN'])
    if git_diagnostic:
        summary['verdict']='DIAGNOSTIC_ONLY'
        summary['limitations'][0]='Git trace instrumentation; not monitoring overhead acceptance'
    if result.get('host_wait'):
        summary['limitations'][2]='Owned-vCPU BPF wait trace enabled; no hardware counters; whole capture only'
        summary['verdict']='DIAGNOSTIC_ONLY'
    if original_wait:
        summary['limitations'][0]='Original guest runner with host wait probe; one short diagnostic group, no monitoring acceptance'
        summary['limitations'].append('Step durations use the original guest date realtime clock; host wait uses host monotonic clock; no per-command dual-clock check in this batch')
        summary['guest_runner_sha256']=hashes
    if clock_only:
        summary['guest_runner_sha256']=hashes
        summary['clock_check']=dict(max_abs_realtime_minus_monotonic_ms=max(abs(x['realtime_minus_monotonic_ms']) for x in observations),scope='timeout/bash command wrapper only; excludes outer date and wrapper output write')
        summary['limitations'].extend(['Step 025 uses a C whole-command clock/rusage wrapper; optional segment timing is extra; no monitoring acceptance','CPU totals include worker threads; wall minus summed CPU is not exact blocked time','Fault counts are not waiting durations; host wait is only a whole-window upper bound'])
    if balanced and not git_diagnostic:
        summary['balanced_analysis']=summarize(rows)
        summary['limitations'][0]='One eight-run group; no statistical confidence interval'
        means={label:statistics.mean(x['steps_ms']['025'] for x in observations if x['mode']==label) for label in ('control_A','control_B','off','light')}
        control_delta=1000*(differences['guest_action_sum_seconds']['means']['control_A']-differences['guest_action_sum_seconds']['means']['control_B'])
        monitor_delta=1000*(differences['guest_action_sum_seconds']['means']['light']-differences['guest_action_sum_seconds']['means']['off'])
        summary['step_025_diagnostic']=dict(means_ms=means,control_A_minus_B_ms=means['control_A']-means['control_B'],light_minus_off_ms=means['light']-means['off'],control_guest_sum_difference_fraction=(means['control_A']-means['control_B'])/control_delta if control_delta else None,monitor_guest_sum_difference_fraction=(means['light']-means['off'])/monitor_delta if monitor_delta else None,cause='NOT_IDENTIFIED; original frozen git add -A and git diff --cached')
    if split_commands:
        summary['limitations'].append('Separate Git wrappers add process creation and JSON writes; calibration scope is warm synthetic workload only')
    if bash_times:
        summary['limitations'].append('Bash builtin per-Git time has 1ms precision; segment clocks are not independently monotonic-validated; whole command uses dual clocks')
    if add_performance:summary['limitations'].append('Native Git performance log only for add; timers can nest and omit intervals; no complete additive breakdown')
    if guest_cold:summary['limitations'].append('New owned guest drop_caches=1 without sync before step 025, in both conditions; clean file cache only, not all caches/slab; host cache untouched by flush; separate diagnostic changes cache semantics')
    if diskstats:summary['limitations'].append('Guest-wide block counters, includes logs/other guest activity; device and partition counters overlap; read_ms is not exact task blocking time or host physical I/O')
    if cache_probe:summary['limitations'].append('Directory prewarm changes guest and potentially host caches; time moved before frozen step 025 command, retained in step/task total; no monitoring comparison or acceleration claim')
    if open_only:summary['limitations'].append('Add-only opendir libc timing over whole add; inclusive CPU and waiting, not pure syscall or I/O; directory reads/stat/closedir not measured; thread sums may overlap')
    if add_trace2:summary['limitations'].append('Add-only Trace2 EVENT; directory region includes nested operations/logging; not pure I/O or blocking time; overlap uses realtime log boundaries')
    if baseline_add_trace:summary['limitations'].append('Initial add has additional native performance/Trace2 and guest device-wide diskstats; calibration covers their incremental warm-fixture cost only; initial add and step 025 are different file/index states')
    if cache_state:summary['limitations'].append('Guest cache endpoints are non-atomic and counters have delayed accounting; no per-file attribution or precise eviction timing')
    if kvm:summary['limitations'].append('Owned KVM counters cover the entire task capture, not step 025; TRY is not async success; synchronous faults and worker latency NOT_MEASURED')
    if damon_modes:
        summary['damon_comparison']={mode:dict(scan_ms=[r['git_add_trace2']['directory']['seconds']*1000 for r in observations if r['damon_control_mode']==mode],task_seconds=[r['task_window_seconds'] for r in observations if r['damon_control_mode']==mode]) for mode in ('default','damon_disabled')}
        summary['limitations'].append('DAMON switch in newly created guests only, before uploads; state checks outside task timing; short diagnostic comparison, not monitoring acceptance or official baseline replacement')
    if monitor_damon_mode:
        summary.update(monitor_damon_mode=monitor_damon_mode,guest_runner_sha256=hashes)
        summary['limitations'].append('Original frozen guest runner and initial index; complete preparation remains in task window; one fixed DAMON policy per group, state checks outside task timing; no cache flushing or index pre-refresh')
    (directory/'offline-report.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# 固定频率条件下的轻量采样短验证','', '| 次序 | 模式 | 完整任务窗口 (s) | guest 动作耗时合计 (s) | 样本数 |','| --- | --- | ---: | ---: | ---: |']
    for row in observations:lines.append(f"| {row['position']} | {row['mode']} | {row['task_window_seconds']:.3f} | {row['guest_action_sum_seconds']:.3f} | {row['sample_count']} |")
    if compare:lines+=['',f"开启/关闭采样的完整任务窗口均值变化为 {differences['task_window_seconds']['light_vs_off_percent']:+.2f}%。本批只是短验证，不判定 5% 目标。",'工具计时包括采样启停，排除 CPU 设置、10 秒等待、恢复及独立功能检查。每秒采样一次服务资源组，虚拟机内采样关闭。CPU 请求上下限为 2 GHz；本次未采集硬件参考周期，不能据此宣称实际频率严格恒定。','']
    if balanced and not git_diagnostic:
        fraction=summary['step_025_diagnostic']['control_guest_sum_difference_fraction']
        part=f'{100*fraction:.1f}%' if fraction is not None else '未定义（分母为零）'
        lines += [f"两组关闭采样的任务窗口均值变化为 {differences['task_window_seconds']['control_B_vs_A_percent']:+.2f}%。单组没有正式置信区间，5% 目标仍未判定。",f"第 025 步占关闭采样对照 guest 动作合计差异的 {part}。该步骤执行原始 git add -A 与 git diff --cached，具体波动原因尚未定位；不删除该步骤或事后扣除其耗时。",'']
    if original_wait:
        lines += ['原 guest runner 与上传文件哈希一致，没有 Git trace、双时钟包装或逐调用跟踪；步骤耗时仍沿用 guest date 实时时钟。本批所有运行都增加了宿主机等待探针，只用于诊断，不能验收 5% 开销。','', '| 次序 | 第 025 步 (ms) | 整段两虚拟 CPU 排队等待合计上界 (ms) |','| --- | ---: | ---: |']
        for row in observations:lines.append(f"| {row['position']} | {row['steps_ms']['025']:.2f} | {row['host_wait_bound']['summed_runnable_wait_upper_bound_ns']/1e6:.3f} |")
        lines += ['','等待合计只是第 025 步可运行排队等待的上界，不覆盖睡眠、guest I/O/锁或宿主机 I/O 服务线程；不把两线程合计当任务总时间。','']
    if clock_only:
        detail='只对 add 导出原生性能日志，无其他 Git 跟踪或 guest /proc 读取。' if add_performance else '不导出 Git trace 或增加 guest /proc 读取。'
        if add_trace2:detail='只对 add 导出原生性能日志和 Trace2 EVENT，无其他 Git 跟踪或 guest /proc 读取。'
        lines += ['第 025 步使用 C 双时钟包装，分段配置另加各 Git 命令计时；'+detail+'原外层 date 计时包含额外准备与写出，与包装器区间不同。以下均为独立诊断，不验收监控开销。','', '| 次序 | 原外层步骤 (ms) | 命令单调耗时 (ms) | 实时−单调 (ms) | user+system (ms) | 需要 I/O 的缺页次数 | 整段排队等待上界 (ms) |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for row in observations:
            c=row['command_clock']
            lines.append(f"| {row['position']} | {row['steps_ms']['025']:.2f} | {c['monotonic_raw_ns']/1e6:.2f} | {row['realtime_minus_monotonic_ms']:.6f} | {(c['user_ns']+c['system_ns'])/1e6:.2f} | {c['major_faults']} | {row['host_wait_bound']['summed_runnable_wait_upper_bound_ns']/1e6:.3f} |")
        lines += ['','命令 CPU 合计可以因多线程超过墙钟；缺页和切换次数不能替代等待耗时。','']
    if split_commands:
        lines += ['| 次序 | git add (ms) | git diff (ms) | 两命令外的包装耗时 (ms) |','| --- | ---: | ---: | ---: |']
        for row in observations:
            c=row['git_command_clocks']
            lines.append(f"| {row['position']} | {c['add']['monotonic_raw_ns']/1e6:.3f} | {c['diff']['monotonic_raw_ns']/1e6:.3f} | {row['command_minus_segments_ms']:.3f} |")
        lines += ['','分段包装只用于定位；输出写入和额外进程耗时仍包含在步骤总耗时中，不事后扣除以验收监控。','']
    if bash_times:
        lines += ['| 次序 | git add (ms) | git diff (ms) | 整步命令−分段合计 (ms) |','| --- | ---: | ---: | ---: |']
        for row in observations:
            c=row['git_command_times']
            lines.append(f"| {row['position']} | {c['add']['real_seconds']*1000:.0f} | {c['diff']['real_seconds']*1000:.0f} | {row['command_minus_segments_ms']:.3f} |")
        lines += ['','Bash 分段计时精度为 1 ms，没有分别验证分段时钟；整步命令仍有双时钟记录。仅用于定位，不验收平台监控开销。','']
    if add_performance:
        lines += ['| 次序 | Git 原生日志 add 总耗时 (ms) | 最长已记录阶段 | 该阶段耗时 (ms) | 最大日志标记间隔 (ms) |','| --- | ---: | --- | ---: | ---: |']
        for row in observations:
            g=row['git_add_performance'];stage=g['longest_recorded_stage']
            gap=max(row['git_add_marker_gaps'],key=lambda r:r['elapsed_ns'])
            lines.append(f"| {row['position']} | {g['command_seconds']*1000:.3f} | {stage['label']} | {stage['seconds']*1000:.3f} | {gap['elapsed_ns']/1e6:.3f} |")
        lines += ['','原生日志仅在 add 导出；计时可能嵌套，也可能漏掉阶段，不相加当作完整分解。标记间隔包含标记间的操作和可能的日志写出成本，不是某个操作或 I/O 的独立计时。','']
    if add_trace2:
        lines += ['| 次序 | 目录扫描 (ms) | 访问目录 / 路径数 | 最大标记间隔与扫描重合 (ms) |','| --- | ---: | --- | ---: |']
        for row in observations:
            trace=row['git_add_trace2'];d=trace['directory'];c=d['counts']
            lines.append(f"| {row['position']} | {d['seconds']*1000:.3f} | {c['directories-visited']} / {c['paths-visited']} | {trace['largest_marker_gap']['overlap_with_directory_seconds']*1000:.3f} |")
        lines += ['','目录扫描时间包含内部操作和日志成本，不是纯 I/O 或阻塞时间；阶段可能嵌套，不相加。重合使用实时时钟日志边界，未单独验证其单调时钟。','']
    if open_only:
        lines += ['| 次序 | 打开目录次数 | 打开目录总耗时 (ms) | 单次观测最大 (ms) | 失败次数 |','| --- | ---: | ---: | ---: | ---: |']
        for row in observations:
            c=row['git_opendir']['calls']['opendir']
            lines.append(f"| {row['position']} | {c['count']} | {c['total_ns']/1e6:.3f} | {c['max_ns']/1e6:.3f} | {c['null_with_errno']} |")
        lines += ['','只测整个 add 的 opendir C 库调用，包含 CPU 和等待；没有测读取目录内容、属性查询和关闭目录。多线程调用耗时可能重合，不相加作任务总耗时。','']
    if cache_probe:
        lines += ['| 次序 | 条件 | 预读目录 (ms) | 后续扫描 (ms) | 整个第 025 步 (ms) |','| --- | --- | ---: | ---: | ---: |']
        for row in observations:
            warm=row.get('directory_prewarm',{}).get('monotonic_raw_ns',0)/1e6
            lines.append(f"| {row['position']} | {row['directory_cache_mode']} | {warm:.3f} | {row['git_add_trace2']['directory']['seconds']*1000:.3f} | {row['steps_ms']['025']:.3f} |")
        lines += ['','两组都关闭监控。预读包含目录扫描，时间保留在步骤和任务总时间中；不能只根据后续扫描变快宣称任务收益，不能据此区分 guest 与 host 缓存。','']
    if cache_state:
        if runner_phases:
            lines += ['', '| 次序 | 环境准备 (ms) | 初始 Git 准备 (ms) | 动作循环 (ms) | 最终 patch (ms) | 控制器减 guest 调用 (ms) |', '| --- | ---: | ---: | ---: | ---: | ---: |']
            for row in observations:
                phases=row['runner_phases']['phase_ns']
                lines.append(f"| {row['position']} | {phases['entry']/1e6:.3f} | {phases['baseline']/1e6:.3f} | {phases['actions']/1e6:.3f} | {phases['finalize']/1e6:.3f} | {row['controller_minus_guest_envelope_ms']:.3f} |")
            lines += ['', '阶段使用 Bash 内置实时时钟，完整调用以双时钟核对；动作循环包括循环记录和采集。控制器与 guest 调用的差值还含启动、结束及未覆盖边界，不能当作纯网络耗时。', '']
            if baseline_commands:
                lines += ['', '| 次序 | 初始 Git 子操作 | 操作边界 (ms) | 命令墙钟 (ms) | 用户态 CPU (ms) | 内核态 CPU (ms) |','| --- | --- | ---: | ---: | ---: | ---: |']
                for row in observations:
                    for tag,values in row['runner_phases']['baseline_commands'].items():
                        lines.append(f"| {row['position']} | {tag} | {values['boundary_ns']/1e6:.3f} | {values['real_seconds']*1000:.0f} | {values['user_seconds']*1000:.0f} | {values['system_seconds']*1000:.0f} |")
                lines += ['', 'Bash 命令 CPU/墙钟精度为 1 ms；边界包含少量记录工作，不当作纯阻塞或 I/O 时间。Git 的工作线程 CPU 可重叠，不用墙钟减 CPU 来直接推算等待。', '']
        if baseline_add_trace:
            lines += ['', '| 次序 | 初始 add (ms) | 目录扫描 (ms) | 原生日志中最慢阶段 | 该阶段 (ms) |','| --- | ---: | ---: | --- | ---: |']
            for row in observations:
                value=row['runner_phases']['baseline_add_trace'];stage=value['performance']['longest_recorded_stage']
                lines.append(f"| {row['position']} | {value['trace2']['command_seconds']*1000:.3f} | {value['trace2']['directory']['seconds']*1000:.3f} | {stage['label']} | {stage['seconds']*1000:.3f} |")
            lines += ['', '原生阶段可能嵌套或遗漏工作，不能相加当作完整分解；初始 add 前后块设备计数覆盖该 guest 的设备活动，不是该进程专属读取，也不是磁盘等待时间。', '']
        if damon_modes:
            lines += ['', '| 次序 | DAMON 条件 | 扫描 (ms) | 整个任务 (s) |', '| --- | --- | ---: | ---: |']
            for row in observations:
                lines.append(f"| {row['position']} | {row['damon_control_mode']} | {row['git_add_trace2']['directory']['seconds']*1000:.3f} | {row['task_window_seconds']:.3f} |")
            lines += ['', '仅新建沙箱关闭 DAMON，官方默认组保持开启；重放前后检查状态，检查时间不在任务窗口内。少量诊断结果不验收 5% 监控开销。', '']
        lines += ['', '| 次序 | 扫描前可用内存 (MiB) | 扫描前文件缓存 Cached (MiB) | 步骤 001 至 025 前回收文件页 | 第 025 步回收文件页 | 第 025 步文件页重新读入计数 |', '| --- | ---: | ---: | ---: | ---: | ---: |']
        for row in observations:
            state=row['guest_cache_state'];memory=state['before']['meminfo_kib'];pre=state['before_command']['counter_deltas'];during=state['during_command']['counter_deltas']
            lines.append(f"| {row['position']} | {memory['MemAvailable']/1024:.3f} | {memory['Cached']/1024:.3f} | {pre['pgsteal_file']} | {during['pgsteal_file']} | {during['workingset_refault_file']} |")
        lines += ['', '这是整个 guest 的边界计数，不是特定文件的精确测量；计数更新可能延迟，零回收不排除手动缓存失效或模板中此前已回收的页面。', '']
    if kvm:
        lines += ['', '| 次序 | 整段页面获取尝试 | 整段异步缺页成功安排次数 |', '| --- | ---: | ---: |']
        for row in observations:
            threads=row['host_kvm_faults']['threads']
            lines.append(f"| {row['position']} | {sum(t['try_async_get_page'] for t in threads)} | {sum(t['successful_async_setup'] for t in threads)} |")
        lines += ['', '计数覆盖完整采集区间，不能当作第 025 步的精确计数；没有测同步缺页、缺页处理耗时或工作线程完成情况。', '']
    (directory/'report.md').write_text('\n'.join(lines))
    print(f"status=PASS verdict={summary['verdict']} result_dir={directory}")
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory.resolve())
