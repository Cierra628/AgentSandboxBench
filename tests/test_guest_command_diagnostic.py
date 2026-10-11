"""Only owned local subprocesses; no VM, service, device or existing result writes."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from guest_command_diagnostic import capture


class CommandDiagnosticTests(unittest.TestCase):
    def test_nonzero_exploration_is_captured_with_cpu_and_output(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            r=capture("python3 -c 'sum(i*i for i in range(600000));print(42);raise SystemExit(1)'",out,cwd=temp)
            self.assertEqual(r['status'],'CAPTURED');self.assertEqual(r['exit_code'],1)
            self.assertGreater(r['child_user_seconds'],0)
            self.assertEqual((out/'stdout').read_text(),'42\n');self.assertFalse(r['profile'])

    def test_profile_observes_waiting_descendants(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            r=capture("python3 -c 'import subprocess;subprocess.run([\"sleep\",\"0.2\"])'",out,True,cwd=temp)
            rows=[json.loads(s) for s in (out/'processes.jsonl').read_text().splitlines()]
            processes=[p for row in rows for p in row['processes']]
            self.assertEqual(r['status'],'CAPTURED');self.assertGreater(r['scans'],1)
            self.assertTrue(any('sleep 0.2' in p['command'] and p['state']=='S' for p in processes))
            self.assertTrue(all('start_ticks' in p and 'socket_fds' in p for p in processes))

    def test_timeout_cannot_be_reported_as_capture_success(self):
        with tempfile.TemporaryDirectory() as temp:
            r=capture('sleep 5',Path(temp)/'run',timeout=.05,cwd=temp)
            self.assertEqual(r['status'],'TIMEOUT');self.assertLess(r['elapsed_ns'],3e9)
            self.assertLess(r['exit_code'],0)

    def test_inet_socket_is_identified_without_recording_peer_addresses(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'run'
            command="python3 -c 'import socket,time;s=socket.socket();s.bind((\"127.0.0.1\",0));s.listen();time.sleep(.3)'"
            r=capture(command,out,True,cwd=temp)
            rows=[json.loads(s) for s in (out/'processes.jsonl').read_text().splitlines()]
            self.assertEqual(r['status'],'CAPTURED')
            self.assertTrue(any('tcp:0A' in p['inet_socket_states'] for row in rows for p in row['processes']))

    def test_existing_result_and_invalid_budget_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for timeout in (0,121):
                with self.assertRaises(ValueError):capture('true',Path(temp)/'new',timeout=timeout,cwd=temp)
            with self.assertRaises(FileExistsError):capture('true',Path(temp),cwd=temp)

    def test_pip_version_control_only_changes_owned_child_environment(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'PIP_DISABLE_PIP_VERSION_CHECK':'original'}):
            r=capture('printf %s "$PIP_DISABLE_PIP_VERSION_CHECK"',Path(temp)/'run',cwd=temp,disable_pip_version_check=True)
            self.assertEqual((Path(temp)/'run/stdout').read_text(),'1')
            self.assertEqual(os.environ['PIP_DISABLE_PIP_VERSION_CHECK'],'original')
            self.assertTrue(r['pip_version_check_disabled']);self.assertEqual(r['inherited_pip_disable_version_check'],'original')


if __name__=='__main__':unittest.main()
