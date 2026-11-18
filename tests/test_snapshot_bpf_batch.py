"""Synthetic BPF/libc calls only; no kernel maps, probes, devices or services."""
import ctypes
import threading
import errno
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from snapshot_cache_residency import BatchEntries, SnapshotCache, PAGE, PRESENT

UNKNOWN = (1 << 64)-1
INFO = dict(type='hash', bytes_key=8, bytes_value=16, flags=0, max_entries=16)


class Call:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


class FakeBatch:
    def __init__(self, rows=(), rc=-errno.ENOENT, count=None):
        self.rows, self.rc, self.count = rows, rc, count
        self.calls = []
        self.bpf_map_lookup_batch = Call(self.lookup)

    def lookup(self, fd, incoming, outgoing, keys, values, count, opts):
        self.calls.append((fd, incoming, opts))
        self.input_count = ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0]
        outkeys = (ctypes.c_uint64*self.input_count).from_address(keys)
        outvalues = (ctypes.c_uint64*(2*self.input_count)).from_address(values)
        for i, (key, token, pfn) in enumerate(self.rows):
            outkeys[i] = key
            outvalues[2*i], outvalues[2*i+1] = token, pfn
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = len(self.rows) if self.count is None else self.count
        return self.rc


def synthetic_query(lib, batch=None):
    """Use the real query method with synthetic identity, token and mincore."""
    s = SnapshotCache.__new__(SnapshotCache)
    s.query_lock = threading.Lock()
    s.bank, s.limit = 1, 1<<40
    s.fd, s.identity, s.address = 78, (1, 2, 0), 100*PAGE
    s.tracer = SimpleNamespace(poll=lambda: None)
    s.mapfds, s.token, s.query_statistics = {'entries': 78, 'token': 79}, 4, []
    s.query_costs = []
    s.batch_entries, s.lib = batch, lib
    s.events = []
    def update(fd, key, token, flags):
        s.events.append('token')
        assert ctypes.cast(token, ctypes.POINTER(ctypes.c_uint64))[0] == 5
        return 0
    lib.bpf_map_delete_elem = lambda *args:0
    lib.bpf_map_update_elem = update
    def mincore(address, length, vector):
        s.events.append('mincore')
        first = (address-s.address)//PAGE
        for i in range(length//PAGE): vector[i] = 0 if first+i == 5 else 1
        return 0
    s.libc = SimpleNamespace(mincore=mincore)
    return s


class BpfBatchTests(unittest.TestCase):
    def test_capacity_layout_gates_before_library_symbol_or_allocation(self):
        for field, value in [('type','percpu_hash'), ('bytes_key',4), ('bytes_value',8),
                             ('flags',1), ('max_entries',0), ('max_entries',131073),
                             ('max_entries',1.5)]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError): BatchEntries(object(), 78, dict(INFO, **{field:value}))
        with self.assertRaises(ValueError): BatchEntries(object(), 78, {})

    def test_new_symbol_required_only_for_enabled_mode(self):
        self.assertFalse(inspect.signature(SnapshotCache).parameters['batch_lookup'].default)
        with self.assertRaises(AttributeError): BatchEntries(object(), 78, INFO)
        # The scalar query below uses a library with no batch symbol.

    def test_end_with_data_filter_token_requested_keys_and_zero_pfn(self):
        lib = FakeBatch([(1,5,11), (2,4,22), (4,5,0), (99,5,99)])
        batch = BatchEntries(lib, 78, INFO)
        self.assertEqual(batch.read(np.array([1,2,3,4], dtype='<u8'), 5), {1:11,4:0})
        self.assertEqual(batch.last_statistics, dict(mode='batch',returncode=-errno.ENOENT,returned_entries=4))
        self.assertEqual(lib.calls, [(78,None,None)])
        self.assertEqual(lib.input_count, INFO['max_entries'])
        self.assertEqual(batch.buffer_bytes, 16*24)
        self.assertEqual(batch.lookup.restype, ctypes.c_int)
        self.assertEqual(len(batch.lookup.argtypes), 7)

    def test_empty_end_and_unused_tail_never_reused(self):
        lib = FakeBatch([(1,5,11)])
        batch = BatchEntries(lib, 78, INFO)
        batch.keys.fill(2); batch.values[:] = [5,22]
        self.assertEqual(batch.read(np.array([1,2], dtype='<u8'), 5), {1:11})
        ids = (id(batch.keys), id(batch.values))
        lib.rows = []
        self.assertEqual(batch.read(np.array([1,2], dtype='<u8'), 5), {})
        self.assertEqual(ids, (id(batch.keys), id(batch.values)))
        self.assertEqual(batch.last_statistics['returned_entries'], 0)

    def test_nonterminal_and_errors_discard_partial_data_without_retry(self):
        for rc in (0, -errno.ENOSPC, -errno.EFAULT, -errno.EOPNOTSUPP, -errno.EINVAL, -errno.EPERM):
            with self.subTest(rc=rc):
                lib = FakeBatch([(1,5,11)], rc=rc, count=(1<<32)-1 if rc == -errno.EFAULT else None)
                batch = BatchEntries(lib, 78, INFO)
                with self.assertRaisesRegex(RuntimeError, 'incomplete BPF batch'):
                    batch.read(np.array([1],dtype='<u8'),5)
                self.assertEqual(len(lib.calls),1)
                if rc == -errno.EFAULT:
                    self.assertIsNone(batch.last_statistics['returned_entries'])

    def test_invalid_count_rejected_before_buffer_read(self):
        batch = BatchEntries(FakeBatch(count=17),78,INFO)
        with self.assertRaisesRegex(RuntimeError,'exceeds capacity'):
            batch.read(np.array([1],dtype='<u8'),5)

    def pair(self, offsets):
        rows = [(1,5,11), (2,4,22), (4,5,0), (5,5,55), (99,5,99)]
        table = {key:(token,pfn) for key,token,pfn in rows}
        def scalar(fd, key, value):
            scalar_query.events.append('scalar')
            found = table.get(ctypes.cast(key,ctypes.POINTER(ctypes.c_uint64))[0])
            if found is None: return -errno.ENOENT
            value[0],value[1] = found
            return 0
        scalar_lib = SimpleNamespace(bpf_map_lookup_elem=scalar)
        scalar_query = synthetic_query(scalar_lib)
        lib = FakeBatch(rows)
        batch = BatchEntries(lib,78,INFO)
        batch_query = synthetic_query(lib,batch)
        lookup = lib.bpf_map_lookup_batch.fn
        def ordered_lookup(*args):
            batch_query.events.append('batch')
            return lookup(*args)
        lib.bpf_map_lookup_batch.fn = ordered_lookup
        offsets = np.array(offsets,dtype='<u8')
        with patch('snapshot_cache_residency.os.fstat',return_value=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)):
            a,b = scalar_query.query(offsets),batch_query.query(offsets)
        self.assertTrue(np.array_equal(a,b))
        self.assertEqual(a.dtype,np.dtype('<u8'))
        stats = dict(batch_query.query_statistics[0]); stats.pop('bpf_lookup')
        self.assertEqual(stats,scalar_query.query_statistics[0])
        self.assertEqual(batch_query.events[0],'token')
        self.assertEqual(batch_query.events[-1],'batch')
        self.assertTrue(all(e=='mincore' for e in batch_query.events[1:-1]))
        self.assertEqual(len(lib.calls),1)
        return a,stats

    def test_query_preserves_order_duplicates_missing_rows_and_residency(self):
        rows,stats = self.pair([5,1,2,3,3,4,UNKNOWN,1])
        self.assertEqual(rows.tolist(),[0,PRESENT|11,0,0,0,0,0,PRESENT|11])
        self.assertEqual(stats,dict(query=5,requested_pages=5,missing_kernel_results=3,
            mincore_resident_pages=4,confirmed_pfn_pages=2,no_file_mapping_pages=1))

    def test_empty_and_only_unknown_queries(self):
        for offsets in ([], [UNKNOWN,UNKNOWN]):
            with self.subTest(offsets=offsets):
                rows,stats = self.pair(offsets)
                self.assertEqual(rows.tolist(),[0]*len(offsets))
                self.assertEqual(stats['missing_kernel_results'],0)

    def test_query_failure_has_no_success_record_or_scalar_fallback(self):
        lib = FakeBatch([(1,5,11)],rc=-errno.EFAULT)
        s = synthetic_query(lib,BatchEntries(lib,78,INFO))
        with patch('snapshot_cache_residency.os.fstat',return_value=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)):
            with self.assertRaisesRegex(RuntimeError,'returncode.*-14'):
                s.query(np.array([1],dtype='<u8'))
        self.assertEqual(s.query_statistics,[])
        self.assertEqual(len(lib.calls),1)

    def test_constructor_default_needs_no_batch_symbol(self):
        self.constructor(False,dict(INFO,name='AT_entries1'))

    def test_constructor_enabled_uses_owned_entries_info(self):
        self.constructor(True, dict(INFO,name='AT_entries1',id=1))

    def constructor(self, enabled, entries_info):
        # Real temporary file/mapping, but all library/probe operations are mocked.
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            path = directory/'synthetic'; path.write_bytes(bytes(PAGE))
            fds = [os.open(os.devnull,os.O_RDONLY) for _ in range(8)]
            lib = FakeBatch() if enabled else SimpleNamespace()
            lib.bpf_map_delete_elem = Call(lambda *args:0)
            lib.bpf_map_get_fd_by_id = Call(lambda mapid:fds[mapid-1])
            lib.bpf_map_lookup_elem = Call(lambda *args:0)
            lib.bpf_map_update_elem = Call(lambda *args:0)
            libc = SimpleNamespace(mincore=Call(lambda *args:0))
            tracer = SimpleNamespace(pid=12345,returncode=0,poll=lambda:0)
            def start(*args,**kwargs):
                kwargs['stdout'].write('READY\n'); kwargs['stdout'].flush()
                return tracer
            infos=[SimpleNamespace(read_text=lambda i=i:f'map_id: {i}') for i in range(1,9)]
            replies=[json.dumps(dict(entries_info,name=f'AT_entries{i}')).encode() for i in range(1,8)]
            replies.append(json.dumps(dict(INFO,name='AT_token',bytes_value=32)).encode())
            with patch('snapshot_cache_residency.ctypes.CDLL',side_effect=[libc,lib]), \
                 patch('snapshot_cache_residency.subprocess.Popen',side_effect=start), \
                 patch('snapshot_cache_residency.Path.iterdir',return_value=infos), \
                 patch('snapshot_cache_residency.subprocess.check_output',side_effect=replies):
                s = SnapshotCache(None,[],directory,file_path=path,batch_lookup=enabled)
                try:
                    metadata = json.loads((directory/'snapshot-backend.json').read_text())
                    if enabled:
                        self.assertEqual(s.batch_entries.fd,fds[0])
                        self.assertEqual(metadata['batch_lookup']['map_info'],entries_info)
                        self.assertEqual(metadata['batch_lookup']['receive_buffer_bytes'],384)
                        self.assertEqual(metadata['batch_lookup']['mincore_max_gap_pages'],2)
                    else:
                        self.assertIsNone(s.batch_entries)
                        self.assertNotIn('batch_lookup',metadata)
                finally: s.close()
            for fd in fds:
                with self.assertRaises(OSError): os.fstat(fd)

    def test_owned_validator_creation_failure_is_not_pass(self):
        from validate_snapshot_bpf_batch import validate
        lib = SimpleNamespace(bpf_map_create=Call(lambda *args:-errno.EPERM),
            bpf_map_lookup_elem=Call(lambda *args:0),bpf_map_update_elem=Call(lambda *args:0))
        with tempfile.TemporaryDirectory() as name, \
             patch('validate_snapshot_bpf_batch.ctypes.CDLL',return_value=lib):
            out = Path(name)/'result'
            self.assertFalse(validate(out))
            result = json.loads((out/'result.json').read_text())
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['stage'],'create_owned_map')
            self.assertFalse(result['map_created'])

    def test_owned_validator_read_failure_closes_only_own_fd(self):
        from validate_snapshot_bpf_batch import MapInfo, validate
        fd = os.open(os.devnull,os.O_RDONLY)
        def info(fd, ptr, size):
            value = ctypes.cast(ptr,ctypes.POINTER(MapInfo)).contents
            value.type,value.id,value.key_size,value.value_size,value.max_entries,value.map_flags = 1,42,8,16,8,0
            return 0
        lib = FakeBatch(rc=-errno.EFAULT)
        lib.bpf_map_create = Call(lambda *args:fd)
        lib.bpf_obj_get_info_by_fd = Call(info)
        lib.bpf_map_lookup_elem = Call(lambda *args:-errno.ENOENT)
        lib.bpf_map_update_elem = Call(lambda *args:0)
        with tempfile.TemporaryDirectory() as name, \
             patch('validate_snapshot_bpf_batch.ctypes.CDLL',return_value=lib):
            out = Path(name)/'result'
            self.assertFalse(validate(out))
            result = json.loads((out/'result.json').read_text())
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(result['stage'],'empty')
            self.assertTrue(result['own_fd_closed'])
        with self.assertRaises(OSError): os.fstat(fd)


if __name__ == '__main__': unittest.main()
