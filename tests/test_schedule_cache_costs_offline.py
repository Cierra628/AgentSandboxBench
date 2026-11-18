"""Saved-data arithmetic only; no devices, services or cache syscalls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_schedule_cache_costs import cost_group, query_observed


class CacheCostAnalysisTests(unittest.TestCase):
    def test_full_capacity_not_resident_bytes_controls_work_count(self):
        row=dict(mapping_length=64*2**30,resident_bytes=0,aliases=[{},{}],
                 residency={'pages':16777216},start_monotonic_ns=10,end_monotonic_ns=110,cpu_ns=90)
        value=cost_group([row]*7)
        self.assertEqual(value['objects'],7);self.assertEqual(value['capacity_bytes'],448*2**30)
        self.assertEqual(value['pages'],117440512);self.assertEqual(value['chunks'],1792)
        self.assertEqual(value['resident_bytes'],0);self.assertEqual(value['object_window_ns'],700)

    def test_between_inventory_and_query_change_has_separate_valid_readout(self):
        row=dict(status='CHANGED_DURING_QUERY',device=1,inode=20,size=4096,resident_bytes=8192,
            residency=dict(device=1,inode=20,size=8192,stable=True,resident_bytes=8192))
        self.assertTrue(query_observed(row))
        self.assertEqual(row['status'],'CHANGED_DURING_QUERY')
        self.assertEqual(row['size'],4096)

    def test_in_query_change_wrong_identity_and_failed_query_excluded(self):
        row=dict(status='CHANGED_DURING_QUERY',device=1,inode=20,resident_bytes=8192,
            residency=dict(device=1,inode=20,stable=True,resident_bytes=8192))
        for update in ({'stable':False},{'inode':21},{'resident_bytes':4096}):
            self.assertFalse(query_observed(dict(row,residency=dict(row['residency'],**update))))
        self.assertFalse(query_observed(dict(row,status='QUERY_FAILED')))
        self.assertFalse(query_observed(dict(row,status='SEPARATE_SHMEM_OR_HUGETLB')))


if __name__=='__main__':unittest.main()
