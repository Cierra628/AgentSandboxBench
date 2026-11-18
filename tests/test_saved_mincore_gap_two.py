"""Synthetic intervals only; no BPF, mincore, device, payload or sandbox access."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from review_saved_mincore_ranges import PAGE,UNKNOWN
from review_saved_mincore_gap_two import check_requested_filter,compare_queries


def query(offsets,returned,layer='backend'):
    return dict(offsets=np.array(offsets,dtype='<u8'),mapping_length=100*PAGE,
                requested_pages=len(set(offsets)-{UNKNOWN}),layer=layer,point=0,
                side='first',query=1,returned_entries=returned)


class GapTwoReviewTests(unittest.TestCase):
    def test_new_gaps_join_and_both_maps_use_cross_layer_prefix_union(self):
        queries=[query([1,3,6],4),query([6,9],5,'lower')]
        value=compare_queries(queries,16)
        current,candidate=value['policies']
        self.assertEqual((current['calls'],candidate['calls']),(4,2))
        self.assertEqual((current['queried_pages'],candidate['queried_pages']),(6,10))
        self.assertEqual([p['possible_keys_per_map'] for p in current['points']],[4,5])
        self.assertEqual([p['possible_keys_per_map'] for p in candidate['points']],[6,9])
        self.assertEqual(value['delta_from_gap_one']['additional_keys_per_map'],4)
        self.assertEqual(value['delta_from_gap_one']['additional_entries_copied'],6)
        self.assertEqual(candidate['map_headroom'],7)

    def test_capacity_shortage_is_reported_not_hidden_by_successful_counts(self):
        value=compare_queries([query([1,4],2)],3)
        self.assertFalse(value['candidate_within_saved_capacity'])
        self.assertEqual(value['policies'][1]['map_headroom'],-1)
        with self.assertRaisesRegex(AssertionError,'prefix keys'):
            compare_queries([query([1,4],1)],16)

    def test_requested_filter_handles_chained_gaps_duplicates_unknown_and_full_bytes(self):
        for values in ([],[UNKNOWN,UNKNOWN],[255,252,249,249,UNKNOWN],[1<<40,(1<<40)+3]):
            with self.subTest(values=values):
                offsets=np.array(values,dtype='<u8');before=offsets.copy()
                self.assertEqual(check_requested_filter(offsets),len(set(values)-{UNKNOWN}))
                self.assertTrue(np.array_equal(offsets,before))

    def test_two_page_boundary_and_empty_queries(self):
        value=compare_queries([query([1,4,8],3)],16)
        self.assertEqual([p['calls'] for p in value['policies']],[3,2])
        value=compare_queries([query([],0),query([UNKNOWN],0)],16)
        self.assertEqual(value['delta_from_gap_one']['calls_saved'],0)
        self.assertEqual(value['delta_from_gap_one']['additional_query_percent'],0)
        self.assertTrue(value['candidate_within_saved_capacity'])


if __name__=='__main__':unittest.main()
