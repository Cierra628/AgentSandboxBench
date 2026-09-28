import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from measure_file_physical import audit_run, captured_slots, collect, ext4_data_offsets, ram_address, summarize, validate_slots

PRESENT = 1 << 63
FILE = 1 << 61


class FilePhysicalTests(unittest.TestCase):
    """Synthetic PFNs validate logic; none of these tests prove live sharing."""

    def fixture(self, root):
        proc = root / 'proc/12'; proc.mkdir(parents=True)
        exe = root / 'python'; exe.touch(); (proc / 'exe').symlink_to(exe)
        backing = root / 'payload'; backing.write_bytes(b'a' * 8192)
        stat = backing.stat()
        device = f'{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}'
        (proc / 'stat').write_text('12 (name with spaces) ' + ' '.join(['S'] + ['0'] * 18 + ['1234']))
        (proc / 'cgroup').write_text('0::/owned/sandbox\n')
        (proc / 'cmdline').write_bytes(b'python\0/tmp/vmm-sandbox.socket\0')
        (proc / 'maps').write_text(f'1000-3000 rw-p 00000000 {device} {stat.st_ino} /path/with spaces\n')
        (proc / 'smaps_rollup').write_text('Pss: 7 kB\n')
        entries = [0, PRESENT | FILE | 101, PRESENT | FILE | 102]
        (proc / 'pagemap').write_bytes(struct.pack('<QQQ', *entries))
        group = root / 'cgroup/owned/sandbox'; group.mkdir(parents=True)
        (group / 'cgroup.procs').write_text('12\n')
        (group / 'memory.current').write_text('16384\n')
        (group / 'memory.stat').write_text('anon 8192\nfile 4096\nkernel 4096\n')
        vm = dict(id='sandbox', pid=12, start_time_ticks=1234, executable=str(exe),
                  cgroup='/owned/sandbox', api_socket='/tmp/vmm-sandbox.socket',
                  files=[dict(backing=str(backing), owner='/known.bin', offsets=[0, 4096])])
        return dict(page_size=4096, owned_root=str(root), vms=[vm]), proc, backing

    def scan(self, root, manifest):
        return collect(manifest, proc_root=root / 'proc', cgroup_root=root / 'cgroup')

    def test_dedup_across_vms_and_files_and_separate_memory_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            sibling = copy.deepcopy(manifest['vms'][0]); sibling['id'] = 'sibling'
            # Fake process metadata/entries represent two processes sharing PFNs.
            shutil.copytree(proc, root / 'proc/13', symlinks=True)
            sibling['pid'] = 13
            (root / 'cgroup/owned/sandbox/cgroup.procs').write_text('12\n13\n')
            manifest['vms'].append(sibling)
            result, rows = self.scan(root, manifest)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['file_content']['resident_mappings'], 4)
            self.assertEqual(result['file_content']['file_content_physical_bytes'], 8192)
            self.assertEqual(result['vms'][0]['process_pss']['bytes'], 7168)
            self.assertEqual(result['vms'][0]['guest_memory']['status'], 'NOT_MEASURED')
            self.assertEqual(len(result['host_cgroup_memory']), 1)
            self.assertEqual(len(rows), 4)

    def test_unavailable_memory_scopes_are_not_zero_or_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            (proc / 'smaps_rollup').unlink()
            (root / 'cgroup/owned/sandbox/memory.current').unlink()
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['vms'][0]['process_pss']['status'], 'NOT_MEASURED')
            self.assertIsNone(result['vms'][0]['process_pss']['bytes'])
            self.assertEqual(result['host_cgroup_memory']['/owned/sandbox']['status'], 'NOT_MEASURED')
            self.assertIsNone(result['host_cgroup_memory']['/owned/sandbox']['current_bytes'])

    def test_missing_pfn_null_and_first_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            (proc / 'pagemap').write_bytes(struct.pack('<QQQ', 0, PRESENT | FILE | 101, PRESENT))
            result, rows = self.scan(root, manifest)
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['first_failure']['location'], 'sandbox:pagemap:1')
            self.assertIsNone(result['file_content']['file_content_physical_bytes'])
            self.assertEqual(result['file_content']['observed_file_content_unique_pages'], 1)

    def test_offline_audit_cannot_upgrade_failure_or_accept_counter_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            (proc / 'pagemap').write_bytes(struct.pack('<QQQ', 0, PRESENT, PRESENT))
            result, rows = self.scan(root, manifest)
            raw = json.dumps(manifest).encode()
            result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
            (root / 'manifest.json').write_bytes(raw)
            (root / 'pages.json').write_text(json.dumps(rows))
            (root / 'result.json').write_text(json.dumps(result))
            audit = audit_run(root)
            self.assertEqual(audit['measurement_status'], 'FAIL')
            result['status'] = 'PASS'
            (root / 'result.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(RuntimeError, 'inconsistent'):
                audit_run(root)
            result['status'] = 'FAIL'; result['file_content']['file_content_physical_bytes'] = 0
            (root / 'result.json').write_text(json.dumps(result))
            with self.assertRaisesRegex(RuntimeError, 'counters differ'):
                audit_run(root)

    def test_file_copy_and_cow_are_distinct(self):
        rows = [dict(entry=PRESENT | FILE | pfn, kind='file_content', owner='file')
                for pfn in (100, 101, 200, 201)]
        self.assertEqual(summarize(rows)['file_content_physical_bytes'], 16384)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            (proc / 'pagemap').write_bytes(struct.pack('<QQQ', 0, PRESENT | FILE | 101, PRESENT | 202))
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['status'], 'FAIL')
            manifest['vms'][0]['files'][0]['allow_private_cow'] = True
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['file_content']['anonymous_cow_unique_pages'], 1)
            self.assertEqual(result['file_content']['file_content_physical_bytes'], 4096)

    def test_nonresident_and_swapped_are_not_hidden_or_mapping_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            (proc / 'pagemap').write_bytes(struct.pack('<QQQ', 0, 0, 1 << 62))
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['file_content']['file_content_physical_bytes'], 0)
            self.assertEqual(result['file_content']['swapped_mappings'], 1)
            self.assertEqual(result['file_content']['nonresident_mappings'], 1)

    def test_identity_and_file_ownership_fail_before_pagemap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, backing = self.fixture(root)
            original = copy.deepcopy(manifest)
            for key, value in [('start_time_ticks', 999), ('executable', '/wrong'),
                               ('cgroup', '/other'), ('api_socket', '/wrong')]:
                manifest = copy.deepcopy(original); manifest['vms'][0][key] = value
                result, _ = self.scan(root, manifest)
                self.assertEqual(result['first_failure']['location'], 'sandbox:ownership')
            manifest = copy.deepcopy(original); manifest['owned_root'] = str(root / 'other')
            result, _ = self.scan(root, manifest)
            self.assertIn('outside run-owned', result['first_failure']['error'])
            manifest = copy.deepcopy(original); manifest['vms'][0]['files'][0]['offsets'] = [8192]
            result, _ = self.scan(root, manifest)
            self.assertIn('out-of-file', result['first_failure']['error'])

    def test_page_migration_is_not_a_complete_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, _, _ = self.fixture(root)
            with patch('measure_file_physical.read_entry', side_effect=[PRESENT | FILE | 1,
                    PRESENT | FILE | 2, PRESENT | FILE | 3]):
                result, _ = self.scan(root, manifest)
            self.assertEqual(result['first_failure']['location'], 'sandbox:pagemap-recheck')
            self.assertIsNone(result['file_content']['file_content_unique_pages'])

    def test_layout_has_no_hardcoded_ram_cutoff(self):
        slots = [dict(guest=0, host=4096, size=8192),
                 dict(guest=0x200000000, host=12288, size=4096)]
        validate_slots(slots)
        self.assertEqual(ram_address(0x200000000 // 4096, slots), 12288)
        with self.assertRaisesRegex(RuntimeError, '0 RAM slots'):
            ram_address(10, slots)
        with self.assertRaisesRegex(RuntimeError, 'overlapping'):
            validate_slots([dict(guest=0, host=4096, size=8192), dict(guest=4096, host=12288, size=4096)])

    def test_unattributed_cache_not_presented_as_file_content(self):
        rows = [dict(entry=PRESENT | 11, kind='file_lru_unattributed', owner=None)]
        values = summarize(rows)
        self.assertIsNone(values['file_content_physical_bytes'])
        self.assertEqual(values['unattributed_unique_pages'], 1)
        rows.append(dict(entry=PRESENT | 11, kind='file_content', owner='/file'))
        values = summarize(rows)
        self.assertEqual(values['unattributed_unique_pages'], 0)

    def test_guest_marker_unmapped_pfn_and_unsupported_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            marker = root / 'marker.json'; pfns = root / 'file-pfns.bin'
            marker.write_text(json.dumps(dict(sandbox_id='sandbox', marker_pfn=1,
                                              marker_ascii='marker', file_pfn_count=1)))
            pfns.write_bytes(struct.pack('<I', 10))
            (proc / 'mem').write_bytes(bytes(4096) + b'marker')
            vm = manifest['vms'][0]; vm['files'] = []
            capture = root / 'ioctl.tsv'; capture.write_text('READY\n12\t0\t1000\t2000\t1000\t0\n')
            vm.update(ram_slots=[dict(guest=4096, host=4096, size=8192)],
                layout_source='successful-kvm-slot-registration',
                slot_capture=str(capture), slot_capture_start_ticks=1234, ram_slot_ids=[0],
                file_lru_probe=dict(page_size=4096, byteorder='little', marker=str(marker),
                                    pfns=str(pfns), scan_limit_bytes=5 * 1024**3))
            result, _ = self.scan(root, manifest)
            self.assertIn('0 RAM slots', result['first_failure']['error'])
            pfns.write_bytes(struct.pack('<I', 1))
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['status'], 'PARTIAL')
            self.assertIsNone(result['file_content']['file_content_physical_bytes'])
            vm['file_lru_probe']['scan_limit_bytes'] = 8192
            result, _ = self.scan(root, manifest)
            self.assertIn('does not cover', result['first_failure']['error'])
            vm['file_lru_probe']['scan_limit_bytes'] = 5 * 1024**3
            (proc / 'mem').write_bytes(bytes(4096) + b'wrong!')
            result, _ = self.scan(root, manifest)
            self.assertIn('marker does not match', result['first_failure']['error'])

    def test_failed_or_stale_kvm_evidence_cannot_verify_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = Path(tmp) / 'ioctl.tsv'
            capture.write_text('12\t0\t0\t1000\t2000\t-1\n')
            with self.assertRaisesRegex(RuntimeError, 'failed KVM'):
                captured_slots(capture, 12, [0])
            capture.write_text('99\t0\t0\t1000\t2000\t0\n')
            with self.assertRaisesRegex(RuntimeError, 'missing from'):
                captured_slots(capture, 12, [0])

    def test_unsupported_page_size_and_short_read_are_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); manifest, proc, _ = self.fixture(root)
            manifest['page_size'] = 65536
            result, _ = self.scan(root, manifest)
            self.assertEqual(result['first_failure']['location'], 'page-size')
            manifest['page_size'] = 4096
            (proc / 'pagemap').write_bytes(b'')
            result, _ = self.scan(root, manifest)
            self.assertIn('short pagemap', result['first_failure']['error'])

    @unittest.skipUnless(shutil.which('mkfs.ext4') and shutil.which('debugfs'), 'ext4 tools unavailable')
    def test_real_ext4_data_extents_exclude_image_capacity_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); tree = root / 'tree'; tree.mkdir()
            (tree / 'known.bin').write_bytes(b'x' * 5000)
            (tree / 'sparse.bin').write_bytes(bytes(8192))
            image = root / 'tiny.ext4'
            with image.open('wb') as stream: stream.truncate(16 * 1024**2)
            subprocess.run(['mkfs.ext4', '-q', '-F', '-b', '4096', '-d', str(tree), str(image)], check=True)
            offsets = ext4_data_offsets(image, '/known.bin')
            self.assertEqual(len(offsets), 2)
            self.assertEqual(len(set(offsets)), 2)
            self.assertTrue(all(offset < image.stat().st_size for offset in offsets))
            with self.assertRaisesRegex(RuntimeError, 'sparse/incomplete'):
                ext4_data_offsets(image, '/sparse.bin')


if __name__ == '__main__':
    unittest.main()
