"""Pure dictionaries only: no services, devices, or saved workload access."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_python_schedule_matrix import continuation
import run_python_schedule_matrix as matrix


class ResumeTests(unittest.TestCase):
    def fixture(self):
        slots=[dict(task='sphinx7590',round=1,schedule=s) for s in ('grpo','bpo','tvcache')]
        previous=dict(status='FAIL',source_returned_to_stopped=True,runs=[dict(slots[0],status='PASS'),dict(slots[1],status='FAIL')])
        return dict(runs=slots),previous

    def test_completed_slot_is_reused_and_failed_slot_is_first_pending(self):
        planned,previous=self.fixture();original=copy.deepcopy(previous)
        completed,pending,failed=continuation(planned,previous,retry_failed=True)
        self.assertEqual([r['schedule'] for r in completed],['grpo'])
        self.assertEqual([r['schedule'] for r in pending],['bpo','tvcache'])
        self.assertEqual([r['schedule'] for r in failed],['bpo'])
        self.assertEqual(previous,original)

    def test_live_source_duplicate_slots_and_unapproved_retry_rejected(self):
        planned,previous=self.fixture()
        with self.assertRaises(AssertionError):continuation(planned,previous)
        previous['source_returned_to_stopped']=False
        with self.assertRaises(AssertionError):continuation(planned,previous,retry_failed=True)
        previous['source_returned_to_stopped']=True;previous['runs'].append(previous['runs'][0])
        with self.assertRaises(AssertionError):continuation(planned,previous,retry_failed=True)

    def test_older_failed_attempts_stay_in_history(self):
        planned,previous=self.fixture();previous['previous_failures']=[dict(task='sphinx7590',round=1,schedule='bpo',raw='/old/failure')]
        _,_,failed=continuation(planned,previous,retry_failed=True)
        self.assertEqual(len(failed),2);self.assertEqual(failed[0]['raw'],'/old/failure')

    def test_ready_space_is_used_without_delay(self):
        with patch.object(matrix,'available',return_value=100*matrix.GIB),patch.object(matrix.time,'sleep') as sleep:
            budget,checks=matrix.capacity_after_cleanup('/fake',64*matrix.GIB)
        self.assertTrue(budget['ready']);self.assertEqual(len(checks),1);sleep.assert_not_called()

    def test_deleted_successful_volume_can_settle_without_lowering_budget(self):
        previous=dict(status='PASS',storage='/fake/storage')
        with patch.object(matrix.Path,'exists',return_value=False), \
             patch.object(matrix,'available',side_effect=[33*matrix.GIB,97*matrix.GIB]),patch.object(matrix.time,'sleep') as sleep:
            budget,checks=matrix.capacity_after_cleanup('/fake',64*matrix.GIB,previous)
        self.assertTrue(budget['ready']);self.assertEqual(len(checks),2);sleep.assert_called_once_with(1)
        self.assertFalse(checks[0]['budget']['ready'])
        self.assertEqual(budget['required_available_bytes'],90*matrix.GIB)

    def test_settle_is_bounded_and_not_used_for_a_failed_or_existing_volume(self):
        with patch.object(matrix.Path,'exists',return_value=False),patch.object(matrix,'available',return_value=33*matrix.GIB), \
             patch.object(matrix.time,'sleep') as sleep:
            budget,checks=matrix.capacity_after_cleanup('/fake',64*matrix.GIB,dict(status='PASS',storage='/fake'))
        self.assertFalse(budget['ready']);self.assertEqual(len(checks),6);self.assertEqual(sleep.call_count,5)
        for previous,exists in [(dict(status='FAIL',storage='/fake'),False),(dict(status='PASS',storage='/fake'),True)]:
            with patch.object(matrix.Path,'exists',return_value=exists),patch.object(matrix,'available',return_value=33*matrix.GIB), \
                 patch.object(matrix.time,'sleep') as sleep:
                budget,checks=matrix.capacity_after_cleanup('/fake',64*matrix.GIB,previous)
            self.assertFalse(budget['ready']);self.assertEqual(len(checks),1);sleep.assert_not_called()


if __name__=='__main__':unittest.main()
