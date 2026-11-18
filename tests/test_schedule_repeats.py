"""Pure offline plan and descriptive-statistics checks; no sandbox or live query."""
from pathlib import Path
import statistics
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_schedule_repeats import run_plan
from report_schedule_repeats import stats


class ScheduleRepeatTests(unittest.TestCase):
    def test_three_rotations_nine_unique_entries(self):
        plan = run_plan()
        self.assertEqual(len(plan), 9)
        self.assertEqual(len({(r['round'], r['schedule']) for r in plan}), 9)
        self.assertEqual([[r['schedule'] for r in plan if r['round']==n] for n in (1, 2, 3)],
                         [['grpo', 'bpo', 'tvcache'], ['bpo', 'tvcache', 'grpo'], ['tvcache', 'grpo', 'bpo']])
        for schedule in ('grpo', 'bpo', 'tvcache'):
            self.assertEqual(sum(r['schedule']==schedule for r in plan), 3)

    def test_slow_value_retained_and_sample_deviation(self):
        value = stats([2., 3., 100.])
        self.assertEqual(value['count'], 3)
        self.assertEqual(value['median'], 3.)
        self.assertEqual(value['minimum'], 2.)
        self.assertEqual(value['maximum'], 100.)
        self.assertEqual(value['mean'], 35.)
        self.assertEqual(value['stdev'], statistics.stdev([2., 3., 100.]))


if __name__ == '__main__':
    unittest.main()
