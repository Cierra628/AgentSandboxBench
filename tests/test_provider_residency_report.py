"""Report partition and compatibility checks; synthetic metadata only."""
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from provider_residency_report import FIELDS, build_breakdown, partition, point_breakdown
from provider_slot_cache import UNKNOWN, account_observed_provider
import report_live_task_cache as reporter
import test_provider_segments as fixtures


class ProviderResidencyReportTests(unittest.TestCase):
    def test_partition_is_disjoint_and_current_has_priority_over_old_alias(self):
        values = partition(set(range(7)), {0, 1}, {1, 2}, {3},
            dict(all_sources_absent_both={4}, mixed_absent_and_stable_cache={5}))
        self.assertEqual(values['old_snapshot_only_bytes'], {2})
        self.assertEqual(values['unresolved_bytes'], {6})
        self.assertEqual(sum(map(len, values.values())), 7)

    def test_conflicting_categories_fail_instead_of_double_counting(self):
        with self.assertRaises(AssertionError):
            partition({1}, set(), set(), {1}, dict(all_sources_absent_both={1}))
        with self.assertRaises(AssertionError):
            partition({1}, set(), set(), set(), dict(all_sources_absent_both={1},
                mixed_absent_and_new_cache={1}))

    def test_unresolved_sources_and_unmeasured_points_are_not_zero_residency(self):
        values = partition({1}, set(), set(), set(), dict(unresolved_no_absent_proof={1}))
        self.assertEqual(values['unresolved_bytes'], {1})
        result = build_breakdown([Path('/not-opened')], [[dict(point=0)]], ['parent'])
        self.assertEqual(result['sessions']['parent']['status'], 'NOT_APPLICABLE')
        self.assertEqual(result['sessions']['parent']['points'], [])

    def saved_point(self, directory, mode):
        args = fixtures.ProviderSegmentTests().data(length=512)
        for entries in fixtures.ProviderSegmentTests().entries(args):
            if mode == 'unused': entries[:8] = [[0, UNKNOWN] for _ in range(8)]
            elif mode == 'partial': entries[0] = [0, UNKNOWN]
            elif mode == 'other': entries[:8] = [[4, 3 * 4096 + i * 512] for i in range(8)]
        metrics, _ = account_observed_provider(*args)
        saved = dict(point=0, timestamp_ns=1, **metrics)
        prefix = directory / 'pair-0000'
        for suffix, data in [('-before.bin', args[0]), ('-after.bin', args[1])]:
            Path(str(prefix) + suffix).write_bytes(data)
        for suffix, rows in zip(('-host.bin', '-backend.bin', '-lower.bin'), args[2:5], strict=True):
            Path(str(prefix) + suffix).write_bytes(b''.join(struct.pack('<4Q', *r) for r in rows))
        Path(str(prefix) + '-provider.json').write_text(json.dumps(dict(first=args[-2], last=args[-1])))
        region = dict(start=0x100000, end=0x105000, offset=0, device=6, inode=7,
            path='/dev/ublkb1', permissions='rw-p')
        Path(str(prefix) + '-maps.json').write_text(json.dumps(dict(before=[region], after=[region])))
        slots = [dict(guest=0, host=0x100000, size=5 * 4096)]
        return saved, slots, dict(device=6, inode=7)

    def test_point_reuses_admission_and_separates_unused_other_and_partial(self):
        for mode, category in [('current', 'current_cache_bytes'), ('unused', 'unused_allocation_bytes'),
                ('other', 'source_absent_both_bytes'), ('partial', 'unresolved_bytes')]:
            with tempfile.TemporaryDirectory() as name:
                directory = Path(name)
                saved, slots, identity = self.saved_point(directory, mode)
                original = saved.copy()
                result = point_breakdown(directory, saved, slots, identity)
                self.assertEqual(result[category], 8192 if mode == 'current' else 4096)
                self.assertEqual(sum(result[k] for k in FIELDS), result['whole_resident_bytes'])
                self.assertEqual(saved, original)
                if mode == 'partial':
                    self.assertEqual(result['unresolved_reason_bytes'], dict(segment_never_filled=4096))

    def test_changed_saved_metric_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            saved, slots, identity = self.saved_point(directory, 'current')
            saved['host_four_segment_current_union_bytes'] += 4096
            with self.assertRaises(AssertionError):
                point_breakdown(directory, saved, slots, identity)

    def test_inventory_reuse_checks_source_measurements_and_names_without_hashing(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); source = root / 'source'; (source / 'live-cache').mkdir(parents=True)
            (source / 'live-cache/metadata.json').write_text('{}')
            previous = dict(source='source', measurements=[dict(point=0)],
                raw_sha256={'live-cache/metadata.json': 'recorded-digest'})
            path = root / 'summary.json'
            with patch.object(reporter, 'ROOT', root), patch.object(reporter.hashlib, 'sha256', side_effect=AssertionError('unexpected hash')):
                path.write_text(json.dumps(previous))
                self.assertEqual(reporter.raw_inventory(source, previous['measurements'], path), previous['raw_sha256'])
                for key, value in [('source', 'other'), ('measurements', []), ('raw_sha256', {})]:
                    path.write_text(json.dumps(dict(previous, **{key: value})))
                    with self.assertRaises(AssertionError):
                        reporter.raw_inventory(source, previous['measurements'], path)

    def test_platform_report_links_new_outputs_only_when_present(self):
        import platform_tasks
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); workflow = root / 'workflow'; child = root / 'child'
            workflow.mkdir(); (child / 'python-backbone').mkdir(parents=True)
            (workflow / 'result.json').write_text(json.dumps(dict(status='PASS', storage_result_dir=str(child))))
            (workflow / 'effective-config.json').write_text(json.dumps(dict(backend='agentenv',
                task_id='sphinx-doc__sphinx-7590', rollout='single', live_cache_kernel='synthetic')))
            cleanup = {k: True for k in ('owned_container_removed', 'mount_and_loop_removed',
                'successful_fixture_removed', 'original_firecracker_identities_unchanged', 'original_service_unchanged')}
            (child / 'result.json').write_text(json.dumps(dict(status='PASS', cleanup=cleanup)))
            (child / 'python-backbone/result.json').write_text('{}')
            cache = dict(sample_count=2, observed_guest_cached_peak_bytes=1,
                observed_host_confirmed_peak_bytes=2, unconfirmed_capacity_max_bytes=3)
            def task_report(raw, output):
                output.mkdir(parents=True)
                (output / 'summary.json').write_text(json.dumps(dict(task_id='sphinx-doc__sphinx-7590')))
            for enabled in (False, True):
                measured = dict(cache)
                if enabled:
                    measured['provider_buffer_breakdown'] = dict(files=dict(curve='provider-buffer.svg', table='provider-buffer.csv'))
                output = root / '.artifacts' / str(enabled)
                with patch.object(platform_tasks, 'ROOT', root), patch.object(reporter, 'report', return_value=measured), \
                        patch.dict(sys.modules, {'report_xarray6992': SimpleNamespace(report=task_report)}):
                    platform_tasks.report(workflow, output)
                result = json.loads((output / 'summary.json').read_text())['live_cache']
                self.assertEqual('provider_buffer_curve' in result, enabled)
                if enabled:
                    self.assertEqual(result['provider_buffer_table'], 'live-cache/provider-buffer.csv')
                self.assertEqual(result['curve'], 'live-cache/cache.svg')


if __name__ == '__main__':
    unittest.main()
