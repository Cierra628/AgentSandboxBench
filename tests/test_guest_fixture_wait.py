"""Fake time and marker only: never opens guest metadata, devices or services."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guest_cache_child_fixture as fixture

class FixtureWaitTests(unittest.TestCase):
    def test_cli_default_and_existing_options(self):
        args=fixture.parse_args(['/fake','--dynamic-file','--wait-for-begin'])
        self.assertEqual(args.finish_timeout_seconds,120)
        self.assertTrue(args.dynamic_file and args.wait_for_begin)
        args=fixture.parse_args(['/fake','--finish-timeout-seconds','180'])
        self.assertEqual(args.finish_timeout_seconds,180)

    def run_wait(self,limit,arrival):
        clock=[0.0]
        def sleep(_):clock[0]+=1
        with patch.object(fixture.time,'monotonic',side_effect=lambda:clock[0]),\
                patch.object(fixture.time,'sleep',side_effect=sleep),\
                patch.object(Path,'exists',side_effect=lambda:clock[0]>=arrival):
            fixture.wait_for_marker(Path('/fake'),'finish',limit)

    def test_observed_delay_fails_old_budget_but_fits_new_budget(self):
        with self.assertRaisesRegex(AssertionError,'finish deadline'):self.run_wait(120,152.558)
        self.run_wait(180,152.558)

    def test_new_budget_is_still_bounded(self):
        with self.assertRaisesRegex(AssertionError,'finish deadline'):self.run_wait(180,181)

if __name__=='__main__':unittest.main()
