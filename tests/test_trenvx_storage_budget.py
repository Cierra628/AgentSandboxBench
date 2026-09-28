import sys
from pathlib import Path
import unittest
import importlib.util
import json
import tempfile
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from plan_trenvx_storage import GIB, budget


class StorageBudgetTests(unittest.TestCase):
    def test_next_run_fits_but_batch_does_not(self):
        self.assertEqual(budget(50 * GIB, 1)['status'], 'READY')
        self.assertEqual(budget(50 * GIB, 4)['status'], 'BLOCKED')

    def test_ten_pairs_reserve_all_archives(self):
        plan = budget(50 * GIB, 20)
        self.assertEqual(plan['required_free_bytes'], 160 * GIB)
        self.assertEqual(plan['shortfall_bytes'], 110 * GIB)
        self.assertEqual(budget(160 * GIB, 20)['status'], 'READY')

    def test_invalid_count(self):
        with self.assertRaises(ValueError):
            budget(50 * GIB, 0)

    def test_driver_refuses_batch_before_starting_services(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/46-run-trenvx-exp1-monitor-pairs.py'
        spec = importlib.util.spec_from_file_location('pair_driver', path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(driver, 'ROOT', root), \
                 mock.patch.object(driver, 'REFERENCE', root), \
                 mock.patch.object(driver.os, 'umask'), \
                 mock.patch.object(driver.shutil, 'disk_usage', return_value=SimpleNamespace(free=50*GIB)), \
                 mock.patch.object(driver.subprocess, 'Popen') as launch, \
                 mock.patch('builtins.print'):
                self.assertEqual(driver.main(), 1)
                launch.assert_not_called()
            result = json.loads(next(root.glob('.artifacts/*/*/result.json')).read_text())
            self.assertEqual(result['status'], 'FAIL')
            self.assertEqual(result['runs'], [])

    def test_five_pair_driver_requires_whole_batch_space(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/46-run-trenvx-exp1-monitor-pairs.py'
        spec = importlib.util.spec_from_file_location('five_pair_driver', path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(driver, 'ROOT', root), \
                 mock.patch.object(driver, 'REFERENCE', root), \
                 mock.patch.object(driver.os, 'umask'), \
                 mock.patch.object(driver.shutil, 'disk_usage', return_value=SimpleNamespace(free=95*GIB)), \
                 mock.patch.object(driver.subprocess, 'Popen') as launch, \
                 mock.patch('builtins.print'):
                self.assertEqual(driver.main(5), 1)
                launch.assert_not_called()
            result = json.loads(next(root.glob('.artifacts/*/*/result.json')).read_text())
            self.assertEqual(result['pair_count_planned'], 5)
            self.assertEqual(result['runs'], [])
            plan = json.loads(next(root.glob('.artifacts/*/*/storage-plan-1-1.json')).read_text())
            self.assertEqual(plan['remaining_runs'], 10)
            self.assertEqual(plan['status'], 'BLOCKED')
