"""Entry guards only; never reach services, devices, volumes or sandboxes."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_live_child_calibration as entry
import validate_isolated_agentenv as private

class PrivateServerTests(unittest.TestCase):
    def test_outer_rejects_override_without_restore(self):
        with patch.object(sys,'argv',['entry','--output','/unused','--diagnostic-server','/unused']):
            with self.assertRaisesRegex(ValueError,'requires restore-one'):entry.main()
    def test_outer_rejects_missing_binary(self):
        with patch.object(sys,'argv',['entry','--output','/unused','--guests','7','--light-memory','--restore-one','--diagnostic-server','/unused']):
            with self.assertRaisesRegex(ValueError,'executable file'):entry.main()
    def test_private_rejects_override_without_explicit_mode(self):
        with patch.object(private.os,'umask'),patch.object(private.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'explicit child-cgroup'):
                private.main(SimpleNamespace(diagnostic_server_binary=Path('/unused')))

if __name__=='__main__':unittest.main()
