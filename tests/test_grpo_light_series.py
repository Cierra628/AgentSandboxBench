"""Saved-number arithmetic and temporary JSON only; no services or devices."""
import copy
import json
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from report_grpo_light_repeats import aggregate,summarize
from run_grpo_light_series import ORDERS,distribution,report


class GrpoLightSeriesTests(unittest.TestCase):
    def pairs(self,count=10):
        result=[]
        for i in range(count):
            off=100+i;on=off+(-2 if i%2 else 3)
            if i==0:on=150  # Preserve a slow opening group, not a candidate for exclusion.
            result.append(dict(comparison=dict(controller=dict(off_ns=off,on_ns=on,difference_ns=on-off,
                change_percent=(on/off-1)*100),control=dict(off_ns=0,on_ns=2,difference_ns=2,change_percent=None)),
                runs=[dict(sessions=[]),dict(sessions=[dict(samples=3)])]))
        return result

    def test_ten_orders_are_balanced_and_alternating(self):
        self.assertEqual(len(ORDERS),10);self.assertEqual(ORDERS.count('off-on'),5);self.assertEqual(ORDERS.count('on-off'),5)
        self.assertTrue(all(a!=b for a,b in zip(ORDERS,ORDERS[1:])))

    def test_ten_counts_and_slow_group_are_preserved(self):
        result=aggregate(self.pairs())
        self.assertEqual((result['pair_count'],result['run_count'],result['actual_actions'],result['sample_count']),(10,20,3780,30))
        self.assertEqual(result['statistics']['controller']['paired_change_range_percent'][1],50)
        self.assertLess(result['statistics']['controller']['paired_change_range_percent'][0],0)

    def test_sample_deviation_and_no_trim(self):
        values=[1,2,3,4,5,6,7,8,9,100]
        result=distribution(values)
        self.assertEqual(result['maximum'],100);self.assertEqual(result['mean'],14.5)
        self.assertEqual(result['sample_stddev'],statistics.stdev(values));self.assertEqual(result['median'],5.5)

    def test_explicit_ten_scope_rejects_missing_or_duplicate_before_reads(self):
        for paths in ([Path(str(i)) for i in range(9)],[Path('same')]*10):
            with self.assertRaises(AssertionError):summarize(paths,expected_pairs=10)
        with self.assertRaises(AssertionError):summarize([Path(str(i)) for i in range(10)])

    def test_report_adds_distributions_and_does_not_pool_old_three(self):
        with tempfile.TemporaryDirectory() as name:
            out=Path(name);(out/'plan.json').write_text(json.dumps(dict(orders=ORDERS)))
            with patch('run_grpo_light_series.summarize',return_value=copy.deepcopy(aggregate(self.pairs()))) as calculate:
                result=report(out)
            self.assertEqual(calculate.call_args.kwargs,dict(expected_pairs=10))
            self.assertEqual(result['additional_actions_vs_old_three'],3780)
            self.assertEqual(result['statistics']['controller']['paired_change_distribution_percent']['maximum'],50)
            self.assertIsNone(result['statistics']['control']['paired_change_distribution_percent'])
            self.assertTrue((out/'summary.json').exists())


if __name__=='__main__':unittest.main()
