"""Offline mapping only: synthetic segments and private metadata fixtures."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from snapshot_layer_metadata import RawMemoryLayer, HEADER, MAGIC, MAGIC1, UNKNOWN


class CrossSegmentOffsetTests(unittest.TestCase):
    def mapper(self, segments, tags=None):
        layer = RawMemoryLayer.__new__(RawMemoryLayer)
        layer.segments, layer.starts = segments, [s[0] for s in segments]
        layer.tags = [0]*len(segments) if tags is None else tags
        return layer

    def test_all_observed_splits_and_format_limit(self):
        for first in (1, 3, 6, 5):
            with self.subTest(first=first):
                layer = self.mapper([(0, first, 8, False), (first, 16, 8+first, False)])
                self.assertEqual(layer.offsets([0]).tolist(), [1])
        layer = self.mapper([(0, 16383, 8, False), (16383, 32766, 16391, False)])
        self.assertEqual(layer.offsets([2047]).tolist(), [2048])

    def test_more_than_two_segments_and_exact_page_end(self):
        layer = self.mapper([(i, i+1, i+8, False) for i in range(8)])
        self.assertEqual(layer.offsets([0, 1]).tolist(), [1, UNKNOWN])

    def test_gaps_overlap_and_incomplete_tail_stay_unknown(self):
        cases = [[], [(8, 16, 8, False)], [(0, 3, 8, False)],
                 [(0, 3, 8, False), (4, 8, 12, False)],
                 [(0, 3, 8, False), (2, 8, 10, False)],
                 [(0, 3, 8, False), (3, 7, 11, False)]]
        for segments in cases:
            with self.subTest(segments=segments):
                self.assertEqual(self.mapper(segments).offsets([0]).tolist(), [UNKNOWN])

    def test_page_start_in_gap_is_not_extended_from_previous_segment(self):
        layer = self.mapper([(0, 4, 8, False), (9, 24, 17, False)])
        self.assertEqual(layer.offsets([1]).tolist(), [UNKNOWN])

    def test_zero_and_mixed_zero_segments_stay_unknown(self):
        for zeros in ((True, False), (False, True), (True, True)):
            with self.subTest(zeros=zeros):
                layer = self.mapper([(0, 3, 8, zeros[0]), (3, 8, 11, zeros[1])])
                self.assertEqual(layer.offsets([0]).tolist(), [UNKNOWN])

    def test_file_discontinuity_in_either_direction_stays_unknown(self):
        for physical in (10, 12, 24):
            with self.subTest(physical=physical):
                layer = self.mapper([(0, 3, 8, False), (3, 8, physical, False)])
                self.assertEqual(layer.offsets([0]).tolist(), [UNKNOWN])

    def test_unaligned_file_start_stays_unknown(self):
        layer = self.mapper([(0, 3, 9, False), (3, 8, 12, False)])
        self.assertEqual(layer.offsets([0]).tolist(), [UNKNOWN])

    def test_cross_segment_requires_tags_for_this_one_file(self):
        segments = [(0, 3, 8, False), (3, 8, 11, False)]
        for tags in ([0, 1], [1, 0], [1, 1]):
            with self.subTest(tags=tags):
                self.assertEqual(self.mapper(segments, tags).offsets([0]).tolist(), [UNKNOWN])
        layer = self.mapper(segments); del layer.tags
        self.assertEqual(layer.offsets([0]).tolist(), [UNKNOWN])

    def test_existing_single_segment_does_not_gain_tag_preconditions(self):
        layer = self.mapper([(0, 16, 8, False)], [7])
        self.assertEqual(layer.offsets([0, 1]).tolist(), [1, 2])
        del layer.tags
        self.assertEqual(layer.offsets([0, 1]).tolist(), [1, 2])

    def test_unknown_order_duplicates_iterators_and_dtype(self):
        layer = self.mapper([(0, 3, 8, False), (3, 16, 11, False)])
        for pages in ([1, UNKNOWN, 0, 0], iter([1, UNKNOWN, 0, 0]),
                      np.array([1, UNKNOWN, 0, 0], dtype='<u8')):
            actual = layer.offsets(pages)
            self.assertEqual(actual.tolist(), [2, UNKNOWN, 1, 1])
            self.assertEqual(actual.dtype, np.dtype('<u8'))
        self.assertEqual(layer.offsets([]).tolist(), [])
        self.assertEqual(layer.offsets([]).dtype, np.dtype('<u8'))

    def index(self):
        # Include a skipped zero-length row with a nonzero tag.
        return b''.join(struct.pack('<QQ', low, high) for low, high in
                        [(3<<50, 8), (0, 9<<56), (3|(5<<50), 11)])

    def test_constructor_keeps_tags_aligned_without_reading_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'layer'
            raw = bytearray(5*4096)
            raw[:HEADER.size] = HEADER.pack(MAGIC, MAGIC1, 390, 7, 3*4096, 3, 4096)
            raw[-4096:-4096+HEADER.size] = HEADER.pack(MAGIC, MAGIC1, 390, 6, 3*4096, 3, 4096)
            raw[3*4096:3*4096+48] = self.index()
            path.write_bytes(raw)
            import os
            pread = os.pread; calls = []
            def read(fd, length, offset):
                calls.append((length, offset)); return pread(fd, length, offset)
            with patch('snapshot_layer_metadata.os.pread', side_effect=read):
                layer = RawMemoryLayer(path, None)
            self.assertEqual(calls, [(4096, 0), (4096, 4*4096), (48, 3*4096)])
            self.assertEqual(layer.tags, [0, 0])
            self.assertEqual(layer.offsets([0]).tolist(), [1])

    def test_saved_loader_keeps_tags_and_existing_integrity_call(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            (directory/'index.bin').write_bytes(self.index())
            (directory/'metadata.json').write_text(json.dumps(dict(index_offset=3*4096, index_sha256='receipt')))
            # Preserve the existing explicit integrity call without computing a new hash.
            with patch('snapshot_layer_metadata.hashlib.sha256') as digest:
                digest.return_value.hexdigest.return_value = 'receipt'
                layer = RawMemoryLayer.from_saved(directory)
                digest.assert_called_once_with(self.index())
            self.assertEqual(layer.tags, [0, 0])
            self.assertEqual(layer.offsets([0]).tolist(), [1])


if __name__ == '__main__':
    unittest.main()
