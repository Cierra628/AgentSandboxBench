"""Synthetic metadata only; no hashes, live queries, probes or services."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from kernel_cache_metadata import decode, PRESENT, SWAPPED
from provider_slot_cache import UNKNOWN
from report_cache_unknown import classify
from report_guest_unconfirmed import GUARDED, classify_point, partition_reasons
from test_kernel_cache_metadata import dump
import test_provider_segments as fixtures


class GuestUnconfirmedTests(unittest.TestCase):
    def data(self):
        return fixtures.ProviderSegmentTests().data()

    def count(self, args):
        return classify_point(*args[:5], {1: 0, 2: 1}, *args[5:])

    def test_details_do_not_change_existing_positional_lower_contract_or_counts(self):
        args = self.data()
        left, right = decode(args[0])[1], decode(args[1])[1]
        original = classify(left, right, *args[2:4], {1: 0, 2: 1}, args[4])
        details = {}
        self.assertEqual(classify(left, right, *args[2:4], {1: 0, 2: 1}, args[4], details=details), original)
        self.assertEqual(details, {1: 'backend_without_pfn', 2: 'confirmed'})

    def test_complete_legacy_and_segment_bytes_confirm_once_without_mutating_inputs(self):
        args = self.data(); saved = copy.deepcopy(args)
        value, reasons, rescued = self.count(args)
        self.assertEqual(args, saved)
        self.assertEqual(value['counts']['confirmed'], 1)
        self.assertEqual(value['counts']['provider_confirmed'], 1)
        self.assertEqual(value['unconfirmed_guest_capacity_bytes'], 0)
        self.assertEqual(value['three_layer_unconfirmed_guest_capacity_bytes'], 4096)
        self.assertEqual(rescued, {1})
        self.assertEqual(reasons, {1: 'provider_confirmed', 2: 'confirmed'})

    def test_partial_byte_coverage_and_zero_source_rows_stay_unknown(self):
        args = fixtures.ProviderSegmentTests().data(length=512)
        for entries in fixtures.ProviderSegmentTests().entries(args):
            entries[0] = [4, UNKNOWN]
        value, reasons, rescued = self.count(args)
        self.assertEqual(rescued, set())
        self.assertEqual(value['counts']['backend_without_pfn'], 1)
        self.assertEqual(value['unconfirmed_guest_capacity_bytes'], 4096)
        self.assertEqual(reasons[1], 'backend_without_pfn')

    def test_identity_mapping_and_swap_guards_precede_provider(self):
        for kind, reason in [('identity', 'added_or_reused'), ('mapping', 'vmm_pte_changed'), ('swap', 'swapped')]:
            with self.subTest(kind=kind):
                args = self.data()
                if kind == 'identity': args[1] = dump([(1, 6), (2, 4)])
                elif kind == 'mapping': args[2][0] = (1, 0, PRESENT | 60, 0)
                else: args[2][0] = (1, SWAPPED | 30, SWAPPED | 30, SWAPPED | 30)
                value, reasons, rescued = self.count(args)
                self.assertEqual(rescued, set())
                self.assertEqual(reasons[1], reason)
                self.assertEqual(value['unconfirmed_guest_capacity_bytes'], 4096)

    def test_after_membership_excludes_removed_before_and_distinguishes_new_from_reuse(self):
        args = self.data(); args[1] = dump([(2, 4), (3, 6)])
        value, reasons, _ = self.count(args)
        self.assertEqual(set(reasons), {2, 3})
        self.assertEqual(value['counts']['added_or_reused'], 1)
        self.assertEqual(value['added_or_reused_subtypes'], dict(absent_before=1, tag_changed=0))
        args = self.data(); args[1] = dump([(1, 6), (2, 4)])
        self.assertEqual(self.count(args)[0]['added_or_reused_subtypes'], dict(absent_before=0, tag_changed=1))

    def test_inconsistent_provider_identity_proof_is_rejected(self):
        for reason in GUARDED:
            with self.subTest(reason=reason), self.assertRaises(AssertionError):
                partition_reasons({1: reason}, {1})
        with self.assertRaises(AssertionError): partition_reasons({1: 'confirmed'}, {2})

    def test_existing_confirmed_alias_and_provider_rescue_are_disjoint(self):
        original = {1: 'confirmed', 2: 'backend_without_pfn'}
        counts, final, rescued = partition_reasons(original, {1, 2})
        self.assertEqual(original, {1: 'confirmed', 2: 'backend_without_pfn'})
        self.assertEqual((counts['confirmed'], counts['provider_confirmed']), (1, 1))
        self.assertEqual(sum(counts.values()), 2)
        self.assertEqual(rescued, {2})

    def test_stable_provider_can_rescue_changing_backend_results(self):
        args = self.data(); args[4][0] = (1, 0, PRESENT | 80, 0)
        value, _, rescued = self.count(args)
        self.assertEqual(rescued, {1})
        self.assertEqual(value['provider_rescued_from'], {'backend_pfn_changed': 1})
        self.assertEqual(value['unconfirmed_guest_capacity_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
