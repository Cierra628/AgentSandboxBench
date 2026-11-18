"""Pure regression/clock/budget arithmetic; no services, devices or sampling."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_deployment_density import budget_table,count_bounds,intercept_weights,offset_bounds


class DeploymentDensityAnalysisTests(unittest.TestCase):
    def test_intercept_weights_recover_constant_and_linear_intercepts(self):
        xs=[1,2,4,7,10];weights=intercept_weights(xs)
        self.assertAlmostEqual(sum(weights),1)
        self.assertAlmostEqual(sum(w*(13+7*x) for w,x in zip(weights,xs)),13)
        self.assertLess(weights[-1],0)
        with self.assertRaises(ValueError):intercept_weights([2,2])

    def test_clock_bounds_intersect_calls_without_midpoint_assumption(self):
        calls=[dict(start_monotonic_ns=100,duration_ns=20),dict(start_monotonic_ns=200,duration_ns=30)]
        self.assertEqual(offset_bounds(calls,[(10,20),(110,125)]),(90,100))
        self.assertEqual(offset_bounds(calls,[(10,20),(110,125)],2),(88,102))
        with self.assertRaises(ValueError):offset_bounds(calls,[(10,20),(50,60)])

    def test_interval_bounds_preserve_uncertainty_and_reject_counter_reset(self):
        intervals=[dict(start_monotonic_ns=a,end_monotonic_ns=b,refault_delta=v) for a,b,v in ((0,10,2),(10,20,5),(20,30,7),(30,40,11))]
        self.assertEqual(count_bounds(intervals,5,10,20,25),(5,14))
        self.assertEqual(count_bounds(intervals,10,10,20,20),(5,5))
        with self.assertRaises(ValueError):count_bounds([dict(start_monotonic_ns=0,end_monotonic_ns=10,refault_delta=-1)],0,0,10,10)

    def test_budgets_do_not_interpolate_an_unmeasured_pressure_point(self):
        runs=[dict(actions_downloads_seconds=t,execution_current_peak_mib=m) for t,m in ((20,20),(22,60),(19,8),(26,25))]
        result=budget_table(runs)
        self.assertEqual([r['pressure_pair_feasible'] for r in result],[False,False,True,True])
        self.assertTrue(all(r['observed_feasible_gap_reduction_mib'] is None for r in result[:2]))
        self.assertEqual(result[2]['observed_feasible_gap_reduction_mib'],23)
        self.assertEqual(result[2]['observed_feasible_retained_gap_mib'],17)
        self.assertEqual(result[2]['observed_feasible_gap_reduction_mib'],result[3]['observed_feasible_gap_reduction_mib'])


if __name__=='__main__':unittest.main()
