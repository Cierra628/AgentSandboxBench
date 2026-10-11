import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_git_step_diagnostic import host_wait_bound


class HostWaitBoundTests(unittest.TestCase):
    def fixture(self,base):
        (base/'fixed-frequency').mkdir()
        wait=dict(status='PASS',ready_monotonic_ns=100,stop_requested_monotonic_ns=500,
                  threads=[dict(tid=11,runnable_wait_ns=7,observed_switchins=2),dict(tid=22,runnable_wait_ns=9,observed_switchins=3)])
        (base/'fixed-frequency/host-wait.json').write_text(json.dumps(wait))
        (base/'host-wait-task-start-monotonic-ns.txt').write_text('200')
        (base/'host-wait-task-end-monotonic-ns.txt').write_text('400')
        return dict(host_wait=wait,binding=[dict(tid=11),dict(tid=22)],
                    host_wait_initial_state=[dict(tid=11,state='S',observed_monotonic_ns=150),dict(tid=22,state='D',observed_monotonic_ns=160)])

    def test_full_capture_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);r=host_wait_bound(base,self.fixture(base))
            self.assertEqual(r['summed_runnable_wait_upper_bound_ns'],16)
            self.assertEqual(r['task_window_inclusive_ns'],200)

    def test_unbounded_initial_queue_or_late_probe_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);freq=self.fixture(base)
            freq['host_wait_initial_state'][0]['state']='R'
            with self.assertRaises(AssertionError):host_wait_bound(base,freq)
            freq['host_wait_initial_state'][0]['state']='S'
            (base/'host-wait-task-start-monotonic-ns.txt').write_text('90')
            with self.assertRaises(AssertionError):host_wait_bound(base,freq)


if __name__=='__main__':unittest.main()
