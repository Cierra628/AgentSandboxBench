import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from calibrate_trenvx_runner import audit, guest


class CalibrationTests(unittest.TestCase):
    def test_fixture_and_tampered_command_timer(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            senior = ROOT/'IncrementalDAX_moti/motivation/experiments/exp1-single-app-smoke'
            shutil.copy2(senior/'systems/agentenv/guest-runner.sh', base/'guest-runner-original.sh')
            shutil.copytree(senior/'workloads/prettier-14400/actions', base/'actions')
            shutil.copy2(senior/'workloads/prettier-14400/actions.tsv', base/'actions.tsv')
            subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(ROOT/'scripts/command_clock.c'),
                            '-o',str(base/'command-clock')], check=True)
            guest(base)
            result = audit(base/'calibration')
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['iterations'], 32)
            self.assertIsNone(result['performance_threshold'])
            timer = base/'calibration/phase-5/iteration-0/baseline-add-time.tsv'
            timer.write_text('999\t0\t0\n')
            with self.assertRaises(AssertionError):
                audit(base/'calibration')


if __name__ == '__main__':
    unittest.main()
