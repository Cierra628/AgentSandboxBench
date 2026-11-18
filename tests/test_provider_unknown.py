"""Synthetic saved metadata only; no services, devices, or workload file reads."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import PRESENT, SWAPPED
from provider_slot_cache import account_provider
from report_provider_unknown import classify
import test_provider_slot_cache as fixtures
from test_kernel_cache_metadata import dump

FILE=1<<61


class ProviderUnknownTests(unittest.TestCase):
    def data(self,**kwargs):
        return fixtures.ProviderTests().data(**kwargs)

    def count(self,args):
        result=classify(*args[:4],*args[5:])
        original,_=account_provider(*args)
        for key,old in [('unattributed_pages','provider_unattributed_resident_bytes'),
                ('whole_resident_pages','provider_whole_allocation_resident_bytes'),
                ('current_pages','host_provider_current_bytes'),('old_pages','host_provider_old_snapshot_bytes')]:
            self.assertEqual(result[key]*4096,original[old])
        self.assertEqual(result['mixed_or_boundary_pages'],original['provider_mixed_or_edge_resident_pages'])
        self.assertEqual(sum(result['unknown_by_reason_pages'].values()),result['unattributed_pages'])
        return result

    def test_current_and_old_are_removed_from_unknown(self):
        args=self.data();result=self.count(args)
        self.assertEqual(result['current_pages'],2);self.assertEqual(result['unattributed_pages'],0)
        args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
        result=self.count(args);self.assertEqual(result['old_pages'],1)
        self.assertEqual(result['current_pages'],1)

    def test_valid_prefix_does_not_claim_older_tail_or_partial_end_page(self):
        for length,unknown in [(4096,1),(512,2)]:
            result=self.count(self.data(length=length))
            self.assertEqual(result['unknown_by_reason_pages']['outside_valid_read'],unknown)

    def test_changed_or_odd_record_has_priority_over_tail(self):
        args=self.data(length=512);args[-1]['rows'][0]['after'][1]+=2
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['slot_record_changed'],2)
        args=self.data()
        for frame in args[-2:]:
            for key in ['before','after']:frame['rows'][0][key][1]=9
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['slot_record_changed'],2)

    def test_invalid_content_is_separate(self):
        args=self.data()
        for frame in args[-2:]:
            for key in ['before','after']:frame['rows'][0][key][6]=4
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['slot_record_invalid'],2)

    def test_missing_candidate_uses_page_index_without_claiming_non_cache(self):
        result=self.count(self.data(offset=4096))
        self.assertEqual(result['current_pages'],1)
        self.assertEqual(result['unknown_by_reason_pages']['offset_not_in_initial_candidates'],1)

    def test_guest_lifetime_and_vmm_changes_are_distinct(self):
        args=self.data();args[1]=dump([(1,6),(2,4)])
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['guest_identity_changed'],1)
        args=self.data();args[2][0]=(1,0,PRESENT|30,0)
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['vmm_pte_changed'],1)

    def test_file_pfn_conflict(self):
        args=self.data();args[3][1]=(2,1,PRESENT|21,PRESENT|21)
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['file_pfn_conflict'],1)

    def test_mixed_whole_page_and_boundaries_are_not_added_together(self):
        args=self.data(address=4608);args[2][0]=(1,PRESENT|30,PRESENT|30,PRESENT|30)
        result=self.count(args)
        self.assertEqual(result['unknown_by_reason_pages']['mixed_versions'],1)
        self.assertEqual(result['boundary_pages'],2)
        self.assertEqual(result['mixed_whole_pages'],1)

    def test_swapped_version_remains_unknown(self):
        args=self.data();args[2][0]=(1,SWAPPED|30,SWAPPED|30,SWAPPED|30)
        self.assertEqual(self.count(args)['unknown_by_reason_pages']['mixed_versions'],1)

    def test_changed_unavailable_and_released_pages_are_not_physical_unknown_bytes(self):
        args=self.data();args[-1]['rows'][0]['pagemap'][0]=PRESENT|51
        result=self.count(args)
        self.assertEqual(result['excluded_virtual_page_observations']['provider_pagemap_changed'],1)
        self.assertEqual(result['unattributed_pages'],0)
        args=self.data()
        for frame in args[-2:]:frame['rows'][0]['pagemap'][0]=PRESENT
        self.assertEqual(self.count(args)['excluded_virtual_page_observations']['provider_pfn_unavailable'],1)
        args=self.data();args[-1]['rows'][0]['after'][6]=5
        self.assertEqual(self.count(args)['whole_resident_pages'],0)

    def test_physical_aliases_are_deduplicated_before_partitioning(self):
        for offset,unknown in [(0,0),(8192,1)]:
            args=self.data(offset=offset,length=4096)
            for frame in args[-2:]:frame['rows'][0]['pagemap'][1]=PRESENT|50
            result=self.count(args)
            self.assertEqual(result['whole_resident_pages'],1)
            self.assertEqual(result['unattributed_pages'],unknown)
            self.assertEqual(result['unknown_by_reason_pages']['multiple_unknown_aliases'],unknown)


if __name__=='__main__':unittest.main()
