"""Source-history inference on synthetic metadata and payload identities only."""
import copy
import itertools
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provider_segment_model import SegmentModel, UNIT, UNKNOWN
from provider_slot_cache import MAGIC
from report_provider_refills import analyze_point, page_evidence
import test_provider_segments as fixtures


def read(model, offset, length):
    model.begin_read(offset, length)
    model.complete_read(length)


def frame(model, generation, reads, offset, length):
    header = [MAGIC, generation, 4096, model.capacity, offset, length, 3, reads]
    entries = model.snapshot()['entries']
    return dict(before=header, after=header.copy(), segments=dict(schema=1, unit_bytes=UNIT,
        before=entries, after=copy.deepcopy(entries)))


class RefillEvidenceTests(unittest.TestCase):
    def base(self, sectors=8):
        model = SegmentModel(sectors * UNIT)
        read(model, 0, 8 * UNIT)
        return model, frame(model, 6, 1, 0, 8 * UNIT)

    def test_exactly_one_matching_read_proves_source_refill(self):
        model, before = self.base()
        read(model, 0, 8 * UNIT)
        result = page_evidence(before, frame(model, 10, 2, 0, 8 * UNIT), 0)
        self.assertEqual(result['classification'], 'same_source_one_read_proven')
        self.assertEqual(result['counter_evidence']['kind'], 'one_read')

    def test_distinct_published_sources_are_not_called_repeated_refill(self):
        model, before = self.base()
        read(model, 20 * UNIT, 8 * UNIT)
        result = page_evidence(before, frame(model, 10, 2, 20 * UNIT, 8 * UNIT), 0)
        self.assertEqual(result['classification'], 'source_change_observed')
        self.assertEqual(result['source_changed_segments'], 8)

    def test_initial_fill_including_partial_page_is_separate(self):
        for length in (9 * UNIT, 16 * UNIT):
            model, before = self.base(16)
            read(model, 20 * UNIT, length)
            result = page_evidence(before, frame(model, 10, 2, 20 * UNIT, length), 8)
            self.assertEqual(result['classification'], 'initial_fill_observed')
            self.assertEqual(result['initially_unknown_filled_segments'], length // UNIT - 8)

    def test_two_histories_have_identical_four_records_and_counters(self):
        finals = []
        histories = []
        for offsets in ((0, 0), (20 * UNIT, 0)):
            model, before = self.base()
            sources = []
            for offset in offsets:
                read(model, offset, 8 * UNIT)
                sources.append(tuple(e[1] for e in model.entries))
            after = frame(model, 14, 3, 0, 8 * UNIT)
            finals.append((before, after))
            histories.append(sources)
            self.assertEqual(page_evidence(before, after, 0)['classification'], 'same_source_history_unknown')
        self.assertEqual(finals[0], finals[1])
        self.assertNotEqual(histories[0], histories[1])

    def test_odd_or_changing_entry_pairs_do_not_prove_published_sources(self):
        for odd in (False, True):
            model, before = self.base()
            after = copy.deepcopy(before)
            after['segments']['before'][0][0] += 1 if odd else 2
            self.assertEqual(page_evidence(before, after, 0)['classification'], 'incomplete_observation')

    def test_failed_or_get_data_counter_excess_does_not_prove_one_read(self):
        model, before = self.base()
        read(model, 0, 8 * UNIT)
        for generation in (12, 14):
            after = frame(model, generation, 2, 0, 8 * UNIT)
            self.assertEqual(page_evidence(before, after, 0)['classification'], 'same_source_history_unknown')

    def test_unknown_source_and_untouched_tail_are_not_refills(self):
        model, before = self.base(16)
        after = copy.deepcopy(before)
        for side in ('before', 'after'): after['segments'][side][0] = [6, UNKNOWN]
        self.assertEqual(page_evidence(before, after, 0)['classification'], 'incomplete_source_history')
        read(model, 0, UNIT)
        after = frame(model, 10, 2, 0, UNIT)
        # A page fully beyond the request prefix must not be labelled refilled.
        for f in (before, after):
            for side in ('before', 'after'):
                f['segments'][side][8:] = [[4, (8 + i) * UNIT] for i in range(8)]
        self.assertNotEqual(page_evidence(before, after, 8)['classification'], 'same_source_one_read_proven')

    def test_small_histories_check_every_positive_refill_proof(self):
        cases = 0
        for count in (1, 2, 3):
            for operations in itertools.product(((0, UNIT), (0, 8 * UNIT), (20 * UNIT, UNIT), (20 * UNIT, 8 * UNIT)), repeat=count):
                model, before = self.base()
                for offset, length in operations: read(model, offset, length)
                offset, length = operations[-1]
                after = frame(model, 6 + 4 * count, 1 + count, offset, length)
                result = page_evidence(before, after, 0)
                if result['classification'] == 'same_source_one_read_proven':
                    self.assertEqual(count, 1)
                    self.assertEqual(offset, 0)
                cases += 1
        self.assertEqual(cases, 84)

    def test_changed_pagemap_alias_is_not_used_as_source_evidence(self):
        args = fixtures.ProviderSegmentTests().data(length=512)
        for row in args[-1]['rows']:
            for key in ('before', 'after'):
                row[key][1] += 4
                row[key][4:6] = [20 * 4096, 8192]
                row[key][7] += 1
                for index, entry in enumerate(row['segments'][key]):
                    entry[:] = [8, 20 * 4096 + index * UNIT]
        for frame in args[-2:]:
            alias = copy.deepcopy(frame['rows'][0]); alias['name'] += '-alias'
            frame['rows'].append(alias)
        args[-1]['rows'][1]['pagemap'][0] += 100
        result, private = analyze_point(args)
        page = next(p for p in private if p['pfn'] == 50)
        self.assertEqual(len(page['observations']), 1)
        self.assertEqual(result['classification_pages']['source_change_observed'], 2)


if __name__ == '__main__':
    unittest.main()
