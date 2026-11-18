import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_task_cache import LiveTaskCache
from guest_cache_exporter import CONTROL, PUBLICATION, PAGE
from kernel_cache_metadata import HEADER, RECORD
from kernel_cache_metadata import join, PRESENT, SWAPPED
from fast_cache_metadata import decode_array, join_arrays
from test_kernel_cache_metadata import dump


class PublicationTests(unittest.TestCase):
    def collector(self):
        collector=LiveTaskCache.__new__(LiveTaskCache)
        collector.request=0
        collector.addresses=[4096]
        collector.bootstrap={'capacity_bytes':4096}
        collector.memory=type('Memory',(),{'fileno':lambda self:10})()
        return collector

    def test_ignores_odd_and_stale_publications(self):
        collector=self.collector()
        raw=HEADER.pack(b'ASBCACH1',1,4096,1<<21,1,1,0,0,0,0,0,0,0)+RECORD.pack(7,2)
        stale=CONTROL.pack(0,2,len(raw),1,2,4096,0,0,1)
        odd=CONTROL.pack(1,3,len(raw),1,2,4096,0,0,1)
        valid=CONTROL.pack(1,4,len(raw),1,2,4096,0,0,1)
        frames=iter([stale,odd,valid,raw,valid])
        collector._read=lambda offset,size:next(frames)
        with patch('live_task_cache.os.pwrite'),patch('live_task_cache.time.sleep'):
            actual,info=collector._snapshot()
        self.assertEqual(actual,raw)
        self.assertEqual(info['publication'],2)

    def test_rejects_frame_larger_than_export_buffer(self):
        collector=self.collector()
        collector._read=lambda offset,size:CONTROL.pack(1,2,8192,1,2,4096,0,0,1)
        with patch('live_task_cache.os.pwrite'):
            with self.assertRaises(AssertionError):collector._snapshot()

    def test_changed_sequence_retries_before_accepting_payload(self):
        collector=self.collector()
        raw=HEADER.pack(b'ASBCACH1',1,4096,1<<21,0,0,0,0,0,0,0,0,0)
        old=CONTROL.pack(1,2,len(raw),1,2,0,0,0,1)
        new=CONTROL.pack(1,4,len(raw),3,4,0,0,0,1)
        frames=iter([old,raw,new,new,raw,new])
        collector._read=lambda offset,size:next(frames)
        with patch('live_task_cache.os.pwrite'),patch('live_task_cache.time.sleep'):
            actual,info=collector._snapshot()
        self.assertEqual(info['guest_start_ns'],3)


class ArrayAccountingTests(unittest.TestCase):
    def compare(self, before, after, rows):
        expected=join(before,after,rows)
        host=np.array([[r[k] for k in ['guest_pfn','first','middle','last']] for r in rows],dtype='<u8').reshape(-1,4)
        self.assertEqual(join_arrays(decode_array(before),decode_array(after),host),expected)

    def test_sharing_reuse_retirement_changes_and_stale_records(self):
        before=dump([(1,20),(2,22),(3,24),(4,26),(5,28),(6,0)])
        after=dump([(1,20),(2,22),(3,30),(5,28),(6,0),(7,32)])
        rows=[dict(guest_pfn=p,first=PRESENT|100,middle=PRESENT|100,last=PRESENT|100) for p in range(1,6)]
        rows[-1]['last']=PRESENT|101
        self.compare(before,after,rows)

    def test_residency_swap_and_ignored_exclusive_bit(self):
        data=dump([(1,20),(2,22),(3,24)])
        rows=[dict(guest_pfn=1,first=0,middle=0,last=0),
              dict(guest_pfn=2,first=SWAPPED|45,middle=SWAPPED|45,last=SWAPPED|45),
              dict(guest_pfn=3,first=PRESENT|56,middle=PRESENT|56|(1<<56),last=PRESENT|56)]
        self.compare(data,data,rows)

    def test_empty_and_unsorted_input(self):
        self.compare(dump([]),dump([]),[])
        a=dump([(2,22),(1,20)]);b=dump([(1,20),(2,22)])
        self.compare(a,b,[dict(guest_pfn=p,first=PRESENT|100,middle=PRESENT|100,last=PRESENT|100) for p in [2,1]])

    def test_invalid_metadata_and_hidden_host_pfn(self):
        for raw in [dump([(1,20),(1,22)]),dump([(1,20)],missing_removes=1),dump([(1,202)]),dump([(1<<21,20)])]:
            with self.assertRaises(ValueError):decode_array(raw)
        a=decode_array(dump([(1,20)]))
        with self.assertRaises(ValueError):join_arrays(a,a,np.array([[1,PRESENT,PRESENT,PRESENT]],dtype='<u8'))


class BatchedExportTests(unittest.TestCase):
    def collector(self):
        c=LiveTaskCache.__new__(LiveTaskCache)
        c.addresses=[4096,8192,32768]
        c.pm=type('File',(),{'fileno':lambda self:10})()
        c.memory=type('File',(),{'fileno':lambda self:20})()
        return c

    def test_contiguous_partial_pages_use_one_read(self):
        calls=[]
        def read(fd,size,offset):
            calls.append((fd,size,offset))
            if fd==10:return struct.pack('<Q',PRESENT|123)*(size//8)
            return bytes((offset+i)%251 for i in range(size))
        with patch('live_task_cache.os.pread',side_effect=read):
            result=self.collector()._read(4090,20)
        self.assertEqual(result,bytes((4096+4090+i)%251 for i in range(20)))
        self.assertEqual(sum(fd==20 for fd,size,offset in calls),1)
        self.assertEqual([size for fd,size,offset in calls if fd==10],[16,16])

    def test_discontinuous_guest_pages_remain_separate(self):
        calls=[]
        def read(fd,size,offset):
            calls.append((fd,size,offset))
            if fd==10:return struct.pack('<Q',PRESENT|123)*(size//8)
            return b'x'*size
        with patch('live_task_cache.os.pread',side_effect=read):
            self.assertEqual(self.collector()._read(0,3*4096),b'x'*(3*4096))
        self.assertEqual([offset for fd,size,offset in calls if fd==20],[4096,32768])

    def test_mapping_change_in_later_page_is_detected(self):
        n=0
        def read(fd,size,offset):
            nonlocal n
            if fd==10:
                n+=1
                return struct.pack('<2Q',PRESENT|123,PRESENT|(124 if n==1 else 125))
            return b'x'*size
        with patch('live_task_cache.os.pread',side_effect=read):
            with self.assertRaisesRegex(AssertionError,'mapping changed'):
                self.collector()._read(0,2*4096)


if __name__=='__main__':unittest.main()


class RestoredSessionTests(unittest.TestCase):
    def states(self):
        base=dict(status='PASS', samples=2,raw_bytes=100,host_thread_total_cpu_ns=10,
                  interval_seconds=1,diagnostic_kernel='test',collector_version='test',
                  guest_boot_id='same-guest',scope='same scope')
        return [dict(base,sandbox_id='parent',host_identity=dict(pid=10,start_time_ticks=100),
                     first_sample_ns=1,last_sample_ns=2),
                dict(base,sandbox_id='restored',host_identity=dict(pid=11,start_time_ticks=200),
                     first_sample_ns=5,last_sample_ns=6)]

    def test_restore_preserves_guest_but_rebinds_host_and_records_gap(self):
        from live_task_cache import session_index
        states=self.states()
        index=session_index(states,dict(start_ns=3,end_ns=5,reason='restore'))
        self.assertEqual(index['samples'],4)
        self.assertEqual(index['sessions'],['parent','restored'])
        self.assertEqual(index['gap']['start_ns'],3)
        # A reused numeric PID is acceptable only with a different process start time.
        states[1]['host_identity']['pid']=10
        session_index(states,dict(start_ns=3,end_ns=5))

    def test_stale_host_identity_and_reboot_are_rejected(self):
        from live_task_cache import session_index
        for field in ('host_identity','guest_boot_id','sandbox_id'):
            states=self.states();states[1][field]=states[0][field]
            if field=='guest_boot_id':states[1][field]='rebooted'
            with self.assertRaises(AssertionError):session_index(states,dict(start_ns=3,end_ns=5))

    def test_gap_cannot_hide_overlapping_sessions(self):
        from live_task_cache import session_index
        with self.assertRaises(AssertionError):session_index(self.states(),dict(start_ns=1,end_ns=5))


class ResidencyReportTests(unittest.TestCase):
    def test_backend_union_replaces_vmm_lower_bound_and_resolves_only_proven_pages(self):
        from report_live_task_cache import physical_bytes, residency_gaps
        sample=dict(host_confirmed_cache_bytes=20*4096,host_current_union_bytes=60*4096,
                    host_nonresident_pages=100,host_swapped_pages=2,unconfirmed_after_pages=3,
                    host_current_unconfirmed_guest_capacity_bytes=65*4096)
        self.assertEqual(physical_bytes(sample),60*4096)
        self.assertEqual(residency_gaps(sample)['host_residency_unconfirmed_guest_capacity_bytes'],65*4096)

    def test_missing_restored_mapping_is_unconfirmed_not_zero_ram(self):
        from report_live_task_cache import residency_gaps
        sample=dict(host_nonresident_pages=100,host_swapped_pages=2,unconfirmed_after_pages=3)
        gaps=residency_gaps(sample)
        self.assertEqual(gaps['mapping_absent_guest_capacity_bytes'],100*4096)
        self.assertEqual(gaps['host_residency_unconfirmed_guest_capacity_bytes'],105*4096)
