"""Saved-number arithmetic only; no services, tasks or residency queries."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_grpo_light_repeats import aggregate,summarize


class GrpoRepeatTests(unittest.TestCase):
    def test_three_signed_pairs_not_ratio_of_unpaired_medians(self):
        pairs=[]
        for off,on in ((100,110),(200,180),(300,360)):
            pairs.append(dict(comparison=dict(tool_sum=dict(off_ns=off,on_ns=on,difference_ns=on-off,
                change_percent=(on/off-1)*100),control=dict(off_ns=0,on_ns=2,difference_ns=2,change_percent=None)),
                runs=[dict(sessions=[]),dict(sessions=[dict(samples=3)])]))
        value=aggregate(pairs);stats=value['statistics']['tool_sum']
        self.assertAlmostEqual(stats['paired_change_median_percent'],10)
        self.assertAlmostEqual(stats['paired_change_range_percent'][0],-10)
        self.assertAlmostEqual(stats['paired_change_range_percent'][1],20)
        self.assertEqual(stats['paired_difference_median_ns'],10)
        self.assertEqual(value['sample_count'],9)
        self.assertIsNone(value['statistics']['control']['paired_change_median_percent'])

    def test_missing_or_duplicate_pairs_rejected_before_reading(self):
        for pairs in ([Path('one')],[Path('one')]*3):
            with self.subTest(pairs=pairs),self.assertRaises(AssertionError):summarize(pairs)


if __name__=='__main__':unittest.main()
