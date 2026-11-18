"""Pure synthetic memory rows; no live proc files, services or workload execution."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from assess_grpo_boundary_meminfo import FIELDS,phase_check,role_summary,assess


def row(phase,step,free,timestamp):
    data={k+'_kib':'0' for k in FIELDS}
    data.update(MemTotal_kib='1000',MemFree_kib=str(free),Cached_kib='50',
        timestamp_ns=str(timestamp),phase=phase,step=step,sandbox='role',branch='role',round='segment')
    return data


class BoundaryMeminfoTests(unittest.TestCase):
    def memory(self):return [row('baseline','000',100,10),row('before','001',200,20),row('after','001',150,30),row('complete','999',180,40)]

    def test_phase_order_and_fields_are_preserved(self):
        phase_check(self.memory(),[{'step':'001'}],'role','segment')

    def test_missing_misordered_or_wrong_role_rows_are_rejected(self):
        data=self.memory()
        for changed in (data[:-1],[data[1],data[0],*data[2:]], [dict(data[0],sandbox='other'),*data[1:]]):
            with self.subTest(changed=changed),self.assertRaises(AssertionError):phase_check(changed,[{'step':'001'}],'role','segment')

    def test_removing_edges_changes_boundary_peak_and_outside_periodic_points_are_excluded(self):
        periodic=[row('running','001',0,1),row('running','001',250,25),row('running','001',0,50)]
        result=role_summary(self.memory(),periodic)
        self.assertEqual(result['boundary_nonfree_peak_bytes'],900*1024)
        self.assertEqual(result['ablation_peak_loss_bytes']['without_segment_edges'],50*1024)
        self.assertEqual(result['periodic_rows_in_boundary_span'],1)
        self.assertEqual(result['boundary_minus_periodic_nonfree_peak_bytes'],150*1024)

    def test_common_counter_is_not_memavailable_and_negative_peak_difference_is_retained(self):
        periodic=[dict(row('running','001',0,25),MemAvailable_kib='999')]
        result=role_summary(self.memory(),periodic)
        self.assertEqual(result['boundary_minus_periodic_nonfree_peak_bytes'],-100*1024)
        self.assertEqual(result['periodic_nonfree_peak_in_boundary_span_bytes'],1000*1024)

    def test_each_removed_phase_is_compared_with_all_remaining_rows(self):
        for phase in ('baseline','before','after','complete'):
            data=self.memory()
            for r in data:r['MemFree_kib']='100' if r['phase']==phase else '200'
            result=role_summary(data,[row('running','001',250,25)])
            self.assertEqual(result['ablation_peak_loss_bytes']['without_'+phase],100*1024)

    def test_clock_reversal_or_wrong_batch_does_not_get_silently_used(self):
        with self.assertRaises(AssertionError):role_summary(self.memory(),[row('running','001',250,30),row('running','001',200,20)])
        with self.assertRaises(AssertionError):role_summary(list(reversed(self.memory())),[row('running','001',250,25)])
        with self.assertRaises(AssertionError):assess(dict(status='PASS',stage='complete',comparison='sampling'))


if __name__=='__main__':unittest.main()
