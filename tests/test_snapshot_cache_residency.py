import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from snapshot_cache_residency import account, FILE, PRESENT
from fast_cache_metadata import decode_array
from test_kernel_cache_metadata import dump

class SnapshotAccountingTests(unittest.TestCase):
    def test_union_cow_absence_changed_lifetime_and_unknown(self):
        before=decode_array(dump([(i,2*i) for i in range(1,8)]))
        after=decode_array(dump([(i,2*i if i!=5 else 20) for i in range(1,9)]))
        values=[0,PRESENT|FILE|101,PRESENT|201,0,PRESENT|202,PRESENT|203,PRESENT|FILE|107]
        rows=np.array([[i,e,e,e] for i,e in enumerate(values,1)],dtype='<u8')
        rows[5,3]=PRESENT|204
        backing=np.array([[i,i,PRESENT|p,PRESENT|p] for i,p in enumerate([100,101,102,0,104,105,108],1)],dtype='<u8')
        backing[3,2:]=0
        result=account(before,after,rows,backing)
        from audit_snapshot_cache import scalar
        from test_kernel_cache_metadata import dump as raw
        self.assertEqual(result,scalar(raw([(i,2*i) for i in range(1,8)]),
            raw([(i,2*i if i!=5 else 20) for i in range(1,9)]),rows.tolist(),backing.tolist()))
        self.assertEqual(result['host_vmm_confirmed_bytes'],2*4096)
        self.assertEqual(result['host_snapshot_current_bytes'],2*4096)
        self.assertEqual(result['host_vmm_snapshot_intersection_bytes'],4096)
        self.assertEqual(result['host_current_union_bytes'],3*4096)
        self.assertEqual(result['snapshot_old_version_bytes'],4096)
        self.assertEqual(result['snapshot_without_vmm_pte_pages'],1)
        self.assertEqual(result['backend_file_pfn_mismatch_pages'],1)
        self.assertEqual(result['host_current_unconfirmed_guest_capacity_bytes'],5*4096)

    def test_changed_backend_does_not_remove_stable_vmm_proof(self):
        data=decode_array(dump([(1,2)]))
        host=np.array([[1,PRESENT|FILE|100,PRESENT|FILE|100,PRESENT|FILE|100]],dtype='<u8')
        backing=np.array([[1,0,PRESENT|100,PRESENT|101]],dtype='<u8')
        result=account(data,data,host,backing)
        self.assertEqual(result['host_current_union_bytes'],4096)
        self.assertEqual(result['host_snapshot_current_bytes'],0)
        self.assertEqual(result['host_current_unconfirmed_guest_capacity_bytes'],0)

    def test_mmio_hole_offset_uses_captured_slot_and_actual_vma(self):
        from snapshot_cache_residency import SnapshotCache
        s=SnapshotCache.__new__(SnapshotCache)
        s.slots=[dict(guest=0,host=0x100000,size=0x2000),dict(guest=0x100000000,host=0x200000,size=0x2000)]
        s.regions=[dict(start=0x100000,end=0x102000,offset=0),dict(start=0x200000,end=0x202000,offset=0x2000)]
        self.assertEqual(s.offsets(np.array([1,0x100001])).tolist(),[1,3])
        with self.assertRaises(RuntimeError):s.offsets(np.array([3]))

    def test_reported_free_ram_remapped_anonymous_has_no_snapshot_offset(self):
        from snapshot_cache_residency import SnapshotCache
        s=SnapshotCache.__new__(SnapshotCache)
        s.slots=[dict(guest=0,host=0x100000,size=0x3000)]
        s.regions=[dict(start=0x100000,end=0x101000,offset=0,path='/dev/ublkb105'),
                   dict(start=0x101000,end=0x102000,offset=0,path=''),
                   dict(start=0x102000,end=0x103000,offset=0x2000,path='/dev/ublkb105')]
        self.assertEqual(s.offsets(np.array([0,1,2])).tolist(),[0,(1<<64)-1,2])

if __name__=='__main__':unittest.main()
