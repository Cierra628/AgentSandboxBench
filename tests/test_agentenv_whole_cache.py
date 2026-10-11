import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from agentenv_whole_cache import slots_for_ram,recount,normalized_lowers,namespace_path
from trenvx_whole_cache import RECORD,scan_backing


class AgentenvWholeCacheTests(unittest.TestCase):
    def test_runtime_relative_path_matches_exact_layer(self):
        layer=dict(file='/workspace/env/image-cache/layer',digest='sha256:abc',size=4096)
        relative=dict(layer,file='../../../image-cache/layer')
        self.assertEqual(normalized_lowers([layer]),normalized_lowers([relative],'/workspace/env/firecracker-work/agentenv-fc-test/overlaybd'))
        self.assertEqual(namespace_path('upper.data','/owned/overlaybd'),'/owned/overlaybd/upper.data')
        self.assertNotEqual(normalized_lowers([layer]),normalized_lowers([dict(relative,file='../../../../image-cache/layer')],'/workspace/env/firecracker-work/agentenv-fc-test/overlaybd'))

    def test_default_serialization_does_not_change_layer_identity(self):
        layer=dict(file='/owned/layer',digest='sha256:abc',size=4096)
        self.assertEqual(normalized_lowers([layer]),normalized_lowers([dict(layer,gzipIndex='',dir='',targetFile='',uuid='',targetDigest='',repoBlobUrl='')]))
        self.assertNotEqual(normalized_lowers([layer]),normalized_lowers([dict(layer,file='/other/layer')]))
        self.assertNotEqual(normalized_lowers([layer]),normalized_lowers([dict(layer,digest='sha256:def')]))

    def test_ram_selection_uses_actual_ranges(self):
        lines='7\t0\t0\t3000\t10000\t0\n7\t1\t100000000\t2000\t20000\t0\n7\t9\t400000000\t1000\t30000\t0\n'
        ram='00001000-00002fff : System RAM\n100000000-100001fff : System RAM\n'
        slots=slots_for_ram(lines,7,ram)
        self.assertEqual(len(slots),2)
        for bad in (lines.replace('\t0\n','\t-22\n'),lines.replace('\t3000\t','\t2000\t'),lines.replace('7\t','8\t')):
            with self.assertRaises((AssertionError,RuntimeError)):slots_for_ram(bad,7,ram)

    def test_unaligned_tail_counts_allocated_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'upper.index';p.write_bytes(b'X'*4160)
            scan=scan_backing(p,root,0,allow_unaligned=True)
            self.assertEqual(len((root/'backing-0-before.bin').read_bytes()),2)
            self.assertEqual(len((root/'backing-0-pages.bin').read_bytes())//RECORD.size,2)
            self.assertEqual(scan['new_resident_pages'],0)
            with self.assertRaises(AssertionError):scan_backing(p,root,1)

    def fixture(self,root):
        for label in ('before','after'):
            (root/(label+'.json')).write_text(json.dumps(dict(ram_ranges_pfns=[[10,12]])))
            (root/(label+'-flags.bin')).write_bytes(struct.pack('<QQ',32,32))
            (root/(label+'-ram.bin')).write_bytes(RECORD.pack(10,(1<<63)|100,(1<<63)|100)+RECORD.pack(11,(1<<63)|101,(1<<63)|101))
        identity=dict(st_size=8192)
        (root/'layer-inventory.json').write_text(json.dumps(dict(files=[dict(role='shared-image-layer',identity=identity)])))
        (root/'backing-scans.json').write_text(json.dumps([dict(identity=identity)]))
        (root/'backing-0-before.bin').write_bytes(b'\1\1');(root/'backing-0-after.bin').write_bytes(b'\1\1')
        entry=(1<<63)|(1<<61)
        (root/'backing-0-pages.bin').write_bytes(RECORD.pack(0,entry|100,entry|100)+RECORD.pack(1,entry|200,entry|200))

    def test_shared_physical_page_is_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);r=recount(root)
            self.assertEqual(r['cache_union_end_observed_physical_bytes'],3*4096)
            self.assertEqual(r['duplicate_pages_removed'],1)
            self.assertEqual(r['measurement_status'],'PASS')

    def test_hidden_or_changed_ram_is_not_a_valid_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            (root/'after-ram.bin').write_bytes(RECORD.pack(10,1<<63,1<<63)+RECORD.pack(11,(1<<63)|101,(1<<63)|101))
            with self.assertRaises(AssertionError):recount(root)
            (root/'after-ram.bin').write_bytes(RECORD.pack(10,(1<<63)|100,(1<<63)|102)+RECORD.pack(11,(1<<63)|101,(1<<63)|101))
            r=recount(root);self.assertEqual(r['guest_unknown_pages']['after'],1);self.assertEqual(r['measurement_status'],'PARTIAL')

    def test_mutable_upper_size_uses_scan_boundary_without_claiming_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            original=dict(st_dev=1,st_ino=2,st_size=4096)
            scanned=dict(original,st_size=8192)
            (root/'layer-inventory.json').write_text(json.dumps(dict(files=[dict(role='private-upper',identity=original)])))
            (root/'backing-scans.json').write_text(json.dumps([dict(identity=scanned)]))
            result=recount(root)
            self.assertEqual(result['measurement_status'],'PARTIAL')
            self.assertEqual(result['mutable_files_changed_before_scan'],[0])
            scanned['st_ino']=3
            (root/'backing-scans.json').write_text(json.dumps([dict(identity=scanned)]))
            with self.assertRaises(AssertionError):recount(root)


if __name__=='__main__':unittest.main()
