import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_guest_perf_scope import measure


class MeasureOrderTests(unittest.TestCase):
    def test_tracer_ready_precedes_counter_enable_and_setup_failure_detaches(self):
        events=[]
        class Trace:
            def __init__(self,*args):events.append('trace_ready')
            def stop(self):events.append('trace_stopped')
        def fail(*args,**kwargs):
            events.append('counter_started')
            raise OSError('test setup failure')
        with tempfile.TemporaryDirectory() as temp, patch('validate_guest_perf_scope.WaitTrace',Trace), patch('validate_guest_perf_scope.subprocess.Popen',fail):
            with self.assertRaises(OSError):measure(Path('unused'),[10,11],lambda:None,Path(temp)/'counts',trace_wait=True)
        self.assertEqual(events,['trace_ready','counter_started','trace_stopped'])


if __name__=='__main__':unittest.main()
