"""Real query methods with synthetic mincore/BPF only; no maps, probes or VM."""
import ctypes
import errno
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'scripts'),
             str(Path(__file__).resolve().parent/'fixtures')]
from snapshot_cache_residency import BatchEntries,SnapshotCache,PAGE,PRESENT
from snapshot_batch_loop import SnapshotCache as ContiguousCache
from test_snapshot_bpf_batch import FakeBatch,INFO,UNKNOWN,synthetic_query

IDENTITY=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)


def make_query(rows,residency,*,enabled=True,previous=False,fail_at=None,rc=-errno.ENOENT):
    lib=FakeBatch(rows,rc=rc)
    s=synthetic_query(lib,BatchEntries(lib,78,INFO) if enabled else None)
    if previous:s.__class__=ContiguousCache
    io=[];requests=[];vectors=[]
    update=lib.bpf_map_update_elem
    def publish(*args):io.append(('token',));return update(*args)
    lib.bpf_map_update_elem=publish
    table={key:(token,pfn) for key,token,pfn in rows}
    def scalar(fd,key,value):
        offset=int(ctypes.cast(key,ctypes.POINTER(ctypes.c_uint64))[0]);io.append(('scalar',offset))
        if offset not in table:return -errno.ENOENT
        value[0],value[1]=table[offset];return 0
    lib.bpf_map_lookup_elem=scalar
    def mincore(address,length,vector):
        first=(address-s.address)//PAGE
        assert (address-s.address)%PAGE==0 and length%PAGE==0
        assert len(vector)==length//PAGE
        io.append(('mincore',first,length//PAGE));vectors.append(vector)
        if len(vectors)==fail_at:return -1
        for index in range(length//PAGE):vector[index]=residency.get(first+index,1)
        return 0
    s.libc=SimpleNamespace(mincore=mincore)
    lookup=lib.bpf_map_lookup_batch.fn
    def read(*args):io.append(('batch',));return lookup(*args)
    lib.bpf_map_lookup_batch.fn=read
    if enabled:
        original=s.batch_entries.read
        def selected(ordered,token):
            requests.append((ordered.tolist(),token));return original(ordered,token)
        s.batch_entries.read=selected
    return s,io,requests,vectors


def run(s,offsets):
    with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY):
        return s.query(np.array(offsets,dtype='<u8'))


class MincoreGapTests(unittest.TestCase):
    def test_sparse_span_and_original_stats_match_contiguous_reference(self):
        offsets=[9,1,3,6,8,UNKNOWN,8]
        rows=[(1,5,11),(2,5,222),(3,5,0),(6,4,66),(7,5,777),(9,5,99),(99,5,999)]
        residency={1:0,2:1,3:1,6:1,7:1,8:0,9:3}
        old,old_io,_,_=make_query(rows,residency,previous=True)
        new,io,requests,vectors=make_query(rows,residency)
        before,after=run(old,offsets),run(new,offsets)
        self.assertTrue(np.array_equal(before,after))
        self.assertEqual(after.dtype,np.dtype('<u8'))
        self.assertEqual(after.tolist(),[PRESENT|99,0,0,0,0,0,0])
        self.assertEqual(old.query_statistics,new.query_statistics)
        self.assertEqual(new.query_statistics[0]['mincore_resident_pages'],3)
        self.assertEqual(new.query_statistics[0]['missing_kernel_results'],3)
        self.assertEqual(new.query_statistics[0]['bpf_lookup']['returned_entries'],7)
        self.assertEqual(io,[('token',),('mincore',1,9),('batch',)])
        self.assertEqual(requests,[([1,3,6,8,9],5)])
        self.assertEqual([len(v) for v in vectors],[9])
        self.assertEqual(len([e for e in old_io if e[0]=='mincore']),4)

    def test_default_query_keeps_contiguous_ranges_and_scalar_row_order(self):
        offsets=[3,1,3,6,8,UNKNOWN]
        rows=[(i,5,i+100) for i in [1,3,6,8]]
        old,old_io,_,_=make_query(rows,{},enabled=False,previous=True)
        new,io,requests,_=make_query(rows,{},enabled=False)
        with patch('snapshot_cache_residency._query_clock',side_effect=AssertionError('default clock')):
            a,b=run(old,offsets),run(new,offsets)
        self.assertTrue(np.array_equal(a,b))
        self.assertEqual(old.query_statistics,new.query_statistics)
        self.assertEqual(old_io,io)
        self.assertEqual([e for e in io if e[0]=='mincore'],
            [('mincore',1,1),('mincore',3,1),('mincore',6,1),('mincore',8,1)])
        self.assertEqual([e[1] for e in io if e[0]=='scalar'],[3,1,3,6,8])
        self.assertEqual(requests,[]);self.assertEqual(new.query_costs,[])

    def test_resident_gap_pages_never_inflate_resident_statistic(self):
        s,io,_,_=make_query([(0,5,100),(1,5,999),(2,5,102)],{0:0,1:1,2:0})
        result=run(s,[0,2])
        self.assertEqual(result.tolist(),[0,0])
        self.assertEqual(io,[('token',),('mincore',0,3),('batch',)])
        q=s.query_statistics[0]
        self.assertEqual(q['requested_pages'],2)
        self.assertEqual(q['mincore_resident_pages'],0)
        self.assertEqual(q['confirmed_pfn_pages'],0)
        self.assertEqual(q['missing_kernel_results'],0)

    def test_chained_single_gaps_use_real_span_not_number_of_requests(self):
        s,io,_,vectors=make_query([(i,5,i+100) for i in [0,2,4,6,8]],{8:0})
        result=run(s,[8,4,0,6,2,4])
        self.assertEqual(io,[('token',),('mincore',0,9),('batch',)])
        self.assertEqual(len(vectors[0]),9)
        self.assertEqual(result.tolist(),[0,PRESENT|104,PRESENT|100,PRESENT|106,PRESENT|102,PRESENT|104])
        self.assertEqual(s.query_statistics[0]['mincore_resident_pages'],4)

    def test_two_missing_pages_merge_but_three_do_not(self):
        s,io,_,_=make_query([(1,5,101),(4,5,104),(8,5,108)],{})
        run(s,[1,4,8])
        self.assertEqual(io,[('token',),('mincore',1,4),('mincore',8,1),('batch',)])

    def test_empty_unknown_and_single_page(self):
        for offsets,expected in [([],[]),([UNKNOWN,UNKNOWN],[]),([7,7,UNKNOWN],[('mincore',7,1)])]:
            with self.subTest(offsets=offsets):
                s,io,requests,_=make_query([(7,5,107)],{})
                run(s,offsets)
                self.assertEqual([e for e in io if e[0]=='mincore'],expected)
                self.assertEqual(io[0],('token',));self.assertEqual(io[-1],('batch',))
                self.assertEqual(len(requests),1)
                self.assertEqual(s.query_statistics[0]['missing_kernel_results'],0)

    def test_high_offsets_keep_integer_vector_indices(self):
        start=1<<40
        s,io,_,_=make_query([(start,5,101),(start+2,5,102)],{start:0,start+2:3})
        self.assertEqual(run(s,[start+2,start]).tolist(),[PRESENT|102,0])
        self.assertEqual(io,[('token',),('mincore',start,3),('batch',)])

    def test_runtime_projection_keeps_full_bytes_python_ints_and_requested_order(self):
        for start in (0,1<<40,(1<<63)+8):
            with self.subTest(start=start):
                wanted=[start+i for i in (0,2,5,7)]
                values=[128,129,254,255]
                residency=dict(zip(wanted,values))
                residency.update({start+1:255,start+6:129})
                s,io,requests,_=make_query([(p,5,100+i) for i,p in enumerate(wanted)],residency)
                original=s._batch_result
                observed=[]
                def inspect(offsets,ordered,resident,batch):
                    self.assertEqual(list(resident),wanted)
                    self.assertEqual(list(resident.values()),values)
                    self.assertTrue(all(type(k) is int and type(v) is int for k,v in resident.items()))
                    observed.append(True)
                    return original(offsets,ordered,resident,batch)
                s._batch_result=inspect
                result=run(s,[start+7,start,start+2,UNKNOWN,start+5,start+7])
                self.assertEqual(result.dtype,np.dtype('<u8'))
                self.assertEqual(result.tolist(),[PRESENT|103,0,PRESENT|101,0,0,PRESENT|103])
                self.assertEqual(observed,[True])
                self.assertEqual(io,[('token',),('mincore',start,8),('batch',)])
                self.assertEqual(requests,[(wanted,5)])
                self.assertEqual(s.query_statistics,[dict(query=5,requested_pages=4,missing_kernel_results=0,
                    mincore_resident_pages=2,confirmed_pfn_pages=3,no_file_mapping_pages=1,
                    bpf_lookup=dict(mode='batch',returncode=-errno.ENOENT,returned_entries=4))])

    def test_merged_mincore_error_stops_before_batch_and_success_records(self):
        s,io,requests,_=make_query([(i,5,i+100) for i in [1,3,7,9]],{},fail_at=2)
        with self.assertRaises(AssertionError):run(s,[1,3,7,9])
        self.assertEqual(io,[('token',),('mincore',1,3),('mincore',7,3)])
        self.assertEqual(requests,[])
        self.assertEqual(s.query_statistics,[]);self.assertEqual(s.query_costs,[])

    def test_batch_error_still_fails_without_scalar_fallback(self):
        s,io,_,_=make_query([(1,5,101),(3,5,103)],{},rc=-errno.EFAULT)
        with self.assertRaises(RuntimeError):run(s,[1,3])
        self.assertEqual(io,[('token',),('mincore',1,3),('batch',)])
        self.assertEqual(s.query_statistics,[]);self.assertEqual(s.query_costs,[])


if __name__=='__main__':unittest.main()
