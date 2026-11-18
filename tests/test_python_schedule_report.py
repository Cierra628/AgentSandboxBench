"""Offline report arithmetic and temporary saved samples; no live queries."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_python_schedule_matrix import REPEAT_METRICS, repeat_statistics, report_fields, schedule_current, report


class RepeatReportTests(unittest.TestCase):
    def row(self, task, schedule, value, status='PASS'):
        return dict(task=task, schedule=schedule, status=status, **{key:value for key, _, _ in REPEAT_METRICS})

    def test_median_mean_range_and_sample_standard_deviation(self):
        rows=[self.row('sphinx7590','grpo',v) for v in (1,3,5)]
        rows += [dict(task='sphinx7590',schedule='grpo',status='FAIL')]
        result=repeat_statistics(rows)
        self.assertEqual(len(result),1)
        for metric in result[0]['metrics'].values():
            self.assertEqual((metric['count'],metric['median'],metric['mean'],metric['minimum'],metric['maximum'],metric['sample_stddev']),
                             (3,3,3,1,5,2))

    def test_tasks_schedules_and_single_observation_separate(self):
        result=repeat_statistics([self.row('sphinx7590','grpo',1),self.row('sphinx7590','bpo',10),self.row('xarray6992','grpo',100)])
        self.assertEqual(len(result),3)
        self.assertEqual([r['metrics']['current_peak_mib']['mean'] for r in result],[1,10,100])
        self.assertTrue(all(r['metrics']['current_peak_mib']['sample_stddev'] is None for r in result))
        self.assertEqual(repeat_statistics([dict(task='xarray6992',schedule='grpo',status='FAIL')]),[])

    def test_schedule_peak_includes_creation_and_both_boundaries_not_final_tests(self):
        samples = [dict(monotonic_ns=str(t), current=str(m * 2**20))
                   for t,m in ((9,100),(10,7),(15,5),(20,6),(21,200))]
        task = dict(schedule_start_monotonic_ns=10, first_tool_start_monotonic_ns=15,
                    original_paths_end_monotonic_ns=20)
        result = schedule_current(samples,task)
        self.assertEqual(result['current_schedule_peak_mib'],7)
        self.assertEqual(result['current_schedule_window'],dict(start_monotonic_ns=10,
            end_monotonic_ns=20,sample_count=3,peak_monotonic_ns=10))
        self.assertIsNone(schedule_current(samples,{})['current_schedule_peak_mib'])
        self.assertIsNone(schedule_current([],task)['current_schedule_peak_mib'])

    def test_older_saved_row_is_enriched_without_changing_old_peak_or_strict_status(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory); (raw/'host-file').mkdir()
            (raw/'result.json').write_text(json.dumps(dict(schedule_start_monotonic_ns=1,
                original_paths_end_monotonic_ns=2)))
            (raw/'host-file/memory.tsv').write_text('monotonic_ns\tcurrent\n1\t1048576\n3\t2097152\n')
            row = dict(raw=directory, current_peak_mib=2, ordinary_strict_status='PARTIAL',
                ordinary_window=dict(observed_subtotal_bytes=3*2**20,observed_objects=2))
            result = report_fields(row)
            self.assertEqual(result['current_peak_mib'],2)
            self.assertEqual(result['current_schedule_peak_mib'],1)
            self.assertEqual(result['ordinary_window_mib'],3)
            self.assertEqual(result['ordinary_strict_status'],'PARTIAL')
            self.assertNotIn('current_schedule_peak_mib',row)

    def test_absent_new_metrics_remain_unknown_not_zero(self):
        row = self.row('xarray6992','grpo',1)
        row.pop('current_schedule_peak_mib'); row['ordinary_window_mib']=None
        result = repeat_statistics([row])[0]['metrics']
        for key in ('current_schedule_peak_mib','ordinary_window_mib'):
            self.assertEqual(result[key]['count'],0)
            self.assertIsNone(result[key]['mean'])

    def test_report_displays_separate_windows_objects_and_original_status(self):
        row = dict(self.row('sphinx7590','grpo',1),round=1,raw='saved/raw',
            actual_actions=230,logical_actions=230,ordinary_strict_status='PARTIAL',
            ordinary_window=dict(observed_subtotal_bytes=3*2**20,observed_objects=2))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'report'
            report(dict(status='PASS',runs=[row],sphinx_find_exit_diagnostic=True),output)
            content = (output/'REPORT.md').read_text()
            for text in ('current全采集窗口峰值','current调度窗口峰值','已枚举普通文件窗口合计',
                         '| 3.000 | 2 | PARTIAL |','不能再与P1相加'):
                self.assertIn(text,content)
            self.assertNotIn('本批新增Sphinx',content)
            saved = json.loads((output/'summary.json').read_text())['runs'][0]
            self.assertEqual(saved['current_peak_mib'],1)
            self.assertEqual(saved['ordinary_window_mib'],3)


if __name__ == '__main__':unittest.main()
