"""Synthetic mapping text and in-memory vectors only; never reads live /proc."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import PRESENT
from report_vmm_special_split import split


class SpecialSplitTests(unittest.TestCase):
    def fixture(self):
        maps=['1000-2000 rw-s 0 00:10 7 anon_inode:kvm-vcpu:0',
              '2000-3000 rw-s 0 00:10 7 anon_inode:kvm-vcpu:0',
              '3000-4000 rw-s 0 00:10 7 anon_inode:[io_uring]',
              '4000-5000 r--p 0 00:00 0 [vvar]']
        text='\n'.join(maps)+'\n'
        smaps=''.join(line+'\nRss: '+('0' if i==3 else '4')+' kB\nPss: '+('0' if i==3 else '2')+' kB\nVmFlags: rd '+('pf io' if i==3 else 'mm' if i==2 else 'wr')+'\n' for i,line in enumerate(maps))
        segments=[]
        for i in range(4):
            segments.append(dict(region_index=i,start=(i+1)*4096,end=(i+2)*4096,pages=1,
                row_offset=i if i<2 else None,virtual_bytes=4096,unknown_virtual_pages=0 if i<2 else 1,
                status='OBSERVED_NONATOMIC' if i<2 else 'SPECIAL_OR_SMAPS_UNKNOWN',
                physical_union_bytes=4096 if i<2 else None))
        body=dict(maps_before=text,maps_after=text,smaps_before=smaps,smaps_after=smaps,
            summary=dict(nonram_segments=segments,ordinary_nonram_stable_host_union_bytes=4096))
        return body,np.full((2,3),PRESENT|80,dtype='<u8')

    def test_kvm_aliases_deduplicate_and_rss_does_not(self):
        b,x=self.fixture();g=next(g for g in split(b,x)['groups'] if g['category']=='kvm_vcpu')
        self.assertEqual(g['category'],'kvm_vcpu')
        self.assertEqual(g['before_rss_bytes'],8192)
        self.assertEqual(g['pfn_union_observed_bytes'],4096)

    def test_zero_special_pss_is_not_zero_physical_union(self):
        b,x=self.fixture();g=next(g for g in split(b,x)['groups'] if g['category']=='vvar')
        self.assertEqual(g['before_pss_bytes'],0)
        self.assertIsNone(g['pfn_union_observed_bytes'])

    def test_same_inode_does_not_merge_kvm_and_io_uring(self):
        b,x=self.fixture();s=split(b,x)
        self.assertEqual([g['category'] for g in s['groups']],['io_uring','vvar','kvm_vcpu'])
        self.assertIsNone(s['groups'][0]['pfn_union_observed_bytes'])

    def test_missing_smaps_remains_unavailable(self):
        b,x=self.fixture();b['smaps_after']=''
        self.assertTrue(all(g['after_pss_bytes'] is None for g in split(b,x)['groups']))

    def test_bad_vector_length_or_union_rejected(self):
        b,x=self.fixture()
        with self.assertRaises(ValueError):split(b,x[:1])
        b['summary']['ordinary_nonram_stable_host_union_bytes']=8192
        with self.assertRaises(ValueError):split(b,x)

    def test_changed_mapping_does_not_borrow_other_smaps(self):
        b,x=self.fixture();old='1000-2000 rw-s 0 00:10 7';new='1000-2000 rw-s 0 00:10 8'
        b['maps_after']=b['maps_after'].replace(old,new)
        b['smaps_after']=b['smaps_after'].replace(old,new)
        kvm=next(g for g in split(b,x)['groups'] if g['category']=='kvm_vcpu')
        self.assertIsNone(kvm['after_rss_bytes'])


if __name__=='__main__':unittest.main()
