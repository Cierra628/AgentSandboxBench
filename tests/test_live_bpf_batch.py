"""Mocked private option forwarding and pagemap metadata; no devices or services."""
import ctypes
import inspect
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_task_cache import LiveTaskCache, _batch_memory, PAGE, PRESENT
import validate_isolated_agentenv as validator


class LiveBatchTests(unittest.TestCase):
    def start_queries(self, enabled, session='restored'):
        with tempfile.TemporaryDirectory() as name:
            c = LiveTaskCache.__new__(LiveTaskCache)
            c.session,c.batch_lookup = session,enabled
            c.proof,c.directory,c.out = {'slots':['owned-slot']},Path(name),Path(name)
            c.lower_cache,c.provider_cache = True,True
            layer=SimpleNamespace(path=Path('/owned/layer'))
            with patch('snapshot_cache_residency.SnapshotCache') as query, \
                 patch('snapshot_layer_metadata.owned_memory_layer',return_value=layer), \
                 patch('provider_slot_cache.ProviderCache') as provider:
                c._start_snapshot_queries(Path('/owned/proc'))
                if session=='parent':
                    query.assert_not_called();provider.assert_not_called();return
                self.assertEqual(query.call_count,2)
                first,last=query.call_args_list
                self.assertEqual(first.args,(Path('/owned/proc'),['owned-slot'],Path(name)))
                self.assertEqual(first.kwargs,{'batch_lookup':True} if enabled else {})
                expected={'file_path':layer.path}
                if enabled:expected['batch_lookup']=True
                self.assertEqual(last.kwargs,expected)
                self.assertEqual(last.args,(Path('/owned/proc'),[],Path(name)/'lower-query'))
                provider.assert_called_once()

    def test_default_keeps_original_query_arguments(self):
        self.assertFalse(inspect.signature(LiveTaskCache).parameters['batch_lookup'].default)
        self.start_queries(False)

    def test_enabled_is_forwarded_to_both_queries(self): self.start_queries(True)

    def test_parent_does_not_create_query(self): self.start_queries(True,'parent')

    def test_validator_rejects_flag_outside_private_get_data_before_allocation(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'private provider GET_DATA'):
                validator.main(SimpleNamespace(snapshot_bpf_batch=True))
            allocate.assert_not_called()

    def memory(self, entries=None, short=False):
        def array(start,size):return SimpleNamespace(ctypes=SimpleNamespace(data=start),nbytes=size)
        def query(a,b):return SimpleNamespace(batch_entries=SimpleNamespace(keys=a,values=b,buffer_bytes=a.nbytes+b.nbytes))
        queries=[query(array(PAGE+100,PAGE-100),array(2*PAGE,2*PAGE)),
                 query(array(PAGE,100),array(4*PAGE+20,300))]
        entries=entries if entries is not None else [PRESENT|10,PRESENT|20,0,PRESENT|20]
        calls=[]
        def pread(fd,size,offset):
            calls.append((size,offset))
            raw=struct.pack('<4Q',*entries)
            return raw[:-1] if short else raw
        with patch('live_task_cache.Path.open') as opened, \
             patch('live_task_cache.os.pread',side_effect=pread), \
             patch('live_task_cache.Path.read_text',return_value='VmRSS:\t128 kB\n'):
            opened.return_value.__enter__.return_value.fileno.return_value=91
            result=_batch_memory(queries)
        self.assertEqual(calls,[(32,8)])
        return result

    def test_memory_counts_full_pages_boundaries_and_physical_alias_once(self):
        x=self.memory()
        self.assertEqual(x['receive_capacity_bytes'],3*PAGE+300)
        self.assertEqual(x['covered_virtual_pages'],4)
        self.assertEqual(x['resident_virtual_pages'],3)
        self.assertEqual(x['whole_resident_virtual_pages'],2)
        self.assertEqual(x['resident_physical_union_bytes'],2*PAGE)
        self.assertEqual(x['whole_resident_physical_bytes'],2*PAGE)
        self.assertEqual(x['boundary_only_resident_physical_bytes'],0)
        self.assertEqual(x['controller_process_rss_bytes'],128*1024)
        self.assertGreaterEqual(x['cpu_ns'],0)

    def test_short_pagemap_fails(self):
        with self.assertRaisesRegex(AssertionError,'short batch buffer'):self.memory(short=True)

    def test_hidden_pfn_fails(self):
        with self.assertRaisesRegex(AssertionError,'PFN unavailable'):
            self.memory(entries=[PRESENT,PRESENT|20,0,PRESENT|20])


if __name__=='__main__':unittest.main()
