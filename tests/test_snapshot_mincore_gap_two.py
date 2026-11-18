"""Actual gap-two entry versus frozen gap-one; all kernel interfaces mocked."""
import errno
import inspect
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),
             str(Path(__file__).resolve().parent/'fixtures')]
from snapshot_cache_residency import PRESENT
from snapshot_gap_one import SnapshotCache as GapOneCache
from test_snapshot_mincore_gap import make_query,run
from test_snapshot_bpf_batch import UNKNOWN


class MincoreGapTwoTests(unittest.TestCase):
    def test_runtime_gap_policy_and_recorded_threshold(self):
        # Query ownership now also differs from the historical gap-one fixture.
        source=(Path(__file__).resolve().parents[1]/'scripts/snapshot_cache_residency.py').read_text()
        self.assertIn('mincore_max_gap_pages=2',source)
        from snapshot_cache_residency import SnapshotCache
        old=inspect.getsource(GapOneCache.query).split('        ordered = ',1)[1]
        new=inspect.getsource(SnapshotCache._query_owned).split('        ordered = ',1)[1]
        self.assertEqual(new,old.replace('np.diff(ordered) > 2','np.diff(ordered) > 3'))
        self.assertEqual(inspect.getsource(SnapshotCache._batch_result),inspect.getsource(GapOneCache._batch_result))

    def test_mixed_gaps_preserve_original_rows_all_statistics_and_filtered_keys(self):
        offsets=[12,9,6,3,1,3,UNKNOWN]
        rows=[(1,5,11),(2,5,222),(3,5,33),(4,5,444),(5,5,555),
              (6,4,66),(7,5,777),(8,5,888),(9,5,0),(10,5,1010),(11,5,1111)]
        resident={1:128,3:255,6:129,9:254,12:1}
        old,old_io,old_keys,_=make_query(rows,resident);old.__class__=GapOneCache
        new,io,keys,vectors=make_query(rows,resident)
        a,b=run(old,offsets),run(new,offsets)
        self.assertTrue(np.array_equal(a,b));self.assertEqual(b.dtype,np.dtype('<u8'))
        self.assertEqual(b.tolist(),[0,0,0,PRESENT|33,0,PRESENT|33,0])
        self.assertEqual(old.query_statistics,new.query_statistics)
        self.assertEqual(new.query_statistics[0]['mincore_resident_pages'],3)
        self.assertEqual(new.query_statistics[0]['missing_kernel_results'],2)
        self.assertEqual(keys,old_keys);self.assertEqual(keys,[([1,3,6,9,12],5)])
        self.assertEqual(io,[('token',),('mincore',1,12),('batch',)])
        self.assertEqual([e for e in old_io if e[0]=='mincore'],
                         [('mincore',1,3),('mincore',6,1),('mincore',9,1),('mincore',12,1)])
        self.assertEqual([len(v) for v in vectors],[12])

    def test_two_gap_resident_bytes_never_add_to_original_statistics(self):
        rows=[(1,5,101),(2,5,102),(3,5,103),(4,5,104)]
        s,io,keys,_=make_query(rows,{1:0,2:255,3:129,4:0})
        self.assertEqual(run(s,[4,1,4,UNKNOWN]).tolist(),[0,0,0,0])
        self.assertEqual(io,[('token',),('mincore',1,4),('batch',)])
        self.assertEqual(keys,[([1,4],5)])
        self.assertEqual(s.query_statistics[0]['requested_pages'],2)
        self.assertEqual(s.query_statistics[0]['mincore_resident_pages'],0)
        self.assertEqual(s.query_statistics[0]['confirmed_pfn_pages'],0)

    def test_chained_two_gaps_split_at_three_and_keep_per_run_allocation(self):
        s,io,_,vectors=make_query([(i,5,i+100) for i in (0,3,6,10,13)],{})
        result=run(s,[13,0,6,3,10])
        self.assertEqual(result.tolist(),[PRESENT|(i+100) for i in (13,0,6,3,10)])
        self.assertEqual(io,[('token',),('mincore',0,7),('mincore',10,4),('batch',)])
        self.assertEqual([len(v) for v in vectors],[7,4]);self.assertIsNot(vectors[0],vectors[1])

    def test_new_merged_call_failure_stops_before_batch_and_batch_error_has_no_fallback(self):
        rows=[(i,5,i+100) for i in (1,4,8,11)]
        for fail_at in (1,2):
            with self.subTest(fail_at=fail_at):
                s,io,keys,_=make_query(rows,{},fail_at=fail_at)
                with self.assertRaises(AssertionError):run(s,[1,4,8,11])
                self.assertEqual(io,[('token',),('mincore',1,4),('mincore',8,4)][:fail_at+1])
                self.assertEqual(keys,[]);self.assertEqual(s.query_statistics,[]);self.assertEqual(s.query_costs,[])
        s,io,_,_=make_query(rows,{},rc=-errno.EFAULT)
        with self.assertRaises(RuntimeError):run(s,[1,4,8,11])
        self.assertEqual(io,[('token',),('mincore',1,4),('mincore',8,4),('batch',)])
        self.assertEqual(s.query_statistics,[]);self.assertEqual(s.query_costs,[])

    def test_extra_batch_rows_change_real_returned_count_not_original_statistics(self):
        old,_,_,_=make_query([(1,5,101),(4,5,104)],{});old.__class__=GapOneCache
        new,_,_,_=make_query([(i,5,i+100) for i in (1,2,3,4)],{})
        self.assertTrue(np.array_equal(run(old,[4,1,4]),run(new,[4,1,4])))
        a,b=map(lambda s:dict(s.query_statistics[0]),(old,new))
        self.assertEqual(a.pop('bpf_lookup')['returned_entries'],2)
        self.assertEqual(b.pop('bpf_lookup')['returned_entries'],4)
        self.assertEqual(a,b)


if __name__=='__main__':unittest.main()
