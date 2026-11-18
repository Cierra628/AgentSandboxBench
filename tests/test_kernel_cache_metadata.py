import sys,struct,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import HEADER,RECORD,decode,join,PRESENT


def dump(rows,**overrides):
    values=dict(version=1,page_size=4096,max_pfn=1<<21,records=len(rows),generation=100,
        shmem_pages=0,duplicate_adds=0,missing_removes=0,out_of_range=0,moved_pages=0,
        stale_records=sum(t==0 for p,t in rows),bitmap_lock_ns=20)
    values.update(overrides)
    return HEADER.pack(b'ASBCACH1',*values.values())+b''.join(RECORD.pack(p,t) for p,t in rows)


class KernelMetadataTests(unittest.TestCase):
    def test_shmem_and_non_lru_membership_no_flag_filter(self):
        info,rows=decode(dump([(1,20),(2,21)]));self.assertEqual(rows,{1:20,2:21})

    def test_reused_pfn_is_not_same_cache_lifetime(self):
        before=dump([(1,20),(2,22)]);after=dump([(1,24),(2,22)])
        rows=[dict(guest_pfn=p,first=PRESENT|100,middle=PRESENT|100,last=PRESENT|100) for p in [1,2]]
        r=join(before,after,rows)
        self.assertEqual(r['host_confirmed_cache_bytes'],4096)
        self.assertEqual(r['retired_or_reused_pages'],1)
        self.assertEqual(r['unconfirmed_after_pages'],1)

    def test_shared_host_pages_count_once_and_stale_rows_retained(self):
        data=dump([(1,20),(2,22),(3,0)])
        rows=[dict(guest_pfn=p,first=PRESENT|100,middle=PRESENT|100,last=PRESENT|100) for p in [1,2]]
        r=join(data,data,rows)
        self.assertEqual(r['host_confirmed_cache_bytes'],4096)
        self.assertEqual(r['unconfirmed_after_pages'],1)

    def test_changed_host_mapping_is_not_zero_residency(self):
        data=dump([(1,20)])
        r=join(data,data,[dict(guest_pfn=1,first=PRESENT|100,middle=PRESENT|101,last=PRESENT|101)])
        self.assertEqual(r['host_mapping_changed_pages'],1);self.assertEqual(r['unconfirmed_after_pages'],1)

    def test_lost_hooks_bad_shape_or_hidden_pfn_rejected(self):
        for data in [dump([(1,20)],missing_removes=1),dump([(1,20),(1,22)]),dump([(1,20)])[:-1]]:
            with self.assertRaises(ValueError):decode(data)
        data=dump([(1,20)])
        with self.assertRaises(ValueError):join(data,data,[dict(guest_pfn=1,first=PRESENT,middle=PRESENT,last=PRESENT)])


if __name__=='__main__':unittest.main()
