"""Synthetic repeat statistics only; no services or memory pressure."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_pressure_extension import dispersion,repeat_pairs


class PressureExtensionReportTests(unittest.TestCase):
    def test_sample_deviation_and_range_keep_extreme_value(self):
        v=dispersion([1,2,9])
        self.assertEqual(v['range'],8);self.assertEqual(v['median'],2)
        self.assertEqual(v['count'],3);self.assertGreater(v['sample_stddev'],4)
        with self.assertRaises(ValueError):dispersion([1])

    def test_reversed_last_pair_preserves_shared_independent_difference(self):
        def run(condition,current,window):return dict(condition=condition,execution_current_peak_mib=current,p1_mincore_mib=current,actions_downloads_seconds=window)
        old=[run('A',10,10),run('B',50,12),run('C',5,11),run('D',25,16)]
        new=[run('I',6,10),run('J',26,16),run('K',28,20),run('L',8,12)]
        result=repeat_pairs(old,new)
        self.assertEqual([(r['shared'],r['independent']) for r in result],[('C','D'),('I','J'),('L','K')])
        self.assertEqual([r['current_retention_percent'] for r in result],[50,50,50])
        self.assertEqual([r['actions_window_gap_seconds'] for r in result],[5,6,8])
        self.assertEqual([r['extra_window_gap_vs_original_controls_seconds'] for r in result],[3,4,6])


if __name__=='__main__':unittest.main()
