"""Tiny saved JSON and interval arithmetic; no service or sandbox calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_grpo_light_pair import interval_union,summarize


class GrpoPairTests(unittest.TestCase):
    def test_parallel_control_intervals_are_not_summed_twice(self):
        self.assertEqual(interval_union([]),0)
        events=[dict(start_monotonic_ns=a,duration_ns=b) for a,b in ((30,5),(0,10),(5,10),(8,1))]
        self.assertEqual(interval_union(events),20)

    def fixture(self,root):
        runs=[]
        for mode,value in [('off',10),('on',11)]:
            runs.append(dict(mode=mode,paths={'role':['a']},patches={'role':'same'},official={'role':{'failed':2}},
                sessions=[] if mode=='off' else [dict(samples=3)],timings_ns=dict(tool_sum=value,monitor_control_call_sum=0 if mode=='off' else 2)))
        (root/'runs.json').write_text(json.dumps(runs));return runs

    def test_summary_keeps_signed_changes_and_undefined_zero_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root);v=summarize(root)
            self.assertEqual(v['actual_actions'],378);self.assertEqual(v['sample_count'],3)
            self.assertAlmostEqual(v['comparison']['tool_sum']['change_percent'],10)
            self.assertIsNone(v['comparison']['monitor_control_call_sum']['change_percent'])

    def test_changed_patch_and_wrong_execution_order_fail(self):
        for case in ('patch','order'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);runs=self.fixture(root)
                if case=='patch':runs[1]['patches']['role']='changed'
                else:runs.reverse()
                (root/'runs.json').write_text(json.dumps(runs))
                with self.assertRaises(AssertionError):summarize(root)

    def test_saved_on_off_order_still_compares_on_to_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runs=self.fixture(root);runs.reverse()
            (root/'runs.json').write_text(json.dumps(runs))
            (root/'plan.json').write_text(json.dumps(dict(order=['on','off'])))
            result=summarize(root)
            self.assertEqual(result['order'],['on','off'])
            self.assertEqual(result['comparison']['tool_sum']['off_ns'],10)
            self.assertEqual(result['comparison']['tool_sum']['on_ns'],11)
            self.assertAlmostEqual(result['comparison']['tool_sum']['change_percent'],10)

    def test_saved_order_mismatch_or_duplicate_mode_fails(self):
        for order in (['on','off'],['off','off']):
            with self.subTest(order=order),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.fixture(root)
                (root/'plan.json').write_text(json.dumps(dict(order=order)))
                with self.assertRaises(AssertionError):summarize(root)


if __name__=='__main__':unittest.main()
