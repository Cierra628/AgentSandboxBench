"""Offline schedule and stop checks; never start Docker services or sandboxes."""
from collections import Counter
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_python_cache_repeats as repeats


class RepeatTests(unittest.TestCase):
    def test_source_override_is_explicit_and_old_default_is_preserved(self):
        tree = ast.parse((repeats.ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        index = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'SERVICE' for t in n.targets))
        code = compile(ast.Module(body=tree.body[index:index+2], type_ignores=[]), '<source selection>', 'exec')
        for setting, expected in [(None, 'agentenv-smoke-20260922T114006Z-1387374'),
                                  ('asb-source-rebuilt-test', 'asb-source-rebuilt-test')]:
            with patch.dict(os.environ, {}, clear=True):
                if setting: os.environ['ASB_AGENTENV_SOURCE_SERVICE'] = setting
                namespace = dict(os=os, re=re); exec(code, namespace)
                self.assertEqual(namespace['SERVICE'], expected)
        with patch.dict(os.environ, dict(ASB_AGENTENV_SOURCE_SERVICE='bad name')):
            with self.assertRaises(ValueError): exec(code, dict(os=os, re=re))

    def test_fixed_count_position_and_order_balance(self):
        plan = repeats.schedule(); self.assertEqual(plan, repeats.schedule())
        self.assertEqual(len(plan['runs']), 36)
        self.assertEqual(len({r['key'] for r in plan['runs']}), 36)
        for task in repeats.TASKS:
            rows = [r for r in plan['runs'] if r['task'] == task]
            self.assertEqual(Counter((r['condition'], r['position']) for r in rows),
                             Counter({(c, p): 2 for c in 'BLH' for p in [1, 2, 3]}))
            orders = [''.join(r['condition'] for r in rows if r['round'] == n) for n in range(1, 7)]
            self.assertEqual(len(set(orders)), 6)
        self.assertEqual([plan['runs'][(n-1)*6]['task'] for n in range(1, 7)], repeats.TASKS*3)

    def test_existing_configs_have_exact_targets(self):
        for item in repeats.schedule()['runs']:
            config = repeats.read(repeats.ROOT/item['config'])
            if item['condition'] == 'B': self.assertTrue(config['guest_cache_baseline'])
            else: self.assertEqual(str(config['guest_cache_target_mib']), item['target'])

    def test_missing_source_stops_before_any_task(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name); repeats.write(out/'plan.json', repeats.schedule())
            with patch.object(repeats, 'source_ready', return_value=dict(status='BLOCKED', reason='missing')), \
                 patch.object(repeats.subprocess, 'run') as launch:
                self.assertEqual(repeats.execute(out), 2); launch.assert_not_called()
            result = repeats.read(out/'result.json')
            self.assertEqual(result['status'], 'BLOCKED'); self.assertEqual(result['completed_runs'], 0)

    def test_pause_request_stops_before_the_next_new_run(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name); repeats.write(out/'plan.json', repeats.schedule())
            (out/'pause-after-current').touch()
            with patch.object(repeats, 'source_ready', return_value=dict(status='READY')), \
                 patch.object(repeats.subprocess, 'run') as launch:
                self.assertEqual(repeats.execute(out),0); launch.assert_not_called()
            result=repeats.read(out/'result.json')
            self.assertEqual(result['status'],'PAUSED'); self.assertEqual(result['next_run'],repeats.schedule()['runs'][0]['key'])

    def test_failed_entry_is_not_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name); repeats.write(out/'plan.json', repeats.schedule())
            with patch.object(repeats, 'source_ready', return_value=dict(status='READY')), \
                 patch.object(repeats.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as launch:
                self.assertEqual(repeats.execute(out), 1)
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(repeats.execute(out), 1)
                self.assertEqual(launch.call_count, 1)

    def test_successful_schedule_and_resume_do_not_add_runs(self):
        with tempfile.TemporaryDirectory() as name:
            out = Path(name); repeats.write(out/'plan.json', repeats.schedule())
            with patch.object(repeats, 'source_ready', return_value=dict(status='READY')), \
                 patch.object(repeats.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as launch, \
                 patch.object(repeats, 'binding_from_log', side_effect=lambda log, item: dict(platform=item['key'])), \
                 patch.object(repeats, 'round_reports'):
                self.assertEqual(repeats.execute(out), 0)
                self.assertEqual(launch.call_count, 36)
                self.assertEqual(repeats.execute(out), 0)
                self.assertEqual(launch.call_count, 36)
                self.assertEqual(len(repeats.read(out/'runs.json')), 36)

    def test_log_binding_ignores_report_path_and_rejects_wrong_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); run = root/'run'; storage = root/'storage'
            report = root/'report-run'; raw = storage/'python-backbone'
            run.mkdir(); raw.mkdir(parents=True); (report/'live-cache').mkdir(parents=True)
            repeats.write(run/'result.json', dict(status='PASS', storage_result_dir=str(storage), report_dir=str(report)))
            repeats.write(storage/'result.json', dict(status='PASS', cleanup=dict(owned_container_removed=True)))
            child = dict(status='PASS', replay=dict(action_count=35), cache_target_mib=98,
                         checks=dict(cache_target_and_damon_restored='PASS'))
            repeats.write(raw/'result.json', child)
            repeats.write(report/'live-cache/summary.json', dict(status='PASS'))
            log = root/'run.log'; log.write_text(f'status=PASS result_dir={report}\nstatus=PASS result_dir={run}\n')
            binding = repeats.binding_from_log(log, dict(target='98'))
            self.assertEqual(binding['platform'], str(run))
            with self.assertRaises(AssertionError): repeats.binding_from_log(log, dict(target='65'))

    def test_reports_pair_targets_only_with_the_same_round_baselines(self):
        runs = {item['key']: dict(platform=item['key']) for item in repeats.schedule()['runs']}
        with tempfile.TemporaryDirectory() as name:
            out = Path(name)
            with patch('report_python_cache_baseline.report') as baseline, \
                 patch('report_python_cache_targets.report') as targets:
                repeats.round_reports(out, runs)
                self.assertEqual(baseline.call_count, 6); self.assertEqual(targets.call_count, 6)
            for number in range(1, 7):
                directory = out/f'round-{number:02d}'
                for task, binding in repeats.read(directory/'baseline-runs.json').items():
                    self.assertEqual(binding['platform'], f'r{number:02d}-{task}-baseline')
                for key, binding in repeats.read(directory/'target-runs.json').items():
                    self.assertEqual(binding['platform'], f'r{number:02d}-{key}')


if __name__ == '__main__':
    unittest.main()
