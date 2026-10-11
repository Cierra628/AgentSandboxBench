import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from diagnose_exp1_cpu_variance import instrument,proc_stat


class CpuDiagnosticTests(unittest.TestCase):
    def test_stat_command_with_spaces_and_parentheses(self):
        fields=['0']*50
        fields[11]='12';fields[12]='8';fields[19]='456';fields[36]='9'
        self.assertEqual(proc_stat('123 (name (worker)) '+' '.join(fields)),
                         dict(user_ticks=12,system_ticks=8,start_ticks=456,cpu=9))

    def test_unknown_runner_rejected(self):
        with self.assertRaises(AssertionError): instrument('echo different')

    def test_failure_stdout_stderr_preserved(self):
        original='''  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?'''
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'stdout').mkdir();(root/'stderr').mkdir()
            (root/'action.sh').write_text('echo output\necho error >&2\nexit 127\n')
            script='out="$CASE_DIR"; action="$CASE_DIR/action.sh"; step=023; timeout_seconds=5\n'
            script+=instrument(original).replace('cd /testbed','cd "$CASE_DIR"')+'\nexit "$status"\n'
            result=subprocess.run(['bash','-c',script],env=dict(os.environ,CASE_DIR=temporary),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,127)
            self.assertEqual((root/'stdout/023.log').read_text(),'output\n')
            self.assertEqual((root/'stderr/023.log').read_text(),'error\n')
            self.assertEqual(len(list(map(float,(root/'cpu-023-time.tsv').read_text().split()))),3)


if __name__=='__main__':unittest.main()
