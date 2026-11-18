"""Pure offline checks; no Docker, guests, or real input files."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_c4_parallel_pair import ALTERNATING_ORDER,TIMING_KEYS,alternating_comparison,pair_changes


class C4ParallelRepeatsTests(unittest.TestCase):
    def fixture(self):
        runs=[]
        for i,(pair,mode) in enumerate(ALTERNATING_ORDER):
            seconds=10 if mode=='off' else {1:11,2:9,3:12}[pair]
            runs.append(dict(pair=pair,mode=mode,storage=f'unit-{i}',task={**{k:seconds*10**9 for k in TIMING_KEYS},'children':[dict(sandbox_id=f'{i}-{j}') for j in range(2)]}))
        return runs

    def test_alternating_pair_statistics_keep_negative_and_slow_runs(self):
        runs=self.fixture();original=copy.deepcopy(runs);value=alternating_comparison(runs)
        self.assertEqual(runs,original)
        self.assertEqual(value['pairs'][1]['order'],['on','off'])
        for row in value['summary'].values():
            self.assertAlmostEqual(row['paired_change_median_percent'],10)
            self.assertAlmostEqual(row['paired_change_range_percent'][0],-10)
            self.assertAlmostEqual(row['paired_change_range_percent'][1],20)
        legacy=pair_changes(runs[0],runs[1]);self.assertEqual(legacy[TIMING_KEYS[0]]['delta_seconds'],1)

    def test_wrong_order_or_reused_guests_rejected(self):
        runs=self.fixture();runs[0],runs[1]=runs[1],runs[0]
        with self.assertRaises(AssertionError):alternating_comparison(runs)
        runs=self.fixture();runs[-1]['task']['children']=runs[0]['task']['children']
        with self.assertRaises(AssertionError):alternating_comparison(runs)


if __name__=='__main__':unittest.main()
