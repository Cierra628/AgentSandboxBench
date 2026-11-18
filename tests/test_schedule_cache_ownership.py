"""Temporary directory tests only; no services, mounts or cache release calls."""
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_prettier_memory_scope import smoke_output
from run_schedule_smoke import cache_can_release
class OwnershipTests(unittest.TestCase):
    def test_nested_runs_have_unique_cache_names(self):
        a=smoke_output(Path('/fixture/first-batch'));b=smoke_output(Path('/fixture/second-batch'))
        self.assertNotEqual(a.name,b.name)
        self.assertEqual(a.parent,Path('/fixture/first-batch'))
    def test_existing_receipt_is_never_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);receipt=p/'resources.json';receipt.write_text('{"released":true}')
            before=receipt.stat().st_mtime_ns
            self.assertFalse(cache_can_release(p,True))
            self.assertTrue(cache_can_release(p,False))
            self.assertEqual(before,receipt.stat().st_mtime_ns)
    def test_missing_receipt_is_not_released(self):
        with tempfile.TemporaryDirectory() as tmp:self.assertFalse(cache_can_release(Path(tmp),False))
if __name__=='__main__':unittest.main()
