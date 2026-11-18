"""Offline: permission decisions and forked sparse-file controls; no services."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import host_file_monitor as m
from check_mincore_permissions import credentials, query_child, CAP_FOWNER, CAP_DAC_OVERRIDE


class MincorePermissionTests(unittest.TestCase):
    def evidence(self, *, caps=0, owner=65534, uid=0, fsuid=None, writable=False,
                 uid_map='0 0 4294967295'):
        fsuid = uid if fsuid is None else fsuid
        status = f'Uid:\t{uid} {uid} {uid} {fsuid}\nGid:\t0 0 0 0\nCapEff:\t{caps:x}\n'
        def read(path, *args, **kwargs):
            return status if str(path).endswith('/status') else uid_map
        with patch.object(Path, 'read_text', read), patch.object(m.os, 'access', return_value=writable) as access:
            result = m.mincore_permission(17, SimpleNamespace(st_uid=owner))
        return result, access

    def test_host_fowner_and_readonly_owner_remain_usable(self):
        result, access = self.evidence(caps=1 << CAP_FOWNER)
        self.assertEqual(result['basis'], 'HOST_CAP_FOWNER'); access.assert_not_called()
        result, access = self.evidence(uid=1000, owner=1000)
        self.assertEqual(result['basis'], 'HOST_INODE_OWNER'); access.assert_not_called()

    def test_nonowner_write_permission_checks_pinned_fd(self):
        result, access = self.evidence(writable=True)
        self.assertEqual(result['basis'], 'PINNED_FD_WRITABLE')
        access.assert_called_once_with('/proc/thread-self/fd/17', os.W_OK, effective_ids=True)

    def test_missing_permission_and_mismatched_fsuid_are_rejected(self):
        with self.assertRaisesRegex(PermissionError, 'kernel may return all ones'):
            self.evidence()
        with self.assertRaises(PermissionError):
            self.evidence(fsuid=1000, writable=True)

    def test_namespace_capability_or_overflow_uid_is_not_host_proof(self):
        with self.assertRaises(PermissionError):
            self.evidence(caps=1 << CAP_FOWNER, uid=65534, owner=65534, uid_map='0 100000 65536')
        result, _ = self.evidence(writable=True, uid_map='0 100000 65536')
        self.assertEqual(result['basis'], 'PINNED_FD_WRITABLE')

    def test_rejected_query_never_maps_and_preserves_callers_fd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'empty'
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.ftruncate(fd, 4097)
                with patch.object(m, 'mincore_permission', side_effect=PermissionError('permission denied')), \
                     patch.object(m.ctypes, 'CDLL') as libc:
                    with self.assertRaises(PermissionError):m.mincore_file(path, file_descriptor=fd)
                libc.assert_not_called()
                self.assertEqual(os.fstat(fd).st_size, 4097)
            finally:os.close(fd)

    def test_owned_unlinked_fd_and_empty_file_still_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'empty'
            fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                self.assertEqual(m.mincore_file(path)['resident_bytes'], 0)
                os.ftruncate(fd, 4097); path.unlink()
                row = m.mincore_file(path, file_descriptor=fd, include_metadata=True)
                self.assertEqual(row['pages'], 2); self.assertEqual(row['resident_bytes'], 0)
                self.assertIn('mincore_permission', row); self.assertTrue(row['stable'])
            finally:os.close(fd)

    def test_inventory_cannot_accept_denial_as_valid_zero_residency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/'sparse'; path.touch()
            with patch.object(m, 'counters', return_value=dict(file=0)), \
                 patch.object(m, 'mincore_permission', side_effect=PermissionError('permission denied')):
                row = m.residency_check(root, [path], root/'inventory.json')
            self.assertEqual(row['status'], 'INCOMPLETE')
            self.assertEqual(len(row['errors']), 1); self.assertEqual(row['files'], [])
            self.assertIn('PermissionError', row['errors'][0]['error'])
            self.assertEqual(json.loads((root/'inventory.json').read_text())['status'], 'INCOMPLETE')

    @unittest.skipUnless(os.geteuid() == 0, 'requires host-root capabilities for fork-only controls')
    def test_real_kernel_false_positive_is_rejected_without_changing_parent(self):
        before = credentials()
        if before['uid_map'] != ['0', '0', '4294967295'] or any(
                not before['cap_eff'] & (1 << bit) for bit in (0, CAP_FOWNER, CAP_DAC_OVERRIDE)):
            self.skipTest('full host UID map and CHOWN/FOWNER/DAC_OVERRIDE required')
        with tempfile.TemporaryDirectory() as tmp:
            for name, owner in [('foreign', 65534), ('owned', 0)]:
                path = Path(tmp)/name
                fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    os.ftruncate(fd, 4*2**20+123); os.fchown(fd, owner, 0); os.fchmod(fd, 0o444)
                finally:os.close(fd)
            cases = [('foreign', [], 0, 'OBSERVED'),
                     ('foreign', [CAP_FOWNER], 0, 'OBSERVED'),
                     ('foreign', [CAP_FOWNER, CAP_DAC_OVERRIDE], 4198400, 'REJECTED'),
                     ('owned', [CAP_FOWNER, CAP_DAC_OVERRIDE], 0, 'OBSERVED')]
            for name, drop, expected, status in cases:
                path = Path(tmp)/name; fd = os.open(path, os.O_RDONLY)
                try:q = query_child(path, fd, drop, name)
                finally:os.close(fd)
                self.assertEqual(q['query']['resident_bytes'], expected)
                self.assertEqual(q['guarded_query']['status'], status)
                if status == 'OBSERVED':self.assertEqual(q['guarded_query']['query']['resident_bytes'], 0)
                self.assertEqual(path.stat().st_blocks, 0)
        self.assertEqual(credentials(), before)


if __name__ == '__main__':unittest.main()
