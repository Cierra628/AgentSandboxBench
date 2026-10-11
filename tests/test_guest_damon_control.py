import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from guest_damon_control import PROGRAM, PARAMETERS, parse, instrument, audit


def fields(before='Y', worker_before='64', after='N', worker_after='-1'):
    return f'kernel=6.1.175\nenabled_before={before}\nworker_before={worker_before}\nenabled_after={after}\nworker_after={worker_after}\n'


class DamonControlTests(unittest.TestCase):
    def test_default_and_disabled_states(self):
        parse(fields(after='Y', worker_after='64'), 'default', 'prepare')
        parse(fields(), 'damon_disabled', 'prepare')
        parse(fields(before='N', worker_before='-1'), 'damon_disabled', 'after_task')
        for text in (fields(worker_after='64'), fields(before='N'), fields() + 'enabled_after=N\n', fields().replace('6.1.175', '6.19.9+')):
            with self.assertRaises(AssertionError): parse(text, 'damon_disabled', 'prepare')

    def test_no_write_in_default_or_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for phase, mode, state, pid in (('prepare', 'default', 'Y', '64'), ('before_task', 'damon_disabled', 'N', '-1'), ('after_task', 'damon_disabled', 'N', '-1')):
                (root / 'enabled').write_text(state)
                (root / 'kdamond_pid').write_text(pid)
                before = (root / 'enabled').stat().st_mtime_ns
                p = subprocess.run(['bash', '-c', PROGRAM.replace(PARAMETERS, str(root)), 'control', phase, mode], capture_output=True, text=True, timeout=2)
                self.assertEqual(p.returncode, 0)
                self.assertEqual((root / 'enabled').stat().st_mtime_ns, before)

    def test_disabled_write_and_worker_timeout_is_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'enabled').write_text('Y'); (root / 'kdamond_pid').write_text('64')
            program = PROGRAM.replace(PARAMETERS, str(root)).replace('uname -r', "printf '6.1.175\\n'").replace('seq 1 50', 'seq 1 1').replace('sleep 0.1', ':')
            p = subprocess.run(['bash', '-c', program, 'control', 'prepare', 'damon_disabled'], capture_output=True, text=True, timeout=2)
            self.assertEqual((root / 'enabled').read_text(), 'N')
            with self.assertRaises(AssertionError): parse(p.stdout, 'damon_disabled', 'prepare')

    def test_receipt_requires_owned_sandbox_and_all_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp); sid = '12345678-1234-1234-1234-123456789abc'
            (raw / 'sandbox-id.txt').write_text(sid)
            for phase in ('prepare', 'before_task', 'after_task'):
                text = fields() if phase == 'prepare' else fields(before='N', worker_before='-1')
                receipt = dict(status='PASS', exit_code=0, sandbox_id=sid, mode='damon_disabled', phase=phase, stdout=text, observations=parse(text,'damon_disabled',phase),controller_elapsed_ns=100)
                (raw / f'damon-{phase}.json').write_text(json.dumps(receipt))
            self.assertEqual(len(audit(raw, 'damon_disabled')), 3)
            receipt['sandbox_id'] = '87654321-1234-1234-1234-123456789abc'
            (raw / 'damon-after_task.json').write_text(json.dumps(receipt))
            with self.assertRaises(AssertionError): audit(raw, 'damon_disabled')

    def test_control_is_before_upload_and_outside_task(self):
        text = '''aenv upload "$sandbox_id" "$workload_root/actions" /workspace/replay-actions
task_window_start_ns="$(date +%s%N)"
# Delete before rebuilding the report so the summary can confirm cleanup.
'''
        result = instrument(text, Path('/tmp/a b.py'), 'damon_disabled')
        self.assertLess(result.index('damon-prepare.json'), result.index('aenv upload'))
        self.assertLess(result.index('damon-before_task.json'), result.index('task_window_start_ns='))
        subprocess.run(['bash', '-n'], input=result, text=True, check=True, timeout=2)


if __name__ == '__main__': unittest.main()
