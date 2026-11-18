"""Default path and measured-candidate integration; no real BPF or mincore."""
import ast
import ctypes
import errno
import inspect
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),str(Path(__file__).resolve().parent/'fixtures')]
from snapshot_cache_residency import SnapshotCache
from snapshot_batch_loop import SnapshotCache as PreviousCache
from profile_saved_query_results import array_result
from test_snapshot_bpf_batch import synthetic_query,UNKNOWN


class RuntimeResultArrayTests(unittest.TestCase):
    def test_measured_candidate_and_runtime_method_have_identical_body(self):
        def body(method):
            node=ast.parse(textwrap.dedent(inspect.getsource(method))).body[0]
            return [ast.dump(n,include_attributes=False) for n in node.body[1:]]
        self.assertEqual(body(SnapshotCache._batch_result),body(array_result))

    def test_default_query_matches_previous_full_entry_without_array_helper(self):
        table={1:(5,11),2:(4,22),4:(5,0),5:(5,55)}
        def make(cls):
            events=[]
            def lookup(fd,key,value):
                events.append('lookup')
                found=table.get(ctypes.cast(key,ctypes.POINTER(ctypes.c_uint64))[0])
                if found is None:return -errno.ENOENT
                value[0],value[1]=found;return 0
            s=synthetic_query(SimpleNamespace(bpf_map_lookup_elem=lookup))
            s.__class__=cls
            return s,events
        for offsets in ([5,1,2,3,3,4,UNKNOWN,1],[],[UNKNOWN,UNKNOWN]):
            with self.subTest(offsets=offsets):
                a,events_a=make(PreviousCache);b,events_b=make(SnapshotCache)
                with patch('snapshot_cache_residency.os.fstat',return_value=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)), \
                     patch.object(SnapshotCache,'_batch_result',side_effect=AssertionError('default must not call array helper')):
                    old,new=a.query(np.array(offsets,dtype='<u8')),b.query(np.array(offsets,dtype='<u8'))
                self.assertTrue(np.array_equal(old,new))
                self.assertEqual(a.query_statistics,b.query_statistics)
                self.assertEqual(a.events,b.events)
                self.assertEqual(events_a,events_b)


if __name__=='__main__':unittest.main()
