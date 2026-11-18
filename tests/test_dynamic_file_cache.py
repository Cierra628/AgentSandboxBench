"""Synthetic binary data, fake proc/sysfs and mock CLI only; no VM or probe."""
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import file_cache_metadata as metadata
import guest_file_cache_query as query
from kernel_cache_metadata import HEADER as GHEADER, RECORD as GRECORD, FIELDS as GFIELDS, PRESENT, SWAPPED
from live_task_cache import HOST_ROW, LiveTaskCache
from report_dynamic_file_cache import analyze, damon_delta


def file_dump(rows, **changes):
    fields = dict.fromkeys(metadata.FIELDS, 0)
    fields.update(version=1, page_size=4096, records=len(rows), start_ns=10, end_ns=11,
                  file_dev=123, file_ino=10, file_generation=1, size_before=8192, size_after=8192,
                  mapping_dev=123, mapping_ino=20, mapping_generation=2,
                  generation_before=100, generation_after=100,
                  unknown_records=sum(t == 0 for i, p, t in rows),
                  mapping_pages_before=len(rows), mapping_pages_after=len(rows))
    fields.update(changes)
    return metadata.HEADER.pack(b'ASBFILE1', *(fields[k] for k in metadata.FIELDS)) + b''.join(metadata.RECORD.pack(*r) for r in rows)


def guest_dump(rows):
    fields = dict.fromkeys(GFIELDS, 0)
    fields.update(version=1, page_size=4096, max_pfn=1 << 21, records=len(rows), generation=100)
    return GHEADER.pack(b'ASBCACH1', *(fields[k] for k in GFIELDS)) + b''.join(GRECORD.pack(*r) for r in rows)


def observe(left, right=None, host=None, allguest=None):
    right = left if right is None else right
    a = metadata.decode(file_dump(left))
    b = metadata.decode(file_dump(right, start_ns=20, end_ns=21))
    guest = ({}, dict((p, t) for i, p, t in left)) if allguest is None else ({}, allguest)
    last = ({}, dict((p, t) for i, p, t in right)) if allguest is None else ({}, allguest)
    if host is None:
        host = [dict(guest_pfn=p, first=PRESENT | (1000+p), middle=PRESENT | (1000+p), last=PRESENT | (1000+p)) for i, p, t in left]
    return metadata.account(a, b, guest, last, host)


class DynamicFileCacheTests(unittest.TestCase):
    def test_reloaded_file_uses_new_pfn_not_initial_list(self):
        initial = observe([(0, 1, 2)])
        reloaded = observe([(0, 9, 18)])
        self.assertEqual(initial['confirmed_host_pfns'], [1001])
        self.assertEqual(reloaded['confirmed_host_pfns'], [1009])
        self.assertEqual(reloaded['current_file_cache_after_bytes'], 4096)

    def test_change_between_queries_is_unknown_not_stable_old_file(self):
        row = observe([(0, 1, 2)], [(0, 9, 18)])
        self.assertEqual(row['confirmed_host_union_bytes'], 0)
        self.assertEqual(row['unconfirmed_after_pages'], 1)
        self.assertEqual(row['status'], 'PARTIAL')

    def test_pfn_reused_by_other_file_is_not_confirmed(self):
        row = observe([(0, 1, 2)], allguest={1: 6})
        self.assertEqual(row['stable_resident_guest_pages'], 0)
        self.assertEqual(row['unconfirmed_after_pages'], 1)

    def test_stale_record_is_unknown_not_file_zero(self):
        row = observe([(0, 1, 0)])
        self.assertIsNone(row['current_file_cache_after_bytes'])
        self.assertEqual(row['unconfirmed_after_pages'], 1)

    def test_empty_actual_query_can_observe_zero(self):
        row = observe([])
        self.assertEqual(row['current_file_cache_after_bytes'], 0)
        self.assertEqual(row['status'], 'OBSERVED')

    def test_changed_mapping_and_hidden_pfn(self):
        host = [dict(guest_pfn=1, first=PRESENT | 50, middle=PRESENT | 51, last=PRESENT | 51)]
        self.assertEqual(observe([(0, 1, 2)], host=host)['unconfirmed_after_pages'], 1)
        host = [dict(guest_pfn=1, first=PRESENT, middle=PRESENT, last=PRESENT)]
        with self.assertRaisesRegex(ValueError, 'hidden'):
            observe([(0, 1, 2)], host=host)

    def test_deduplicates_physical_pages_and_separates_swap(self):
        host = [dict(guest_pfn=p, first=PRESENT | 50, middle=PRESENT | 50, last=PRESENT | 50) for p in (1, 2)]
        row = observe([(0, 1, 2), (1, 2, 4)], host=host)
        self.assertEqual(row['confirmed_host_union_bytes'], 4096)
        self.assertEqual(row['stable_resident_guest_pages'], 2)
        host = [dict(guest_pfn=1, first=SWAPPED | 8, middle=SWAPPED | 8, last=SWAPPED | 8)]
        self.assertEqual(observe([(0, 1, 2)], host=host)['host_swapped_pages'], 1)

    def test_identity_size_and_count_changes_not_silently_accepted(self):
        before = metadata.decode(file_dump([(0, 1, 2)]))
        for kw in ({'file_ino': 11}, {'mapping_ino': 21}, {'size_after': 4096}):
            after = metadata.decode(file_dump([(0, 1, 2)], start_ns=20, end_ns=21, **kw))
            with self.assertRaises(ValueError):
                metadata.account(before, after, ({}, {1: 2}), ({}, {1: 2}), [])
        after = metadata.decode(file_dump([], start_ns=20, end_ns=21, mapping_pages_after=1))
        row = metadata.account(before, after, ({}, {1: 2}), ({}, {}), [])
        self.assertIsNone(row['current_file_cache_after_bytes'])
        self.assertEqual(row['status'], 'PARTIAL')

    def test_decoder_rejects_corrupt_and_duplicate_records(self):
        for raw in (b'', file_dump([(0, 1, 2)])[:-1],
                    file_dump([(0, 1, 2), (0, 2, 4)]), file_dump([(0, 1, 2), (1, 1, 2)]),
                    file_dump([(0, 1, 2)], unknown_records=1)):
            with self.assertRaises(ValueError):
                metadata.decode(raw)

    def test_missing_damon_is_unknown_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'enabled').write_text('Y\n')
            row = query.damon_snapshot(path)
            self.assertEqual(row['values']['enabled'], 'Y')
            self.assertIsNone(row['values']['bytes_reclaimed_regions'])
            self.assertEqual(row['status'], 'UNAVAILABLE')
            self.assertEqual(list(path.iterdir()), [path/'enabled'])
            self.assertEqual((path/'enabled').read_text(), 'Y\n')

    def test_damon_reset_has_no_reclaimed_byte_delta(self):
        a = dict(values={'bytes_reclaimed_regions': 10, 'enabled': 'Y'})
        b = dict(values={'bytes_reclaimed_regions': 5, 'enabled': 'Y'})
        self.assertIsNone(damon_delta(a, b)['values']['bytes_reclaimed_regions'])

    def test_missing_interface_is_error_not_touching_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'file'; path.write_bytes(b'x')
            with self.assertRaises(FileNotFoundError):
                query.file_query(path, Path(tmp)/'missing-proc')
            raw, receipt = query.file_query(Path(tmp)/'not-created')
            self.assertIsNone(raw)
            self.assertEqual(receipt['status'], 'UNAVAILABLE')

    def test_fd_query_reads_only_proc_not_target_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'file'; path.write_bytes(b'x'*8192)
            st = path.stat()
            raw = file_dump([(0, 1, 2)], file_dev=st.st_dev, file_ino=st.st_ino)
            class Endpoint(io.BytesIO):
                def write(self, request):
                    self.request = request
                    return len(request)
            endpoint = Endpoint(raw)
            with patch.object(Path, 'open', return_value=endpoint), patch('guest_file_cache_query.os.read', side_effect=AssertionError('payload read')):
                result, receipt = query.file_query(path)
            self.assertEqual(result, raw)
            self.assertEqual(receipt['payload_read_bytes'], 0)
            self.assertTrue(endpoint.request.strip().isdigit())

    def test_old_saved_data_without_sidecar_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = analyze(Path(tmp), Path(tmp)/'missing')
            self.assertEqual(row['status'], 'UNAVAILABLE')
            self.assertEqual(row['observations'], [])

    def test_exporter_failure_downloads_sidecars_without_hiding_failure(self):
        for download_error in (None, RuntimeError('download failed')):
            live = LiveTaskCache.__new__(LiveTaskCache)
            live.stop_event = Mock(); live.thread = None
            live.memory = SimpleNamespace(fileno=lambda: 10, close=Mock())
            live.addresses = [4096]; live.sid = 'a'; live.guest = '/fake-guest'
            live.file_cache_path = '/workspace/file'
            live.cli = Mock(side_effect=RuntimeError('exporter failed'))
            live._download_file_queries = Mock(side_effect=download_error)
            live.provider = live.lower_query = live.backend = None
            live.capture = Mock()
            with patch('live_task_cache.os.pwrite'):
                with self.assertRaisesRegex(RuntimeError, 'exporter failed') as failure:
                    live.close()
            live._download_file_queries.assert_called_once()
            if download_error:
                self.assertIn('download failed', failure.exception.__notes__[0])

    def fixture(self, root):
        live, side = root/'live', root/'side'
        live.mkdir(); side.mkdir()
        (live/'marker.json').write_text(json.dumps(dict(sandbox_id='a', boot_id='boot',
            file_cache_query={'path': '/workspace/file'})))
        for pub, started in ((1, 10), (2, 20)):
            raw = file_dump([(0, 9, 18)], start_ns=started, end_ns=started+1)
            (side/f'file-{pub:04}.bin').write_bytes(raw)
            receipt = dict(version=1, publication=pub, sandbox_id='a', boot_id='boot',
                file_cache=dict(status='OBSERVED', path='/workspace/file', header=metadata.decode(raw)[0], identity={'st_ino': 10}),
                damon=dict(values={'enabled': 'Y', 'bytes_reclaimed_regions': 100*pub}))
            (side/f'file-{pub:04}.json').write_text(json.dumps(receipt))
        point = dict(point=0, publication=2, guest_start_ns=20, guest_end_ns=22,
                     guest_publication_before=dict(publication=1, guest_start_ns=10, guest_end_ns=12))
        (live/'samples.jsonl').write_text(json.dumps(point)+'\n')
        for suffix in ('before', 'after'):
            (live/f'pair-0000-{suffix}.bin').write_bytes(guest_dump([(9, 18)]))
        (live/'pair-0000-host.bin').write_bytes(HOST_ROW.pack(9, PRESENT | 900, PRESENT | 900, PRESENT | 900))
        return live, side

    def test_saved_sidecar_to_physical_pair_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, side = self.fixture(Path(tmp))
            row = analyze(live, side)
            self.assertEqual(row['status'], 'OBSERVED')
            self.assertEqual(row['observations'][0]['confirmed_host_union_bytes'], 4096)
            self.assertEqual(row['observations'][0]['damon_delta']['values']['bytes_reclaimed_regions'], 100)

    def test_wrong_guest_or_query_time_is_rejected(self):
        for field, value in (('sandbox_id', 'b'), ('boot_id', 'other-boot'), ('publication', 7)):
            with tempfile.TemporaryDirectory() as tmp:
                live, side = self.fixture(Path(tmp))
                p = side/'file-0001.json'; receipt = json.loads(p.read_text()); receipt[field] = value
                p.write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError, 'another publication/guest'):
                    analyze(live, side)
        with tempfile.TemporaryDirectory() as tmp:
            live, side = self.fixture(Path(tmp))
            p = live/'samples.jsonl'; s = json.loads(p.read_text()); s['guest_start_ns'] = 25
            p.write_text(json.dumps(s)+'\n')
            with self.assertRaisesRegex(ValueError, 'outside its publication'):
                analyze(live, side)


if __name__ == '__main__':
    unittest.main()
