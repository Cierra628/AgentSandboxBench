"""Synthetic segment-era unknown classifications; no live operations."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_provider_segment_unknown import classify
from provider_slot_cache import account_observed_provider,UNKNOWN,PRESENT,SWAPPED
import test_provider_segments as fixtures
from test_kernel_cache_metadata import dump


class SegmentUnknownTests(unittest.TestCase):
    def data(self):return fixtures.ProviderSegmentTests().data(length=512)

    def entries(self,args):return fixtures.ProviderSegmentTests().entries(args)

    def count(self,args):
        result=classify(*args);online,_=account_observed_provider(*args)
        self.assertEqual(result['unattributed_pages']*4096,online['provider_segment_unattributed_resident_bytes'])
        self.assertEqual(sum(result['unknown_by_reason_pages'].values()),result['unattributed_pages'])
        return result

    def reason(self,args,name):return self.count(args)['unknown_by_reason_pages'][name]

    def test_known_current_and_old_pages_are_removed(self):
        args=self.data();self.assertEqual(self.count(args)['unattributed_pages'],0)
        args[2][1]=(2,PRESENT|30,PRESENT|30,PRESENT|30)
        result=self.count(args);self.assertEqual(result['current_pages'],1);self.assertEqual(result['old_pages'],1)

    def test_initial_unknown_and_later_invalidated_are_distinct(self):
        for generation,name in ((0,'segment_never_filled'),(6,'segment_invalidated')):
            args=self.data()
            for entries in self.entries(args):entries[0]=[generation,UNKNOWN]
            self.assertEqual(self.reason(args,name),1)

    def test_changed_and_odd_records_precede_unknown_sources(self):
        args=self.data();self.entries(args)[-1][0][0]+=2
        self.assertEqual(self.reason(args,'segment_record_changed'),1)
        for entries in self.entries(args):entries[0]=[5,UNKNOWN]
        self.assertEqual(self.reason(args,'segment_record_changed'),1)

    def test_missing_candidate_is_not_a_noncache_classification(self):
        args=self.data()
        for entries in self.entries(args):entries[0][1]=2**30
        self.assertEqual(self.reason(args,'offset_not_in_initial_candidates'),1)

    def test_guest_lifetime_vmm_mapping_and_file_pfn_conflict(self):
        for kind in ('guest_identity_changed','vmm_pte_changed','file_pfn_conflict'):
            args=self.data()
            if kind=='guest_identity_changed':args[1]=dump([(1,6),(2,4)])
            if kind=='vmm_pte_changed':args[2][0]=(1,0,PRESENT|30,0)
            if kind=='file_pfn_conflict':args[3][1]=(2,1,PRESENT|21,PRESENT|21)
            self.assertEqual(self.reason(args,kind),1)

    def test_mixed_and_uniform_unknown_versions_are_distinct(self):
        args=self.data();args[2][1]=(2,PRESENT|30,PRESENT|30,PRESENT|30)
        for entries in self.entries(args):entries[7][1]=4096
        self.assertEqual(self.reason(args,'mixed_versions'),1)
        args=self.data();args[2][0]=(1,SWAPPED|20,SWAPPED|20,SWAPPED|20)
        self.assertEqual(self.reason(args,'version_unknown'),1)

    def test_allocation_changes_and_excluded_physical_observations(self):
        args=self.data();args[-1]['rows'][0]['after'][2]+=512
        self.assertEqual(self.reason(args,'allocation_changed'),2)
        for kind in ('pfn','released','hidden'):
            args=self.data()
            if kind=='pfn':args[-1]['rows'][0]['pagemap'][0]=PRESENT|90
            if kind=='released':args[-1]['rows'][0]['after'][6]=5
            if kind=='hidden':
                for frame in args[-2:]:frame['rows'][0]['pagemap'][0]=PRESENT
            self.assertEqual(self.count(args)['unattributed_pages'],0)

    def test_alias_reasons_are_deduplicated_not_added_to_known_pages(self):
        args=self.data()
        for f in args[-2:]:f['rows'][0]['pagemap'][1]=PRESENT|50
        for entries in self.entries(args):entries[0]=[0,UNKNOWN]
        self.assertEqual(self.count(args)['unattributed_pages'],0)
        for entries in self.entries(args):entries[8]=[6,UNKNOWN]
        self.assertEqual(self.reason(args,'multiple_unknown_aliases'),1)


if __name__=='__main__':unittest.main()
