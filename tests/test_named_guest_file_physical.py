import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from named_guest_file_physical import recount

PRESENT=1<<63
FILE=1<<61


class NamedFileTests(unittest.TestCase):
    def fixture(self):
        file=dict(path='/testbed/file.js',identity=dict(st_dev=os.makedev(8,1),st_ino=17,
            st_size=5000,st_mtime_ns=1,st_ctime_ns=1),address=4096,total_pages=2,
            selected_indices=[0,1],mincore_after=[True,True],
            mapping='1000-3000 rw-p 00000000 08:01 17 /testbed/file.js',
            pages=[dict(index=i,offset=i*4096,valid_bytes=4096 if i==0 else 904,
                        pfn=100+i,entry=PRESENT|FILE|(100+i),flags=32,sha256='a'*64) for i in range(2)])
        before=dict(status='PASS',sandbox_id='vm',page_size=4096,pid=7,boot_id='same',files=[file],
            initial_residency=[dict(path=file['path'],resident_before=[True,True])])
        after=copy.deepcopy(before)
        rows=[dict(vm='vm',owner=file['path'],index=i,offset=i*4096,valid_bytes=4096 if i==0 else 904,
            guest_pfn=100+i,entry=PRESENT|101,address=4096*(100+i),content_sha256='a'*64) for i in range(2)]
        return before,after,rows

    def test_alias_dedup_and_tail_page(self):
        a,b,rows=self.fixture();result=recount(a,b,rows,'vm')
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['verified_mapping_pages'],2)
        self.assertEqual(result['verified_file_cache_physical_bytes'],4096)

    def test_changed_type_excludes_shared_host_page_instead_of_promoting_pass(self):
        a,b,rows=self.fixture();b['files'][0]['pages'][1]['flags']|=1<<12
        result=recount(a,b,rows,'vm')
        self.assertEqual(result['status'],'PARTIAL')
        self.assertEqual(result['unknown_resident_unique_pages'],1)
        self.assertEqual(result['verified_file_cache_physical_bytes'],0)

    def test_source_identity_or_host_content_change_is_rejected(self):
        a,b,rows=self.fixture();b['files'][0]['identity']['st_ino']=18
        with self.assertRaises(AssertionError):recount(a,b,rows,'vm')
        a,b,rows=self.fixture();rows[0]['content_sha256']='b'*64
        with self.assertRaises(AssertionError):recount(a,b,rows,'vm')

    def test_initially_absent_pages_are_not_invented(self):
        a,b,rows=self.fixture()
        a['initial_residency'][0]['resident_before']=[True,False]
        for item in [a,b]:
            item['files'][0]['selected_indices']=[0];item['files'][0]['pages']=item['files'][0]['pages'][:1]
        result=recount(a,b,rows[:1],'vm')
        self.assertEqual(result['verified_mapping_pages'],1)
        self.assertEqual(result['files'][0]['initially_nonresident_pages'],1)
        self.assertEqual(result['verified_file_cache_physical_bytes'],4096)

    def test_empty_selected_set_has_known_zero_without_reading_file(self):
        a,b,rows=self.fixture();a['initial_residency'][0]['resident_before']=[False,False]
        for item in [a,b]:item['files'][0]['selected_indices']=[];item['files'][0]['pages']=[]
        result=recount(a,b,[],'vm')
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['verified_mapping_pages'],0)
        self.assertEqual(result['verified_file_cache_physical_bytes'],0)

    def test_guest_entry_not_file_or_duplicate_indices_are_rejected(self):
        a,b,rows=self.fixture();a['files'][0]['pages'][0]['entry']&=~FILE
        self.assertEqual(recount(a,b,rows,'vm')['status'],'PARTIAL')
        a,b,rows=self.fixture();a['files'][0]['selected_indices']=[0,0]
        with self.assertRaises(AssertionError):recount(a,b,rows,'vm')

    def test_present_host_page_with_hidden_pfn_is_rejected(self):
        a,b,rows=self.fixture()
        for row in rows:row['entry']=PRESENT
        with self.assertRaisesRegex(AssertionError,'host PFN hidden'):
            recount(a,b,rows,'vm')
