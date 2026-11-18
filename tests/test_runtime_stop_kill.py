"""Owned fake processes only; no sandbox, service, or real signals."""
from contextlib import ExitStack, redirect_stderr
import io
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import sandbox_runtime_monitor as monitor


class StopKillTests(unittest.TestCase):
    def run_timeout(self, *, remains=False, signal_error=None, changed=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            directory = Path(temp)
            identity = dict(pid=987654321, start_ticks='42', boot_id='test-boot')
            (directory / 'identity.json').write_text(json.dumps(identity))
            # A stale PASS must never make forced reclamation pass sampling.
            (directory / 'collector-status.json').write_text('{"status":"PASS"}')
            clock = [0.0]
            alive = [True]
            def sleep(seconds): clock[0] += seconds
            def send(fd, sig):
                self.assertEqual((fd, sig), (79, signal.SIGKILL))
                if signal_error: raise signal_error
                if not remains: alive[0] = False
            def state(_):
                return dict(state='S' if alive[0] else 'ABSENT', target_disappeared=not alive[0])
            stat = Path('/proc/987654321/stat')
            cmdline = stat.with_name('cmdline')
            original_read, original_exists, original_bytes = Path.read_text, Path.exists, Path.read_bytes
            def read(path, *args, **kwargs):
                if path == stat: return '987654321 (owned collector) S 0'
                return original_read(path, *args, **kwargs)
            def exists(path): return True if path == stat else original_exists(path)
            def byte_content(path):
                if path == cmdline: return b'python3\0collect\0' + str(directory).encode() + b'\0'
                return original_bytes(path)
            lookup = Mock(side_effect=[identity, dict(identity, start_ticks='999')] if changed else None,
                          return_value=identity)
            stack.enter_context(patch.object(monitor, 'process_identity', lookup))
            stack.enter_context(patch.object(monitor, 'collector_process_state', side_effect=state))
            stack.enter_context(patch.object(Path, 'read_text', read))
            stack.enter_context(patch.object(Path, 'exists', exists))
            stack.enter_context(patch.object(Path, 'read_bytes', byte_content))
            stack.enter_context(patch.object(monitor.time, 'monotonic', side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(monitor.time, 'sleep', side_effect=sleep))
            term = stack.enter_context(patch.object(monitor.os, 'kill'))
            opened = stack.enter_context(patch.object(monitor.os, 'pidfd_open', return_value=79))
            kill = stack.enter_context(patch.object(monitor.signal, 'pidfd_send_signal', side_effect=send))
            closed = stack.enter_context(patch.object(monitor.os, 'close'))
            stderr = io.StringIO()
            with redirect_stderr(stderr), self.assertRaisesRegex(TimeoutError, 'owned collector did not exit; retained for diagnosis'):
                monitor.stop(directory)
            event = json.loads((directory / 'stop-timeout.json').read_text())
            term.assert_called_once_with(identity['pid'], signal.SIGTERM)
            opened.assert_called_once_with(identity['pid'])
            closed.assert_called_once_with(79)
            self.assertEqual(event['status'], 'FAIL')
            self.assertEqual(event['identity'], identity)
            self.assertEqual(event['stop_timeout_seconds'], 5)
            self.assertEqual(event['kill_wait_seconds'], 2)
            self.assertIn('COLLECTOR_STOP_TIMEOUT', stderr.getvalue())
            self.assertGreaterEqual(clock[0], 5)
            self.assertLess(clock[0], 7.1)
            if changed: kill.assert_not_called()
            else: kill.assert_called_once_with(79, signal.SIGKILL)
            return event

    def test_timeout_kill_disappears_still_fails(self):
        event = self.run_timeout()
        self.assertTrue(event['kill_success'])
        self.assertEqual(event['after_kill']['state'], 'ABSENT')

    def test_kill_still_present_keeps_original_error(self):
        event = self.run_timeout(remains=True)
        self.assertTrue(event['signal_sent'])
        self.assertFalse(event['kill_success'])
        self.assertFalse(event['after_kill']['target_disappeared'])

    def test_kill_permission_failure_is_recorded(self):
        event = self.run_timeout(remains=True, signal_error=PermissionError('denied'))
        self.assertFalse(event['signal_sent'])
        self.assertFalse(event['kill_success'])
        self.assertIn('denied', str(event['errors']))

    def test_pid_identity_change_never_kills_replacement(self):
        event = self.run_timeout(remains=True, changed=True)
        self.assertFalse(event['kill_attempted'])
        self.assertIn('identity changed', str(event['errors']))

    def test_host_keeps_event_and_fail_even_with_pass_receipt(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); raw = root/'raw'; raw.mkdir()
            guest = root/'session'; guest.mkdir()
            identity = dict(pid=123, start_ticks='1', boot_id='test')
            event = dict(event='COLLECTOR_STOP_TIMEOUT', status='FAIL', kill_success=True)
            (guest/'stop-timeout.json').write_text(json.dumps(event))
            (guest/'collector-status.json').write_text(json.dumps(dict(status='PASS', sandbox_id='owned', session_id='session')))
            def cli(label, *argv, **kwargs):
                if label == 'monitor-stop': raise TimeoutError('original stop timeout')
                if label == 'monitor-reaped': return json.dumps(dict(status='REAPED', **identity))
                if label == 'monitor-download':
                    shutil.copytree(guest, Path(argv[-1])/'session'); return ''
                return ''
            runtime = monitor.RuntimeMonitor(raw, cli)
            entry = dict(sandbox_id='owned', role='early_1', session_id='session', identity=identity,
                         guest_directory=str(guest), status='INCOMPLETE')
            runtime.active['owned'] = entry; runtime.sessions.append(entry)
            worker = Mock(); worker.is_alive.return_value = False
            runtime.workers['owned'] = (worker, [])
            with self.assertRaisesRegex(TimeoutError, 'original stop timeout'): runtime.stop('owned')
            self.assertEqual(entry['status'], 'FAIL')
            self.assertEqual(entry['stop_timeout_event'], event)
            self.assertEqual(json.loads((raw/'runtime-monitor.json').read_text())['status'], 'INCOMPLETE')
            worker.join.assert_called_once_with(timeout=10)


if __name__ == '__main__': unittest.main()
