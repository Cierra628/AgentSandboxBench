from pathlib import Path
import stat
import struct
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_file_inventory import frozen_selection,physical_types


class FrozenReportTests(unittest.TestCase):
    def test_tail_uses_head_flags_and_swapbacked_is_excluded(self):
        base=0xffff888000000000
        raw=b''.join(struct.pack('<8Q',*fields) for fields in (
            (16,0,0,0,0,0,0,0), (0,base+1,0,0,0,0,0,0),
            (16|(1<<19),0,0,0,0,0,0,0)))
        self.assertEqual(frozen_selection(raw,[(0,3)],base),([0,1],1))

    def test_internal_lru_includes_inactive_and_excludes_active_only(self):
        raw=b''.join(struct.pack('<8Q',flag,0,0,0,0,0,0,0) for flag in (16,32,16|32))
        self.assertEqual(frozen_selection(raw,[(0,3)],0)[0],[0,2])
        self.assertEqual(frozen_selection(raw,[(0,3)],0,5)[0],[1,2])

    def test_wrong_raw_length_and_unexported_head_rejected(self):
        with self.assertRaises(AssertionError):frozen_selection(b'',[(1,2)],0)
        raw=struct.pack('<8Q',0,1,0,0,0,0,0,0)
        with self.assertRaisesRegex(AssertionError,'outside'):frozen_selection(raw,[(1,2)],0)

    def test_physical_dedup_and_untyped_inode_stays_partial(self):
        rows=[dict(guest_pfn=1,status='PASS',mode=stat.S_IFREG,inode_type='regular_file'),
              dict(guest_pfn=2,status='PASS',mode=0,inode_type='other_inode')]
        entry=(1<<63)|99
        result=physical_types(rows,[(1,entry,entry),(2,entry,entry)])
        self.assertEqual(result['frozen_guest_file_cache_physical_bytes'],4096)
        self.assertEqual(result['cross_type_duplicate_host_pages'],1)
        self.assertEqual(result['classification_status'],'PARTIAL')
        self.assertEqual(result['frozen_cache_physical_status'],'PASS')

    def test_hidden_physical_page_rejected_and_changed_mapping_unknown(self):
        rows=[dict(guest_pfn=1,status='PASS',mode=stat.S_IFREG,inode_type='regular_file')]
        with self.assertRaisesRegex(AssertionError,'hidden'):physical_types(rows,[(1,1<<63,1<<63)])
        result=physical_types(rows,[(1,(1<<63)|99,(1<<63)|100)])
        self.assertEqual(result['host_unknown_pages'],1)
        self.assertEqual(result['frozen_guest_file_cache_physical_bytes'],0)
        self.assertEqual(result['frozen_cache_physical_status'],'PARTIAL')

    def test_snapshot_fallback_is_counted_once_with_present_ram_alias(self):
        rows=[dict(guest_pfn=p,status='PASS',mode=stat.S_IFREG,inode_type='regular_file') for p in (1,2)]
        entry=(1<<63)|(1<<61)|99
        fallback=[dict(guest_pfn=1,entry=entry,last=entry,recheck_entry=entry)]
        result=physical_types(rows,[(1,0,0),(2,entry,entry)],fallback)
        self.assertEqual(result['frozen_guest_file_cache_physical_bytes'],4096)
        self.assertEqual(result['snapshot_fallback_pages'],1)
        self.assertEqual(result['host_unknown_pages'],0)

    def test_snapshot_fallback_cannot_hide_swapped_or_changed_ram(self):
        rows=[dict(guest_pfn=1,status='PASS',mode=stat.S_IFREG,inode_type='regular_file')]
        entry=(1<<63)|(1<<61)|99
        fallback=[dict(guest_pfn=1,entry=entry,last=entry,recheck_entry=entry)]
        with self.assertRaises(AssertionError):physical_types(rows,[(1,1<<62,1<<62)],fallback)
        with self.assertRaises(AssertionError):physical_types(rows,[(1,0,1<<63)],fallback)


    def test_allocator_label_requires_independent_object_proof(self):
        row=dict(guest_pfn=1,status='PASS',mode=0,inode=1,inode_pointer=123,inode_type='filesystem_allocation_metadata')
        entry=(1<<63)|99
        with self.assertRaisesRegex(AssertionError,'unverified allocator'):physical_types([row],[(1,entry,entry)])
        result=physical_types([row],[(1,entry,entry)],allocation_inodes={123})
        self.assertEqual(result['classification_status'],'PASS')
        self.assertEqual(result['inode_type_unique_physical_bytes'],{'filesystem_allocation_metadata':4096})


if __name__=='__main__':unittest.main()
