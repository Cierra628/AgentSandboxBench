import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_trenvx_persistent_comparison import orders, METRICS
from run_persistent_metrics_comparison import summarize


class PersistentComparisonTests(unittest.TestCase):
    def test_conditions_have_equal_positions_and_reproducible_order(self):
        for stage,count in [('smoke',1),('balanced',5)]:
            schedule=orders(stage,count,17)
            self.assertEqual(schedule,orders(stage,count,17))
            for order in schedule:
                half=len(order)//2
                self.assertEqual(order[half:],order[:half][::-1])
                self.assertEqual(set(order[:half]),{'off','host','guest','both'} if stage=='smoke'
                                 else {'control_A','control_B','off','host','guest','both'})
                self.assertTrue(all(order.count(label)==2 for label in set(order)))

    def test_summary_keeps_task_and_end_to_end_effects_separate(self):
        rows=[]
        for label,task,total in [('off',100,1000),('host',110,1050),('guest',120,1100),
                                ('both',130,1150),('control_A',100,1000),('control_B',100,1000)]:
            rows.extend([dict(label=label,task_window_ns=task,guest_action_sum_ns=100,
                              controller_end_to_end_ns=total)]*2)
        result=summarize([rows],METRICS)
        self.assertAlmostEqual(result['task_window_ns']['both']['geometric_mean_percent'],30)
        self.assertAlmostEqual(result['controller_end_to_end_ns']['both']['geometric_mean_percent'],15)
        self.assertAlmostEqual(result['guest_action_sum_ns']['both']['geometric_mean_percent'],0)

    def test_smoke_cannot_expand_to_five_groups(self):
        with self.assertRaises(ValueError):orders('smoke',5,17)


if __name__=='__main__':unittest.main()
