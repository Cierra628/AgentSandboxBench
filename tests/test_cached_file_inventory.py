import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cached_file_inventory import recount
from guest_cached_file_inventory import catalog,capture
from guest_named_file_probe import file_identity
from trenvx_whole_cache import RECORD


class InventoryTests(unittest.TestCase):
    def write(self,root,name,value):
        (root/name).write_text(json.dumps(value))

    def fixture(self,root):
        flags=struct.pack('<QQQQQ',0,32,32,32,32)
        for label in ('before','after'):
            self.write(root,f'inventory-{label}.json',dict(ram_ranges_pfns=[[0,5]]))
            (root/f'inventory-{label}-flags.bin').write_bytes(flags)
        (root/'selected-ram.bin').write_bytes(b''.join(RECORD.pack(p,(1<<63)|(100+p),(1<<63)|(100+p)) for p in range(1,5)))
        files=[dict(path='/owned/hot-a',aliases=['/owned/hot-a','/owned/hot-alias'],identity=dict(st_size=8192),status='PASS',selected_indices=[0,1],resident_indices=[0,1],
                    pages=[dict(index=i,valid_bytes=4096,pfn=i+1,flags=32,sha256=str(i),status='PASS') for i in range(2)]),
               dict(path='/owned/hot-tail',aliases=['/owned/hot-tail'],identity=dict(st_size=123),status='PASS',selected_indices=[0],resident_indices=[0],
                    pages=[dict(index=0,valid_bytes=123,pfn=3,flags=32,sha256='tail',status='PASS')])]
        self.write(root,'before.json',dict(sandbox_id='own',page_size=4096,files=files,catalog=dict(regular_paths=3,errors=[],excluded_entry_counts={})))
        self.write(root,'after.json',dict(sandbox_id='own',files=files))
        self.write(root,'config.json',dict(sandbox_id='own',mode='fixture',root='/owned'))
        hosts=[dict(path=f['path'],index=p['index'],guest_pfn=p['pfn'],entry=(1<<63)|(100+p['pfn']),last=(1<<63)|(100+p['pfn']),sha256=p['sha256']) for f in files for p in f['pages']]
        self.write(root,'host-pages.json',hosts)
        return files,hosts

    def test_hardlinks_count_once_and_unattributed_cache_remains(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);r=recount(root)
            self.assertEqual(r['fixture_checks'],'PASS')
            self.assertEqual(r['file_contents_verified_physical_bytes'],12288)
            self.assertEqual(r['guest_cache_unattributed_physical_bytes'],4096)
            self.assertIsNone(r['whole_task_file_content_physical_bytes'])

    def test_wrong_host_content_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,hosts=self.fixture(root);hosts[0]['sha256']='wrong';self.write(root,'host-pages.json',hosts)
            r=recount(root);self.assertEqual(r['unknown_file_page_references'],1)
            self.assertEqual(r['fixture_checks'],'FAIL');self.assertEqual(r['file_contents_verified_physical_bytes'],8192)

    def test_changed_guest_pfn_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);files,_=self.fixture(root);files=copy.deepcopy(files);files[0]['pages'][0]['pfn']=9
            self.write(root,'after.json',dict(sandbox_id='own',files=files));r=recount(root)
            self.assertEqual(r['unknown_file_page_references'],1)

    def test_hidden_host_pfn_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            (root/'selected-ram.bin').write_bytes(b''.join(RECORD.pack(p,1<<63,1<<63) for p in range(1,5)))
            with self.assertRaisesRegex(AssertionError,'hidden'):recount(root)

    def test_directory_inventory_groups_hardlinks_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a').write_bytes(b'hello')
            import os
            os.link(root/'a',root/'b');(root/'link').symlink_to('a')
            with patch('guest_cached_file_inventory.Path.read_text',return_value='1 0 1:1 / / rw - ext4 disk rw\n'):
                r=catalog(root,time.monotonic()+5)
            self.assertEqual(r['regular_paths'],2);self.assertEqual(len(r['files']),1)
            self.assertEqual(len(r['files'][0]['aliases']),2);self.assertEqual(r['excluded_entry_counts']['symlink'],1)

    def test_cold_file_does_not_read_content_or_pagemap(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'cold';p.write_bytes(b'C'*8192)
            item=dict(path=str(p),aliases=[str(p)],identity=file_identity(p.stat()))
            with patch('guest_cached_file_inventory.resident',return_value=[False,False]),patch('guest_cached_file_inventory.os.pread',side_effect=AssertionError('cold page touched')):
                r=capture(item,-1,-1)
            self.assertEqual(r['pages'],[]);self.assertEqual(r['selected_indices'],[])


    def frozen_fixture(self,root):
        files,_=self.fixture(root)
        for name in ('cold','empty','sparse'):
            files.append(dict(path='/owned/'+name,aliases=['/owned/'+name],identity=dict(st_size=0),status='PASS',pages=[],resident_indices=[]))
        before=json.loads((root/'before.json').read_text());before['files']=files;before['mode']='fixture';before['catalog']['regular_paths']=6
        self.write(root,'before.json',before)
        after=copy.deepcopy(files)
        for row in after:
            row['resident_indices']=[]
            row['pages']=[dict(index=p['index'],status='NONRESIDENT') for p in row['pages']]
        self.write(root,'after.json',dict(sandbox_id='own',files=after))
        self.write(root,'kernel-file-types.json',dict(frozen_cache_physical_status='PASS',checks=dict(known_file_contents_match_ram='PASS',known_file_type_device_inode_index='PASS')))
        pages=[dict(path=f['path'],index=p['index'],valid_bytes=p['valid_bytes'],sha256=p['sha256'],identity=f['identity']) for f in files for p in f['pages']]
        self.write(root,'frozen-fixture-result.json',dict(status='PASS',calibration_scope='paused exact-kernel point',interval_fixture_status='FAIL',contents_after_measurement=dict(status='PASS',checked_pages=3,pages=pages)))

    def test_frozen_point_keeps_failed_interval_separate_from_correct_content(self):
        from report_file_inventory import audit_frozen_fixture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.frozen_fixture(root);result=audit_frozen_fixture(root)
            self.assertEqual(result['status'],'PASS');self.assertEqual(result['interval_fixture_status'],'FAIL')
            self.assertEqual(result['after_export_nonresident_pages'],3)

    def test_post_measurement_wrong_content_does_not_pass_frozen_calibration(self):
        from report_file_inventory import audit_frozen_fixture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.frozen_fixture(root)
            saved=json.loads((root/'frozen-fixture-result.json').read_text());saved['contents_after_measurement']['pages'][0]['sha256']='wrong'
            self.write(root,'frozen-fixture-result.json',saved)
            with self.assertRaises(AssertionError):audit_frozen_fixture(root)


if __name__=='__main__':unittest.main()
