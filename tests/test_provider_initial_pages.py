"""Untouched-page classification on synthetic metadata; no live resources."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_provider_initial_pages import initial_pages
from provider_slot_cache import PRESENT, UNKNOWN
import test_provider_segments as fixtures


class InitialPagesTests(unittest.TestCase):
    def data(self, **kwargs):
        args = fixtures.ProviderSegmentTests().data(length=512, **kwargs)
        for frame in args[-2:]:
            for row in frame['rows']:
                for key in ('before', 'after'):
                    row[key][5:7] = [0, 4]
                    row['segments'][key] = [[0, UNKNOWN] for _ in row['segments'][key]]
        return args[-2:]

    def entries(self, frames):
        return [r['segments'][k] for f in frames for r in f['rows'] for k in ('before', 'after')]

    def test_only_all_eight_initial_sectors_count_as_whole(self):
        frames = self.data()
        self.assertEqual(len(initial_pages(*frames)['whole_initial']), 2)
        for entries in self.entries(frames):
            entries[0] = [4, 0]
        result = initial_pages(*frames)
        self.assertEqual(len(result['whole_initial']), 1)
        self.assertEqual(len(result['partial_initial']), 1)

    def test_changed_odd_and_invalidated_records_are_not_whole_initial(self):
        for kind in ('changed', 'odd', 'invalidated'):
            frames = self.data()
            if kind == 'changed':
                self.entries(frames)[-1][0] = [2, UNKNOWN]
            else:
                for entries in self.entries(frames):
                    entries[0] = [1 if kind == 'odd' else 2, UNKNOWN]
            result = initial_pages(*frames)
            self.assertEqual(len(result['whole_initial']), 1, kind)
            self.assertEqual(len(result['partial_initial']), int(kind == 'invalidated'), kind)

    def test_pfn_changes_release_and_allocation_changes_reject_admission(self):
        for kind in ('pfn', 'released', 'allocation', 'nonresident', 'hidden'):
            frames = self.data()
            if kind == 'pfn':
                frames[-1]['rows'][0]['pagemap'][0] = PRESENT | 90
            elif kind == 'released':
                frames[-1]['rows'][0]['after'][6] = 5
            elif kind == 'allocation':
                frames[-1]['rows'][0]['after'][2] += 512
            else:
                for f in frames:
                    f['rows'][0]['pagemap'][0] = PRESENT if kind == 'hidden' else 0
            self.assertEqual(len(initial_pages(*frames)['whole_initial']),
                0 if kind in ('released', 'allocation') else 1, kind)

    def test_global_header_change_does_not_discard_untouched_tail(self):
        frames = self.data()
        frames[-1]['rows'][0]['after'][1] += 4
        self.assertEqual(len(initial_pages(*frames)['whole_initial']), 2)

    def test_boundary_pages_are_separate(self):
        result = initial_pages(*self.data(address=4608))
        self.assertEqual(len(result['whole_initial']), 1)
        self.assertEqual(len(result['boundary']), 2)

    def test_aliases_deduplicate_and_conflicting_alias_blocks_whole(self):
        frames = self.data()
        for f in frames:
            f['rows'][0]['pagemap'][1] = f['rows'][0]['pagemap'][0]
        self.assertEqual(len(initial_pages(*frames)['whole_initial']), 1)
        for entries in self.entries(frames):
            entries[8] = [4, 0]
        self.assertEqual(len(initial_pages(*frames)['whole_initial']), 0)

    def test_unstable_and_boundary_aliases_block_whole(self):
        for kind in ('unstable', 'boundary'):
            frames = self.data()
            for f in frames:
                row = copy.deepcopy(f['rows'][0])
                row['name'] += '-alias'
                if kind == 'boundary':
                    for key in ('before', 'after'):
                        row[key][2] += 512
                f['rows'].append(row)
            if kind == 'unstable':
                frames[-1]['rows'][1]['pagemap'][0] = PRESENT | 90
            self.assertNotIn(50, initial_pages(*frames)['whole_initial'])

    def test_contradictory_legacy_read_fails_instead_of_reclassifying(self):
        frames = self.data()
        for f in frames:
            for key in ('before', 'after'):
                f['rows'][0][key][5:7] = [4096, 3]
        with self.assertRaises(AssertionError):
            initial_pages(*frames)


if __name__ == '__main__':
    unittest.main()
