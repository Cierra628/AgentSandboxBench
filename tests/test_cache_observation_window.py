"""Synthetic PTEs and independent clocks only; no real collection."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import PRESENT
from review_cache_observation_window import ORDER,mapping_intervals,point_windows


class ObservationWindowTests(unittest.TestCase):
    def point(self):
        stages={name:dict(host_start_ns=10**12+20*i,host_end_ns=10**12+20*i+10,
                          elapsed_ns=10) for i,name in enumerate(ORDER)}
        return dict(host_sample_start_ns=10**12,host_elapsed_ns=20*len(ORDER),
                    host_stage_costs=stages,guest_publication_before=dict(guest_start_ns=100,guest_end_ns=110),
                    guest_start_ns=300,guest_end_ns=320)

    def test_partition_both_intervals_and_return_to_first(self):
        a,b,c=PRESENT|1,PRESENT|2,PRESENT|3
        got=mapping_intervals([[a,a,a],[a,b,b],[a,a,b],[a,b,a],[a,b,c]])
        self.assertEqual(got,dict(first_middle_only=1,middle_last_only=1,both_intervals=2,returned_to_first=1,stable=1))

    def test_ignored_flags_and_empty_input(self):
        self.assertEqual(mapping_intervals([[PRESENT|1,PRESENT|1|(1<<56),PRESENT|1]])['stable'],1)
        self.assertTrue(all(v==0 for v in mapping_intervals([]).values()))

    def test_host_and_guest_clocks_are_never_subtracted_from_each_other(self):
        p=self.point();before=copy.deepcopy(p);got=point_windows(p)
        self.assertEqual(got['guest_publication_span_ns'],220)
        self.assertEqual(got['guest_publication_gap_ns'],190)
        self.assertEqual(got['host_guest_read_span_ns'],230)
        self.assertEqual(got['preparation_elapsed_ns'],30)
        self.assertEqual(got['first_queries_elapsed_ns'],20)
        self.assertEqual(p,before)

    def test_wrong_order_or_overlapping_stage_is_rejected(self):
        p=self.point();p['host_stage_costs']['maps_before']['host_start_ns']=10**12
        with self.assertRaises(AssertionError):point_windows(p)
        p=self.point();p['host_stage_costs']=dict(reversed(list(p['host_stage_costs'].items())))
        with self.assertRaises(AssertionError):point_windows(p)

    def test_reversed_guest_publications_are_rejected(self):
        p=self.point();p['guest_start_ns']=50
        with self.assertRaises(AssertionError):point_windows(p)


if __name__=='__main__':unittest.main()
