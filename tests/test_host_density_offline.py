"""Tiny saved-table fixtures only; no service, page query or kernel operation."""
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import analyze_host_density_offline as a


class OfflineDensityTests(unittest.TestCase):
    def test_window_keeps_peak_and_changes_median_without_overwriting_input(self):
        data=[dict(monotonic_ns=t,current=c) for t,c in ((0,1),(1,3),(2,100),(3,90),(4,90))]
        all_values=a.window_metrics(data,0,4);early=a.window_metrics(data,0,2)
        self.assertEqual(all_values['current_peak_mib'],early['current_peak_mib'])
        self.assertEqual(all_values['current_median_mib'],90/a.MIB)
        self.assertEqual(early['current_median_mib'],3/a.MIB)
        self.assertEqual(len(data),5)
        with self.assertRaises(ValueError):a.window_metrics(data,5,6)

    def test_logging_boundaries_do_not_become_rpc_or_tool_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'guest';raw.mkdir();(raw/'memory').mkdir()
            events=[dict(stage='replay',start_monotonic_ns=1000,duration_ns=1000),dict(stage='monitor-finish',start_monotonic_ns=2500,duration_ns=10),dict(stage='monitor-collect-unit',start_monotonic_ns=900,duration_ns=1600)]
            (raw/'events.json').write_text(json.dumps(events));(raw/'result.json').write_text(json.dumps(dict(replay_end_monotonic_ns=2490)))
            session=dict(start_host_timestamp_ns=10000,start_host_monotonic_ns=0,raw_directory='memory',receipt=dict(cpu_user_seconds=.1,cpu_system_seconds=.2))
            (raw/'runtime-monitor.json').write_text(json.dumps(dict(sessions=[session])))
            (raw/'tools.tsv').write_text('start_monotonic_ns\tend_monotonic_ns\n100\t500\n')
            (raw/'memory/guest-memory.tsv').write_text('monotonic_ns\n90\n510\n610\n')
            out=raw/'010-replay.stdout';out.write_text('');os.utime(out,ns=(11500,11500))
            with patch.object(a,'ROOT',root):value=a.replay_record(raw,'guest-00')
            self.assertEqual(value['stdout_mtime_estimated_monotonic_ns'],1500)
            self.assertEqual(value['stdout_mtime_to_recorded_end_seconds'],500/1e9)
            self.assertEqual(value['recorded_end_to_state_end_seconds'],490/1e9)
            self.assertEqual(value['guest_tool_span_seconds'],400/1e9)
            self.assertEqual(value['guest_sampler_tail_points'],2)
            self.assertEqual(value['guest_sampler_tail_maximum_gap_seconds'],100/1e9)


if __name__=='__main__':unittest.main()
