"""Deterministic clock tests only; no live services or device I/O."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_task_cache import _timed


class StageTimingTests(unittest.TestCase):
    def test_exact_clock_differences_and_argument_forwarding(self):
        costs={};marker=object();calls=[]
        def function(value,*,flag):
            calls.append((value,flag));return marker
        with patch('live_task_cache.time.monotonic_ns',side_effect=[100,170]), \
             patch('live_task_cache.time.thread_time_ns',side_effect=[20,50]):
            actual=_timed(costs,'query',function,marker,flag=True)
        self.assertIs(actual,marker);self.assertEqual(calls,[(marker,True)])
        self.assertEqual(costs,dict(query=dict(cpu_ns=30,elapsed_ns=70,host_start_ns=100,host_end_ns=170)))

    def test_failure_propagates_without_a_successful_cost_record(self):
        costs={}
        def fail():raise RuntimeError('expected test failure')
        with self.assertRaisesRegex(RuntimeError,'expected test failure'):
            _timed(costs,'failed',fail)
        self.assertEqual(costs,{})


if __name__=='__main__':unittest.main()
