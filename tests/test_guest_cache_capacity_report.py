"""Counter and time-estimate checks use synthetic observations only."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_guest_cache_capacity import summarize


class CapacityReportTests(unittest.TestCase):
    def test_before_and_after_are_separate_and_time_is_sample_hold(self):
        target=64*2**20
        phase=dict(name='low',target_bytes=target,start_ns=0,end_ns=8*10**9)
        rows=[dict(phase='low',before=dict(start_ns=t*10**9,cache_bytes=target+4096),
                   after=dict(cache_bytes=target)) for t in [4,5,6,7]]
        result=summarize(rows,phase)
        self.assertEqual(result['samples'],3)
        self.assertEqual(result['observed_window_ns'],3*10**9)
        self.assertEqual(result['sample_hold_above_target_estimate_ns'],3*10**9)
        self.assertEqual(result['max_before_excess_bytes'],4096)
        self.assertEqual(result['max_after_excess_bytes'],0)

    def test_other_phases_do_not_enter_selected_window(self):
        phase=dict(name='high',target_bytes=128*2**20,start_ns=0,end_ns=8*10**9)
        rows=[dict(phase=name,before=dict(start_ns=t*10**9,cache_bytes=100),after=dict(cache_bytes=100))
              for name,t in [('low',5),('high',5),('high',6)]]
        result=summarize(rows,phase)
        self.assertEqual(result['samples'],2)
        self.assertEqual(result['before_mean_bytes'],100)


if __name__=='__main__':unittest.main()
