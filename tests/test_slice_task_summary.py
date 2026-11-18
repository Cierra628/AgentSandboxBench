"""Offline report arithmetic and saved-object status handling; no live services."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import report_slice_task_summary as report
from report_slice_task_summary import describe, coverage, coverage_totals


class SliceTaskSummary(unittest.TestCase):
    def test_single_observation_has_no_repeat_statistics(self):
        self.assertEqual(describe([12]), dict(n=1, observed=12, mean=None, median=None,
            range=None, sample_stddev=None))

    def test_three_observations_retain_slow_run(self):
        value = describe([1, 2, 30])
        self.assertEqual((value['n'], value['mean'], value['median'], value['range']), (3, 11, 2, 29))
        self.assertAlmostEqual(value['sample_stddev'], (271)**.5)

    def test_partial_with_unknown_object_is_not_complete(self):
        def file(inode, phase, window):
            return dict(device=1, inode=inode, status='CHANGED_DURING_QUERY',
                change_phase=phase, query_window_status=window, aliases=[dict(path='/saved/'+str(inode))])
        metrics = dict(expanded_files=dict(status='PARTIAL', query_window_regular=dict(status='PARTIAL', unknown_objects=1, changed_before_query_objects=1),
            files=[file(1, 'BETWEEN_INVENTORY_AND_QUERY', 'OBSERVED'), file(2, 'DURING_QUERY', 'UNKNOWN')]),
            cache_layers=dict(block_devices=dict(method_totals=dict(cachestat=dict(status='PARTIAL', unknown_objects=1)),
                objects=[dict(object_key=['block', 2], aliases=[], queries=dict(cachestat=dict(status='UNKNOWN', error='changed')))]),
                outer_loop=dict(status='UNKNOWN', query_window=dict(status='OBSERVED'), residency=dict(metadata_changed=True))))
        value = coverage(metrics)
        self.assertEqual(value['ordinary_changed_before_query_objects'], 1)
        self.assertEqual(value['ordinary_changed_during_query_objects'], 1)
        self.assertEqual(value['ordinary_unknown_objects'], 1)
        self.assertEqual(value['block_status'], 'PARTIAL')
        self.assertEqual(value['block_unknown_objects'], 1)
        self.assertEqual(value['loop_status'], 'UNKNOWN')
        total = coverage_totals([dict(task='c4', coverage=value)])[1]
        self.assertEqual((total['block_unknown_runs'], total['loop_unknown_runs']), (1, 1))


    def test_rank_preserves_exact_ties(self):
        self.assertEqual(report.order(dict(grpo=2, bpo=2, tvcache=1)), [['bpo', 'grpo'], ['tvcache']])

    def test_mean_and_median_can_give_different_counterexamples(self):
        rows=[]
        # TVCache is shortest in round 1, longest in rounds 2/3;
        # BPO still leads the mean, but not the median.
        times=dict(grpo=[41,42,43], bpo=[49,50,51], tvcache=[36,52,53])
        for n in (1,2,3):
            for schedule in report.SCHEDULES:
                rows.append(dict(task='demo', round=n, schedule=schedule,
                    schedule_seconds=times[schedule][n-1],
                    pss_per_guest_mib=dict(grpo=3,bpo=1,tvcache=2)[schedule],
                    slice_current_peak_mib=dict(grpo=1,bpo=2,tvcache=3)[schedule],
                    slice_current_schedule_peak_mib=dict(grpo=1,bpo=2,tvcache=3)[schedule]))
        groups=[dict(task='demo',schedule=schedule,metrics={key:describe([r[key] for r in rows if r['schedule']==schedule])
                for key in report.COMPARE_METRICS}) for schedule in report.SCHEDULES]
        with patch.object(report,'TASKS',(('demo','示例'),)):
            findings=report.compare_schedules(rows,groups)
        mean,median=findings['task_rankings']
        self.assertEqual(mean['order']['schedule_seconds'],[['bpo'],['tvcache'],['grpo']])
        self.assertEqual(median['order']['schedule_seconds'],[['tvcache'],['bpo'],['grpo']])
        self.assertEqual([(r['round'],r['schedule']) for r in findings['schedule_round_exceptions']],[(1,'grpo'),(1,'bpo')])
        self.assertFalse(findings['pss_round_exceptions'])
        self.assertEqual(findings['current_mismatch_task_counts']['mean'],
                         dict(slice_current_peak_mib=1,slice_current_schedule_peak_mib=1))
        self.assertIn('TVCACHE > GRPO（PSS相反）',mean['inverted_pairs']['slice_current_peak_mib'])


if __name__ == '__main__':
    unittest.main()
