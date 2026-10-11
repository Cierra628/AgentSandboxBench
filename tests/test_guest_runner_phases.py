import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_runner_phases import instrument, instrument_host, instrument_baseline, BASELINE_BLOCK, collect

SOURCE = '''#!/bin/bash
set -euo pipefail
out="$1"
mkdir -p "$out/stdout" "$out/stderr"
git -C /testbed add -A
git -C /testbed write-tree > "$out/baseline-tree.txt"
for action in "$actions"/*.sh; do
  bash "$action"
done
if [[ $end_step == 026 ]]; then
  :
fi
'''


class RunnerPhaseTests(unittest.TestCase):
    def test_traced_initial_add_failure_stops_before_other_git_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source='''set -euo pipefail
out="$1"
git() { printf 'git failed\\n' >&2; return 7; }
'''+BASELINE_BLOCK+'\n'
            p=subprocess.run(['bash','-c',instrument_baseline(source,add_trace=True),'probe',str(root)],capture_output=True,text=True,timeout=5)
            self.assertEqual((p.returncode,p.stdout,p.stderr),(7,'','git failed\n'))
            self.assertTrue((root/'baseline-add-before.diskstats').exists())
            self.assertFalse((root/'baseline-add-after.diskstats').exists())
            self.assertFalse((root/'baseline-write_tree-time.tsv').exists())

    def test_initial_add_trace_is_scoped_and_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir();out=root/'out';actions=root/'actions';actions.mkdir()
            (actions/'001.sh').write_text(':\n')
            for args in (['init','-q'],['config','user.name','fixture'],['config','user.email','fixture@localhost']):
                subprocess.run(['git','-C',str(repo)]+args,check=True,capture_output=True,timeout=5)
            (repo/'file').write_text('original\n')
            subprocess.run(['git','-C',str(repo),'add','-A'],check=True,timeout=5)
            subprocess.run(['git','-C',str(repo),'commit','-qm','fixture'],check=True,timeout=5)
            (repo/'file').write_text('changed\n')
            source=SOURCE.replace('out="$1"','out="$1"; actions="$2"; end_step=026')
            source=source.replace('git -C /testbed add -A\ngit -C /testbed write-tree > "$out/baseline-tree.txt"',BASELINE_BLOCK)
            source=instrument(source,baseline_commands=True,baseline_add_trace=True).replace('/testbed',str(repo))
            source+='[[ ! -v GIT_TRACE2_EVENT && ! -v GIT_TRACE_PERFORMANCE ]]\n'
            real=time.time_ns();mono=time.monotonic_ns()
            p=subprocess.run(['bash','-c',source,'probe',str(out),str(actions)],capture_output=True,text=True,timeout=10)
            clock=dict(exit_code=p.returncode,realtime_ns=time.time_ns()-real,monotonic_raw_ns=time.monotonic_ns()-mono)
            self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
            (out/'runner-envelope.json').write_text(json.dumps(clock))
            result=collect(out,baseline_commands=True,baseline_add_trace=True,baseline_repository=repo)
            self.assertGreater(result['baseline_add_trace']['trace2']['command_seconds'],0)
            self.assertTrue(result['baseline_add_trace']['diskstats'])
            with self.assertRaises(AssertionError):collect(out,baseline_commands=True)
            path=out/'baseline-add-events.jsonl';events=[json.loads(line) for line in path.read_text().splitlines()]
            for event in events:
                if event['event']=='start':event['argv']=['git','diff']
            path.write_text('\n'.join(json.dumps(event) for event in events)+'\n')
            with self.assertRaises(AssertionError):collect(out,baseline_commands=True,baseline_add_trace=True,baseline_repository=repo)

    def test_baseline_failure_preserves_stderr_exit_and_timeformat(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            source='''set -euo pipefail
out="$1"
TIMEFORMAT=unchanged
git() { printf 'expected-error\\n' >&2; return 7; }
'''+BASELINE_BLOCK+'\n'
            source=instrument_baseline(source)
            subprocess.run(['bash','-n'],input=source,text=True,check=True,timeout=2)
            p=subprocess.run(['bash','-c',source,'probe',str(directory)],capture_output=True,text=True,timeout=2)
            self.assertEqual(p.returncode,7)
            self.assertEqual(p.stderr,'expected-error\n')
            self.assertEqual(p.stdout,'')
            self.assertFalse((directory/'baseline-write_tree-time.tsv').exists())
            success=source.replace("printf 'expected-error\\n' >&2; return 7;",':;')+'printf \'%s\\n\' "$TIMEFORMAT"\n'
            p=subprocess.run(['bash','-c',success,'probe',str(directory)],capture_output=True,text=True,timeout=2)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,'unchanged\n')

    def test_baseline_command_boundaries_and_missing_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory)
            (directory/'baseline-commands.tsv').write_text('add\t10.001100\nwrite_tree\t10.002100\nreset\t10.002300\nrev_parse\t10.002500\ndone\t10.002700\n')
            for tag in ('add','write_tree','reset','rev_parse'):(directory/f'baseline-{tag}-time.tsv').write_text('0.000\t0.000\t0.000\n')
            result=collect(directory,baseline_commands=True)
            self.assertEqual(result['baseline_commands']['add']['boundary_ns'],1000000)
            self.assertEqual(result['baseline_outside_command_boundaries_ns'],400000)
            with self.assertRaises(AssertionError):collect(directory)
            (directory/'baseline-reset-time.tsv').unlink()
            with self.assertRaises(AssertionError):collect(directory,baseline_commands=True)

    def test_action_body_and_host_target_are_preserved(self):
        source = instrument(SOURCE)
        body = SOURCE.split('for action')[1].split('done')[0]
        self.assertIn('for action' + body + 'done', source)
        subprocess.run(['bash', '-n'], input=source, text=True, check=True, timeout=2)
        self.assertIn('/workspace/command-clock /workspace/artifacts/replay/runner-envelope.json bash /workspace/guest-runner.sh', instrument_host('bash /workspace/guest-runner.sh"'))
        with self.assertRaises(AssertionError): instrument(SOURCE + SOURCE)

    def fixture(self, directory):
        (directory/'runner-phases.tsv').write_text('entry\t10.000000\nbaseline\t10.001000\nactions\t10.003000\nfinalize\t10.010000\nexit\t10.012000\n')
        (directory/'runner-envelope.json').write_text(json.dumps(dict(exit_code=0, realtime_ns=13000000, monotonic_raw_ns=13000000)))

    def test_breakdown_and_missing_or_backward_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory)
            result=collect(directory,6000000)
            self.assertEqual(result['phase_ns'],dict(entry=1000000,baseline=2000000,actions=7000000,finalize=2000000))
            self.assertEqual(result['loop_outside_action_timers_ns'],1000000)
            path=directory/'runner-phases.tsv';path.write_text(path.read_text().replace('10.010000','10.002000'))
            with self.assertRaises(AssertionError): collect(directory)
            self.fixture(directory);path.write_text(path.read_text().replace('baseline\t10.001000\n',''))
            with self.assertRaises(AssertionError): collect(directory)

    def test_clock_jump_or_failed_envelope_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory);path=directory/'runner-envelope.json'
            for clock in (dict(exit_code=1,realtime_ns=13000000,monotonic_raw_ns=13000000),dict(exit_code=0,realtime_ns=13000000,monotonic_raw_ns=9000000),dict(exit_code=0,realtime_ns=1000000,monotonic_raw_ns=1000000)):
                path.write_text(json.dumps(clock))
                with self.assertRaises(AssertionError): collect(directory)


if __name__ == '__main__': unittest.main()
