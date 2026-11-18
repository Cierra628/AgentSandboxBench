import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from provider_slot_cache import MAGIC,account_provider,content_method
from kernel_cache_metadata import PRESENT
from test_kernel_cache_metadata import dump

FILE=1<<61


class ProviderTests(unittest.TestCase):
    def test_content_method_preserves_legacy_and_checks_negotiation(self):
        self.assertEqual(content_method({}),'invalid before kernel fetch')
        self.assertIn('valid during fetch',content_method(dict(content_validity='before_overwrite',negotiated_flags=4)))
        with self.assertRaisesRegex(AssertionError,'not negotiated'):
            content_method(dict(content_validity='before_overwrite',negotiated_flags=0))

    def test_independent_byte_audit_uses_page_indices_and_handles_edges_and_cow(self):
        from audit_provider_get_data import independent_provider
        for kind in ['current','edge','changed','cow']:
            args=self.data(address=4608 if kind=='edge' else 4096)
            if kind=='changed':args[-1]['rows'][0]['after'][1]+=2
            if kind=='cow':args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
            expected,_=account_provider(*args)
            saved=dict(point=0,**expected)
            three={20,40}|({30} if kind=='cow' else set())
            with tempfile.TemporaryDirectory() as name:
                directory=Path(name);prefix=directory/'pair-0000'
                for suffix,data in [('-before.bin',args[0]),('-after.bin',args[1])]:
                    Path(str(prefix)+suffix).write_bytes(data)
                for suffix,rows in zip(['-host.bin','-backend.bin','-lower.bin'],args[2:5]):
                    Path(str(prefix)+suffix).write_bytes(b''.join(struct.pack('<4Q',*row) for row in rows))
                Path(str(prefix)+'-provider.json').write_text(json.dumps(dict(first=args[5],last=args[6])))
                with patch('audit_provider_get_data.check_point',return_value=({},three)):
                    actual=independent_provider(directory,saved)
                    self.assertTrue(all(actual[k]==v for k,v in expected.items()),kind)

    def data(self,address=4096,offset=0,length=8192):
        meta=dump([(1,2),(2,4)])
        host=[(1,0,0,0),(2,PRESENT|FILE|20,PRESENT|FILE|20,PRESENT|FILE|20)]
        direct=[(1,0,0,0),(2,1,PRESENT|20,PRESENT|20)]
        lower=[(1,0,0,0),(2,1,PRESENT|40,PRESENT|40)]
        row=[MAGIC,8,address,8192,offset,length,3,1]
        first_address=address//4096*4096
        count=(address+8192+4095)//4096-first_address//4096
        first=dict(rows=[dict(name='slot',before=row,after=row.copy(),first_address=first_address,
                             pagemap=[PRESENT|p for p in [50,20,60][:count]])])
        return [meta,meta,host,direct,lower,first,copy.deepcopy(first)]

    def test_unions_deduplicate_and_resolve_absent_vmm_mapping(self):
        args=self.data();value,pfns=account_provider(*args)
        self.assertEqual(pfns,{20,40,50})
        self.assertEqual(value['host_provider_current_bytes'],8192)
        self.assertEqual(value['host_provider_intersection_three_bytes'],4096)
        self.assertEqual(value['provider_resolved_guest_pages'],1)
        self.assertEqual(value['host_four_unconfirmed_guest_capacity_bytes'],0)

    def test_generation_change_failure_and_hidden_or_changed_pfn_remain_unknown(self):
        for kind in ['generation','invalid','odd','pfn-change','pfn-hidden','partial']:
            args=self.data();first,last=args[-2:]
            if kind=='generation':last['rows'][0]['after'][1]+=2
            elif kind=='invalid':
                for frame in [first,last]:
                    frame['rows'][0]['before'][6]=4;frame['rows'][0]['after'][6]=4
            elif kind=='odd':
                for frame in [first,last]:
                    frame['rows'][0]['before'][1]=9;frame['rows'][0]['after'][1]=9
            elif kind=='pfn-change':last['rows'][0]['pagemap'][0]=PRESENT|51
            elif kind=='pfn-hidden':
                for frame in [first,last]:frame['rows'][0]['pagemap'][0]=PRESENT
            else:
                for frame in [first,last]:
                    frame['rows'][0]['before'][5]=512;frame['rows'][0]['after'][5]=512
            value,_=account_provider(*args)
            self.assertEqual(value['provider_resolved_guest_pages'],0,kind)
            self.assertEqual(value['host_four_unconfirmed_guest_capacity_bytes'],4096,kind)

    def test_old_cow_copy_is_separate(self):
        args=self.data();args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
        value,pfns=account_provider(*args)
        self.assertEqual(value['host_provider_old_snapshot_bytes'],4096)
        self.assertNotIn(50,pfns)

    def test_unaligned_page_requires_both_guest_pages_and_full_coverage(self):
        args=self.data(address=4608)
        value,_=account_provider(*args)
        self.assertEqual(value['host_provider_current_bytes'],4096)
        self.assertEqual(value['provider_mixed_or_edge_resident_pages'],2)
        self.assertEqual(value['provider_resolved_guest_pages'],0)
        # The single whole allocation page covers the tail/head of two guest pages.
        args[1]=dump([(1,6),(2,4)])
        value,_=account_provider(*args)
        self.assertEqual(value['host_provider_current_bytes'],0)
        self.assertEqual(value['provider_resolved_guest_pages'],0)

    def test_mixed_old_current_and_offset_alias_are_not_current(self):
        args=self.data(address=4608);args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
        value,_=account_provider(*args)
        self.assertEqual(value['host_provider_current_bytes'],0)
        self.assertEqual(value['host_provider_old_snapshot_bytes'],0)
        args=self.data();args[3][1]=(2,0,PRESENT|20,PRESENT|20)
        args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
        value,_=account_provider(*args)
        self.assertEqual(value['host_provider_current_bytes'],0)


if __name__=='__main__':unittest.main()
