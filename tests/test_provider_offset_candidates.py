"""Saved/synthetic metadata only; no device, service, or guest operations."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_provider_offset_candidates import MISSING, classify_sources, membership, reverse_page
from report_provider_segment_unknown import classify
from provider_slot_cache import UNKNOWN
import test_provider_segments as fixtures


class OffsetCandidateTests(unittest.TestCase):
    def data(self):
        slots = [dict(guest=0, host=0x100000, size=8192),
            dict(guest=0x100000000, host=0x200000, size=8192)]
        identity = dict(device=6, inode=7)
        regions = [dict(start=s['host'], end=s['host'] + s['size'], offset=i * 8192,
            device=6, inode=7, permissions='rw-p', path='/dev/ublkb1') for i, s in enumerate(slots)]
        maps = dict(before=regions, after=copy.deepcopy(regions))
        return maps, slots, identity

    def test_inverse_uses_actual_file_offset_and_gpa_gap(self):
        maps, slots, identity = self.data()
        self.assertEqual(reverse_page(1, maps['before'], slots, identity), {1})
        self.assertEqual(reverse_page(3, maps['before'], slots, identity), {0x100001})
        self.assertEqual(reverse_page(4, maps['before'], slots, identity), set())

    def test_anonymous_holes_identity_and_permissions_are_not_guessed(self):
        for kind in ('anonymous', 'inode', 'permissions', 'partial'):
            maps, slots, identity = self.data()
            for key in ('before', 'after'):
                row = maps[key][0]
                if kind == 'anonymous': row['path'] = ''
                elif kind == 'inode': row['inode'] += 1
                elif kind == 'permissions': row['permissions'] = 'rw-s'
                else: row['end'] -= 512
            self.assertEqual(membership(1, maps, slots, identity, {}, {})[0], 'mapping_unresolved', kind)

    def test_ambiguous_or_changed_mapping_is_unresolved(self):
        for kind in ('alias', 'change'):
            maps, slots, identity = self.data()
            if kind == 'alias':
                for key in ('before', 'after'): maps[key][1]['offset'] = 0
            else:
                maps['after'][0]['offset'] = 4096
            self.assertEqual(membership(0, maps, slots, identity, {}, {})[0], 'mapping_unresolved', kind)

    def test_membership_absence_is_distinct_from_zero_and_identity_changes(self):
        args = self.data()
        for left, right, expected in [({}, {}, 'absent_both'), ({}, {0: 2}, 'entered_cache'),
                ({0: 2}, {}, 'left_cache'), ({0: 2}, {0: 2}, 'stable_cache'),
                ({0: 2}, {0: 4}, 'cache_identity_changed'), ({0: 0}, {}, 'membership_unknown')]:
            self.assertEqual(membership(0, *args, left, right), (expected, 0))

    def test_whole_pages_and_mixed_sources_are_separate(self):
        maps, slots, identity = self.data()
        details = dict(reasons={50: {MISSING}, 51: {MISSING}, 52: {MISSING}},
            source_pages={50: {0}, 51: {1, 2}, 52: {2, 3}}, candidate_offsets={1})
        summary, trace = classify_sources(details, maps, slots, identity, {1: 2}, {1: 2, 0x100001: 4})
        counts = summary['by_source_membership_pages']
        self.assertEqual([counts[k] for k in ('all_sources_absent_both',
            'mixed_absent_and_stable_cache', 'mixed_absent_and_new_cache')], [1, 1, 1])
        self.assertEqual(summary['missing_offset_membership'], dict(absent_both=2, entered_cache=1))
        self.assertEqual(summary['newly_entered_guest_pages'], 1)
        self.assertEqual(len(trace['pages']), 3)

    def test_new_cache_alone_does_not_admit_a_current_page(self):
        maps, slots, identity = self.data()
        details = dict(reasons={50: {MISSING}}, source_pages={50: {0}}, candidate_offsets=set())
        summary, _ = classify_sources(details, maps, slots, identity, {}, {0: 2})
        self.assertEqual(summary['unresolved_without_absent_source_pages'], 1)
        self.assertEqual(summary['pages_with_absent_source'], 0)

    def test_physical_pages_deduplicate_and_conflicting_prior_reasons_stay_out(self):
        maps, slots, identity = self.data()
        details = dict(reasons={50: {MISSING}, 51: {MISSING, 'segment_record_changed'}},
            source_pages={50: {0, 2}, 51: {0}}, candidate_offsets=set())
        summary, _ = classify_sources(details, maps, slots, identity, {}, {})
        self.assertEqual(summary['missing_candidate_physical_pages'], 1)
        self.assertEqual(summary['missing_source_offset_pages'], 2)

    def test_optional_details_preserve_original_classification(self):
        args = fixtures.ProviderSegmentTests().data(length=512)
        for entries in fixtures.ProviderSegmentTests().entries(args): entries[0][1] = 2**30
        expected = classify(*args)
        details = {}
        self.assertEqual(classify(*args, details=details), expected)
        self.assertEqual(details['candidate_offsets'], {0, 1})
        self.assertEqual(details['reasons'][50], {MISSING})
        self.assertIn(2**30 // 4096, details['source_pages'][50])
        self.assertNotIn(UNKNOWN, details['source_pages'][50])


if __name__ == '__main__':
    unittest.main()
