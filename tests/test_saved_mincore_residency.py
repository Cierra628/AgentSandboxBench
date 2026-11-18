"""Pure offset/byte projection checks; never create a query object or live map."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from profile_saved_mincore_residency import (ORIGINAL, UNKNOWN, check_projection,
    projection_functions, synthetic_chunks)

# The offline entry compares the captured pre-change expression, not runtime.
SOURCE = f'''class SnapshotCache:
    def query(self, offsets):
        for run in chunks:
            if self.batch_entries is not None:
                {ORIGINAL}
'''


class ResidencyProjectionTests(unittest.TestCase):
    def check_offsets(self, offsets, phase=0):
        functions, _ = projection_functions(SOURCE)
        ordered, chunks = synthetic_chunks(np.array(offsets, dtype='<u8'), phase)
        results = {name: function(chunks) for name, function in functions.items()}
        self.assertEqual(results['current'], results['candidate'])
        for result in results.values():
            check_projection(result, ordered, phase)
        return results['candidate'], chunks

    def test_empty_and_unknown(self):
        for values in ([], [UNKNOWN], [UNKNOWN, UNKNOWN]):
            with self.subTest(values=values):
                result, chunks = self.check_offsets(values)
                self.assertEqual(result, {}); self.assertEqual(chunks, [])

    def test_duplicates_unsorted_and_full_byte_values(self):
        result, _ = self.check_offsets([7, 0, 3, 2, 1, 6, 5, 4, UNKNOWN, 7])
        self.assertEqual(list(result.values()), [0, 1, 2, 3, 128, 129, 254, 255])

    def test_resident_gap_is_not_requested(self):
        result, chunks = self.check_offsets([0, 2, 4])
        self.assertEqual(list(result), [0, 2, 4])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0][2][1], 1)
        self.assertEqual(sum(v & 1 for v in result.values()), 0)

    def test_two_page_gap_is_separate(self):
        _, chunks = self.check_offsets([1, 4, 6, 11])
        self.assertEqual([(start, len(v)) for _, start, v in chunks], [(1, 1), (4, 3), (11, 1)])

    def test_high_unsigned_offsets_remain_python_ints(self):
        for start in (1 << 40, (1 << 63)+8, UNKNOWN-4):
            with self.subTest(start=start):
                result, _ = self.check_offsets([start+2, start, UNKNOWN], phase=3)
                self.assertEqual(list(result), [start, start+2])

    def test_single_page_and_phase(self):
        result, chunks = self.check_offsets([12, 12], phase=3)
        self.assertEqual(result, {12: 255}); self.assertEqual(len(chunks[0][2]), 1)

    def test_source_drift_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            projection_functions(SOURCE.replace('vector[int(offset)-start]', 'vector[0]'))


if __name__ == '__main__':
    unittest.main()
