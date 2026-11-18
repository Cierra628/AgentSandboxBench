"""Saved timing arithmetic only; no workloads or live services."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_c4_call_timing import call_rows


class C4CallTimingReportTests(unittest.TestCase):
    def event(self):
        return dict(stage='replay',call_timing_version=2,exit_code=0,command_returned=True,stdout_log_completed=True,events_log_completed=True,
            start_monotonic_ns=100,command_start_monotonic_ns=101,command_end_monotonic_ns=111,command_duration_ns=10,
            stdout_log_start_monotonic_ns=112,stdout_log_end_monotonic_ns=132,stdout_log_duration_ns=20,
            event_lock_wait_ns=5,events_log_duration_ns=30,duration_ns=37,call_end_monotonic_ns=167,call_elapsed_ns=67)

    def test_phase_costs_and_residual_are_kept_separate(self):
        result=call_rows([self.event()])[0]
        self.assertEqual(result['command_duration_ns'],10);self.assertEqual(result['other_ns'],2)
        self.assertEqual(result['legacy_duration_ns'],37);self.assertEqual(result['call_elapsed_ns'],67)

    def test_missing_old_schema_failed_write_and_inconsistent_clock_are_rejected(self):
        for change in (dict(call_timing_version=1),dict(events_log_completed=False),dict(command_end_monotonic_ns=110),dict(call_elapsed_ns=64)):
            with self.subTest(change=change),self.assertRaises((ValueError,AssertionError)):
                call_rows([dict(self.event(),**change)])


if __name__=='__main__':unittest.main()
