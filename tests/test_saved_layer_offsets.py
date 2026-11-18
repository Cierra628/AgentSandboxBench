"""Saved-audit compatibility; private metadata only, no real hashes or services."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_lower_cache import captured_layer_offsets, saved_offsets, check_point
from snapshot_layer_metadata import RawMemoryLayer, UNKNOWN
import test_saved_cache_costs as saved_fixtures

ROOT = Path(__file__).resolve().parents[1]


class SavedLayerOffsetTests(unittest.TestCase):
    def layer(self):
        layer = RawMemoryLayer.__new__(RawMemoryLayer)
        layer.segments = [(0, 3, 8, False), (3, 16, 11, False)]
        layer.starts, layer.tags = [0, 3], [0, 0]
        return layer

    def test_saved_old_method_takes_precedence_over_current_mapping(self):
        source = ('raise RuntimeError("module must not run")\n'
                  'class RawMemoryLayer:\n'
                  ' raise RuntimeError("class must not run")\n'
                  ' def offsets(self, pages): return np.full(len(pages), UNKNOWN, dtype="<u8")\n')
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td); (directory/'code').mkdir()
            (directory/'code/snapshot_layer_metadata.py').write_text(source)
            self.assertEqual(self.layer().offsets([0]).tolist(), [1])
            self.assertEqual(saved_offsets(directory, self.layer(), [0]).tolist(), [UNKNOWN])

    def test_saved_new_method_uses_captured_cross_segment_logic(self):
        method = captured_layer_offsets((ROOT/'scripts/snapshot_layer_metadata.py').read_text())
        self.assertEqual(method(self.layer(), [0, 1, UNKNOWN]).tolist(), [1, 2, UNKNOWN])

    def test_no_source_existing_callers_keep_single_segment_contract(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(saved_offsets(Path(td), self.layer(), [0, 1]).tolist(), [UNKNOWN, 2])

    def test_present_but_broken_source_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td); (directory/'code').mkdir()
            (directory/'code/snapshot_layer_metadata.py').write_text('this is invalid:')
            with self.assertRaises(SyntaxError): saved_offsets(directory, self.layer(), [0])

    def run_audit(self, corrupt=False):
        saved, rows, _, layer, _ = saved_fixtures.SavedCostTests().data()
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            for suffix in ('-before.bin', '-after.bin', '-host.bin', '-backend.bin', '-lower.bin'):
                value = rows[suffix]
                if suffix == '-lower.bin' and corrupt:
                    data = np.frombuffer(value, dtype='<u8').copy().reshape(-1, 4)
                    data[0, 1] += 1; value = data.tobytes()
                (directory/('pair-0000'+suffix)).write_bytes(value)
            (directory/'lower-metadata').mkdir(); (directory/'lower-query').mkdir()
            (directory/'lower-metadata/metadata.json').write_text(json.dumps(dict(device=1, inode=2, size_bytes=4096)))
            (directory/'lower-query/snapshot-backend.json').write_text(json.dumps(
                dict(identity=dict(device=1, inode=2), mapping_length=4096)))
            with patch('audit_lower_cache.RawMemoryLayer.from_saved', return_value=layer):
                return check_point(directory, saved)

    def test_existing_audit_entry_keeps_physical_recount(self):
        result, physical = self.run_audit()
        self.assertEqual(result['host_three_current_union_bytes'], len(physical)*4096)

    def test_existing_audit_still_rejects_wrong_saved_offset(self):
        with self.assertRaises(AssertionError): self.run_audit(corrupt=True)


if __name__ == '__main__':
    unittest.main()
