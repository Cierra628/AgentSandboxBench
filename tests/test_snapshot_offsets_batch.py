"""Pure mapping tests; no SnapshotCache constructor, devices or services."""
import copy
from pathlib import Path
import random
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from measure_file_physical import ram_address
from snapshot_cache_residency import PAGE, SnapshotCache


def scalar_offsets(mapper, pfns):
    # The successful capture's original per-page implementation is the oracle.
    offsets = []
    for pfn in pfns:
        address = ram_address(int(pfn), mapper.slots)
        matches = [r for r in mapper.regions if r['start'] <= address < r['end']]
        assert len(matches) == 1
        r = matches[0]
        is_file = r.get('path', '/dev/ublkb').startswith('/dev/ublkb')
        if is_file and hasattr(mapper, 'identity'):
            assert (r['device'], r['inode']) == mapper.identity[:2], 'RAM backend replaced'
            assert r['permissions'] == 'rw-p', 'RAM backend is no longer private'
        offsets.append((r['offset']+address-r['start'])//PAGE if is_file else (1<<64)-1)
    return np.array(offsets, dtype='<u8')


class BatchOffsetTests(unittest.TestCase):
    def mapper(self):
        s = SnapshotCache.__new__(SnapshotCache)
        s.slots = [dict(guest=0, host=0x100000, size=4*PAGE),
                   dict(guest=0x100000000, host=0x200000, size=2*PAGE)]
        s.identity = (6, 25, 0)
        def region(start, end, offset, path='/dev/ublkb25'):
            return dict(start=start, end=end, offset=offset, path=path,
                        device=6, inode=25, permissions='rw-p')
        s.regions = [region(0x100000, 0x102000, 0),
                     region(0x102000, 0x103000, 2*PAGE, ''),
                     region(0x103000, 0x104000, 3*PAGE),
                     region(0x200000, 0x202000, 4*PAGE)]
        return s

    def same(self, s, pfns):
        expected = scalar_offsets(s, pfns)
        actual = s.offsets(pfns)
        self.assertTrue(np.array_equal(actual, expected))
        self.assertEqual(actual.dtype, np.dtype('<u8'))
        return actual

    def test_order_duplicates_hole_boundaries_and_anonymous(self):
        s = self.mapper()
        pfns = [0x100001, 3, 0, 2, 1, 0x100000, 3]
        self.assertEqual(self.same(s, pfns).tolist(), [5, 3, 0, (1<<64)-1, 1, 4, 3])
        self.same(s, np.array(pfns, dtype='<u8'))
        self.assertEqual(s.offsets(iter(pfns)).tolist(), scalar_offsets(s, pfns).tolist())

    def test_empty_input_does_not_require_mapping_fields(self):
        s = SnapshotCache.__new__(SnapshotCache)
        s.slots = [{}]; s.regions = [{}]
        self.assertEqual(s.offsets([]).tolist(), [])
        self.assertEqual(s.offsets(iter([])).dtype, np.dtype('<u8'))

    def test_old_no_identity_and_missing_path_contract(self):
        s = self.mapper(); del s.identity
        for r in s.regions:
            r.pop('path'); r.pop('device'); r.pop('inode'); r.pop('permissions')
        self.same(s, [0, 2, 3, 0x100001])

    def test_missing_or_overlapping_ram_keeps_runtime_error(self):
        for pfn in (4, 0x100002, -1, 1<<64):
            with self.subTest(pfn=pfn):
                for fn in (scalar_offsets, lambda s, p: s.offsets(p)):
                    with self.assertRaises(RuntimeError): fn(self.mapper(), [pfn])
        s = self.mapper(); s.slots.append(copy.deepcopy(s.slots[0]))
        for fn in (scalar_offsets, lambda s, p: s.offsets(p)):
            with self.assertRaisesRegex(RuntimeError, 'has 2 RAM slots'): fn(s, [0])

    def test_missing_or_overlapping_vma_keeps_assertion(self):
        for kind in ('missing', 'overlap'):
            s = self.mapper()
            if kind == 'missing': s.regions.pop(0)
            else: s.regions.append(copy.deepcopy(s.regions[0]))
            for fn in (scalar_offsets, lambda s, p: s.offsets(p)):
                with self.assertRaises(AssertionError): fn(s, [0])

    def test_selected_file_identity_and_permissions_still_checked(self):
        for key, value, message in [('device', 7, 'RAM backend replaced'),
                                     ('inode', 26, 'RAM backend replaced'),
                                     ('permissions', 'rw-s', 'RAM backend is no longer private')]:
            s = self.mapper(); s.regions[0][key] = value
            for fn in (scalar_offsets, lambda s, p: s.offsets(p)):
                with self.assertRaisesRegex(AssertionError, message): fn(s, [0])

    def test_unused_and_anonymous_regions_do_not_gain_identity_requirements(self):
        s = self.mapper()
        for r in (s.regions[2], s.regions[3]):
            for key in ('device', 'inode', 'permissions'): r.pop(key)
        self.same(s, [0, 2])

    def test_unused_ram_slot_does_not_require_host_address(self):
        s = self.mapper()
        s.slots.append(dict(guest=1<<40, size=PAGE))
        self.same(s, [0, 1])

    def test_changed_maps_are_recomputed_without_cached_results(self):
        s = self.mapper(); self.same(s, [0, 1])
        s.regions[0]['offset'] += PAGE
        self.assertEqual(self.same(s, [0, 1]).tolist(), [1, 2])
        s.regions[0]['path'] = ''
        self.assertEqual(self.same(s, [0, 1]).tolist(), [(1<<64)-1]*2)

    def test_byte_offsets_are_not_assumed_page_aligned(self):
        s = self.mapper()
        s.slots[0]['host'] += 511
        s.regions[0]['offset'] = PAGE-1
        self.same(s, [0, 1])

    def test_shuffled_maps_slots_and_fixed_random_orders_match_scalar(self):
        rng = random.Random(4016)
        for _ in range(24):
            s = self.mapper()
            rng.shuffle(s.slots); rng.shuffle(s.regions)
            self.same(s, rng.choices([0, 1, 2, 3, 0x100000, 0x100001], k=41))


if __name__ == '__main__': unittest.main()
