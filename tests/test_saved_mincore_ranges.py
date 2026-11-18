"""Pure interval/count checks; no real mincore, BPF or sandbox operations."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from review_saved_mincore_ranges import query_ranges,union_ranges,range_pages,summarize,PAGE,UNKNOWN


class SavedMincoreRangeTests(unittest.TestCase):
    def ranges(self,offsets,gap):return query_ranges(np.array(offsets,dtype='<u8'),gap)

    def test_baseline_sorts_deduplicates_and_removes_unknown(self):
        self.assertEqual(self.ranges([UNKNOWN,5,1,2,2,8],0),[(1,3),(5,6),(8,9)])

    def test_empty_and_only_unknown(self):
        self.assertEqual(self.ranges([],1),[])
        self.assertEqual(self.ranges([UNKNOWN,UNKNOWN],1),[])

    def test_one_gap_joins_only_one_missing_page(self):
        self.assertEqual(self.ranges([1,3,6,8],1),[(1,4),(6,9)])
        self.assertEqual(self.ranges([1,3,6,8],2),[(1,9)])

    def test_join_can_chain_without_changing_requested_offsets(self):
        offsets=np.array([1,3,5,7],dtype='<u8');before=offsets.copy()
        spans=query_ranges(offsets,1)
        self.assertEqual(spans,[(1,8)])
        self.assertEqual(range_pages(spans),7)
        self.assertTrue(np.array_equal(offsets,before))
        # A future reader must select only requested vector entries.
        vector=np.arange(7,dtype=np.uint8)
        selected={int(p):int(vector[int(p)-spans[0][0]]) for p in offsets}
        self.assertEqual(selected,{1:0,3:2,5:4,7:6})

    def test_union_handles_nested_overlap_adjacent_and_disjoint(self):
        spans=union_ranges([(8,10),(1,4),(2,3),(4,7),(9,11)])
        self.assertEqual(spans,[(1,7),(8,11)])
        self.assertEqual(range_pages(spans),9)

    def query(self,offsets,layer='backend',query=1):
        return dict(offsets=np.array(offsets,dtype='<u8'),layer=layer,point=0,side='first',
            query=query,mapping_length=10000*PAGE,requested_pages=len(set(offsets)-{UNKNOWN}),returned_entries=0)

    def test_each_map_uses_both_layer_prefixes_not_sum_or_single_layer(self):
        qs=[self.query([1,3]),self.query([3,5],layer='lower'),self.query([1,3],query=2)]
        x=summarize(qs,1)
        self.assertEqual([p['possible_keys_per_map'] for p in x['points']],[3,5,5])
        self.assertEqual(x['possible_keys_per_map'],5)
        self.assertEqual(x['possible_entries_copied'],13)
        self.assertEqual(x['calls'],3)
        self.assertEqual(x['queried_pages'],9)
        self.assertEqual(x['extra_queried_pages'],3)

    def test_span_must_fit_saved_mapping(self):
        q=self.query([0,2]);q['mapping_length']=2*PAGE
        with self.assertRaises(AssertionError):summarize([q],1)

    def test_kernel_vector_chunks_count_pages_not_bytes(self):
        q=self.query(list(range(0,5000)))
        x=summarize([q],0)
        self.assertEqual(x['calls'],1)
        self.assertEqual(x['max_span_pages'],5000)
        self.assertEqual(x['vector_chunks'],2)


if __name__=='__main__':unittest.main()
