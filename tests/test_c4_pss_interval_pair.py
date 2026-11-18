"""Saved-data statistics only; no live process, cgroup or residency query."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_c4_pss_interval_pair import gaps,scan_stats


class C4PssIntervalPairTests(unittest.TestCase):
    def test_actual_intervals_counts_and_costs(self):
        points=[dict(monotonic_ns=i*100_000_000,pss_kib=str(1024+i) if i%20==0 else '',
                     pss_scan_duration_ns='2000000' if i%20==0 else '') for i in range(42)]
        receipt=dict(status='PASS',samples=42,pss_samples=3,pss_interval_seconds=2,
                     cpu_user_seconds=.02,cpu_system_seconds=.01,elapsed_ns=4_100_000_000)
        value=scan_stats(points,receipt)
        self.assertEqual(value['pss_samples'],3)
        self.assertAlmostEqual(value['ordinary_gaps']['median_seconds'],.1)
        self.assertEqual(value['pss_gaps']['median_seconds'],2)
        self.assertAlmostEqual(value['cpu_seconds'],.03)
        self.assertAlmostEqual(value['pss_scan_wall_seconds'],.006)
        self.assertEqual(value['pss_scan_median_ms'],2)
        self.assertEqual(value['observed_pss_peak_mib'],1064/1024)
        self.assertEqual(value['collector_elapsed_seconds'],4.1)
        with self.assertRaises(AssertionError):scan_stats(points,dict(receipt,pss_samples=4))
        with self.assertRaises(AssertionError):scan_stats(points,dict(receipt,pss_interval_seconds=3))

    def test_missing_or_reversed_samples_are_not_zero_cost(self):
        for times in ([],[1],[2,1],[1,1]):
            with self.subTest(times=times),self.assertRaises(AssertionError):
                gaps([dict(monotonic_ns=t) for t in times])


if __name__=='__main__':unittest.main()
