"""Pure Python/ctypes buffers only; never load libc/BPF, map files or run a VM."""
import ast
import ctypes
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import snapshot_cache_residency as runtime
from profile_saved_mincore_vectors import (ALLOCATION,START,PATTERN,UNKNOWN,allocation_counts,
    allocation_functions,compare,query_functions,query_runs,synthetic_query)

# Preserve the measured gap-one baseline independently of the current policy.
SOURCE=(Path(__file__).resolve().parent/'fixtures/snapshot_gap_one.py').read_text()


class SavedMincoreVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.functions,cls.candidate=query_functions(SOURCE)

    def test_only_reviewed_batch_statements_change(self):
        self.assertEqual(self.candidate.replace(START+'\n            vector = None\n            capacity = 0\n',START+'\n')
            .replace('                if length > capacity:\n                    vector = (ctypes.c_ubyte*length)()\n                    capacity = length',ALLOCATION),SOURCE)
        with self.assertRaises(AssertionError):query_functions(SOURCE.replace(ALLOCATION,'                vector = None'))
        # Allocation benchmark uses the same AST statements, not another allocator.
        def growth(tree):
            return [ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(tree))
                    if isinstance(n,ast.If) and ast.unparse(n.test)=='length > capacity']
        from profile_saved_mincore_vectors import GROWTH
        self.assertEqual(growth(self.candidate),growth(GROWTH.strip()))

    def test_captured_gap_one_survives_runtime_policy_change_and_helper_drift_is_rejected(self):
        current=(Path(__file__).resolve().parents[1]/'scripts/snapshot_cache_residency.py').read_text()
        with self.assertRaises(AssertionError):query_functions(current)
        with self.assertRaisesRegex(AssertionError,'batch result helper'):
            query_functions(SOURCE.replace('values = [batch.get(int(offset)) for offset in ordered]','values = []'))

    def test_growth_reuse_only_prefix_gaps_full_bytes_and_original_rows(self):
        offsets=np.array([40,3,1,2,4,5,10,11,20,21,22,23,24,25,26,UNKNOWN,3],dtype='<u8')
        compare(self.functions,offsets,0)
        result=synthetic_query(self.functions['candidate'],offsets)
        vectors=result['vectors']
        self.assertEqual([len(v) for v in vectors],[5,5,7,7])
        self.assertIs(vectors[0],vectors[1]);self.assertIs(vectors[2],vectors[3])
        self.assertIsNot(vectors[0],vectors[2])
        self.assertEqual(vectors[1][2],int(PATTERN[3]))  # stale tail is still present
        self.assertEqual(vectors[3][1],int(PATTERN[21%8]))
        # Chained one-page gaps are queried, but excluded from byte/bit statistics.
        offsets=np.array([9,1,3,5,7,9,UNKNOWN],dtype='<u8')
        compare(self.functions,offsets,4)
        result=synthetic_query(self.functions['candidate'],offsets,4)
        self.assertEqual(list(result['projections'][0]),[1,3,5,7,9])
        self.assertEqual(result['state'].query_statistics[0]['mincore_resident_pages'],5)

    def test_empty_unknown_high_offsets_and_missing_zero_pfns(self):
        for offsets in ([],[UNKNOWN,UNKNOWN],[1<<40],list(range(1,32))+[13,22,UNKNOWN]):
            with self.subTest(offsets=offsets):compare(self.functions,np.array(offsets,dtype='<u8'),7)

    def test_each_query_starts_with_fresh_capacity(self):
        function=self.functions['candidate']
        result=synthetic_query(function,[1,2,3,4,5,10])
        state=result['state'];previous=list(result['vectors'])
        identity=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)
        with patch.object(runtime.os,'fstat',return_value=identity):function(state,np.array([20],dtype='<u8'))
        self.assertEqual(len(result['vectors'][-1]),1)
        self.assertTrue(all(result['vectors'][-1] is not v for v in previous))
        self.assertEqual([r['query'] for r in state.query_statistics],[5,6])

    def test_mincore_failure_aborts_before_batch_and_no_success_records(self):
        for fail_at in (1,2):
            for name,function in self.functions.items():
                with self.subTest(name=name,fail_at=fail_at):
                    result=synthetic_query(function,[1,2,3,10,11],fail_at=fail_at)
                    self.assertEqual(result['error'],'AssertionError')
                    self.assertEqual(len(result['vectors']),fail_at)
                    self.assertFalse(any(e[0]=='batch' for e in result['events']))
                    self.assertEqual(result['state'].query_statistics,[])
                    self.assertEqual(result['state'].query_costs,[])

    def test_batch_failure_does_not_return_partial_success_or_fallback(self):
        for function in self.functions.values():
            result=synthetic_query(function,[1,3,10],batch_error=True)
            self.assertEqual(result['error'],'RuntimeError')
            self.assertEqual(result['state'].query_statistics,[])
            self.assertEqual(result['state'].query_costs,[])
            self.assertFalse(any(e[0]=='scalar' for e in result['events']))

    def test_growth_allocation_failure_stops_before_next_call(self):
        class FailingType:
            calls=0
            def __mul__(self,length):
                self.calls+=1
                if self.calls==2:raise MemoryError('synthetic allocation failure')
                return ctypes.c_ubyte*length
        function=self.functions['candidate']
        fake=SimpleNamespace(c_ubyte=FailingType(),c_uint64=ctypes.c_uint64,byref=ctypes.byref)
        with patch.dict(function.__globals__,ctypes=fake):
            result=synthetic_query(function,[1,10,11,12])
        self.assertEqual(result['error'],'MemoryError')
        self.assertEqual(len(result['vectors']),1)
        self.assertFalse(any(e[0]=='batch' for e in result['events']))
        self.assertEqual(result['state'].query_statistics,[])
        self.assertEqual(result['state'].query_costs,[])

    def test_default_scalar_entry_identical_and_never_reads_clock(self):
        for offsets in ([],[UNKNOWN],[5,1,2,3,3,4,UNKNOWN,1,13,22]):
            results=[]
            for function in self.functions.values():
                with patch.dict(function.__globals__,_query_clock=lambda:(_ for _ in ()).throw(AssertionError('clock'))):
                    results.append(synthetic_query(function,offsets,batch=False))
            a,b=results
            self.assertNotIn('error',a);self.assertNotIn('error',b)
            self.assertTrue(np.array_equal(a['result'],b['result']))
            self.assertEqual(a['state'].query_statistics,b['state'].query_statistics)
            self.assertEqual(a['events'],b['events'])
            self.assertEqual([len(v) for v in a['vectors']],[len(v) for v in b['vectors']])
            self.assertEqual(b['state'].query_costs,[])

    def test_allocation_steps_counts_and_run_spans(self):
        offsets=np.array([1,3,10,20,21,22,23,24,40],dtype='<u8')
        _,runs=query_runs(offsets)
        self.assertEqual(runs,[(1,3),(10,1),(20,5),(40,1)])
        lengths=[n for _,n in runs]
        self.assertEqual(allocation_counts(lengths),dict(current=dict(allocations=4,allocated_vector_bytes=10),
            candidate=dict(allocations=2,allocated_vector_bytes=8),maximum_vector_bytes=5))
        functions=allocation_functions()
        self.assertEqual(len(functions['current'](lengths)),1)
        self.assertEqual(len(functions['candidate'](lengths)),5)
        self.assertIsNone(functions['current']([]));self.assertIsNone(functions['candidate']([]))


if __name__=='__main__':unittest.main()
