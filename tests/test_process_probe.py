"""Use an isolated temporary counter; no guest, service or project data."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]


class ProcessProbeTests(unittest.TestCase):
    def test_concurrent_reads_never_observe_truncated_counter(self):
        with tempfile.TemporaryDirectory() as name:
            directory=Path(name)/'probe'
            script=Path(name)/'probe.sh'
            source=(ROOT/'scripts/exp1-process-probe.sh').read_text()
            script.write_text(source.replace('/workspace/exp1-process-probe',str(directory)).replace('sleep 0.1','sleep 0.001'))
            try:
                first=subprocess.check_output(['bash',str(script),'start'],text=True,timeout=5)
                before=dict(line.split('=',1) for line in first.splitlines())
                samples=[];deadline=time.monotonic()+.5
                while time.monotonic()<deadline:
                    raw=(directory/'count').read_text().strip()
                    self.assertTrue(raw.isdecimal(),repr(raw))
                    samples.append(int(raw))
                after=dict(line.split('=',1) for line in subprocess.check_output(['bash',str(script)],text=True,timeout=5).splitlines())
                self.assertEqual([before[k] for k in ('pid','start_ticks','boot_id')],
                                 [after[k] for k in ('pid','start_ticks','boot_id')])
                self.assertGreater(int(after['count']),int(before['count']))
                self.assertGreater(len(samples),100)
                self.assertEqual(samples,sorted(samples))
            finally:
                if (directory/'pid').exists():
                    pid=int((directory/'pid').read_text())
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass


if __name__=='__main__':unittest.main()
