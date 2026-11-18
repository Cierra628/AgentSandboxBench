"""Guest cache control unit tests; no host cache, service, or devices touched."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import guest_page_cache_control as guest
import validate_isolated_agentenv as validator
from types import SimpleNamespace


class GuestCacheControlTests(unittest.TestCase):
    def test_only_drop_clean_writes_one_and_both_conditions_sync(self):
        for policy in ['preserve', 'drop-clean', 'observe']:
            snapshots = [dict(boot_id='guest', guest_cache_bytes=x) for x in [100, 100, 20]]
            with self.subTest(policy=policy), patch.object(guest, 'snapshot', side_effect=copy.deepcopy(snapshots)) as read, \
                 patch.object(guest.os, 'sync') as sync, patch.object(Path, 'write_text', autospec=True) as write:
                result = guest.control(policy)
                self.assertEqual(result['status'], 'PASS')
                self.assertEqual(read.call_count, 1 if policy == 'observe' else 3)
                self.assertEqual(sync.call_count, int(policy != 'observe'))
                if policy == 'drop-clean':
                    write.assert_called_once_with(Path('/proc/sys/vm/drop_caches'), '1\n')
                else:
                    write.assert_not_called()

    def test_missing_diagnostic_metadata_fails_before_sync_or_write(self):
        with patch.object(guest, 'snapshot', side_effect=FileNotFoundError), \
             patch.object(guest.os, 'sync') as sync, patch.object(Path, 'write_text') as write:
            with self.assertRaises(FileNotFoundError):
                guest.control('drop-clean')
            sync.assert_not_called(); write.assert_not_called()

    def test_policy_rejected_outside_sampled_pair_before_allocating(self):
        for pair, sampling in [(False, None), (True, 'off')]:
            with self.subTest(pair=pair,sampling=sampling),patch.object(validator.os, 'geteuid', return_value=0), \
                 patch.object(validator.os, 'posix_fallocate') as allocate:
                with self.assertRaisesRegex(ValueError, 'guest cache policy requires'):
                    validator.main(SimpleNamespace(prettier6604_guest_cache_policy='drop-clean',
                        prettier6604_snapshot_cache_pair=pair,prettier6604_snapshot_cache_sampling=sampling))
                allocate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
