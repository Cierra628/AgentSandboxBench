import gzip
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import diagnose_dax_restore as diagnostic


class ArchiveSafetyTests(unittest.TestCase):
    def test_workqueue_name_is_not_mistaken_for_stack_frame(self):
        text='READY\nGUP 1 2 flags=1\nWORK async_pf_execute\n    get_user_pages_remote+1\n'
        result=diagnostic.trace_summary(text)
        self.assertFalse(result['async_worker_in_stack'])
        self.assertEqual(result['work_functions'],['async_pf_execute'])
        self.assertEqual(result['gup_for_target_pages'],['1'])
        self.assertEqual(result['completed_for_target_pages'],0)

    def fixture(self, root):
        out=root/'.artifacts/dax-restore-diagnostic/run'
        (out/'snapshot').mkdir(parents=True)
        source=out/'snapshot/memory-ranges'
        source.write_bytes(b'known snapshot content\0'*1024)
        return out,source

    def test_verified_archive_preserves_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out,source=self.fixture(root);before=source.read_bytes()
            with patch.object(diagnostic,'ROOT',root):diagnostic.archive_memory(out)
            self.assertFalse(source.exists())
            self.assertEqual(gzip.decompress(source.with_suffix('.gz').read_bytes()),before)

    def test_existing_archive_keeps_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out,source=self.fixture(root);before=source.read_bytes()
            source.with_suffix('.gz').write_bytes(b'previous archive')
            with patch.object(diagnostic,'ROOT',root),self.assertRaises(FileExistsError):
                diagnostic.archive_memory(out)
            self.assertEqual(source.read_bytes(),before)

    def test_outside_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(diagnostic,'ROOT',root),self.assertRaises(ValueError):
                diagnostic.archive_memory(root/'other')


if __name__=='__main__':unittest.main()
