import sys,tempfile,struct,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from snapshot_layer_metadata import RawMemoryLayer,HEADER,MAGIC,MAGIC1,UNKNOWN,account_layers
from fast_cache_metadata import decode_array
from test_kernel_cache_metadata import dump
from kernel_cache_metadata import PRESENT
from snapshot_cache_residency import FILE

class LayerTests(unittest.TestCase):
    def fixture(self,path):
        raw=bytearray(5*4096)
        raw[:HEADER.size]=HEADER.pack(MAGIC,MAGIC1,390,7,3*4096,2,3*4096)
        raw[-4096:-4096+HEADER.size]=HEADER.pack(MAGIC,MAGIC1,390,6,3*4096,2,3*4096)
        struct.pack_into('<4Q',raw,3*4096,8<<50,8,8|(8<<50),1<<55)
        path.write_bytes(raw)
    def test_reads_metadata_only_and_resolves_sector_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'layer';self.fixture(p)
            import os
            original=os.pread;calls=[]
            def read(fd,n,offset):calls.append((n,offset));return original(fd,n,offset)
            with patch('snapshot_layer_metadata.os.pread',side_effect=read):layer=RawMemoryLayer(p,None)
            self.assertEqual(calls,[(4096,0),(4096,4*4096),(32,3*4096)])
            self.assertEqual(layer.offsets(np.array([0,1,2,UNKNOWN],dtype='<u8')).tolist(),[1,UNKNOWN,UNKNOWN,UNKNOWN])
    def test_rejects_compressed_or_wrapped_input(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'layer';p.write_bytes(b'x'*(5*4096))
            with self.assertRaises(AssertionError):RawMemoryLayer(p,None)
    def test_three_level_union_and_old_cow_copies(self):
        meta=decode_array(dump([(1,2),(2,4),(3,6)]))
        host=np.array([[1,0,0,0],[2,PRESENT|FILE|20,PRESENT|FILE|20,PRESENT|FILE|20],
                       [3,PRESENT|30,PRESENT|30,PRESENT|30]],dtype='<u8')
        direct=np.array([[1,0,0,0],[2,1,PRESENT|20,PRESENT|20],[3,2,PRESENT|21,PRESENT|21]],dtype='<u8')
        lower=np.array([[1,1,PRESENT|40,PRESENT|40],[2,2,PRESENT|41,PRESENT|41],[3,3,PRESENT|42,PRESENT|42]],dtype='<u8')
        r=account_layers(meta,meta,host,direct,lower)
        self.assertEqual(r['host_three_current_union_bytes'],4*4096)
        self.assertEqual(r['host_lower_current_bytes'],2*4096)
        self.assertEqual(r['host_lower_old_snapshot_bytes'],4096)
        self.assertEqual(r['lower_resolved_guest_pages'],1)
        self.assertEqual(r['host_three_unconfirmed_guest_capacity_bytes'],0)

if __name__=='__main__':unittest.main()
