import itertools
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_trenvx_off_controls import distribution,summarize


class OffControlPermutationTests(unittest.TestCase):
    def test_equal_measurements_include_all_ties(self):
        value=summarize([(100,100,100)]*5)
        self.assertEqual(value['permutation_count'],7776)
        self.assertEqual(value['extreme_count'],7776)
        self.assertEqual(value['two_sided_tail_fraction'],1)
        self.assertEqual(value['paired_sign_flip']['permutation_count'],32)
        self.assertEqual(value['paired_sign_flip']['two_sided_tail_fraction'],1)

    def test_two_group_distribution_matches_direct_schedule_assignments(self):
        rows=[(1,2,4),(3,7,10)]
        observed,values=distribution(rows)
        direct=[]
        for first in itertools.permutations(rows[0]):
            for second in itertools.permutations(rows[1]):
                direct.append((math.log(first[1]/first[0])+math.log(second[1]/second[0]))/2)
        self.assertEqual(len(values),36)
        for actual,expected in zip(sorted(values),sorted(direct)):
            self.assertAlmostEqual(actual,expected)
        self.assertAlmostEqual(observed,(math.log(2)+math.log(7/3))/2)

    def test_swapping_labels_keeps_two_sided_tail_fraction(self):
        rows=[(1,2,4),(3,7,10),(11,12,14)]
        forward=summarize(rows);reverse=summarize([(b,a,c) for a,b,c in rows])
        self.assertEqual(forward['two_sided_tail_fraction'],reverse['two_sided_tail_fraction'])
        self.assertAlmostEqual(forward['observed_mean_log_ratio'],-reverse['observed_mean_log_ratio'])

    def test_changing_units_per_group_does_not_change_test(self):
        rows=[(1,2,4),(3,7,10),(11,12,14)]
        original=summarize(rows)
        scaled=summarize([tuple(v*scale for v in row) for row,scale in zip(rows,(1000,100,10))])
        self.assertEqual(original['two_sided_tail_fraction'],scaled['two_sided_tail_fraction'])
        self.assertAlmostEqual(original['observed_mean_log_ratio'],scaled['observed_mean_log_ratio'])

    def test_rejects_invalid_values_and_excessive_enumeration(self):
        for rows in ([],[(1,2,3)]*6,[(0,1,2)],[(1,float('inf'),2)],[(1,2)]):
            with self.assertRaises(ValueError):distribution(rows)


if __name__=='__main__':unittest.main()
