"""Only synthetic saved pages; no /proc, service or sandbox access."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import HEADER,RECORD,FIELDS,PRESENT,SWAPPED,join
from report_private_cache_breakdown import partition


def dump(rows):
    info=dict.fromkeys(FIELDS,0)
    info.update(version=1,page_size=4096,max_pfn=1<<21,records=len(rows),generation=100,
                stale_records=sum(t==0 for p,t in rows),shmem_pages=sum(bool(t&1) for p,t in rows))
    return HEADER.pack(b'ASBCACH1',*(info[k] for k in FIELDS))+b''.join(RECORD.pack(p,t) for p,t in rows)


class PrivateCacheBreakdownTests(unittest.TestCase):
    def check(self,left,right,host):
        before=dump(left);after=dump(right);value,physical=partition(before,after,host)
        saved=join(before,after,[dict(zip(('guest_pfn','first','middle','last'),r)) for r in host])
        for k in ('guest_cache_after_bytes','host_confirmed_cache_bytes','stable_resident_guest_pages',
                  'host_nonresident_pages','host_swapped_pages','unconfirmed_after_pages'):
            self.assertEqual(value[k],saved[k],k)
        return value,physical

    def test_physical_union_does_not_double_count_shared_page_between_classes(self):
        entries=[(p,PRESENT|100,PRESENT|100,PRESENT|100) for p in (1,2)]
        value,physical=self.check([(1,2),(2,5)],[(1,2),(2,5)],entries)
        self.assertEqual(value['guest_ordinary_capacity_bytes'],4096)
        self.assertEqual(value['guest_shmem_capacity_bytes'],4096)
        self.assertEqual(value['host_confirmed_cache_bytes'],4096)
        self.assertEqual(value['ordinary_shmem_host_overlap_bytes'],4096)
        self.assertEqual(physical['ordinary'],physical['shmem'])

    def test_new_lifetime_stale_and_mapping_change_are_distinct_unknowns(self):
        left=[(1,2),(2,0),(3,6),(5,10),(6,12)]
        right=[(1,4),(2,8),(3,6),(4,14),(5,0),(6,12)]
        host=[(1,PRESENT|100,PRESENT|100,PRESENT|100),
              (3,PRESENT|103,PRESENT|104,PRESENT|104),
              (5,PRESENT|105,PRESENT|105,PRESENT|105),
              (6,PRESENT|106,PRESENT|106,PRESENT|106)]
        value,_=self.check(left,right,host)
        for name in ('stale_after','stale_before','new_pfn','reused_pfn','host_mapping_changed'):
            self.assertEqual(value[name+'_pages'],1)
        self.assertEqual(value['unconfirmed_after_pages'],5)
        self.assertEqual(value['host_confirmed_cache_bytes'],4096)

    def test_retired_before_page_is_not_an_after_unknown(self):
        value,_=self.check([(1,2),(2,4)],[(2,4)],[(1,PRESENT|100,PRESENT|100,PRESENT|100),
            (2,PRESENT|101,PRESENT|101,PRESENT|101)])
        self.assertEqual(value['retired_before_pages'],1)
        self.assertEqual(value['unconfirmed_after_pages'],0)

    def test_nonresident_and_swapped_are_not_physical_or_unknown(self):
        value,_=self.check([(1,2),(2,5)],[(1,2),(2,5)],[(1,0,0,0),(2,SWAPPED|10,SWAPPED|10,SWAPPED|10)])
        self.assertEqual(value['host_confirmed_cache_bytes'],0)
        self.assertEqual(value['unconfirmed_after_pages'],0)
        self.assertEqual(value['host_nonresident_pages'],1)
        self.assertEqual(value['host_swapped_pages'],1)

    def test_duplicate_or_missing_host_rows_fail(self):
        for rows in ([],[(1,0,0,0),(1,0,0,0)]):
            with self.assertRaisesRegex(ValueError,'host rows'):
                partition(dump([(1,2)]),dump([(1,2)]),rows)


if __name__=='__main__':unittest.main()
