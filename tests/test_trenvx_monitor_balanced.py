import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_trenvx_monitor_balanced as driver
from plan_trenvx_storage import GIB, budget, successful_image_cleanup_budget


class TrenvxBalancedTests(unittest.TestCase):
    def test_serial_cleanup_budget_does_not_require_repeated_archives(self):
        self.assertEqual(budget(47*GIB,10)['status'],'BLOCKED')
        plan=successful_image_cleanup_budget(47*GIB,40)
        self.assertEqual(plan['status'],'READY')
        self.assertEqual(plan['required_free_bytes'],45*GIB)
        self.assertEqual(successful_image_cleanup_budget(39*GIB,1)['status'],'BLOCKED')
        with self.assertRaises(ValueError): successful_image_cleanup_budget(47*GIB,0)

    def test_insufficient_disk_never_launches(self):
        with tempfile.TemporaryDirectory() as name, \
                patch.object(driver.shutil,'disk_usage',return_value=SimpleNamespace(free=39*GIB)), \
                patch.object(driver,'guarded_run') as launch:
            with self.assertRaisesRegex(RuntimeError,'insufficient space'):
                driver.execute(Path(name),'light',8)
            launch.assert_not_called()

    def test_live_run_error_is_not_treated_as_success(self):
        with tempfile.TemporaryDirectory() as name, \
                patch.object(driver.shutil,'disk_usage',return_value=SimpleNamespace(free=47*GIB)), \
                patch.object(driver,'guarded_run',side_effect=RuntimeError('injected live failure')), \
                patch.object(driver,'inspect_run') as verify:
            with self.assertRaisesRegex(RuntimeError,'injected live failure'):
                driver.execute(Path(name),'off',8)
            verify.assert_not_called()

    def test_offline_audit_rejects_stopped_or_failed_batch(self):
        with tempfile.TemporaryDirectory() as name:
            directory=Path(name)
            (directory/'result.json').write_text(json.dumps({'status':'STOPPED_UNSTABLE_CONTROL'}))
            with self.assertRaises(AssertionError): driver.audit(directory)


if __name__ == '__main__': unittest.main()
