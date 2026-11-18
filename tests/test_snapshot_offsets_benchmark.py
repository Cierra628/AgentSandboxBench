"""Pure benchmark bookkeeping; no real capture or timing passes."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from benchmark_snapshot_offsets import offset_candidate, outside_offsets, recorded_mapper


class OffsetBenchmarkTests(unittest.TestCase):
    def test_candidate_replaces_only_offsets_not_new_runtime_query(self):
        class Captured:
            def offsets(self, pfns): return ['old']
            def query(self, pfns): return ['captured-query']
        class Current:
            def offsets(self, pfns): return list(pfns)
            def query(self, pfns): raise AssertionError('new runtime query must not run')
        candidate = offset_candidate(Captured, Current.offsets)()
        self.assertEqual(candidate.offsets([1,2]), [1,2])
        self.assertEqual(candidate.query([]), ['captured-query'])
        self.assertEqual(Captured().offsets([]), ['old'])

    def test_change_scope_ignores_only_offset_method(self):
        original = 'class SnapshotCache:\n def offsets(self,pfns): return 1\n def query(self,pfns): return 2\n'
        changed = original.replace('return 1', 'return 3')
        self.assertEqual(outside_offsets(original), outside_offsets(changed))
        self.assertNotEqual(outside_offsets(original), outside_offsets(original.replace('return 2', 'return 4')))

    def test_recorder_calls_method_every_time_without_reusing_result(self):
        class Mapper:
            def offsets(self, pfns): return [self.slots, *pfns]
        mapper, calls = recorded_mapper(Mapper, 'first', (1, 2, 3))
        self.assertEqual(mapper.offsets([4]), ['first', 4])
        mapper.slots = 'second'
        self.assertEqual(mapper.offsets([5]), ['second', 5])
        self.assertEqual(calls, [['first', 4], ['second', 5]])


if __name__ == '__main__': unittest.main()
