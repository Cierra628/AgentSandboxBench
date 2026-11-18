"""Synthetic index records only; no payload, kernel calls or services."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from review_guest_source_index import explain_page
from snapshot_layer_metadata import RawMemoryLayer, UNKNOWN


class SourceIndexTests(unittest.TestCase):
    def old(self, segments, page):
        layer = RawMemoryLayer.__new__(RawMemoryLayer)
        layer.segments, layer.starts = segments, [r[0] for r in segments]
        return int(layer.offsets(np.array([page], dtype='<u8'))[0])

    def test_max_length_boundary_is_contiguous_but_old_mapper_returns_unknown(self):
        segments = [(0, 16383, 8, False), (16383, 32766, 16391, False)]
        value = explain_page(segments, 2047)
        self.assertEqual(self.old(segments, 2047), UNKNOWN)
        self.assertEqual(value['reason'], 'cross_segment_contiguous')
        self.assertEqual(value['candidate_file_page'], 2048)
        self.assertEqual([p['sectors'] for p in value['pieces']], [7, 1])

    def test_all_four_observed_split_shapes(self):
        for first in (1, 3, 6, 5):
            with self.subTest(first=first):
                value = explain_page([(0, first, 8, False), (first, 16, 8+first, False)], 0)
                self.assertEqual(value['candidate_file_page'], 1)
                self.assertEqual([p['sectors'] for p in value['pieces']], [first, 8-first])

    def test_single_segment_preserves_existing_offset(self):
        segments = [(0, 32, 8, False)]
        value = explain_page(segments, 2)
        self.assertEqual(value['reason'], 'single_segment')
        self.assertEqual(value['candidate_file_page'], self.old(segments, 2))

    def test_gaps_include_empty_missing_prefix_and_internal_holes(self):
        for segments in ([], [(8, 16, 8, False)], [(0, 4, 8, False), (5, 16, 13, False)]):
            with self.subTest(segments=segments):
                value = explain_page(segments, 0)
                self.assertEqual(value['reason'], 'index_gap')
                self.assertEqual(value['candidate_file_page'], UNKNOWN)

    def test_zero_segments_and_mixed_data_zero_do_not_map(self):
        for segments in ([(0, 16, 0, True)], [(0, 4, 8, False), (4, 16, 0, True)]):
            with self.subTest(segments=segments):
                value = explain_page(segments, 0)
                self.assertEqual(value['reason'], 'zero_segment')
                self.assertEqual(value['candidate_file_page'], UNKNOWN)

    def test_file_discontinuity_is_not_hidden_by_virtual_adjacency(self):
        value = explain_page([(0, 4, 8, False), (4, 16, 24, False)], 0)
        self.assertEqual(value['reason'], 'file_discontinuity')
        self.assertEqual(value['candidate_file_page'], UNKNOWN)

    def test_unaligned_file_start_stays_unknown(self):
        value = explain_page([(0, 4, 9, False), (4, 16, 13, False)], 0)
        self.assertEqual(value['reason'], 'unaligned_file_page')
        self.assertEqual(value['candidate_file_page'], UNKNOWN)

    def test_unknown_input_is_preserved(self):
        self.assertEqual(explain_page([], UNKNOWN), dict(reason='unknown_input', candidate_file_page=UNKNOWN, pieces=[]))


if __name__ == '__main__':
    unittest.main()
