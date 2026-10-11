import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from fixed_runner_git_identity import instrument as fixed
from persistent_guest_sampling import instrument as persistent
from guest_runner_lifecycle import instrument,collect,calibrate,audit_calibration


class LifecycleTests(unittest.TestCase):
    def fixture(self,base):
        senior=ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke'
        (base/'lifecycle-original.sh').write_text(fixed((senior/'systems/agentenv/guest-runner.sh').read_text()))
        (base/'actions').mkdir();shutil.copy2(senior/'workloads/prettier-14400/actions/025.sh',base/'actions/025.sh')
        shutil.copy2(senior/'workloads/prettier-14400/actions.tsv',base/'actions.tsv')
        subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(ROOT/'scripts/command_clock.c'),'-o',str(base/'command-clock')],check=True)

    def test_small_fixture_clocks_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);self.fixture(base);calibrate(base)
            audit=audit_calibration(base/'lifecycle-calibration');self.assertEqual(audit['iterations'],32)
            timer=base/'lifecycle-calibration/run-04/replay/lifecycle-envelope.json'
            row=json.loads(timer.read_text());row['realtime_ns']=row['monotonic_raw_ns']=1;timer.write_text(json.dumps(row))
            with self.assertRaises(AssertionError):audit_calibration(base/'lifecycle-calibration')

    def test_persistent_collector_stops_and_failure_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);self.fixture(base);calibrate(base)
            repo=base/'lifecycle-calibration/fixture'
            source=instrument(persistent((base/'lifecycle-original.sh').read_text()),collector=True).replace('/testbed',str(repo))
            runner=base/'runner.sh';runner.write_text(source)
            env=dict(os.environ,ACTIONS_DIR=str(base/'actions'),GUEST_MEMORY_SAMPLING='1',DROP_GUEST_CACHES='0',
                MEMORY_SAMPLE_INTERVAL_SECONDS='.1',ASB_GUEST_MEMORY_COLLECTOR=str(ROOT/'scripts/guest_memory_collector.py'))
            for failed in (False,True):
                out=base/('failed' if failed else 'passed');out.mkdir()
                runenv=dict(env,OUTPUT_DIR=str(out))
                if failed:
                    bindir=base/'bin';bindir.mkdir();git=bindir/'git'
                    git.write_text('#!/bin/bash\nif [[ ${*: -2} == "reset --mixed" ]]; then echo reset-failed >&2; exit 7; fi\nexec /usr/bin/git "$@"\n');git.chmod(0o755)
                    runenv['PATH']=str(bindir)+':'+env['PATH']
                result=subprocess.run([str(base/'command-clock'),str(out/'lifecycle-envelope.json'),'bash',str(runner)],env=runenv,capture_output=True,timeout=20)
                receipt=json.loads((out/'collector-status.json').read_text());self.assertEqual(receipt['status'],'PASS')
                with self.assertRaises(ProcessLookupError):os.kill(receipt['pid'],0)
                self.assertEqual(result.returncode,7 if failed else 0)
                if failed:
                    self.assertIn(b'reset-failed',result.stderr)
                    with self.assertRaises(AssertionError):collect(out,True,0)
                else:
                    steps=list(csv.DictReader((out/'steps.tsv').read_text().splitlines(),delimiter='\t'))
                    measured=collect(out,True,sum(int(s['duration_ns']) for s in steps))
                    self.assertGreater(measured['durations_ns']['collector_startup_ns'],0)
                    self.assertGreaterEqual(measured['durations_ns']['collector_stop_ns'],0)


if __name__=='__main__':unittest.main()
