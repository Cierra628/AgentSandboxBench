"""Saved-field classification only; no files, devices, processes or syscalls."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_schedule_layer_gaps import window_candidate


class SavedLoopAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.permission=dict(host_root_cap_fowner=True)
        self.loop=dict(status='UNKNOWN',error="ValueError('loop/backing changed during query')",
            resident_bytes=None,loop_before=dict(backing_device=1,backing_inode=2,offset=0),
            loop_after=dict(backing_device=1,backing_inode=2,offset=0),
            residency=dict(device=1,inode=2,identity_stable=True,size_stable=True,
                size=8192,mapping_length=8192,resident_bytes=4096,stable=False,metadata_changed=True))

    def test_mtime_only_is_a_new_view_candidate_without_changing_old_fields(self):
        original=copy.deepcopy(self.loop)
        self.assertTrue(window_candidate(self.loop,self.permission))
        self.assertEqual(self.loop,original)
        self.assertIsNone(self.loop['resident_bytes'])
        self.assertFalse(self.loop['residency']['stable'])

    def test_identity_capacity_or_loop_config_change_stays_ineligible(self):
        for field,value in [('identity_stable',False),('size_stable',False),
                            ('inode',3),('mapping_length',4096),('resident_bytes',None)]:
            with self.subTest(field=field):
                row=copy.deepcopy(self.loop);row['residency'][field]=value
                self.assertFalse(window_candidate(row,self.permission))
        row=copy.deepcopy(self.loop);row['loop_after']['offset']=4096
        self.assertFalse(window_candidate(row,self.permission))

    def test_other_failures_are_not_misclassified_as_mtime_only(self):
        for error in ("ValueError('mount identity mismatch')", "OSError('query failed')"):
            row=copy.deepcopy(self.loop);row['error']=error
            self.assertFalse(window_candidate(row,self.permission))
        self.assertFalse(window_candidate(self.loop,dict(host_root_cap_fowner=False)))

    def test_real_zero_is_distinct_from_absent_readout(self):
        row=copy.deepcopy(self.loop);row['residency']['resident_bytes']=0
        self.assertTrue(window_candidate(row,self.permission))
        row['residency']['resident_bytes']=None
        self.assertFalse(window_candidate(row,self.permission))


if __name__=='__main__':unittest.main()
