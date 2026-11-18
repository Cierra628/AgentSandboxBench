"""Synthetic task-window and unit checks; no experiment replay."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_python_cache_baseline import task_samples,distribution


class BaselineReportTests(unittest.TestCase):
    def rows(self):
        return [dict(target_bytes=None,action=dict(requested_pages=0,write_ns=0),before=dict(start_ns=t,end_ns=t+1,cache_bytes=n*4096,metadata=dict(records=n,duplicate_adds=0,missing_removes=0,out_of_range=0,stale_records=0))) for t,n in [(0,1000),(10,2),(20,3),(30,2000)]]

    def test_preparation_and_evaluation_peaks_excluded(self):
        selected=task_samples(self.rows(),dict(end_ns=9),dict(start_ns=29))
        self.assertEqual([r['before']['cache_bytes'] for r in selected],[8192,12288])

    def test_any_reclaim_even_outside_selected_window_rejected(self):
        for key in ['requested_pages','write_ns']:
            rows=self.rows();rows[0]['action'][key]=1
            with self.assertRaises(AssertionError):task_samples(rows,dict(end_ns=9),dict(start_ns=29))

    def test_member_units_and_loss_rejected(self):
        for key in ['records','missing_removes']:
            rows=self.rows();rows[1]['before']['metadata'][key]+=1
            with self.assertRaises(AssertionError):task_samples(rows,dict(end_ns=9),dict(start_ns=29))

    def test_changed_identity_is_retained_as_unknown_not_missing_events(self):
        rows=self.rows();rows[1]['before']['metadata']['stale_records']=1
        self.assertEqual(len(task_samples(rows,dict(end_ns=9),dict(start_ns=29))),2)

    def test_peak_and_nearest_rank_p95_keep_bytes(self):
        value=distribution(list(range(1,21)))
        self.assertEqual(value['peak_bytes'],20);self.assertEqual(value['p95_bytes'],19);self.assertEqual(value['mean_bytes'],10.5)


if __name__=='__main__':unittest.main()
