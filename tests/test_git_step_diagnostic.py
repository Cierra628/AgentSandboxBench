import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from git_step_diagnostic import instrument, instrument_clock_only,add_performance_records,performance_marker_gaps,add_trace2_records,open_close_records
from report_git_step_diagnostic import commands


class GitDiagnosticTests(unittest.TestCase):
    def run_action(self,action_text,syscalls=False,clock_only=False,step='025',split_commands=False,add_performance=False,add_trace2=False,open_close=False,open_only=False,directory_prewarm=False,guest_diskstats=False,guest_cache_state=False,guest_damon_state=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'out';(out/'stdout').mkdir(parents=True);(out/'stderr').mkdir()
            testbed=root/'testbed';testbed.mkdir()
            subprocess.run(['git','init','-q',str(testbed)],check=True)
            (testbed/'tracked').write_text('content\n')
            helper=None
            if syscalls:
                from guest_syscall_bundle import prepare
                prepare(root/'tools');helper=root/'tools/run-strace'
            if clock_only:
                helper=root/'command-clock'
                source=Path(__file__).resolve().parents[1]/'scripts/command_clock.c'
                subprocess.run(['gcc','-O2',str(source),'-o',str(helper)],check=True)
            library=None
            if open_close:
                library=root/'directory-profile.so'
                subprocess.run(['gcc','-O2','-shared','-fPIC','-DDIRECTORY_OPEN_ONLY' if open_only else '-DDIRECTORY_OPEN_CLOSE_ONLY',str(Path(__file__).resolve().parents[1]/'scripts/directory_profile.c'),'-ldl','-pthread','-o',str(library)],check=True)
            action=root/'action.sh';action.write_text(action_text)
            template='''#!/bin/bash
set -euo pipefail
out="$1"; action="$2"; testbed="$3"; timeout_seconds=5; step=025
  start_ns="$(date +%s%N)"
  set +e
  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
  set -e
  end_ns="$(date +%s%N)"
printf '%s' "$status" > "$out/status"
'''
            modified=instrument_clock_only(template,helper,split_commands=split_commands,add_performance=add_performance,add_trace2=add_trace2,open_close_library=library,directory_prewarm_helper=Path(__file__).resolve().parents[1]/'scripts/prewarm_directories.py' if directory_prewarm else None,guest_diskstats=guest_diskstats,guest_cache_state=guest_cache_state,guest_damon_state=guest_damon_state) if clock_only else instrument(template,syscall_helper=helper)
            if guest_damon_state:
                parameters=root/'damon';parameters.mkdir()
                for key,value in dict(enabled='Y',bytes_reclaim_tried_regions='64',bytes_reclaimed_regions='32',nr_reclaimed_regions='1').items():
                    (parameters/key).write_text(value+'\n')
                modified=modified.replace('/sys/module/damon_reclaim/parameters/',str(parameters)+'/')
            script=root/'runner.sh';script.write_text(modified.replace('cd /testbed','cd "$testbed"').replace(' /testbed ', ' "$testbed" ').replace('step=025','step='+step))
            subprocess.run(['bash',str(script),str(out),str(action),str(testbed)],check=True,timeout=10)
            performance=(out/'git-025-add-performance.txt').read_text() if (out/'git-025-add-performance.txt').exists() else ''
            trace2=(out/'git-025-add-events.jsonl').read_text() if (out/'git-025-add-events.jsonl').exists() else ''
            return dict(status=(out/'status').read_text(),stdout=(out/f'stdout/{step}.log').read_text(),stderr=(out/f'stderr/{step}.log').read_text(),times=(out/'git-025-time.tsv').read_text() if (out/'git-025-time.tsv').exists() else '',events=(out/'git-025-events.jsonl').read_text() if (out/'git-025-events.jsonl').exists() else '',syscall_files=len(list(out.glob('git-025-syscalls.*'))),clock=json.loads((out/'git-025-clocks.json').read_text()) if (out/'git-025-clocks.json').exists() else None,segments={name:json.loads((out/f'git-025-{name}-clocks.json').read_text()) for name in ('add','diff') if (out/f'git-025-{name}-clocks.json').exists()},bash_segments={name:[float(v) for v in (out/f'git-025-{name}-time.tsv').read_text().split()] for name in ('add','diff') if (out/f'git-025-{name}-time.tsv').exists()},diskstats_files=sorted(p.name for p in out.glob('git-025-*.diskstats')),damon_files={p.name:p.read_text() for p in out.glob('*.damon')},vmstat_files={p.name:p.read_text() for p in out.glob('*.vmstat')},cache_files=sorted(p.name for p in out.iterdir() if p.suffix in ('.vmstat','.meminfo')),performance=performance,trace2=trace2,open_close=open_close_records(out,open_only=open_only) if list(out.glob('directory-*.json')) else None,prewarm=json.loads((out/'prewarm-directories.json').read_text()) if (out/'prewarm-directories.json').exists() else None)
    def test_damon_reads_preserve_action_and_do_not_write_parameters(self):
        row=self.run_action('echo marker; exit 7\n',clock_only=True,guest_cache_state=True,guest_damon_state=True)
        self.assertEqual((row['status'],row['stdout'],row['stderr']),('7','marker\n',''))
        self.assertEqual(row['damon_files'],{})
        self.assertEqual(set(row['vmstat_files']),{'git-025-before.vmstat','git-025-after.vmstat'})
        for value in row['vmstat_files'].values():
            self.assertTrue(value.endswith('damon_enabled Y\ndamon_bytes_reclaim_tried_regions 64\ndamon_bytes_reclaimed_regions 32\ndamon_nr_reclaimed_regions 1\n'))

    def test_cache_reads_preserve_failed_action_and_step_boundary(self):
        for step in ('001','025','024'):
            row=self.run_action('echo marker; echo failure >&2; exit 7\n',clock_only=True,guest_cache_state=True,step=step)
            self.assertEqual((row['status'],row['stdout'],row['stderr']),('7','marker\n','failure\n'))
            expected=['cache-start.meminfo','cache-start.vmstat'] if step=='001' else ([f'git-025-{phase}.{kind}' for phase in ('after','before') for kind in ('meminfo','vmstat')] if step=='025' else [])
            self.assertEqual(row['cache_files'],sorted(expected))

    def test_failure_output_and_exit_preserved(self):
        row=self.run_action('echo marker; echo failure >&2; exit 7\n')
        self.assertEqual(row['status'],'7');self.assertEqual(row['stdout'],'marker\n');self.assertEqual(row['stderr'],'failure\n')
        self.assertEqual(len(row['times'].split()),3)
    def test_git_events_written_separately(self):
        row=self.run_action('git add -A && git diff --cached\n')
        self.assertEqual(row['status'],'0');self.assertEqual(row['stderr'],'')
        events=[json.loads(v) for v in row['events'].splitlines()]
        commands=[v['argv'][1:] for v in events if v['event']=='start']
        self.assertEqual(commands,[['add','-A'],['diff','--cached']])
        self.assertIn('diff --git',row['stdout'])

    def test_syscall_tracing_preserves_failed_action(self):
        row=self.run_action('echo marker; echo failure >&2; exit 7\n',syscalls=True)
        self.assertEqual(row['status'],'7');self.assertEqual(row['stdout'],'marker\n');self.assertEqual(row['stderr'],'failure\n')
        self.assertGreater(row['syscall_files'],0)

    def test_missing_command_exit_is_rejected(self):
        events=[dict(event='start',sid='a',argv=['git','add','-A']),dict(event='start',sid='b',argv=['git','diff','--cached']),dict(event='exit',sid='b',code=0,t_abs=.01)]
        with self.assertRaises(AssertionError):commands(events)
    def test_failed_git_command_is_rejected(self):
        events=[dict(event='start',sid='a',argv=['git','add','-A']),dict(event='exit',sid='a',code=1,t_abs=.01)]
        with self.assertRaises(AssertionError):commands(events)

    def test_clock_only_preserves_failure_and_does_not_add_native_trace(self):
        row=self.run_action('echo marker; echo failure >&2; exit 7\n',clock_only=True)
        self.assertEqual(row['status'],'7');self.assertEqual(row['stdout'],'marker\n');self.assertEqual(row['stderr'],'failure\n')
        self.assertEqual(row['clock']['exit_code'],7);self.assertGreater(row['clock']['monotonic_raw_ns'],0)
        self.assertEqual(row['events'],'');self.assertEqual(row['times'],'');self.assertEqual(row['syscall_files'],0)

    def test_other_step_has_no_clock_wrapper(self):
        row=self.run_action('echo other; exit 3\n',clock_only=True,step='024')
        self.assertEqual(row['status'],'3');self.assertEqual(row['stdout'],'other\n');self.assertIsNone(row['clock'])

    def test_split_git_commands_preserve_output_and_short_circuit(self):
        action='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n'
        plain=self.run_action(action,clock_only=True)
        split=self.run_action(action,clock_only=True,split_commands=True)
        self.assertEqual((split['stdout'],split['stderr'],split['status']),(plain['stdout'],plain['stderr'],plain['status']))
        self.assertEqual(set(split['segments']),{'add','diff'})
        self.assertTrue(all(c['exit_code']==0 for c in split['segments'].values()))
        failed=self.run_action('git add --invalid-option && git diff --cached\n',clock_only=True,split_commands=True)
        baseline=self.run_action('git add --invalid-option && git diff --cached\n',clock_only=True)
        self.assertEqual((failed['stdout'],failed['stderr'],failed['status']),(baseline['stdout'],baseline['stderr'],baseline['status']))
        self.assertEqual(set(failed['segments']),{'add'})
        self.assertNotEqual(failed['segments']['add']['exit_code'],0)

    def test_bash_times_preserve_stderr_failure_and_other_step(self):
        for action in ('echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n','git add --invalid-option && git diff --cached\n'):
            plain=self.run_action(action,clock_only=True)
            split=self.run_action(action,clock_only=True,split_commands='bash')
            self.assertEqual((split['stdout'],split['stderr'],split['status']),(plain['stdout'],plain['stderr'],plain['status']))
            self.assertEqual(set(split['bash_segments']),{'add','diff'} if split['status']=='0' else {'add'})
            self.assertTrue(all(len(v)==3 for v in split['bash_segments'].values()))
        other=self.run_action('git add -A\n',clock_only=True,split_commands='bash',step='024')
        self.assertEqual(other['status'],'0');self.assertEqual(other['bash_segments'],{})

    def test_native_add_performance_keeps_output_and_does_not_trace_diff(self):
        action='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n'
        baseline=self.run_action(action,clock_only=True,split_commands='bash')
        row=self.run_action(action,clock_only=True,split_commands='bash',add_performance=True)
        self.assertEqual((row['stdout'],row['stderr'],row['status']),(baseline['stdout'],baseline['stderr'],baseline['status']))
        records=add_performance_records(row['performance'])
        self.assertEqual([r['label'] for r in records['records'] if r['label'].startswith('git command:')],['git command: git add -A'])
        self.assertEqual(row['events'],'')
        other=self.run_action(action,clock_only=True,split_commands='bash',add_performance=True,step='024')
        self.assertEqual(other['performance'],'')
        failed='git add --invalid-option && git diff --cached\n'
        baseline=self.run_action(failed,clock_only=True,split_commands='bash')
        row=self.run_action(failed,clock_only=True,split_commands='bash',add_performance=True)
        self.assertEqual((row['stdout'],row['stderr'],row['status']),(baseline['stdout'],baseline['stderr'],baseline['status']))
        self.assertEqual(set(row['bash_segments']),{'add'})

    def test_native_performance_rejects_missing_or_other_command(self):
        with self.assertRaises(AssertionError):add_performance_records('')
        with self.assertRaises(AssertionError):add_performance_records('01:02:03.000001 trace.c:1 performance: 0.100000 s: git command: git diff --cached\n')

    def test_native_marker_gap_handles_midnight_without_calling_it_io(self):
        p=dict(command_seconds=.2,records=[dict(timestamp='23:59:59.950000',label='first'),dict(timestamp='00:00:00.050000',label='second')])
        self.assertEqual(performance_marker_gaps(p),[dict(before='first',after='second',elapsed_ns=100000000)])
        p['command_seconds']=.05
        with self.assertRaises(AssertionError):performance_marker_gaps(p)

    def test_add_only_trace2_preserves_semantics_and_pairs_directory(self):
        action='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n'
        baseline=self.run_action(action,clock_only=True,split_commands='bash',add_performance=True)
        row=self.run_action(action,clock_only=True,split_commands='bash',add_performance=True,add_trace2=True)
        self.assertEqual((row['stdout'],row['stderr'],row['status']),(baseline['stdout'],baseline['stderr'],baseline['status']))
        performance=add_performance_records(row['performance']);trace=add_trace2_records(row['trace2'],performance)
        self.assertGreaterEqual(trace['directory']['counts']['paths-visited'],1)
        events=[json.loads(line) for line in row['trace2'].splitlines()]
        self.assertEqual([e['argv'] for e in events if e['event']=='start'],[['git','add','-A']])
        for selected in ([e for e in events if not(e['event']=='region_leave' and e.get('label')=='read_directory')],[dict(e,code=1) if e['event']=='exit' else e for e in events]):
            with self.assertRaises(AssertionError):add_trace2_records('\n'.join(json.dumps(e) for e in selected),performance)
        failed='git add --invalid-option && git diff --cached\n'
        before=self.run_action(failed,clock_only=True,split_commands='bash',add_performance=True)
        after=self.run_action(failed,clock_only=True,split_commands='bash',add_performance=True,add_trace2=True)
        self.assertEqual((after['stdout'],after['stderr'],after['status']),(before['stdout'],before['stderr'],before['status']))
        other=self.run_action(action,clock_only=True,split_commands='bash',add_performance=True,add_trace2=True,step='024')
        self.assertEqual(other['trace2'],'')

    def test_open_only_add_scope_output_and_failure(self):
        self.check_directory_scope(True)

    def test_open_close_add_scope_output_and_failure(self):
        self.check_directory_scope(False)

    def check_directory_scope(self, open_only):
        action='git add -A && git diff --cached; test -z "${LD_PRELOAD+x}"; test -z "${DIRECTORY_PROFILE_DIR+x}"\n'
        flags=dict(clock_only=True,split_commands='bash',add_performance=True,add_trace2=True)
        baseline=self.run_action(action,**flags)
        row=self.run_action(action,open_close=True,open_only=open_only,**flags)
        self.assertEqual((row['stdout'],row['stderr'],row['status']), (baseline['stdout'],baseline['stderr'],baseline['status']))
        self.assertEqual(set(row['open_close']['calls']), {'opendir'} if open_only else {'opendir','closedir'})
        other=self.run_action(action,open_close=True,open_only=open_only,step='024',**flags)
        self.assertIsNone(other['open_close'])
        failed='git add --invalid-option && git diff --cached\n'
        before=self.run_action(failed,**flags)
        after=self.run_action(failed,open_close=True,open_only=open_only,**flags)
        self.assertEqual((after['stdout'],after['stderr'],after['status']), (before['stdout'],before['stderr'],before['status']))

    def test_open_close_missing_or_incomplete_summary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(AssertionError):open_close_records(root)
            (root/'directory-1.json').write_text(json.dumps(dict(schema=1,pid=1,calls={})))
            with self.assertRaises(AssertionError):open_close_records(root)

    def test_directory_prewarm_preserves_original_action_output(self):
        action='echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached\n'
        flags=dict(clock_only=True,split_commands='bash',add_performance=True,add_trace2=True)
        before=self.run_action(action,**flags)
        after=self.run_action(action,directory_prewarm=True,**flags)
        self.assertEqual((after['stdout'],after['stderr'],after['status']), (before['stdout'],before['stderr'],before['status']))
        self.assertEqual(after['prewarm']['status'],'PASS')
        other=self.run_action(action,directory_prewarm=True,step='024',**flags)
        self.assertIsNone(other['prewarm'])

    def test_diskstats_endpoints_preserve_failure_exit_and_output(self):
        action='echo marker; echo failed >&2; exit 7\n'
        before=self.run_action(action,clock_only=True)
        after=self.run_action(action,clock_only=True,guest_diskstats=True)
        self.assertEqual((after['stdout'],after['stderr'],after['status']), (before['stdout'],before['stderr'],before['status']))
        self.assertEqual(after['diskstats_files'],['git-025-after.diskstats','git-025-before.diskstats'])


if __name__=='__main__':unittest.main()
