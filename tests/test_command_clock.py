import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class CommandClockTests(unittest.TestCase):
    def test_output_exit_and_sleep_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);binary=root/'clock'
            subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(ROOT/'scripts/command_clock.c'),'-o',str(binary)],check=True)
            report=root/'result.json'
            p=subprocess.run([str(binary),str(report),'bash','-c','printf marker; printf failure >&2; sleep .05; exit 7'],capture_output=True)
            self.assertEqual(p.returncode,7);self.assertEqual(p.stdout,b'marker');self.assertEqual(p.stderr,b'failure')
            r=json.loads(report.read_text());self.assertEqual(r['exit_code'],7)
            self.assertGreaterEqual(r['monotonic_raw_ns'],45000000)
            self.assertLess(abs(r['realtime_ns']-r['monotonic_raw_ns']),5000000)
            self.assertLess(r['user_ns']+r['system_ns'],r['monotonic_raw_ns'])


if __name__=='__main__':unittest.main()
