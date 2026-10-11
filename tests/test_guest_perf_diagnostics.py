import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_guest_perf_diagnostics import verify_counters,loop_errors


class ScopeTests(unittest.TestCase):
    def rows(self):
        return [dict(tid=t,scope=s,enabled_ns=100,running_ns=100,cycles=100,instructions=200)
                for t in (10,11) for s in ('host','guest')]
    def test_wrong_tid_rejected(self):
        with self.assertRaises(AssertionError):verify_counters(self.rows(),[10,12])
    def test_missing_scope_rejected(self):
        with self.assertRaises(AssertionError):verify_counters(self.rows()[:-1],[10,11])
    def test_low_coverage_rejected(self):
        rows=self.rows();rows[0]['running_ns']=90
        with self.assertRaises(AssertionError):verify_counters(rows,[10,11])
    def test_known_work_with_fixed_background(self):
        phases=[dict(iterations=n,guest_instructions=2*n+1000000) for n in (0,1000000000,2000000000,1000000000,0)]
        self.assertEqual(loop_errors(phases),[0,0,0])
    def test_startup_work_detected(self):
        phases=[dict(iterations=n,guest_instructions=2*n+1000000) for n in (0,1000000000,2000000000,1000000000,0)]
        phases[1]['guest_instructions']+=2000000000
        self.assertEqual(loop_errors(phases),[100,0,0])


if __name__=='__main__':unittest.main()
