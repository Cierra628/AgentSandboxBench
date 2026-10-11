import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_exp1_monitor_balanced import schedule, summarize


class BalancedTests(unittest.TestCase):
    def rows(self, overhead=0, control=0):
        rows=[]
        for group, order in enumerate(schedule(20260929, 5), 1):
            for position, label in enumerate(order, 1):
                # A linear position trend cancels exactly in every mirrored pair.
                value=100+position*2
                if label=='light': value *= 1+overhead/100
                if label=='control_B': value *= 1+control/100
                rows.append(dict(group=group, position=position, label=label, task_window_ns=value))
        return rows

    def test_each_mode_balanced_in_position(self):
        for order in schedule(20260929, 5):
            self.assertEqual(order, order[::-1])
            for label in set(order):
                self.assertEqual(sum(i+1 for i,x in enumerate(order) if x==label), 9)

    def test_linear_drift_cancels(self):
        result=summarize(self.rows(overhead=2))
        self.assertAlmostEqual(result['monitor']['geometric_mean_percent'], 2)
        self.assertEqual(result['verdict'], 'WITHIN_5_PERCENT_FOR_THIS_PROTOCOL')

    def test_large_control_rejects_acceptance(self):
        self.assertEqual(summarize(self.rows(control=6))['verdict'], 'INCONCLUSIVE')

    def test_above_threshold(self):
        self.assertEqual(summarize(self.rows(overhead=7))['verdict'], 'ABOVE_5_PERCENT_FOR_THIS_PROTOCOL')

    def test_smoke_cannot_accept(self):
        self.assertEqual(summarize(self.rows()[:8])['verdict'], 'INCONCLUSIVE')


if __name__=='__main__':
    unittest.main()
