"""Synthetic saved rows and captured source only; no BPF calls or devices."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from profile_saved_query_results import (UNKNOWN,PRESENT,MASK,array_result,context,
    original_result_function,recover_batch,prepare_unique,prepare_runs,allocate_vectors)
from snapshot_cache_residency import SnapshotCache


class QueryResultReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        source=Path(cls.directory.name);(source/'code').mkdir()
        (source/'code/snapshot_cache_residency.py').write_bytes(
            (Path(__file__).resolve().parent/'fixtures/snapshot_batch_loop.py').read_bytes())
        reference,cls.tail=original_result_function(source)
        cls.reference=staticmethod(reference)

    @classmethod
    def tearDownClass(cls):cls.directory.cleanup()

    def run_pair(self,offsets,batch,residency):
        offsets=np.array(offsets,dtype='<u8');ordered=prepare_unique(offsets)
        query=dict(query=7,bpf_lookup=dict(mode='batch',returncode=-2,returned_entries=len(batch)))
        a,b,c=context(query),context(query),context(query)
        x=self.reference(a,offsets,ordered,residency,batch)
        y=array_result(b,offsets,ordered,residency,batch)
        z=SnapshotCache._batch_result(c,offsets,ordered,residency,batch)
        self.assertTrue(np.array_equal(x,y))
        self.assertEqual(y.dtype,np.dtype('<u8'))
        self.assertEqual(a.query_statistics,b.query_statistics)
        self.assertTrue(np.array_equal(x,z))
        self.assertEqual(z.dtype,np.dtype('<u8'))
        self.assertEqual(a.query_statistics,c.query_statistics)
        return x,a.query_statistics[0]

    def test_mixed_residency_missing_duplicate_unknown_zero_pfn(self):
        result,stats=self.run_pair([5,1,2,3,3,4,UNKNOWN,1],
            {1:11,2:22,4:0,5:55,99:99},{1:1,2:0,3:1,4:1,5:0})
        self.assertEqual(result.tolist(),[0,PRESENT|11,0,0,0,0,0,PRESENT|11])
        self.assertEqual(stats['missing_kernel_results'],2)
        self.assertEqual(stats['confirmed_pfn_pages'],2)
        self.assertEqual(stats['no_file_mapping_pages'],1)

    def test_stale_token_is_absent_from_post_filter_batch(self):
        _,stats=self.run_pair([2,2,1],{1:11},{1:1,2:1})
        self.assertEqual(stats['missing_kernel_results'],2)

    def test_empty_and_only_unknown(self):
        for offsets in ([],[UNKNOWN,UNKNOWN]):
            with self.subTest(offsets=offsets):
                result,stats=self.run_pair(offsets,{}, {})
                self.assertEqual(result.tolist(),[0]*len(offsets))
                self.assertEqual(stats['missing_kernel_results'],0)

    def test_order_aliases_high_pfn_and_residency_low_bit(self):
        result,stats=self.run_pair([8,0,8,2],{0:MASK,2:3,8:4},{0:3,2:2,8:1})
        self.assertEqual(result.tolist(),[PRESENT|4,PRESENT|MASK,PRESENT|4,0])
        self.assertEqual(stats['mincore_resident_pages'],2)

    def test_full_input_recovery_preserves_aliases_and_ignores_unknown(self):
        offsets=np.array([8,0,UNKNOWN,8],dtype='<u8')
        result,stats=self.run_pair(offsets,{0:11,8:12},{0:1,8:1})
        batch,residency=recover_batch(offsets,result,stats)
        self.assertEqual(batch,{0:11,8:12})
        self.assertEqual(residency,{0:1,8:1})

    def test_nonresident_zero_output_does_not_determine_pfn(self):
        offsets=np.array([1],dtype='<u8')
        a,stats_a=self.run_pair(offsets,{1:0},{1:0})
        b,stats_b=self.run_pair(offsets,{1:123},{1:0})
        self.assertTrue(np.array_equal(a,b))
        self.assertEqual(stats_a,stats_b)
        self.assertIsNone(recover_batch(offsets,a,stats_a))

    def test_resident_zero_pfn_and_missing_cannot_be_recovered(self):
        offsets=np.array([1],dtype='<u8')
        for batch,residency in [({1:0},{1:1}),({},{1:1})]:
            result,stats=self.run_pair(offsets,batch,residency)
            self.assertIsNone(recover_batch(offsets,result,stats))

    def test_invalidated_output_or_bad_request_count_is_rejected(self):
        offsets=np.array([1],dtype='<u8')
        result,stats=self.run_pair(offsets,{1:10},{1:1})
        with self.assertRaisesRegex(AssertionError,'invalidated'):
            recover_batch(offsets,np.zeros(1,dtype='<u8'),stats)
        with self.assertRaisesRegex(AssertionError,'request count'):
            recover_batch(offsets,result,dict(stats,requested_pages=2))

    def test_conflicting_alias_pfn_is_rejected(self):
        offsets=np.array([1,1],dtype='<u8')
        result,stats=self.run_pair(offsets,{1:10},{1:1})
        result[1]=PRESENT|11
        with self.assertRaisesRegex(AssertionError,'conflicting duplicate'):
            recover_batch(offsets,result,stats)

    def test_preparation_runs_and_allocation_never_call_mincore(self):
        ordered=prepare_unique(np.array([UNKNOWN,9,2,3,3,7],dtype='<u8'))
        runs=prepare_runs(ordered)
        self.assertEqual([run.tolist() for run in runs],[[2,3],[7],[9]])
        self.assertEqual(len(allocate_vectors(runs)),1)
        self.assertIsNone(allocate_vectors(prepare_runs(np.array([],dtype='<u8'))))
        self.assertNotIn('.mincore(',self.tail)
        self.assertNotIn('.read(',self.tail)

    def test_array_capture_is_not_mislabelled_as_original_loop(self):
        with tempfile.TemporaryDirectory() as name:
            source=Path(name);(source/'code').mkdir()
            (source/'code/snapshot_cache_residency.py').write_bytes(
                (Path(__file__).resolve().parents[1]/'scripts/snapshot_cache_residency.py').read_bytes())
            with self.assertRaisesRegex(ValueError,'pre-array inline batch result loop'):
                original_result_function(source)


if __name__=='__main__':unittest.main()
