"""No service, BPF attachment or real process; verify the diagnostic program scope."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from private_delete_trace import program,LOG_FILTER
class PrivateDeleteTraceTests(unittest.TestCase):
    def test_every_probe_is_pid_filtered_and_only_observes(self):
        text=program(123,'/proc/123/root/server','network_release_symbol')
        self.assertEqual(text.count('pid == 123'),8)
        for key in ('fsync','fdatasync','rocksdb_delete','network_release'):
            self.assertIn('E '+key,text);self.assertIn('X '+key,text)
        self.assertNotIn('override(',text);self.assertNotIn('signal(',text)
        self.assertNotIn('str(arg',text);self.assertIn('ustack(12)',text)
    def test_filter_is_specific_not_global_trace(self):
        self.assertIn('agentenv::orchestrator=debug',LOG_FILTER)
        self.assertNotIn('=trace',LOG_FILTER)
if __name__=='__main__':unittest.main()
