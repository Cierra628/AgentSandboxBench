"""File-only reproduction of slot-name collision and verified volume recovery."""
import gzip
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from capacity_checkpoint_recovery import archive_released_slots,restore_volume


class CapacityRecoveryTests(unittest.TestCase):
    def test_released_metadata_is_archived_before_reusing_same_slot_name(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);directory=root/'slots';directory.mkdir()
            p=directory/'32-105-0-0.bin';raw=struct.pack('<8Q',0x31544f4c53425341,2,0,4096,0,0,5,0)
            p.write_bytes(raw);p.with_suffix('.json').write_text('{}')
            with self.assertRaises(FileExistsError):p.open('xb')
            result=archive_released_slots(directory,root/'archive')
            self.assertEqual(result['released_slots'],1)
            with p.open('xb') as stream:stream.write(b'new metadata')
            self.assertEqual((root/'archive'/p.name).read_bytes(),raw)

    def test_active_or_unfinished_metadata_is_not_moved(self):
        for generation,state in [(2,3),(3,5)]:
            with tempfile.TemporaryDirectory() as name:
                root=Path(name);directory=root/'slots';directory.mkdir()
                p=directory/'slot.bin';p.write_bytes(struct.pack('<8Q',0x31544f4c53425341,generation,0,4096,0,0,state,0))
                p.with_suffix('.json').write_text('{}')
                with self.assertRaises(AssertionError):archive_released_slots(directory,root/'archive')
                self.assertTrue(p.exists());self.assertFalse((root/'archive').exists())

    def test_full_volume_restore_rejects_different_contents(self):
        with tempfile.TemporaryDirectory() as name:
            source=Path(name);raw=b'retained checkpoint data'*50;image=source/'new.ext4';image.write_bytes(bytes(len(raw)))
            (source/'result.json').write_text(json.dumps(dict(status='FAIL',failed_stage='prettier6604-reused-capacity-128',
                capacity_bytes=len(raw),cleanup=dict(owned_container_removed=True,mount_and_loop_removed=True))))
            backup=dict(status='PASS',uncompressed_bytes=len(raw),source_sha256=hashlib.sha256(raw).hexdigest())
            (source/'backup.json').write_text(json.dumps(backup))
            with gzip.open(source/'volume.ext4.gz','wb') as stream:stream.write(raw)
            self.assertEqual(restore_volume(source,image)['status'],'PASS');self.assertEqual(image.read_bytes(),raw)
            with gzip.open(source/'volume.ext4.gz','wb') as stream:stream.write(b'X'*len(raw))
            with self.assertRaises(AssertionError):restore_volume(source,image)


if __name__=='__main__':unittest.main()
