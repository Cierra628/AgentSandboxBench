"""Saved Django r1 reuse only; mock data, no service or business calls."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_django_schedule_matrix import planned_runs,reuse_django_first_round,accepted_measurement


class DjangoFirstRound(unittest.TestCase):
    def fixture(self):
        meta=dict(profile='django16819',workload='fixed')
        proposed=dict(task=meta,runs=planned_runs('django16819'))
        saved=dict(metadata=meta,runs=[]);evidence=dict(policy='segments_v3',runs=[])
        for schedule in ('grpo','bpo','tvcache'):
            saved['runs'].append(dict(task='django16819',round=1,schedule=schedule,status='FAIL' if schedule=='grpo' else 'PASS',
                raw='/saved/'+schedule+'/external-workload',storage='/saved/'+schedule,experiment_parent='/dedicated/'+schedule))
            evidence['runs'].append(dict(task='django16819',round=1,schedule=schedule,source_storage='/saved/'+schedule,
                recomputed=dict(experiment_scope_validation='PASS',closure_policy='segments_v3',experiment_parent='/dedicated/'+schedule)))
        def read(path):
            if str(path)=='/saved/base.json':return saved
            if str(path)=='/saved/v3.json':return evidence
            return dict(metadata=meta,status='PASS',schedule=Path(path).parents[1].name,checks={'original':'PASS'})
        return proposed,saved,evidence,read

    def test_six_slots_only_and_original_failure_preserved(self):
        proposed,saved,evidence,read=self.fixture();before=deepcopy(saved)
        with patch('run_django_schedule_matrix.read',side_effect=read):
            completed,pending=reuse_django_first_round(proposed,Path('/saved/base.json'),Path('/saved/v3.json'))
        self.assertEqual([(r['round'],r['schedule'])for r in pending],[(2,'bpo'),(2,'tvcache'),(2,'grpo'),(3,'tvcache'),(3,'grpo'),(3,'bpo')])
        self.assertEqual(len(completed),3);self.assertEqual(saved,before)
        self.assertEqual(completed[0]['status'],'ACCEPTED_SEGMENTS_V3')
        self.assertEqual(completed[0]['original_status'],'FAIL')
        self.assertTrue(all(accepted_measurement(r)for r in completed))
        self.assertEqual(len(planned_runs('django16819')),9)

    def test_reject_wrong_rule_duplicate_missing_or_failed(self):
        for fault in ('version','duplicate','missing','failed','parent','metadata','order'):
            p,s,e,read=self.fixture()
            if fault=='version':e['policy']='segments_v2'
            elif fault=='duplicate':e['runs'].append(deepcopy(e['runs'][0]))
            elif fault=='missing':e['runs'].pop()
            elif fault=='failed':e['runs'][0]['recomputed']['experiment_scope_validation']='FAIL'
            elif fault=='parent':e['runs'][0]['recomputed']['experiment_parent']='/wrong'
            elif fault=='metadata':s['metadata']=dict(profile='wrong')
            elif fault=='order':s['runs'].reverse()
            with self.subTest(fault=fault),patch('run_django_schedule_matrix.read',side_effect=read),self.assertRaises(AssertionError):
                reuse_django_first_round(p,Path('/saved/base.json'),Path('/saved/v3.json'))

    def test_other_tasks_and_partial_matrices_still_rejected(self):
        for task,rounds in (('c4',3),('django16819',1)):
            p,_,_,_=self.fixture();p['task']['profile']=task;p['runs']=planned_runs(task,rounds)
            with self.subTest(task=task),self.assertRaises(AssertionError):reuse_django_first_round(p,Path('/missing'),Path('/missing'))

    def test_only_django_first_grpo_fail_can_be_accepted(self):
        row=dict(task='django16819',round=1,schedule='grpo',status='ACCEPTED_SEGMENTS_V3',original_status='FAIL',
            experiment_scope_validation='PASS',closure_policy='segments_v3')
        self.assertTrue(accepted_measurement(row))
        for key,value in (('round',2),('schedule','bpo'),('closure_policy','segments_v2'),('experiment_scope_validation','FAIL')):
            self.assertFalse(accepted_measurement(dict(row,**{key:value})))


if __name__=='__main__':unittest.main()
