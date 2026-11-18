"""Temporary proc/cgroup/slot files; no services, devices or real process queries."""
import json
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
import test_snapshot_lower_owner as fixtures
from provider_slot_cache import ProviderCache,MAGIC,UNKNOWN
from kernel_cache_metadata import PRESENT

class ProviderSiblingTests(unittest.TestCase):
    def tearDown(self):self.tmp.cleanup()
    def setUp(self):
        fixtures.OwnerTests.setUp(self)
        self.out=self.root/'mount/controller/run'
        self.directory=self.out/'live-cache/restored/provider'
        lower=self.directory.parent/'lower-metadata';lower.mkdir(parents=True)
        self.env=self.out.parent.parent/'env';self.config=self.env/'snapshot-local-cache/runtime/s/memory/image.json'
        self.config.parent.mkdir(parents=True);self.config.write_text('{}')
        self.image='/workspace/env/snapshot-local-cache/runtime/s/memory/image.json'
        (self.env/'logs').mkdir();(self.env/'logs/server.log').write_text(f'key={self.image} path=/dev/ublkb3 sandbox_id=sid\n')
        self.layer=self.env/'snapshot-store/repository/managed-layers/layer';self.layer.parent.mkdir(parents=True);self.layer.write_text('layer')
        (self.proc/'20/fd/4').unlink();(self.proc/'20/fd/4').symlink_to(self.layer)
        import snapshot_lower_owner
        owners=snapshot_lower_owner.service_openers(self.binding,self.vm,self.layer)
        (lower/'ownership.json').write_text(json.dumps(dict(config=str(self.config),layer={'file':'/workspace/env/snapshot-store/repository/managed-layers/layer'},open_descriptors=owners)))
        self.slots=self.env/'provider-slots';self.slots.mkdir()
        (self.proc/'20/status').write_text('NSpid:\t20\t2\n')
        (self.proc/'20/pagemap').write_bytes(struct.pack('<3Q',0,PRESENT|50,PRESENT|51))
        maps=[]
        for slot in range(2):
            value=dict(schema=1,pid=2,image_config=self.image,dev_id=3,queue=0,slot=slot,nr_queues=1,queue_depth=2,max_io_buf_bytes=4096,negotiated_flags=4,content_validity='before_overwrite',process_stat=(self.proc/'20/stat').read_text(),segments=dict(schema=1,unit_bytes=512,entry_offset=64,entry_bytes=16,count=8))
            (self.slots/f's{slot}.json').write_text(json.dumps(value))
            data=self.slots/f's{slot}.bin';data.write_bytes(struct.pack('<8Q',MAGIC,2,4096*(slot+1),4096,0,4096,3,1)+b''.join(struct.pack('<2Q',2,i*512) for i in range(8)))
            st=data.stat();maps.append(f'1000-2000 rw-s 00000000 {os.major(st.st_dev):x}:{os.minor(st.st_dev):x} {st.st_ino} /workspace/env/provider-slots/{data.name}\n')
        (self.proc/'20/maps').write_text(''.join(maps))
    def make(self):return ProviderCache(self.out,self.proc/'10',self.directory,binding=self.binding,vm=self.vm,device_path='/dev/ublkb3')
    def test_constructor_and_snapshot_with_sibling_groups(self):
        obj=self.make()
        try:
            self.assertEqual(len(obj.snapshot()['rows']),2)
            self.assertEqual(obj.identity['ownership_mode'],'explicit_private_service_sibling')
        finally:obj.close()
    def test_other_device_config_is_not_admitted(self):
        (self.slots/'unrelated.json').write_text(json.dumps(dict(image_config='/other/device')))
        obj=self.make()
        try:self.assertEqual(len(obj.slots),2)
        finally:obj.close()
    def test_same_config_wrong_device_rejected(self):
        p=self.slots/'s0.json';v=json.loads(p.read_text());v['dev_id']=4;p.write_text(json.dumps(v))
        with self.assertRaisesRegex(AssertionError,'device differs'):self.make()
    def test_missing_slot_rejected(self):
        (self.slots/'s1.json').unlink()
        with self.assertRaises(AssertionError):self.make()
    def test_wrong_mapping_device_rejected(self):
        p=self.proc/'20/maps';p.write_text(p.read_text().replace(p.read_text().split()[3],'ffff:ffff'))
        with self.assertRaises(AssertionError):self.make()
    def test_reuse_after_registration_rejected(self):
        obj=self.make()
        p=self.proc/'20/stat';p.write_text(p.read_text().replace('123','456'))
        try:
            with self.assertRaisesRegex(ValueError,'identity changed'):obj.snapshot()
        finally:
            for _,f,_ in obj.slots:f.close()
            obj.pm.close()
    def test_close_identity_failure_still_closes_owned_descriptors(self):
        obj=self.make();(self.proc/'20/cgroup').write_text('0::/other\n')
        with self.assertRaisesRegex(ValueError,'left owned'):obj.close()
        self.assertTrue(obj.pm.closed)
        self.assertTrue(all(f.closed for _,f,_ in obj.slots))

    def test_fd_change_after_registration_rejected(self):
        obj=self.make();p=self.proc/'20/fd/4';p.unlink();p.symlink_to(self.file)
        try:
            with self.assertRaisesRegex(ValueError,'FD changed'):obj.snapshot()
        finally:
            for _,f,_ in obj.slots:f.close()
            obj.pm.close()

if __name__=='__main__':unittest.main()
