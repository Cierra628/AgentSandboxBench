"""Pure parsing/classification checks; do not attach probes or create cgroups."""
import copy
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_existing_cache_owner import parse, program, summarize


class ExistingCacheTests(unittest.TestCase):
    def fixture(self):
        return dict(totals={1:4,2:4,5:1}, states={0:2,2:2},
                    owners=[dict(raw_group_id=10,online_group_id=10,raw_group_online=True,pages=1),
                            dict(raw_group_id=20,online_group_id=5,raw_group_online=False,pages=1)],
                    unknown={},changes={})

    def test_live_and_offline_owner_classification(self):
        result=summarize(self.fixture(),dict(pages=4,resident_pages=2),{10})
        self.assertEqual((result['inside_pages'],result['outside_pages'],result['unknown_pages']),(1,1,0))
        self.assertEqual(result['status'],'OBSERVED')
        self.assertEqual(summarize(self.fixture(),dict(pages=4,resident_pages=2),{5})['inside_pages'],1)

    def test_unknown_is_not_zero(self):
        data=self.fixture(); data['owners'].pop(); data['unknown']={1:1}
        result=summarize(data,dict(pages=4,resident_pages=2),{10})
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['unknown_bytes'],os.sysconf('SC_PAGE_SIZE'))

    def test_missing_returns_order_changes_and_residency_mismatch_fail(self):
        for field,key,value in [('totals',2,3),('totals',3,1),('totals',6,1),('changes',1,1),('states',2,1)]:
            data=copy.deepcopy(self.fixture());data[field][key]=value
            with self.assertRaises(AssertionError):summarize(data,dict(pages=4,resident_pages=2),{10})

    def test_parser_and_lost_events(self):
        text='READY\n@totals[1]: 4\n@owners[20, 5, 0]: 2\nDONE\n'
        self.assertEqual(parse(text,'')['owners'][0]['online_group_id'],5)
        for bad,error in [(text,'Lost 1 events'),(text.replace('DONE','DEADLINE'),''),(text,'ERROR')]:
            with self.assertRaises(AssertionError):parse(bad,error)

    def test_program_is_exact_file_and_scanner_filtered(self):
        text=program(SimpleNamespace(st_dev=os.makedev(8,1),st_ino=12345),67890,{})
        self.assertIn('pid == 67890',text);self.assertIn('==12345',text);self.assertIn('==8388609',text)
        self.assertIn('kretprobe:filemap_get_entry',text)
        self.assertIn('sys_enter_mincore',text)
        self.assertIn('mm_filemap_delete_from_page_cache',text)
        self.assertNotIn('bpf_override_return',text)


if __name__=='__main__':unittest.main()
