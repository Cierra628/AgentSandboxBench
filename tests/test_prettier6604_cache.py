"""Cache cleanup regression: JSON listing and backing-file ownership are required."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_prettier6604_cache import release,xarray_backup_metadata


class CacheRelease(unittest.TestCase):
    def test_failed_task_ownership_comes_from_preserved_child_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'python-backbone').mkdir()
            (root/'result.json').write_text(json.dumps(dict(status='FAIL')))
            (root/'python-backbone/result.json').write_text(json.dumps(dict(task_id='pydata__xarray-6992',status='FAIL')))
            (root/'backup.json').write_text(json.dumps(dict(uncompressed_bytes=24*2**30,backup='archive',backup_sha256='sha')))
            self.assertEqual(xarray_backup_metadata(root)['archive'],'archive')
            (root/'python-backbone/result.json').write_text(json.dumps(dict(task_id='other',status='FAIL')))
            with self.assertRaises(AssertionError):xarray_backup_metadata(root)

    def check(self,wrong_file=False):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'.artifacts/prettier6604/seed';out.mkdir(parents=True)
            (out/'resources.json').write_text(json.dumps(dict(readonly_mounted=False,memory_mounted=False,
                                                              loop_device='/dev/loop-owned',released=False)))
            calls=[]
            def command(args,timeout=180):
                calls.append(args)
                if args[:2]==['losetup','--list']:
                    return json.dumps(dict(loopdevices=[{'name':'/dev/loop-owned','back-file':str(root/'other' if wrong_file else out/'memory/volume.ext4')}]))
                if args[:2]==['losetup','-d']:return ''
                return '/dev/loop-owned: backing file' # Status form is not JSON even with -J.
            with patch('prepare_prettier6604_cache.ROOT',root),patch('prepare_prettier6604_cache.call',side_effect=command):
                if wrong_file:
                    with self.assertRaises(AssertionError):release(out)
                    self.assertFalse(any(c[:2]==['losetup','-d'] for c in calls))
                else:
                    release(out)
                    state=json.loads((out/'resources.json').read_text())
                    self.assertTrue(state['released']);self.assertIsNone(state['loop_device'])
            return calls
    def test_partial_cleanup_uses_json_list_and_detaches_owned_loop(self):self.check()
    def test_unrelated_backing_file_is_never_detached(self):self.check(wrong_file=True)


if __name__=='__main__':unittest.main()
