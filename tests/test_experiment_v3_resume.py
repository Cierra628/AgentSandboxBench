"""Offline continuation checks; no systemd, Docker or workload execution."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_django_schedule_matrix import reuse_v3_prefix,planned_runs,accepted_measurement


class ExperimentV3ResumeTests(unittest.TestCase):
    def data(self):
        metadata=dict(profile='matplotlib24177',workload='fixed')
        proposed=dict(task=metadata,runs=planned_runs('matplotlib24177'))
        checked=dict(experiment_scope_validation='PASS')
        record=dict(task='matplotlib24177',round=1,schedule='grpo',source_storage='/saved/storage',source_summary='/saved/old-summary.json',recomputed=checked)
        row=dict(task='matplotlib24177',round=1,schedule='grpo',status='ACCEPTED_SEGMENTS_V3',original_status='FAIL')
        evidence=dict(policy='segments_v3',runs=[record])
        return proposed,evidence,row

    def test_exact_eight_remaining_in_original_rotation_and_raw_failure_retained(self):
        proposed,evidence,row=self.data()
        with patch('run_django_schedule_matrix.read',side_effect=[evidence,dict(metadata=proposed['task'])]),patch('run_django_schedule_matrix.collect',return_value=row):
            completed,pending=reuse_v3_prefix(proposed,Path('/saved/v3-summary.json'))
        self.assertEqual(len(completed),1);self.assertEqual(len(pending),8)
        self.assertEqual([(r['round'],r['schedule']) for r in pending],[(1,'bpo'),(1,'tvcache'),(2,'bpo'),(2,'tvcache'),(2,'grpo'),(3,'tvcache'),(3,'grpo'),(3,'bpo')])
        self.assertEqual(completed[0]['original_status'],'FAIL')
        self.assertTrue(accepted_measurement(completed[0]))
        self.assertEqual(len(planned_runs('matplotlib24177')),9)

    def test_only_current_scope_pass_and_unique_candidate_can_be_reused(self):
        for fault in ('version','duplicate','failed','metadata'):
            proposed,evidence,row=self.data()
            if fault=='version':evidence['policy']='segments_v2'
            elif fault=='duplicate':evidence['runs']*=2
            elif fault=='failed':evidence['runs'][0]['recomputed']['experiment_scope_validation']='FAIL'
            old_meta=dict(metadata=proposed['task'] if fault!='metadata' else dict(profile='different'))
            with self.subTest(fault=fault),patch('run_django_schedule_matrix.read',side_effect=[evidence,old_meta]),patch('run_django_schedule_matrix.collect') as collect:
                with self.assertRaises(AssertionError):reuse_v3_prefix(proposed,Path('/saved/v3-summary.json'))
                collect.assert_not_called()

    def test_not_other_tasks_or_partial_matrices(self):
        for task,rounds in [('c4',3),('matplotlib24177',1)]:
            proposed,_,_=self.data();proposed['task']['profile']=task;proposed['runs']=planned_runs(task,rounds)
            with self.subTest(task=task,rounds=rounds),self.assertRaises(AssertionError):reuse_v3_prefix(proposed,Path('/missing'))

    def test_failure_without_explicit_revalidation_is_not_accepted(self):
        self.assertFalse(accepted_measurement(dict(task='matplotlib24177',status='FAIL')))
        self.assertFalse(accepted_measurement(dict(task='matplotlib24177',status='ACCEPTED_SEGMENTS_V3',original_status='FAIL',experiment_scope_validation='FAIL')))
        self.assertFalse(accepted_measurement(dict(task='c4',status='ACCEPTED_SEGMENTS_V3',original_status='FAIL',experiment_scope_validation='PASS')))


if __name__=='__main__':unittest.main()
