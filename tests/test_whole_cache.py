import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from guest_whole_cache_probe import ram_ranges,selected_flags
from trenvx_whole_cache import RECORD,recount,owned_image_paths,same_physical_mapping


class WholeCacheTests(unittest.TestCase):
    def test_exclusive_flag_change_does_not_change_physical_identity(self):
        first=(1<<63)|(1<<61)|(1<<56)|123
        self.assertTrue(same_physical_mapping(first,first^(1<<56)))
        self.assertFalse(same_physical_mapping(first,first+1))
        self.assertFalse(same_physical_mapping(first,first^(1<<61)))

    def fixture(self,path):
        flags=struct.pack('<QQQ',1<<5,1<<5,(1<<5)|(1<<12))
        for label in ('before','after'):
            (path/f'{label}-flags.bin').write_bytes(flags)
            (path/f'{label}.json').write_text(json.dumps(dict(page_size=4096,
                ram_ranges_pfns=[[10,13]],selected_file_lru_pages=2)))
        present=1<<63;filebit=1<<61
        (path/'ram-pages.bin').write_bytes(RECORD.pack(10,present|100,present|100)+RECORD.pack(11,present|101,present|101))
        (path/'host-scan.json').write_text(json.dumps(dict(backing_files=[dict(index=0)])))
        (path/'backing-0-before.bin').write_bytes(b'\1\0')
        (path/'backing-0-after.bin').write_bytes(b'\1\0')
        (path/'backing-0-pages.bin').write_bytes(RECORD.pack(0,present|filebit|200,present|filebit|200))
        (path/'vmm-image-0-pages.bin').write_bytes(RECORD.pack(0,present|filebit|200,present|filebit|200)+RECORD.pack(1,present|300,present|300))

    def test_actual_ram_ranges_and_types(self):
        ranges=ram_ranges('00001000-00002fff : System RAM\n00003000-00003fff : Reserved\n00005000-00006fff : System RAM\n')
        self.assertEqual(ranges,[(1,3),(5,7)])
        self.assertEqual(selected_flags(struct.pack('<QQQQ',32,32|4096,32|16384,32),ranges),[1,6])

    def test_full_union_deduplicates_mapped_and_unmapped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);self.fixture(path);r=recount(path)
            self.assertEqual(r['cache_union_stable_physical_bytes'],3*4096)
            self.assertEqual(r['cache_and_private_image_union_stable_physical_bytes'],4*4096)
            self.assertEqual(r['duplicate_cache_pages_removed'],1)

    def test_type_change_is_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);self.fixture(path)
            (path/'after-flags.bin').write_bytes(struct.pack('<QQQ',32,32|4096,32|4096))
            (path/'after.json').write_text(json.dumps(dict(page_size=4096,ram_ranges_pfns=[[10,13]],selected_file_lru_pages=1)))
            r=recount(path)
            self.assertEqual(r['measurement_status'],'PARTIAL')
            self.assertEqual(r['unstable_observed_unique_pages'],1)
            self.assertEqual(r['guest_file_cache_stable_physical_bytes'],4096)

    def test_hidden_pfn_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);self.fixture(path)
            (path/'ram-pages.bin').write_bytes(RECORD.pack(10,1<<63,1<<63)+RECORD.pack(11,(1<<63)|101,(1<<63)|101))
            with self.assertRaises(AssertionError):recount(path)

    def test_namespace_alias_matches_device_inode(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);image=root/'data/templates/t/image/rootfs.ext4';image.parent.mkdir(parents=True)
            image.write_bytes(b'x'*4096);proc=root/'proc';(proc/'fd').mkdir(parents=True)
            st=image.stat();maps=f'1000-2000 r--p 00000000 {os.major(st.st_dev):x}:{os.minor(st.st_dev):x} {st.st_ino} /nonexistent/namespace/rootfs.ext4\n'
            found=owned_image_paths(proc,root,maps)
            self.assertEqual(list(found),[str(image)])
            with self.assertRaises(AssertionError):owned_image_paths(proc,root,maps.replace(str(st.st_ino),str(st.st_ino+1)))

    def test_unmapped_ram_uses_resident_snapshot_backing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);self.fixture(path)
            (path/'ram-pages.bin').write_bytes(RECORD.pack(10,0,0)+RECORD.pack(11,(1<<63)|101,(1<<63)|101))
            (path/'ram-attestation.json').write_text(json.dumps(dict(slots=[dict(guest=0,host=0x100000,size=4096*20)])))
            scan=json.loads((path/'host-scan.json').read_text())
            scan['backing_files'].append(dict(index=1,role='vm-memory-snapshot',regions=[dict(start=0x100000,end=0x114000,offset=0,permissions='rw-p')]))
            (path/'host-scan.json').write_text(json.dumps(scan))
            (path/'backing-1-before.bin').write_bytes(bytes([0]*10+[1]+[0]*9))
            (path/'backing-1-after.bin').write_bytes(bytes([0]*10+[1]+[0]*9))
            entry=(1<<63)|(1<<61)|400
            (path/'backing-1-pages.bin').write_bytes(RECORD.pack(10,entry,entry))
            (path/'vmm-image-1-pages.bin').write_bytes(b'')
            result=recount(path)
            self.assertEqual(result['guest_file_lru_backing_resident_without_vmm_pte_pages'],1)
            self.assertEqual(result['guest_file_lru_unresolved_pages'],0)
            self.assertEqual(result['guest_file_cache_stable_physical_bytes'],8192)
            (path/'backing-1-before.bin').write_bytes(bytes(20));(path/'backing-1-after.bin').write_bytes(bytes(20))
            (path/'backing-1-pages.bin').write_bytes(b'')
            result=recount(path)
            self.assertEqual(result['guest_file_lru_nonresident_without_backing_cache_pages'],1)
            self.assertEqual(result['guest_file_cache_stable_physical_bytes'],4096)


if __name__=='__main__':unittest.main()
