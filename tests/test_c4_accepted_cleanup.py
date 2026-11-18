"""Pure continuation dictionaries and temporary report output; no live operations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_django_schedule_matrix as m
from report_python_schedule_matrix import REPEAT_METRICS,collect


class AcceptedCleanupTests(unittest.TestCase):
    def fixture(self):
        runs=[dict(task='c4',round=n,schedule=s,status='NOT_RUN') for n,order in enumerate(m.ORDERS,1) for s in order]
        previous=dict(status='FAIL',source_returned_to_stopped=True,runs=[dict(runs[0],status='FAIL',storage='/saved',raw='/saved/external-workload')])
        accepted=dict(runs[0],status='ACCEPTED_CLEANUP_TIMEOUT',original_status='FAIL',raw='/saved/external-workload')
        return dict(task={'profile':'c4'},runs=runs),previous,accepted

    def test_explicit_reuse_skips_only_original_grpo_and_keeps_failure(self):
        proposed,previous,accepted=self.fixture();original=copy.deepcopy(previous)
        with patch.object(m,'collect',return_value=accepted) as query:
            done,pending,failed=m.resume_runs(proposed,previous,accept_c4_cleanup_timeout=True)
        self.assertEqual(done[0]['status'],'ACCEPTED_CLEANUP_TIMEOUT')
        self.assertEqual(len(pending),8);self.assertEqual((pending[0]['round'],pending[0]['schedule']),(1,'bpo'))
        self.assertEqual(failed[0]['status'],'FAIL');self.assertEqual(previous,original)
        query.assert_called_once_with(Path('/saved'),dict(task='c4',round=1,schedule='grpo'),accept_c4_cleanup_timeout=True)

    def test_no_approval_other_failure_or_wrong_task_rejected(self):
        proposed,previous,_=self.fixture()
        with self.assertRaises(AssertionError):m.resume_runs(proposed,previous)
        for kind in ('task','round','schedule','second_failure'):
            p=copy.deepcopy(proposed);old=copy.deepcopy(previous)
            if kind=='task':p['task']['profile']='django16819'
            if kind=='round':old['runs'][0]['round']=2
            if kind=='schedule':old['runs'][0]['schedule']='bpo'
            if kind=='second_failure':old['runs'].append(dict(p['runs'][1],status='FAIL'))
            with self.subTest(kind=kind),patch.object(m,'collect') as query:
                with self.assertRaises(AssertionError):m.resume_runs(p,old,accept_c4_cleanup_timeout=True)
                query.assert_not_called()

    def test_future_resource_resume_retains_exception_not_convert_to_pass(self):
        proposed,previous,accepted=self.fixture();previous['runs']=[accepted]
        done,pending,failed=m.resume_runs(proposed,previous)
        self.assertEqual(done[0]['status'],'ACCEPTED_CLEANUP_TIMEOUT');self.assertEqual(len(pending),8)
        self.assertEqual(failed,[])

    def test_report_keeps_original_status_and_excludes_only_controller_statistics(self):
        _,_,row=self.fixture()
        row.update({k:1 for k,_,_ in REPEAT_METRICS});row.update(storage='/saved',lower_physical_files=9,
            ordinary_strict_status='OBSERVED',ordinary_window={'observed_objects':163},endpoint_wall_seconds=.9,endpoint_cpu_seconds=.8)
        normal=dict(row,status='PASS',round=2,controller_seconds=2)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);m.publish(dict(status='RUNNING',runs=[row,normal],metadata={'profile':'c4'}),out)
            saved=json.loads((out/'summary.json').read_text());stats=saved['repeat_statistics'][0]['metrics']
            self.assertEqual(saved['runs'][0]['status'],'ACCEPTED_CLEANUP_TIMEOUT')
            self.assertEqual(saved['runs'][0]['controller_seconds'],1)
            self.assertEqual(stats['controller_seconds']['count'],1)
            self.assertEqual(stats['controller_seconds']['mean'],2)
            self.assertEqual(stats['tools_seconds']['count'],2)
            text=(out/'REPORT.md').read_text();self.assertIn('有效2／9',text);self.assertIn('原始FAIL',text)

    def test_ordinary_collector_still_rejects_failed_source(self):
        with patch('report_python_schedule_matrix.read',return_value={'status':'FAIL'}):
            with self.assertRaises(AssertionError):collect(Path('/saved'),dict(task='c4',round=1,schedule='grpo'))

    def test_completed_report_has_no_pending_run_and_labels_approved_history(self):
        proposed,previous,row=self.fixture()
        row.update({k:1 for k,_,_ in REPEAT_METRICS})
        row.update(lower_physical_files=9,ordinary_strict_status='OBSERVED',
            ordinary_window={'observed_objects':163},endpoint_wall_seconds=.9,endpoint_cpu_seconds=.8)
        runs=[dict(row,**{k:r[k] for k in ('task','round','schedule')},status='PASS') for r in proposed['runs']]
        runs[0]=row
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);m.publish(dict(status='PASS_WITH_CLEANUP_EXCEPTION',runs=runs,
                previous_failures=previous['runs'],metadata={'profile':'c4'}),out)
            text=(out/'REPORT.md').read_text()
            self.assertIn('有效9／9',text)
            self.assertIn('原九份记录已齐',text)
            self.assertIn('此前失败（原FAIL保留；获准测量计入',text)
            self.assertNotIn('只继续原九次清单的未完成项',text)


if __name__=='__main__':unittest.main()
