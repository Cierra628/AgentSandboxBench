import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_checkpoint_bundle import export,restore,load,IMAGE,sha,BoundedOutput
from platform_tasks import configuration,commands


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.repo=self.root/'source/snapshot-store'
        for n in ['catalog/records','catalog/aliases','snapshots/id','managed-layers']:(self.repo/n).mkdir(parents=True)
        (self.repo/'catalog/records/id.json').write_text('{"snapshot":"id"}')
        (self.repo/'catalog/aliases/example').write_text('id')
        (self.repo/'snapshots/id/vm_state.bin').write_bytes(b'VM-state')
        (self.repo/'managed-layers/memory').write_bytes(bytes(range(256))*1024)
        self.bundle=self.root/'bundle'
    def pack(self):return export(self.repo,self.bundle,dict(status='PASS',phase='capture',server_image=IMAGE))
    def test_full_repository_roundtrip_in_new_directory(self):
        value=self.pack();dest=self.root/'new-service/snapshot-store';dest.parent.mkdir()
        proof=restore(self.bundle,dest);self.assertEqual(proof['files_verified'],4)
        self.assertEqual((dest/'managed-layers/memory').read_bytes(),(self.repo/'managed-layers/memory').read_bytes())
        self.assertEqual(load(self.bundle)['archive_sha256'],value['archive_sha256'])
    def test_fixed_server_nested_repository_layout(self):
        nested=self.repo/'repository';nested.mkdir()
        for name in ('catalog','snapshots','managed-layers'):(self.repo/name).rename(nested/name)
        value=self.pack();self.assertEqual(value['repository_prefix'],'repository')
        destination=self.root/'other/snapshot-store';destination.parent.mkdir()
        self.assertEqual(restore(self.bundle,destination)['status'],'PASS')
        self.assertTrue((destination/'repository/snapshots/id/vm_state.bin').is_file())
    def test_modified_archive_refused_before_extraction(self):
        self.pack();archive=self.bundle/'repository.tar.gz';archive.write_bytes(archive.read_bytes()+b'extra')
        with self.assertRaises(AssertionError):restore(self.bundle,self.root/'snapshot-store')
        self.assertFalse((self.root/'snapshot-store').exists())
    def test_bounded_archive_does_not_exceed_budget(self):
        stream=io.BytesIO();bounded=BoundedOutput(stream,3);bounded.write(b'123')
        with self.assertRaises(OSError):bounded.write(b'4')
        self.assertEqual(stream.getvalue(),b'123')
    def test_repository_links_not_followed(self):
        (self.repo/'external').symlink_to('/etc/passwd')
        with self.assertRaises(AssertionError):self.pack()
        self.assertFalse(self.bundle.exists())
    def test_archive_escape_refused_before_extraction(self):
        self.pack();archive=self.bundle/'repository.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            entry=tarfile.TarInfo('../outside');entry.size=3;tar.addfile(entry,io.BytesIO(b'bad'))
        receipt=json.loads((self.bundle/'bundle.json').read_text());receipt['archive_sha256']=sha(archive)
        (self.bundle/'bundle.json').write_text(json.dumps(receipt))
        with self.assertRaises(AssertionError):restore(self.bundle,self.root/'snapshot-store')
        self.assertFalse((self.root/'outside').exists())
    def test_restore_command_uses_bundle_and_capture_omits_it(self):
        cfg=configuration(Path(__file__).resolve().parents[1]/'configs/platform-sphinx7590.json')
        _,capture=commands(cfg,Path('/seed'),durable_phase='capture')
        check,live=commands(cfg,Path('/seed'),durable_phase='restore',bundle=self.bundle)
        self.assertIn('--python-durable-checkpoint-demo',capture)
        self.assertNotIn('--python-durable-checkpoint-bundle',capture)
        self.assertIn('--python-durable-checkpoint-bundle',live);self.assertIn('--bundle',check)
        self.assertNotIn('--immutable-cache-seed',live)


if __name__=='__main__':unittest.main()
