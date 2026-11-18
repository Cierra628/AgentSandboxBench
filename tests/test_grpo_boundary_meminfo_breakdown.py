"""Synthetic timing dictionaries only; no service, workload, network or devices."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_grpo_boundary_meminfo import COMPONENTS,critical_difference,analyze,publish


def path():
    return dict(role='late_1',exec_assignment='UNIQUE',**dict(zip(COMPONENTS,(10,5,2,3,1))))


class MeminfoBreakdownTests(unittest.TestCase):
    def test_difference_is_merged_minus_separate_and_sums_exactly(self):
        a=path();b=path();b['command_ns']+=8;b['exec_minus_command_min_ns']-=3
        separate=dict(paths=[a],critical_role='late_1',window_ns=21)
        merged=dict(paths=[b],critical_role='late_1',window_ns=26)
        value=critical_difference(separate,merged)
        self.assertEqual(value['components_ns']['command_ns'],8)
        self.assertEqual(value['components_ns']['exec_minus_command_min_ns'],-3)
        self.assertEqual(sum(value['components_ns'].values()),value['window_difference_ns'])
        self.assertEqual(critical_difference(merged,separate)['window_difference_ns'],-5)
        with self.assertRaises(AssertionError):critical_difference(separate,dict(merged,window_ns=27))

    def test_ambiguous_critical_path_is_rejected(self):
        a=path();a['exec_assignment']='BOUNDED';value=dict(paths=[a],critical_role='late_1',window_ns=21)
        with self.assertRaises(AssertionError):critical_difference(value,value)

    def test_other_comparisons_are_not_relabelled(self):
        for comparison in ('sampling','boundary-vmstat'):
            with self.subTest(comparison=comparison),self.assertRaises(AssertionError):
                analyze(dict(status='PASS',stage='complete',comparison=comparison))

    def test_existing_output_is_not_overwritten_or_read(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_boundary_meminfo.read') as read:
            out=Path(tmp);marker=out/'saved.txt';marker.write_text('saved')
            with self.assertRaises(AssertionError):publish(out/'source.json',out)
            read.assert_not_called();self.assertEqual(marker.read_text(),'saved')


if __name__=='__main__':unittest.main()
