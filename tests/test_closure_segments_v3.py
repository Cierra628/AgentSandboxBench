"""Offline only: fixed v3 boundaries and 13 saved runs; no services/devices."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_experiment_schedule import (periodic_closure,periodic_closure_v2,
    FILE_CLOSURE_THRESHOLD_BYTES,MAX_SEGMENT_SECONDS,NOMINAL_INTERVAL_SECONDS,closure_report)


class ClosureSegmentsV3Tests(unittest.TestCase):
    def series(self,bad_points,total=300,step_ns=100_000_000):
        return [dict(monotonic_ns=i*step_ns,experiment_memory=dict(file_closure=dict(
            status='MISMATCH' if i in bad_points else 'WITHIN_THRESHOLD',
            parent_minus_children_bytes=2*FILE_CLOSURE_THRESHOLD_BYTES if i in bad_points else 0))) for i in range(total)]

    def test_six_points_pass_v3_but_v2_failure_is_retained(self):
        row=periodic_closure(self.series(set(range(1,7))))
        self.assertEqual(row['policy'],'segments_v3')
        self.assertEqual(row['status'],'PASS')
        self.assertEqual(row['longest_segment_seconds'],.6)
        self.assertEqual(row['historical_segments_v2']['status'],'FAIL')
        self.assertEqual(row['duration_fallback_segments'],0)

    def test_exact_one_second_inclusive_and_one_nanosecond_over_fails(self):
        for extra,expected in [(0,'PASS'),(1,'FAIL')]:
            samples=self.series(set(range(1,11)))
            samples[11]['monotonic_ns']+=extra
            row=periodic_closure(samples)
            self.assertEqual(row['requirements']['longest_segment']['status'],expected)
            self.assertEqual(row['status'],expected)
        self.assertEqual(MAX_SEGMENT_SECONDS,1)
        self.assertEqual(NOMINAL_INTERVAL_SECONDS,.1)

    def test_real_timestamps_not_point_count_determine_duration(self):
        row=periodic_closure(self.series({1,2},step_ns=600_000_000))
        self.assertEqual(row['longest_segment_seconds'],1.2)
        self.assertEqual(row['status'],'FAIL')
        self.assertEqual(row['historical_segments_v2']['status'],'PASS')

    def test_missing_or_invalid_segment_timestamp_uses_explicit_nominal_conversion(self):
        for kind in ('missing','nonincreasing','nonfinite'):
            samples=self.series(set(range(1,11)))
            if kind=='missing':samples[5].pop('monotonic_ns')
            elif kind=='nonincreasing':samples[5]['monotonic_ns']=samples[4]['monotonic_ns']
            else:samples[5]['monotonic_ns']=float('nan')
            row=periodic_closure(samples)
            self.assertEqual(row['status'],'PASS')
            self.assertEqual(row['longest_segment_seconds'],1)
            self.assertEqual(row['duration_fallback_segments'],1)
            self.assertEqual(row['windows'][0]['duration_basis'],'nominal_interval')
            self.assertIsNotNone(row['windows'][0]['duration_fallback_reason'])
        samples=self.series(set(range(1,12)))
        samples[12].pop('monotonic_ns')
        self.assertEqual(periodic_closure(samples)['status'],'FAIL')

    def test_unrecovered_fraction_empty_and_incomplete_still_fail(self):
        unrecovered=periodic_closure(self.series({299}))
        self.assertEqual(unrecovered['status'],'FAIL')
        self.assertFalse(unrecovered['all_segments_recovered'])
        self.assertEqual(periodic_closure(self.series(set(range(0,30,2)),total=300))['status'],'PASS')
        fraction=periodic_closure(self.series(set(range(0,32,2)),total=300))
        self.assertEqual(fraction['requirements']['deviation_fraction']['status'],'FAIL')
        self.assertEqual(periodic_closure([])['status'],'FAIL')
        samples=self.series(set());samples[3]['experiment_memory']['file_closure']['status']='INCOMPLETE'
        self.assertEqual(periodic_closure(samples)['status'],'FAIL')

    def test_saved_thirteen_runs_and_report_have_real_seconds(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/closure-segments-v3-20261112.json').read_text())
        self.assertEqual(len(fixture['runs']),13)
        for r in fixture['runs']:
            samples=[dict(experiment_memory=dict(file_closure=dict(status=c['status'],parent_minus_children_bytes=c['delta_bytes'])))
                for c in r['closure_runs'] for _ in range(c['count'])]
            for index,timestamp in r['segment_timestamps_ns'].items():samples[int(index)]['monotonic_ns']=timestamp
            with self.subTest(task=r['task'],schedule=r['schedule'],round=r['round']):
                row=periodic_closure(samples);e=r['expected']
                self.assertEqual(row['total_points'],e['total_points'])
                self.assertEqual(row['deviation_count'],e['deviation_count'])
                self.assertEqual(row['status'],e['v3'])
                self.assertEqual(periodic_closure_v2(samples)['status'],e['v2'])
                self.assertEqual(row['longest_segment_seconds'],e['longest_segment_seconds'])
                self.assertEqual(row['duration_fallback_segments'],0)
                report='\n'.join(closure_report([dict(task=r['task'],schedule=r['schedule'],round=r['round'],periodic_file_closure=row)]))
                self.assertIn('1秒',report)
                self.assertIn(f"{row['longest_segment_seconds']:.6f}",report)
                self.assertIn('历史segments_v2判定',report)


if __name__=='__main__':unittest.main()
