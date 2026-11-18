"""Offline data checks only; no guests, services or devices."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import HEADER,RECORD,FIELDS
from report_cache_capacity_initial_gap import membership_change,memory_trace,gap_terms


def metadata(rows):
    values={k:0 for k in FIELDS}
    values.update(version=1,page_size=4096,max_pfn=1<<21,records=len(rows),generation=100)
    return HEADER.pack(b'ASBCACH1',*(values[k] for k in FIELDS))+b''.join(RECORD.pack(*r) for r in rows)


class InitialGapTests(unittest.TestCase):
    def test_reused_pfn_is_not_a_stable_lifetime(self):
        result=membership_change(metadata([(1,2),(2,4),(3,6)]),metadata([(1,2),(2,8),(4,10)]))
        self.assertEqual(result['stable_pages'],1)
        self.assertEqual(result['retired_or_reused_pages'],2)
        self.assertEqual(result['new_lifetime_pages'],2)
        self.assertEqual(result['net_change_bytes'],0)

    def test_count_drop_reconciles_retired_and_new_lifetimes(self):
        result=membership_change(metadata([(1,2),(2,4),(3,6)]),metadata([(1,2),(4,8)]))
        self.assertEqual(result['net_change_bytes'],-4096)
        self.assertEqual(result['retired_or_reused_pages'],2)
        self.assertEqual(result['new_lifetime_pages'],1)

    def test_missing_kernel_event_is_rejected(self):
        raw=metadata([(1,2)]);header=list(HEADER.unpack_from(raw));header[1+FIELDS.index('missing_removes')]=1
        with self.assertRaises(ValueError):membership_change(HEADER.pack(*header)+raw[HEADER.size:],raw)

    def trace(self,path,times):
        fields=['timestamp_ns','monotonic_ns','Cached_kib','Buffers_kib','MemAvailable_kib','phase']
        with path.open('w') as stream:
            w=csv.DictWriter(stream,fieldnames=fields,delimiter='\t');w.writeheader()
            for t,c in times:w.writerow(dict(timestamp_ns=10**18-t,monotonic_ns=t,Cached_kib=c,
                Buffers_kib=8,MemAvailable_kib=4096,phase='restored-checks'))

    def test_guest_monotonic_orders_and_excludes_control_not_wall_clock(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'memory.tsv';self.trace(p,[(10,132),(20,45),(30,40),(40,128)])
            result=memory_trace(p,40)
            self.assertEqual(result['samples'],3)
            self.assertEqual(result['largest_adjacent_drop']['delta_bytes'],-87*1024)
            self.assertEqual(result['last']['guest_monotonic_ns'],30)

    def test_non_increasing_guest_clock_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'memory.tsv';self.trace(p,[(20,132),(10,45)])
            with self.assertRaises(AssertionError):memory_trace(p,30)

    def test_initial_gap_is_decomposed_without_treating_reclaim_total_as_capacity(self):
        low=dict(first_pair=dict(before_bytes=132,after_bytes=45,net_change_bytes=-87),first_control_cache_bytes=44)
        high=dict(first_pair=dict(before_bytes=128,after_bytes=127,net_change_bytes=-1),first_control_cache_bytes=128)
        result=gap_terms(low,high)
        self.assertEqual(result['high_minus_low_before_first_pair_bytes'],-4)
        self.assertEqual(result['different_net_change_during_pair_bytes'],86)
        self.assertEqual(result['after_pair_to_first_control_bytes'],2)
        self.assertEqual(result['high_minus_low_at_first_control_bytes'],84)


if __name__=='__main__':unittest.main()
