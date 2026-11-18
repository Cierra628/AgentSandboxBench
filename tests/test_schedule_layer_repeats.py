"""Pure argument/plan checks; no service, device, or cache query."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from run_schedule_layer_repeats import command
from run_schedule_repeats import run_plan
from report_schedule_layer_repeats import describe, cell


class LayerRepeatTests(unittest.TestCase):
    def test_each_run_explicitly_selects_one_schedule_and_all_layers(self):
        for item in run_plan():
            path = Path('/unused')/f"r{item['round']}-{item['schedule']}"
            args = command(path, item['schedule'])
            self.assertEqual(args[args.index('--output')+1], str(path))
            self.assertEqual(args[args.index('--schedule')+1], item['schedule'])
            self.assertEqual(args[args.index('--block-cache-method')+1], 'cachestat')
            self.assertIn('--expanded-files', args)
            self.assertIn('--cache-layers', args)
            self.assertIn('--unused-network-pools', args)

    def test_nine_new_unique_runs_without_changing_old_order(self):
        plan = run_plan()
        self.assertEqual(len(plan), 9)
        self.assertEqual(len({(r['round'], r['schedule']) for r in plan}), 9)
        self.assertEqual([r['schedule'] for r in plan],
            ['grpo', 'bpo', 'tvcache', 'bpo', 'tvcache', 'grpo', 'tvcache', 'grpo', 'bpo'])

    def test_missing_is_not_zero_and_slow_observation_is_kept(self):
        value = describe([0, None, 100])
        self.assertEqual(value['count'], 2)
        self.assertEqual(value['missing'], 1)
        self.assertEqual(value['mean'], 50)
        self.assertEqual(value['minimum'], 0)
        self.assertEqual(value['maximum'], 100)
        self.assertIn('有效2/3', cell(value))
        self.assertEqual(cell(describe([None, None, None])), '未知（0/3）')
        self.assertEqual(describe([1, 2, 100])['median'], 2)
        self.assertIn('0.000100', cell(describe([.0001, .0001, .0001]), 6))


if __name__ == '__main__':
    unittest.main()
