import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prewarm_directories import prewarm


class PrewarmDirectoriesTests(unittest.TestCase):
    def test_directory_entries_without_symlink_traversal_or_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'tree';root.mkdir();(root/'nested').mkdir()
            content=root/'nested/content';content.write_bytes(b'unchanged\n')
            (root/'cycle').symlink_to(root,target_is_directory=True)
            before=content.stat();digest=hashlib.sha256(content.read_bytes()).hexdigest()
            row=prewarm(root)
            self.assertEqual((row['directories'],row['entries']),(2,3))
            self.assertEqual(row['status'],'PASS')
            self.assertEqual(content.stat().st_mtime_ns,before.st_mtime_ns)
            self.assertEqual(hashlib.sha256(content.read_bytes()).hexdigest(),digest)
            self.assertGreater(row['monotonic_raw_ns'],0)

    def test_missing_root_does_not_produce_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):prewarm(Path(tmp)/'missing')


if __name__=='__main__':unittest.main()
