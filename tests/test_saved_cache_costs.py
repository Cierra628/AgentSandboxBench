"""Synthetic saved data only; constructors never open services or devices."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from fast_cache_metadata import decode_array, join_arrays
from profile_saved_cache_costs import COUNTED_STAGES, UNKNOWN, captured_snapshot_class, query_shape, replay
from provider_slot_cache import account_observed_provider
from snapshot_cache_residency import SnapshotCache, account
from snapshot_layer_metadata import RawMemoryLayer, account_layers
import test_provider_segments as fixtures


class SavedCostTests(unittest.TestCase):
    def data(self):
        args = fixtures.ProviderSegmentTests().data(length=512)
        before, after = map(decode_array, args[:2])
        host, direct, lower = [np.array(r, dtype='<u8') for r in args[2:5]]
        saved = dict(point=0, host_thread_cpu_ns=10**9, provider_query_cpu_ns=100)
        saved.update(join_arrays(before, after, host))
        saved.update(account(before, after, host, direct))
        saved.update(account_layers(before, after, host, direct, lower))
        measured, physical = account_observed_provider(*args)
        saved.update(measured)
        maps = [dict(start=4096, end=12288, offset=0, device=1, inode=2,
                     permissions='rw-p', path='/dev/ublkb0')]
        raw = dict(zip(('-before.bin', '-after.bin'), args[:2]))
        for suffix, rows in zip(('-host.bin', '-backend.bin', '-lower.bin'), args[2:5]):
            raw[suffix] = b''.join(struct.pack('<4Q', *r) for r in rows)
        raw['-provider.json'] = json.dumps(dict(first=args[5], last=args[6])).encode()
        raw['-maps.json'] = json.dumps(dict(before=maps, after=maps)).encode()
        mapper = SnapshotCache.__new__(SnapshotCache)
        mapper.slots = [dict(guest=4096, host=4096, size=8192)]
        mapper.identity = (1, 2, 0)
        layer = RawMemoryLayer.__new__(RawMemoryLayer)
        layer.segments = [(0, 16, 0, False)]; layer.starts = [0]
        return saved, raw, mapper, layer, physical

    def test_replay_retains_all_metrics_and_physical_set(self):
        saved, raw, mapper, layer, physical = self.data()
        result = replay(saved, raw, mapper, layer)
        self.assertEqual(result['physical'], physical)
        self.assertEqual(result['cache_pages'], 2)
        self.assertEqual(result['replay_inside_point_cpu_ns'],
                         sum(result['costs'][k]['cpu_ns'] for k in COUNTED_STAGES))
        self.assertNotIn('serialization_after_point_timer', COUNTED_STAGES)
        self.assertNotIn('offline_json_decode', COUNTED_STAGES)

    def test_corrupt_saved_metric_is_rejected(self):
        saved, raw, mapper, layer, _ = self.data()
        saved['host_four_segment_current_union_bytes'] += 4096
        with self.assertRaisesRegex(AssertionError, 'saved measurement differs'):
            replay(saved, raw, mapper, layer)

    def test_wrong_offset_is_rejected(self):
        saved, raw, mapper, layer, _ = self.data()
        maps = json.loads(raw['-maps.json']); maps['before'][0]['offset'] += 4096
        raw['-maps.json'] = json.dumps(maps).encode()
        with self.assertRaisesRegex(AssertionError, 'saved direct offsets differ'):
            replay(saved, raw, mapper, layer)

    def test_changed_mapping_requires_saved_query_invalidation(self):
        saved, raw, mapper, layer, _ = self.data()
        maps = json.loads(raw['-maps.json']); maps['after'][0]['offset'] += 4096
        raw['-maps.json'] = json.dumps(maps).encode()
        with self.assertRaises(AssertionError):
            replay(saved, raw, mapper, layer)

    def test_historical_profile_selects_captured_offset_implementation(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name); (directory/'code').mkdir()
            (directory/'code/snapshot_cache_residency.py').write_text(
                "from snapshot_cache_residency import account\n"
                "class SnapshotCache:\n    def offsets(self, pfns): return ['captured']\n")
            cls = captured_snapshot_class(directory)
            self.assertEqual(cls.__new__(cls).offsets([]), ['captured'])

    def test_changed_backend_accounting_is_rejected_for_historical_profile(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name); (directory/'code').mkdir()
            (directory/'code/snapshot_cache_residency.py').write_text(
                "def account(*args): return {}\nclass SnapshotCache: pass\n")
            with self.assertRaisesRegex(AssertionError, 'backend accounting differs'):
                captured_snapshot_class(directory)

    def test_query_shape_separates_rows_unique_offsets_and_runs(self):
        self.assertEqual(query_shape(np.array([UNKNOWN, 5, 6, 6, 9], dtype='<u8')),
            dict(requested_rows=5, file_rows=4, unique_file_offsets=3, contiguous_runs=2))
        self.assertEqual(query_shape(np.array([UNKNOWN], dtype='<u8')),
            dict(requested_rows=1, file_rows=0, unique_file_offsets=0, contiguous_runs=0))


if __name__ == '__main__':
    unittest.main()
