import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_page_cache_metadata import PageCacheTable, PAGE


class MetadataTests(unittest.TestCase):
    def test_current_membership_is_not_total_allocations(self):
        table = PageCacheTable()
        self.assertEqual(table.apply('ADD', 100, 1, 2, 0, 0), PAGE)
        self.assertEqual(table.apply('DELETE', 100, 1, 2, 0, 0), 0)
        self.assertEqual(table.apply('ADD', 100, 1, 3, 8, 0), PAGE)
        self.assertEqual(table.added_bytes, PAGE * 2)
        self.assertEqual(table.peak_bytes, PAGE)

    def test_large_folio_counts_all_base_pages(self):
        table = PageCacheTable()
        self.assertEqual(table.apply('ADD', 100, 1, 2, 0, 3), 8 * PAGE)
        self.assertEqual(table.apply('DELETE', 100, 1, 2, 0, 3), 0)

    def test_missing_initial_state_or_lost_add_is_rejected(self):
        with self.assertRaises(ValueError):
            PageCacheTable().apply('DELETE', 100, 1, 2, 0, 0)

    def test_wrong_owner_and_duplicate_pfn_are_rejected(self):
        table = PageCacheTable()
        table.apply('ADD', 100, 1, 2, 0, 0)
        with self.assertRaises(ValueError):
            table.apply('ADD', 100, 1, 2, 0, 0)
        with self.assertRaises(ValueError):
            table.apply('DELETE', 100, 1, 3, 0, 0)
        self.assertEqual(table.apply('DELETE', 100, 1, 2, 0, 0), 0)


if __name__ == '__main__':
    unittest.main()
