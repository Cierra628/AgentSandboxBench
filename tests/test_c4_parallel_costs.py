"""Pure synthetic arithmetic tests; never queries a running sandbox."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_c4_parallel_costs import thin_pss,step_differences


class C4ParallelCostTests(unittest.TestCase):
    def test_thinning_ignores_blank_pss_and_keeps_real_peak_loss(self):
        points=[dict(monotonic_ns=str(i*10**9),pss_kib=value) for i,value in enumerate(['10','','100','20','15'])]
        result=thin_pss(points,3)
        self.assertEqual(result['original_points'],4);self.assertEqual(result['retained_points'],2)
        self.assertEqual(result['observed_peak_loss_percent'],80)

    def test_thinning_rejects_reversed_saved_times(self):
        with self.assertRaises(AssertionError):thin_pss([dict(monotonic_ns='2',pss_kib='1'),dict(monotonic_ns='1',pss_kib='2')],2)

    def test_step_totals_keep_negative_other_actions(self):
        off=[1.]*36;on=off.copy();on[24]+=3;on[0]-=1
        result=step_differences(off,on)
        self.assertEqual(result['total_seconds'],2);self.assertEqual(result['heavy_seconds'],3)
        self.assertEqual(result['other_seconds'],-1);self.assertEqual(result['heavy_fraction_percent'],150)


if __name__=='__main__':unittest.main()
