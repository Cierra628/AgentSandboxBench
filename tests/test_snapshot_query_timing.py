"""Deterministic clocks and synthetic BPF/libc only; no probes, maps or services."""
import copy
import errno
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'scripts'),
               str(Path(__file__).resolve().parent/'fixtures')]
from snapshot_cache_residency import BatchEntries, SnapshotCache, _query_clock, _query_cost
from snapshot_batch_loop import SnapshotCache as PreviousCache
from live_task_cache import _timed
from test_snapshot_bpf_batch import FakeBatch, INFO, UNKNOWN, synthetic_query

IDENTITY = SimpleNamespace(st_dev=1, st_ino=2, st_rdev=0)
STAGES = ['prepare', 'mincore_scan', 'batch_lookup', 'batch_filter', 'result_restore']
ROWS = [(1,5,11), (2,4,22), (4,5,0), (5,5,55), (99,5,99)]


def make_query(rows=ROWS, **kwargs):
    lib = FakeBatch(rows, **kwargs)
    s = synthetic_query(lib, BatchEntries(lib, 78, INFO))
    lookup = lib.bpf_map_lookup_batch.fn
    def read(*args):
        s.events.append('batch')
        return lookup(*args)
    lib.bpf_map_lookup_batch.fn = read
    return s, lib


class QueryTimingTests(unittest.TestCase):
    def check_cost(self, cost):
        self.assertEqual(list(cost['stages']), STAGES)
        cursor = cost['host_start_ns']
        for stage in cost['stages'].values():
            self.assertGreaterEqual(stage['host_start_ns'], cursor)
            self.assertEqual(stage['elapsed_ns'], stage['host_end_ns']-stage['host_start_ns'])
            self.assertGreaterEqual(stage['cpu_ns'], 0)
            cursor = stage['host_end_ns']
        self.assertLessEqual(cursor, cost['host_end_ns'])
        for dimension in ('cpu', 'elapsed'):
            key = dimension+'_ns'
            self.assertGreaterEqual(cost['unmeasured_'+key], 0)
            self.assertEqual(cost[key], sum(s[key] for s in cost['stages'].values())+cost['unmeasured_'+key])
        self.assertEqual(cost['elapsed_ns'], cost['host_end_ns']-cost['host_start_ns'])

    def test_clock_exact_differences(self):
        with patch('snapshot_cache_residency.time.monotonic_ns', side_effect=[100,170]), \
             patch('snapshot_cache_residency.time.thread_time_ns', side_effect=[20,50]):
            self.assertEqual(_query_cost(_query_clock()),
                dict(cpu_ns=30,elapsed_ns=70,host_start_ns=100,host_end_ns=170))

    def test_full_query_matches_frozen_reference_outputs_statistics_and_io(self):
        for offsets in ([5,1,2,3,3,4,UNKNOWN,1], [], [UNKNOWN,UNKNOWN]):
            with self.subTest(offsets=offsets):
                current,lib = make_query()
                previous,old_lib = make_query(); previous.__class__ = PreviousCache
                values = np.array(offsets,dtype='<u8')
                with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY), \
                     patch('snapshot_cache_residency.time.monotonic_ns',side_effect=iter(range(100,100000,100))), \
                     patch('snapshot_cache_residency.time.thread_time_ns',side_effect=iter(range(10,10000,10))):
                    old = previous.query(values); new = current.query(values)
                self.assertTrue(np.array_equal(old,new))
                self.assertEqual(old.dtype,new.dtype)
                self.assertEqual(previous.query_statistics,current.query_statistics)
                self.assertEqual(previous.events,current.events)
                self.assertEqual(lib.calls,old_lib.calls)
                self.assertEqual(len(lib.calls),1)
                self.assertEqual(len(current.query_costs),1)
                self.assertEqual(current.query_costs[0]['query'],5)
                self.check_cost(current.query_costs[0])

    def test_default_query_does_not_read_clocks_or_add_costs(self):
        s = synthetic_query(SimpleNamespace(bpf_map_lookup_elem=lambda *args:-errno.ENOENT))
        with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY), \
             patch('snapshot_cache_residency.time.monotonic_ns',side_effect=AssertionError('default clock')), \
             patch('snapshot_cache_residency.time.thread_time_ns',side_effect=AssertionError('default clock')):
            s.query(np.array([1,1,UNKNOWN],dtype='<u8'))
        self.assertEqual(s.query_costs,[])
        self.assertNotIn('query_costs',s.query_statistics[0])

    def test_clock_reads_are_constant_not_per_mincore_run(self):
        counts=[]
        for offsets in ([1,2,3], list(range(1,100,3)), list(range(1,100,4))):
            s,_ = make_query()
            with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY), \
                 patch('snapshot_cache_residency.time.monotonic_ns',return_value=100) as wall, \
                 patch('snapshot_cache_residency.time.thread_time_ns',return_value=10) as cpu:
                s.query(np.array(offsets,dtype='<u8'))
            self.check_cost(s.query_costs[0])
            counts.append((wall.call_count,cpu.call_count))
        self.assertEqual(counts,[(11,11),(11,11),(11,11)])

    def test_consecutive_queries_keep_tokens_and_previous_costs(self):
        s,lib = make_query()
        with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY):
            s.query(np.array([1],dtype='<u8'))
            first = copy.deepcopy(s.query_costs[0])
            lib.rows = [(1,6,12)]
            s.lib.bpf_map_update_elem = lambda *args:0
            s.query(np.array([1],dtype='<u8'))
        self.assertEqual([c['query'] for c in s.query_costs],[5,6])
        self.assertEqual([c['query'] for c in s.query_statistics],[5,6])
        self.assertEqual(s.query_costs[0],first)
        self.assertEqual(len(lib.calls),2)
        for cost in s.query_costs:self.check_cost(cost)

    def test_failure_never_adds_success_cost_or_falls_back(self):
        for boundary in ('identity','tracer','token','mincore','batch','restore'):
            with self.subTest(boundary=boundary):
                s,lib = make_query(rc=-errno.EFAULT if boundary=='batch' else -errno.ENOENT)
                s.lib.bpf_map_lookup_elem = Mock(side_effect=AssertionError('scalar fallback'))
                if boundary=='identity':s.identity = (9,9,9)
                if boundary=='tracer':s.tracer.poll = lambda:1
                if boundary=='token':s.lib.bpf_map_update_elem = lambda *args:-1
                if boundary=='mincore':s.libc.mincore = lambda *args:-1
                if boundary=='restore':s._batch_result = Mock(side_effect=RuntimeError('restore failure'))
                with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY):
                    with self.assertRaises((AssertionError,RuntimeError)):
                        s.query(np.array([1],dtype='<u8'))
                self.assertEqual(s.query_costs,[])
                self.assertEqual(s.query_statistics,[])
                self.assertEqual(len(lib.calls),1 if boundary in ('batch','restore') else 0)
                s.lib.bpf_map_lookup_elem.assert_not_called()

    def test_batch_failure_replaces_previous_partial_costs(self):
        s,lib = make_query()
        s.batch_entries.read(np.array([1],dtype='<u8'),5)
        self.assertEqual(list(s.batch_entries.last_costs),['batch_lookup','batch_filter'])
        lib.rc = -errno.EFAULT
        with self.assertRaises(RuntimeError):s.batch_entries.read(np.array([1],dtype='<u8'),5)
        self.assertEqual(list(s.batch_entries.last_costs),['batch_lookup'])
        self.assertIsNone(s.batch_entries.last_statistics['returned_entries'])

    def test_outer_query_timer_includes_cost_record_bookkeeping(self):
        s,_ = make_query()
        clocks=dict(wall=100,cpu=10)
        class CostList(list):
            def append(self,record):
                clocks['cpu']+=1000;clocks['wall']+=2000
                super().append(record)
        s.query_costs = CostList()
        costs={}
        with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY), \
             patch('snapshot_cache_residency.time.monotonic_ns',side_effect=lambda:clocks['wall']), \
             patch('snapshot_cache_residency.time.thread_time_ns',side_effect=lambda:clocks['cpu']):
            _timed(costs,'backend_query_first',s.query,np.array([1],dtype='<u8'))
        self.check_cost(s.query_costs[0])
        self.assertEqual(s.query_costs[0]['cpu_ns'],0)
        self.assertEqual(costs['backend_query_first']['cpu_ns'],1000)
        self.assertEqual(costs['backend_query_first']['elapsed_ns'],2000)

    def test_close_serializes_costs_separately_only_in_batch_mode(self):
        for enabled in (False,True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as name:
                s,_ = make_query()
                if not enabled:s.batch_entries = None
                s.directory=Path(name)
                s.mapping=Mock()
                with patch('snapshot_cache_residency.os.fstat',return_value=IDENTITY):
                    s.query(np.array([UNKNOWN],dtype='<u8'))
                from snapshot_cache_residency import SourceTracer
                s.tracer=SimpleNamespace(pid=1,poll=lambda:0,returncode=0)
                s.service=SourceTracer.__new__(SourceTracer)
                s.service.proc=s.tracer;s.service.leased={s.bank};s.service.fds={}
                s.service.pending_receipts=[];s.service.key=('mock','lower')
                SourceTracer.sources[s.service.key]=s.service
                s.mapfds={}
                with patch('snapshot_cache_residency.os.close') as close:s.close()
                close.assert_called_once_with(78)
                result=json.loads((s.directory/'snapshot-query-result.json').read_text())
                self.assertEqual(result['queries'],s.query_statistics)
                if enabled:
                    self.assertEqual(result['query_costs'],s.query_costs)
                    self.assertEqual(len(result['query_costs']),1)
                else:self.assertNotIn('query_costs',result)
                self.assertTrue(all('query_costs' not in q for q in result['queries']))

    def test_existing_owned_map_validator_query_helper_keeps_its_contract(self):
        from validate_snapshot_bpf_batch import query
        lib=FakeBatch(ROWS)
        lib.bpf_map_lookup_elem=Mock(side_effect=AssertionError('scalar fallback'))
        with patch('validate_snapshot_bpf_batch.os.fstat',return_value=IDENTITY):
            result,statistics,details=query(lib,78,BatchEntries(lib,78,INFO))
        self.assertEqual(len(result),9)
        self.assertEqual(statistics['query'],5)
        self.assertEqual(statistics['missing_kernel_results'],4)
        self.assertEqual(details,dict(mode='batch',returncode=-2,returned_entries=5))
        self.assertNotIn('query_costs',statistics)
        lib.bpf_map_lookup_elem.assert_not_called()


if __name__=='__main__':unittest.main()
