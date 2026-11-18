"""Saved/synthetic metadata only: no real devices, guests, or services."""
import copy
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from provider_slot_cache import (MAGIC,PRESENT,ProviderCache,ROW,SEGMENT,UNIT,UNKNOWN,
    account_observed_provider,account_provider)
from report_live_task_cache import physical_bytes,residency_gaps
import test_provider_slot_cache as fixtures
from test_kernel_cache_metadata import dump
from audit_provider_segments import independent_segments


class ProviderSegmentTests(unittest.TestCase):
    def data(self,**kwargs):
        args=fixtures.ProviderTests().data(**kwargs)
        for frame in args[-2:]:
            for row in frame['rows']:
                entries=[[4,i*UNIT] for i in range(row['before'][3]//UNIT)]
                row['segments']=dict(schema=1,unit_bytes=UNIT,before=entries,after=copy.deepcopy(entries))
        return args

    def entries(self,args):
        return [row['segments'][k] for f in args[-2:] for row in f['rows'] for k in ('before','after')]

    def test_tail_adds_current_copies_without_redefining_legacy_fields(self):
        args=self.data(length=512);legacy,_=account_provider(*args)
        result,pfns=account_observed_provider(*args)
        self.assertTrue(all(result[k]==v for k,v in legacy.items()))
        self.assertEqual(result['host_provider_current_bytes'],0)
        self.assertEqual(result['host_provider_segment_added_current_bytes'],8192)
        self.assertEqual(result['host_provider_combined_current_bytes'],8192)
        self.assertEqual(result['host_four_segment_unconfirmed_guest_capacity_bytes'],0)
        self.assertEqual(pfns,{20,40,50})

    def test_changed_global_record_does_not_discard_untouched_segments(self):
        args=self.data(length=512);args[-1]['rows'][0]['after'][1]+=4
        result,_=account_observed_provider(*args)
        self.assertEqual(result['provider_stable_slots'],0)
        self.assertEqual(result['host_provider_segment_current_bytes'],8192)

    def test_all_four_copies_and_odd_or_unknown_entry_reject_whole_host_page(self):
        for copy_index in range(4):
            args=self.data(length=512);self.entries(args)[copy_index][0][0]+=2
            result,_=account_observed_provider(*args)
            self.assertEqual(result['host_provider_segment_current_bytes'],4096)
        for generation,offset in ((5,0),(4,UNKNOWN)):
            args=self.data(length=512)
            for entries in self.entries(args):entries[0]=[generation,offset]
            self.assertEqual(account_observed_provider(*args)[0]['host_provider_segment_current_bytes'],4096)

    def test_current_old_and_mixed_versions_remain_separate(self):
        args=self.data(length=512)
        args[2][1]=(2,PRESENT|30,PRESENT|30,PRESENT|30)
        for entries in self.entries(args):entries[7][1]=4096
        result,_=account_observed_provider(*args)
        self.assertEqual(result['host_provider_segment_current_bytes'],0)
        self.assertEqual(result['host_provider_segment_old_snapshot_bytes'],4096)
        self.assertEqual(result['provider_segment_mixed_or_edge_resident_pages'],1)
        self.assertEqual(result['provider_segment_unattributed_resident_bytes'],4096)

    def test_missing_candidate_guest_identity_or_provider_pfn_change_stays_unknown(self):
        for kind in ('candidate','guest','pfn','released','allocation'):
            args=self.data(length=512)
            if kind=='candidate':
                for entries in self.entries(args):entries[0][1]=2**30
            elif kind=='guest':args[1]=dump([(1,6),(2,4)])
            elif kind=='pfn':args[-1]['rows'][0]['pagemap'][0]=PRESENT|90
            elif kind=='released':args[-1]['rows'][0]['after'][6]=5
            else:args[-1]['rows'][0]['after'][2]+=512
            result,_=account_observed_provider(*args)
            self.assertEqual(result['host_provider_segment_current_bytes'],0 if kind in ('released','allocation') else 4096,kind)

    def test_host_boundary_pages_are_not_counted_as_full_copies(self):
        result,_=account_observed_provider(*self.data(address=4608,length=512))
        self.assertEqual(result['host_provider_segment_current_bytes'],4096)
        self.assertEqual(result['provider_segment_mixed_or_edge_resident_pages'],2)
        self.assertEqual(result['provider_segment_resolved_guest_pages'],0)

    def test_aliases_and_existing_three_layers_are_deduplicated(self):
        args=self.data(length=512)
        for f in args[-2:]:f['rows'][0]['pagemap'][1]=PRESENT|50
        result,pfns=account_observed_provider(*args)
        self.assertEqual(result['host_provider_segment_current_bytes'],4096)
        self.assertEqual(result['host_four_segment_current_union_bytes'],len(pfns)*4096)
        self.assertEqual(pfns,{20,40,50})

    def test_legacy_and_segment_coverage_are_unioned_not_replaced(self):
        args=self.data(length=4096)
        for entries in self.entries(args):
            for i in range(8):entries[i]=[6,UNKNOWN]
        result,_=account_observed_provider(*args)
        self.assertEqual(result['host_provider_current_bytes'],4096)
        self.assertEqual(result['host_provider_segment_current_bytes'],4096)
        self.assertEqual(result['host_provider_combined_current_bytes'],8192)
        self.assertEqual(result['provider_segment_unattributed_resident_bytes'],0)

    def test_old_format_retains_values_and_report_priority(self):
        args=fixtures.ProviderTests().data()
        self.assertEqual(account_observed_provider(*args),account_provider(*args))
        sample=dict(host_confirmed_cache_bytes=1,unconfirmed_after_pages=0,host_nonresident_pages=0,
            host_swapped_pages=0,host_four_current_union_bytes=2,
            host_four_unconfirmed_guest_capacity_bytes=3,
            host_four_segment_current_union_bytes=4,host_four_segment_unconfirmed_guest_capacity_bytes=5)
        self.assertEqual(physical_bytes(sample),4)
        self.assertEqual(residency_gaps(sample)['host_residency_unconfirmed_guest_capacity_bytes'],5)
        sample.pop('host_four_segment_current_union_bytes');sample.pop('host_four_segment_unconfirmed_guest_capacity_bytes')
        self.assertEqual(physical_bytes(sample),2)
        self.assertEqual(residency_gaps(sample)['host_residency_unconfirmed_guest_capacity_bytes'],3)

    def test_independent_byte_marking_matches_ranges_and_physical_sets(self):
        for kind in ('tail','edge','mixed','changed','released','unknown','alias'):
            args=self.data(address=4608 if kind=='edge' else 4096,length=512)
            three={20,40}
            if kind=='mixed':
                args[2][1]=(2,PRESENT|30,PRESENT|30,PRESENT|30);three={30}
                for entries in self.entries(args):entries[7][1]=4096
            if kind=='changed':args[-1]['rows'][0]['after'][1]+=4
            if kind=='released':args[-1]['rows'][0]['after'][6]=5
            if kind=='unknown':
                for entries in self.entries(args):entries[0][1]=UNKNOWN
            if kind=='alias':
                for f in args[-2:]:f['rows'][0]['pagemap'][1]=PRESENT|50
            result,pfns=account_observed_provider(*args)
            independent,physical=independent_segments(*args,three)
            self.assertTrue(all(result[k]==v for k,v in independent.items()),kind)
            self.assertEqual(pfns,physical,kind)

    def test_reader_opens_only_synthetic_metadata_and_pagemap_with_optional_extension(self):
        for extended in (False,True):
            with tempfile.TemporaryDirectory() as name:
                directory=Path(name);proc=directory/'proc';proc.mkdir()
                (proc/'stat').write_text('7 (fake) '+' '.join(['0']*19+['123']))
                path=directory/'slot.bin'
                header=ROW.pack(MAGIC,8,4096,8192,0,512,3,1)
                path.write_bytes(header+b''.join(SEGMENT.pack(4,i*UNIT) for i in range(16)))
                pm=directory/'pagemap';pm.write_bytes(struct.pack('<3Q',0,PRESENT|50,PRESENT|20))
                reader=ProviderCache.__new__(ProviderCache)
                reader.owner=None
                reader.proc=proc;reader.start='123';reader.directory=directory
                segments=dict(schema=1,unit_bytes=UNIT,entry_offset=64,entry_bytes=16,count=16) if extended else None
                reader.slots=[(path,path.open('rb'),segments)];reader.pm=pm.open('rb')
                saved=reader.snapshot();row=saved['rows'][0]
                self.assertEqual(row['before'],ROW.unpack(header))
                self.assertEqual(row['after'],ROW.unpack(header))
                self.assertEqual('segments' in row,extended)
                self.assertEqual(saved['metadata_bytes_read'],640 if extended else 128)
                self.assertLessEqual(saved['host_start_ns'],saved['host_end_ns'])
                self.assertEqual(saved['elapsed_ns'],saved['host_end_ns']-saved['host_start_ns'])
                reader.close()


if __name__=='__main__':unittest.main()
