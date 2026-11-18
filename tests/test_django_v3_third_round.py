"""Explicit Django r3 retry planning only; no task or service calls."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_django_schedule_matrix import planned_runs, reuse_django_first_two_rounds


class DjangoThirdRound(unittest.TestCase):
    def fixture(self):
        meta = dict(profile='django16819', workload='fixed')
        proposed = dict(task=meta, runs=planned_runs('django16819'))
        saved = dict(metadata=meta, experiment_memory_standard=True, status='FAIL', source_returned_to_stopped=True,
                     reuse_django_first_round='/saved/first.json', runs=[])
        tasks = {}
        for item in proposed['runs'][:7]:
            label = f"r{item['round']}-{item['schedule']}"
            row = dict(item, status='PASS', raw='/saved/'+label+'/raw', storage='/saved/'+label,
                       experiment_parent='/dedicated/'+label, experiment_scope_validation='PASS', closure_policy='segments_v3')
            if item['round'] == 1 and item['schedule'] == 'grpo':
                row.update(status='ACCEPTED_SEGMENTS_V3', original_status='FAIL')
            if item['round'] == 3: row['status'] = 'FAIL'
            tasks[row['raw']+'/result.json'] = dict(metadata=meta, schedule=item['schedule'], status='PASS', checks={'original':'PASS'},
                experiment_memory_context=dict(status='AVAILABLE', parent=row['experiment_parent']),
                cleanup_errors=['monitor-stop: exit 1'] if item['round'] == 3 else [])
            saved['runs'].append(row)
        def read(path): return saved if str(path) == '/saved/previous.json' else tasks[str(path)]
        return proposed, saved, tasks, read

    def run_reuse(self, proposed, saved, read):
        with patch('run_django_schedule_matrix.read', side_effect=read), patch('run_django_schedule_matrix.reuse_django_first_round', return_value=(saved['runs'][:3], [])):
            return reuse_django_first_two_rounds(proposed, Path('/saved/previous.json'), Path('/saved/v3.json'))

    def test_only_third_round_pending_preserves_six_and_old_fail(self):
        p, s, _, read = self.fixture(); original = deepcopy(s)
        done, pending, failures = self.run_reuse(p, s, read)
        self.assertEqual([(r['round'],r['schedule']) for r in pending], [(3,'tvcache'),(3,'grpo'),(3,'bpo')])
        self.assertEqual(len(done), 6); self.assertEqual(s, original)
        self.assertEqual(done[0]['status'], 'ACCEPTED_SEGMENTS_V3')
        self.assertEqual(done[3]['measurement_origin'], 'existing_second_round_pass')
        self.assertEqual(failures, [s['runs'][-1]])

    def test_reject_missing_failed_relabelled_or_wrong_scope(self):
        for defect in ('missing','second_fail','parent','wrong_failure','unfinished','other_task'):
            p,s,tasks,read = self.fixture()
            if defect == 'missing': s['runs'].pop(4)
            if defect == 'second_fail': s['runs'][4]['status'] = 'FAIL'
            if defect == 'parent': tasks[s['runs'][4]['raw']+'/result.json']['experiment_memory_context']['parent'] = '/wrong'
            if defect == 'wrong_failure': tasks[s['runs'][-1]['raw']+'/result.json']['cleanup_errors'] = ['other failure']
            if defect == 'unfinished': s['source_returned_to_stopped'] = False
            if defect == 'other_task': p['task']['profile'] = 'c4'
            with self.subTest(defect=defect), self.assertRaises(AssertionError): self.run_reuse(p,s,read)


if __name__ == '__main__': unittest.main()
