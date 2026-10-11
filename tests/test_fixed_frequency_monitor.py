import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_fixed_frequency_monitor_smoke import instrument
from report_fixed_frequency_monitor import report
from git_step_diagnostic import instrument_clock_only


class WindowGateTests(unittest.TestCase):
    def run_case(self,fail,host_wait=False):
        with tempfile.TemporaryDirectory(prefix='frequency gate ') as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir()
            helper=root/'helper.py'
            helper.write_text('''import sys,time
from pathlib import Path
p=Path(sys.argv[2]);p.mkdir()
if sys.argv[4]=='fail':sys.exit(1)
(p/'ready.json').write_text('{}')
while not (p/'stop').exists():time.sleep(.01)
(p/'done').write_text('ok')
''')
            template='''#!/bin/bash
set -euo pipefail
raw_dir="$1"
sandbox_id=test
exec_status=0
cleanup_runtime() { :; }
aenv() { :; }
task_window_start_ns="$(date +%s%N)"
printf ran > "$raw_dir/action-ran"
printf '%s\\n' "$exec_status" > "$raw_dir/replay-command-exit-code.txt"
# Delete before rebuilding the report so the summary can confirm cleanup.
'''
            script=root/'runner.sh';script.write_text(instrument(template,helper,root,'fail' if fail else 'ok',root/'checker',host_wait=host_wait))
            subprocess.run(['bash','-n',str(script)],check=True)
            p=subprocess.run(['bash',str(script),str(raw)],capture_output=True,timeout=5)
            if host_wait and not fail:
                start=int((raw/'host-wait-task-start-monotonic-ns.txt').read_text())
                end=int((raw/'host-wait-task-end-monotonic-ns.txt').read_text())
                assert start<end
            return p.returncode,(raw/'action-ran').exists(),(raw/'fixed-frequency/done').exists()
    def test_failed_preparation_blocks_task(self):
        code,ran,done=self.run_case(True)
        self.assertNotEqual(code,0);self.assertFalse(ran);self.assertFalse(done)
    def test_ready_then_task_then_stop(self):
        code,ran,done=self.run_case(False)
        self.assertEqual(code,0);self.assertTrue(ran);self.assertTrue(done)
    def test_host_only_gate_and_monotonic_markers(self):
        code,ran,done=self.run_case(False,host_wait=True)
        self.assertEqual(code,0);self.assertTrue(ran);self.assertTrue(done)


class OriginalWaitReportTests(unittest.TestCase):
    def test_fixed_damon_monitor_report_checks_receipts_and_unchanged_runner(self):
        from guest_damon_control import instrument as instrument_damon,parse
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base)
            result=json.loads((base/'result.json').read_text());original=result['runs'][0]
            result.update(host_wait=False,monitor_damon_mode='damon_disabled',damon_control_modes=[],
                          order=['off','light','light','off'],runs=[])
            helper=base/'guest_damon_control.py';helper.write_bytes((Path(__file__).resolve().parents[1]/'scripts/guest_damon_control.py').read_bytes())
            result['damon_control_source_sha256']=hashlib.sha256(helper.read_bytes()).hexdigest()
            template='aenv upload "$sandbox_id" "$workload_root/actions" /workspace/replay-actions\ntask_window_start_ns="$(date +%s%N)"\n# Delete before rebuilding the report so the summary can confirm cleanup.\n'
            (base/'runner-fixed.sh').write_text(template);result['damon_host_runner_sha256']={}
            for mode in ('default','damon_disabled'):
                p=base/f'runner-{mode}.sh';p.write_text(instrument_damon(template,helper,mode))
                result['damon_host_runner_sha256'][mode]=hashlib.sha256(p.read_bytes()).hexdigest()
            for n,label in enumerate(result['order'],1):
                target=base/f'raw-{n}';shutil.copytree(raw,target)
                audit_dir=base/f'audit-{n}';shutil.copytree(base/'audit',audit_dir)
                row=json.loads(json.dumps(original));row.update(position=n,label=label,raw_dir=str(target),audit_dir=str(audit_dir),damon_control_mode='damon_disabled',damon_control={})
                sid=f'12345678-1234-1234-1234-{n:012d}';(target/'sandbox-id.txt').write_text(sid)
                for phase in ('prepare','before_task','after_task'):
                    text='kernel=6.1.175\nenabled_before='+('Y' if phase=='prepare' else 'N')+'\nworker_before='+('64' if phase=='prepare' else '-1')+'\nenabled_after=N\nworker_after=-1\n'
                    receipt=dict(status='PASS',exit_code=0,sandbox_id=sid,mode='damon_disabled',phase=phase,stdout=text,observations=parse(text,'damon_disabled',phase),controller_elapsed_ns=100)
                    (target/f'damon-{phase}.json').write_text(json.dumps(receipt));row['damon_control'][phase]=receipt
                (target/'damon-host-runner-sha256.txt').write_text(result['damon_host_runner_sha256']['damon_disabled']+'  runner\n')
                stdout=target/'replay/stdout';stdout.mkdir();(stdout/'025.log').write_text('fixture output\n')
                (audit_dir/'effective-config.json').write_text(json.dumps(dict(damon_control_mode='damon_disabled',sandbox_memory_mib=4096,sandbox_cpu=2,drop_guest_caches=0)))
                if label=='light':
                    row['sample_count']=2;row['checks']['host_sampling']='PASS'
                    (target/'host').mkdir();(target/'host/memory-samples.tsv').write_text('sample\n1\n2\n')
                    (audit_dir/'checks.json').write_text(json.dumps(row['checks']))
                result['runs'].append(row)
            (base/'result.json').write_text(json.dumps(result))
            with patch('report_fixed_frequency_monitor.PATCH',digest),patch('report_fixed_frequency_monitor.ORIGINAL_GUEST_SHA256',result['guest_runner_sha256']['guest-runner-original.sh']),patch('report_fixed_frequency_monitor.STEP_025_STDOUT_SHA256',hashlib.sha256(b'fixture output\n').hexdigest()):
                observed=report(base)
                self.assertEqual(observed['monitor_damon_mode'],'damon_disabled')
                self.assertEqual(observed['differences']['task_window_seconds']['light_vs_off_percent'],0)
                (base/'guest-runner-effective.sh').write_text('changed')
                with self.assertRaises(AssertionError):report(base)
                (base/'guest-runner-effective.sh').write_bytes((base/'guest-runner-original.sh').read_bytes())
                (base/'raw-2/damon-after_task.json').unlink()
                with self.assertRaises(FileNotFoundError):report(base)

    def fixture(self,base,clock_only=False,split_commands=False,bash_times=False,add_performance=False,add_trace2=False,open_only=False,cache_probe=False,guest_cold=False,cache_state=False):
        raw=base/'raw';audit=base/'audit';replay=raw/'replay';freq_dir=raw/'fixed-frequency'
        for p in (audit,replay,freq_dir/'frequency-policy'):p.mkdir(parents=True)
        def save(path,value):path.write_text(json.dumps(value))
        script=b'original guest runner\n';digest=hashlib.sha256(script).hexdigest()
        if clock_only:
            script=b'''  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?''';digest=hashlib.sha256(script).hexdigest()
        for name in ('guest-runner-original.sh','guest-runner-effective.sh'):(base/name).write_bytes(script)
        effective_digest=digest
        if clock_only:
            effective=instrument_clock_only(script.decode(),'/workspace/command-clock',split_commands='bash' if bash_times else split_commands,add_performance=add_performance,add_trace2=add_trace2,open_close_library='/workspace/directory-profile.so' if open_only else None,guest_diskstats=guest_cold,guest_drop_file_cache=guest_cold,guest_cache_state=cache_state)
            (base/'guest-runner-effective.sh').write_text(effective);effective_digest=hashlib.sha256(effective.encode()).hexdigest()
            save(replay/'git-025-clocks.json',dict(exit_code=0,monotonic_raw_ns=5000000,realtime_ns=5000200,user_ns=1000000,system_ns=1000000,minor_faults=1,major_faults=0,voluntary_switches=1,involuntary_switches=0))
            if split_commands:
                for name in ('add','diff'):save(replay/f'git-025-{name}-clocks.json',dict(exit_code=0,monotonic_raw_ns=1000000,realtime_ns=1000100,user_ns=100000,system_ns=100000,minor_faults=1,major_faults=0,voluntary_switches=1,involuntary_switches=0))
            if bash_times:
                for name in ('add','diff'):(replay/f'git-025-{name}-time.tsv').write_text('0.001\t0.000\t0.000\n')
            if add_performance:
                (replay/'git-025-add-performance.txt').write_text('01:02:03.000001 read-cache.c:1 performance: 0.000100 s: read cache .git/index\n01:02:03.000802 trace.c:1 performance: 0.000800 s: git command: git add -A\n')
            if add_trace2:
                common=dict(sid='fixture',thread='main')
                events=[dict(common,event='start',argv=['git','add','-A']),dict(common,event='region_enter',category='dir',label='read_directory',nesting=1,time='2026-09-30T01:02:03.000050Z'),dict(common,event='data',category='read_directory',key='directories-visited',value='1'),dict(common,event='data',category='read_directory',key='paths-visited',value='2'),dict(common,event='region_leave',category='dir',label='read_directory',nesting=1,time='2026-09-30T01:02:03.000150Z',t_rel=.0001),dict(common,event='exit',code=0,t_abs=.0008)]
                (replay/'git-025-add-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        (raw/'guest-runner-sha256.txt').write_text(effective_digest+'  /workspace/guest-runner.sh\n')
        patch_digest=hashlib.sha256(b'fixture-patch').hexdigest();(replay/'patch.diff').write_bytes(b'fixture-patch')
        lines=['step\texit_code\tduration_ns']+[f'{i:03}\t{127 if i in (8,16) else 0}\t10000000' for i in range(1,27)]
        (replay/'steps.tsv').write_text('\n'.join(lines)+'\n')
        (raw/'task-window-duration-ns.txt').write_text('1000000000');(raw/'controller-exec-duration-ns.txt').write_text('900000000')
        checks={k:'PASS' for k in ('input_hashes','runner','replay_correctness','cleanup','task_window')};checks.update(host_sampling='DISABLED',guest_sampling='DISABLED');save(audit/'checks.json',checks)
        receipt=dict(status='PASS',reason='controller_finally',after={str(c):dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil') for c in (2,4,66,68)})
        wait=dict(status='PASS',ready_monotonic_ns=100,stop_requested_monotonic_ns=500,threads=[dict(tid=11,runnable_wait_ns=7,observed_switchins=2),dict(tid=22,runnable_wait_ns=9,observed_switchins=3)])
        frequency=dict(status='PASS',restoration=receipt,binding=[dict(tid=11,cpu=2),dict(tid=22,cpu=4)],host_wait=wait,host_wait_initial_state=[dict(tid=11,state='S',observed_monotonic_ns=150),dict(tid=22,state='S',observed_monotonic_ns=160)])
        save(freq_dir/'result.json',frequency);save(freq_dir/'host-wait.json',wait);save(freq_dir/'frequency-policy/restored.json',receipt)
        (raw/'host-wait-task-start-monotonic-ns.txt').write_text('200');(raw/'host-wait-task-end-monotonic-ns.txt').write_text('400')
        independent={k:dict(javascript_formatted=True,idempotent=True) for k in ('svg','nested_svg','html_control')};save(raw/'independent.json',independent)
        row=dict(position=1,label='off',raw_dir=str(raw),audit_dir=str(audit),checks=checks,patch_sha256=patch_digest,task_window_ns=1000000000,controller_exec_ns=900000000,guest_action_sum_ns=260000000,frequency=frequency,independent=independent,sample_count=0,observed_min_free_bytes=40*1024**3)
        result=dict(status='PASS',balanced=False,git_diagnostic=False,git_clock=False,git_syscalls=False,git_clock_only=clock_only,git_command_clocks=split_commands,git_command_times=bash_times,git_add_performance=add_performance,git_add_trace2=add_trace2,git_open_only=open_only,host_wait=True,order=['off'],runs=[row],guest_runner_sha256={'guest-runner-original.sh':digest,'guest-runner-effective.sh':effective_digest})
        if open_only:
            (base/'directory_profile.c').write_text('fixture source')
            (base/'directory-profile.so').write_bytes(b'fixture binary')
            hashes={name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in ('directory_profile.c','directory-profile.so')}
            save(base/'directory-profile-inputs.json',hashes)
            (raw/'directory-profile-sha256.txt').write_text(hashes['directory-profile.so']+'  /workspace/directory-profile.so\n')
            save(replay/'directory-123.json',dict(schema=1,pid=123,calls=dict(opendir=dict(count=2,total_ns=100,max_ns=60,null_with_errno=0,null_without_errno=0))))
        if cache_probe:
            source=base/'prewarm_directories.py';source.write_text('fixture helper')
            source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
            result.update(directory_cache_probe=True,directory_cache_modes=['prewarm'],directory_prewarm_source_sha256=source_hash)
            hashes={}
            for mode in ('control','prewarm'):
                text=instrument_clock_only((base/'guest-runner-original.sh').read_text(),'/workspace/command-clock',split_commands='bash',add_performance=add_performance,add_trace2=add_trace2,open_close_library='/workspace/directory-profile.so' if open_only else None,directory_prewarm_helper='/workspace/prewarm_directories.py' if mode=='prewarm' else None,guest_diskstats=guest_cold,guest_drop_file_cache=guest_cold,guest_cache_state=cache_state)
                path=base/f'guest-runner-{mode}.sh';path.write_text(text);hashes[mode]=hashlib.sha256(path.read_bytes()).hexdigest()
            result['directory_cache_runner_sha256']=hashes
            row.update(directory_cache_mode='prewarm',guest_runner_sha256=hashes['prewarm'])
            (raw/'guest-runner-sha256.txt').write_text(hashes['prewarm']+'  /workspace/guest-runner.sh\n')
            (raw/'directory-prewarm-source-sha256.txt').write_text(source_hash+'  /workspace/prewarm_directories.py\n')
            save(replay/'prewarm-directories.json',dict(schema=1,status='PASS',root='/testbed',directories=2,entries=3,monotonic_raw_ns=100,directory_names_sha256='a'*64))
        if guest_cold:
            assert cache_probe
            result.update(guest_file_cache_cold=True,guest_diskstats=True)
            before='254 16 vdb 10 0 20 3 4 0 8 2 0 5 6\n'
            after='254 16 vdb 12 0 36 5 4 0 8 2 0 7 8\n'
            (replay/'git-025-before.diskstats').write_text(before)
            (replay/'git-025-after.diskstats').write_text(after)
            (replay/'guest-file-cache-policy.txt').write_text('drop_caches=1 no_sync guest_only\n')
            path=replay/'prewarm-directories.json';value=json.loads(path.read_text())
            value.update(diskstats_before=before,diskstats_after=after);save(path,value)
        if cache_state:
            from guest_cache_state import GAUGES,COUNTERS,MEMINFO
            result['guest_cache_state']=True
            helper=base/'guest_cache_state.py';helper.write_text('fixture parser')
            result['guest_cache_parser_sha256']=hashlib.sha256(helper.read_bytes()).hexdigest()
            for prefix in ('cache-start','git-025-before','git-025-after'):
                (replay/(prefix+'.vmstat')).write_text(''.join(f'{k} {23 if prefix=="git-025-after" and k=="workingset_refault_file" else 20}\n' for k in GAUGES+COUNTERS))
                (replay/(prefix+'.meminfo')).write_text(''.join(f'{k}: 100 kB\n' for k in MEMINFO))
        save(base/'result.json',result)
        return patch_digest,raw

    def test_cache_report_requires_complete_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True,add_trace2=True,cache_state=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                observed=report(base)['observations'][0]['guest_cache_state']
                self.assertEqual(observed['during_command']['counter_deltas']['workingset_refault_file'],3)
                endpoint=raw/'replay/git-025-after.vmstat';before=endpoint.read_text()
                endpoint.write_text(before.replace('workingset_refault_file 23','workingset_refault_file 19'))
                with self.assertRaises(AssertionError):report(base)
                endpoint.write_text(before)
                (raw/'replay/cache-start.meminfo').unlink()
                with self.assertRaises(AssertionError):report(base)

    def test_kvm_report_checks_raw_counters_and_probe_source(self):
        from owned_thread_wait import parse_kvm_faults,combined_wait_script
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base)
            scripts=Path(__file__).resolve().parents[1]/'scripts'
            copied=base/'workspace/scripts';copied.mkdir(parents=True)
            names=('owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt','fixed_frequency_window.py')
            for name in names:(copied/name).write_bytes((scripts/name).read_bytes())
            (base/'host-probe-inputs.json').write_text(json.dumps({name:hashlib.sha256((copied/name).read_bytes()).hexdigest() for name in names}))
            stdout='KVM 11 5 3 3 2 0\nKVM 22 0 0 0 0 0\n'
            probe=raw/'fixed-frequency/host-wait'
            probe.with_suffix('.stdout').write_text(stdout)
            probe.with_suffix('.bt').write_text(combined_wait_script((copied/'trace_owned_vcpu_wait.bt').read_text(),(copied/'trace_owned_vcpu_faults.bt').read_text()))
            result=json.loads((base/'result.json').read_text());result['host_kvm_faults']=True
            freq=result['runs'][0]['frequency'];freq['host_wait']['kvm_faults']=parse_kvm_faults(stdout,[11,22])
            (raw/'fixed-frequency/result.json').write_text(json.dumps(freq))
            probe.with_suffix('.json').write_text(json.dumps(freq['host_wait']))
            (base/'result.json').write_text(json.dumps(result))
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                observed=report(base)['observations'][0]['host_kvm_faults']['threads']
                self.assertEqual(observed[0]['successful_async_setup'],2)
                probe.with_suffix('.stdout').write_text('KVM 11 5 3 3 2 0\n')
                with self.assertRaises(AssertionError):report(base)
                probe.with_suffix('.stdout').write_text(stdout)
                probe.with_suffix('.bt').write_text('changed probe')
                with self.assertRaises(AssertionError):report(base)

    def test_one_off_smoke_is_diagnostic_without_light_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,_=self.fixture(base)
            with patch('report_fixed_frequency_monitor.PATCH',digest):r=report(base)
            self.assertEqual(r['verdict'],'DIAGNOSTIC_ONLY');self.assertEqual(r['differences'],{})
            self.assertEqual(r['observations'][0]['host_wait_bound']['summed_runnable_wait_upper_bound_ns'],16)

    def test_changed_guest_script_or_extra_git_trace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                (base/'guest-runner-effective.sh').write_text('changed')
                with self.assertRaises(AssertionError):report(base)
                (base/'guest-runner-effective.sh').write_bytes((base/'guest-runner-original.sh').read_bytes())
                (raw/'replay/git-025-events.jsonl').write_text('{}\n')
                with self.assertRaises(AssertionError):report(base)

    def test_clock_only_report_checks_script_and_missing_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['verdict'],'DIAGNOSTIC_ONLY')
                self.assertAlmostEqual(r['clock_check']['max_abs_realtime_minus_monotonic_ms'],.0002)
                (raw/'replay/git-025-clocks.json').unlink()
                with self.assertRaises(AssertionError):report(base)

    def test_split_report_rejects_missing_or_failed_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,split_commands=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(set(r['observations'][0]['git_command_clocks']),{'add','diff'})
                self.assertEqual(r['observations'][0]['command_minus_segments_ms'],3)
                path=raw/'replay/git-025-add-clocks.json';original=path.read_text()
                data=json.loads(original);data['exit_code']=1;path.write_text(json.dumps(data))
                with self.assertRaises(AssertionError):report(base)
                path.write_text(original);(raw/'replay/git-025-diff-clocks.json').unlink()
                with self.assertRaises(AssertionError):report(base)

    def test_bash_report_rejects_missing_or_invalid_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['git_command_times']['add']['real_seconds'],.001)
                path=raw/'replay/git-025-add-time.tsv';original=path.read_text()
                for invalid in ('nan\t0\t0\n','1\t0\t0\n','0.001\t0\n'):
                    path.write_text(invalid)
                    with self.assertRaises(AssertionError):report(base)
                path.write_text(original);(raw/'replay/git-025-diff-time.tsv').unlink()
                with self.assertRaises(AssertionError):report(base)

    def test_add_native_report_rejects_missing_or_unexpected_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['git_add_performance']['command_seconds'],.0008)
                path=raw/'replay/git-025-add-performance.txt';original=path.read_text()
                path.write_text(original.replace('git add -A','git diff --cached'))
                with self.assertRaises(AssertionError):report(base)
                path.unlink()
                with self.assertRaises(AssertionError):report(base)

    def test_add_trace2_report_rejects_missing_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True,add_trace2=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['git_add_trace2']['directory']['seconds'],.0001)
                path=raw/'replay/git-025-add-events.jsonl'
                events=[json.loads(line) for line in path.read_text().splitlines()]
                path.write_text('\n'.join(json.dumps(e) for e in events if e['event']!='region_leave'))
                with self.assertRaises(AssertionError):report(base)
                path.unlink()
                with self.assertRaises(AssertionError):report(base)


    def test_open_only_report_rejects_missing_record_or_wrong_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True,add_trace2=True,open_only=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['git_opendir']['calls']['opendir']['count'],2)
                path=raw/'replay/directory-123.json';original=path.read_text();path.unlink()
                with self.assertRaises(AssertionError):report(base)
                path.write_text(original)
                (raw/'directory-profile-sha256.txt').write_text('wrong  /workspace/directory-profile.so\n')
                with self.assertRaises(AssertionError):report(base)

    def test_directory_cache_report_rejects_missing_prewarm_or_changed_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True,add_trace2=True,open_only=True,cache_probe=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['directory_cache_mode'],'prewarm')
                path=raw/'replay/prewarm-directories.json';original=path.read_text()
                path.unlink()
                with self.assertRaises(FileNotFoundError):report(base)
                path.write_text(original)
                (raw/'guest-runner-sha256.txt').write_text('wrong  /workspace/guest-runner.sh\n')
                with self.assertRaises(AssertionError):report(base)

    def test_guest_cold_report_requires_actual_cache_policy_evidence(self):
        # Only render scripts and validate files; never execute a host cache write.
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);digest,raw=self.fixture(base,clock_only=True,bash_times=True,add_performance=True,add_trace2=True,cache_probe=True,guest_cold=True)
            with patch('report_fixed_frequency_monitor.PATCH',digest):
                r=report(base)
                self.assertEqual(r['observations'][0]['guest_diskstats']['vdb']['reads_completed'],2)
                policy=raw/'replay/guest-file-cache-policy.txt';policy.write_text('drop_caches=3\n')
                with self.assertRaises(AssertionError):report(base)

if __name__=='__main__':unittest.main()
