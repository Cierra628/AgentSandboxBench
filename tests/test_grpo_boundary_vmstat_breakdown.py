"""Synthetic saved timing files only; no service, workload, network or devices."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from analyze_grpo_path_costs import analyze_run
from analyze_grpo_boundary_vmstat import COMPONENTS,PHASES,critical_difference,analyze,report,publish
import test_grpo_path_costs as fixtures


def skipped_fixture(raw):
    fixtures.PathCostTests().fixture(raw,'on')
    for name in ('result.json','effective-config.json'):
        p=raw/name;v=json.loads(p.read_text());v['light_skip_boundary_vmstat']=True;p.write_text(json.dumps(v))
    for p in raw.glob('rollouts/*/*/guest-vmstat.tsv'):p.write_text('timestamp_ns\n')
    return raw


def path(role='a',offset=1):
    p=dict(zip(COMPONENTS,(10,5,2,3,offset)))
    p.update(role=role,exec_assignment='UNIQUE')
    return p


class BoundaryBreakdownTests(unittest.TestCase):
    def test_explicit_skip_retains_memory_and_legacy_default_rejects(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=skipped_fixture(Path(tmp)/'raw')
            with self.assertRaises(AssertionError):analyze_run(raw,'on')
            r=analyze_run(raw,'on',skip_boundary_vmstat=True)
            self.assertEqual((r['boundary_memory_rows'],r['boundary_vmstat_rows']),(8,0))
            self.assertEqual(r['bounded_exec_assignments'],2)

    def test_skip_requires_on_and_both_saved_flags(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=skipped_fixture(Path(tmp)/'raw')
            for mode,flag in (('off',True),('on',1)):
                with self.subTest(mode=mode,flag=flag),self.assertRaises(AssertionError):analyze_run(raw,mode,skip_boundary_vmstat=flag)
            for name in ('result.json','effective-config.json'):
                p=raw/name;v=json.loads(p.read_text());v['light_skip_boundary_vmstat']=False;p.write_text(json.dumps(v))
                with self.assertRaises(AssertionError):analyze_run(raw,'on',skip_boundary_vmstat=True)
                v['light_skip_boundary_vmstat']=True;p.write_text(json.dumps(v))

    def test_skipping_vmstat_does_not_allow_bad_rows_or_missing_memory(self):
        with tempfile.TemporaryDirectory() as tmp,patch('analyze_grpo_path_costs.ROOT',Path(tmp)):
            raw=skipped_fixture(Path(tmp)/'raw')
            p=raw/'rollouts/a/segment/guest-vmstat.tsv';p.write_text('timestamp_ns\n1\n')
            with self.assertRaises(AssertionError):analyze_run(raw,'on',skip_boundary_vmstat=True)
            p.write_text('timestamp_ns\n');(raw/'rollouts/a/segment/guest-memory.tsv').write_text('timestamp_ns\n')
            with self.assertRaises(AssertionError):analyze_run(raw,'on',skip_boundary_vmstat=True)

    def test_critical_difference_keeps_sign_and_exact_sum(self):
        a=path();b=path();b['command_ns']+=8;b['exec_minus_command_min_ns']-=3
        keep=dict(paths=[a],critical_role='a',window_ns=21)
        skip=dict(paths=[b],critical_role='a',window_ns=26)
        r=critical_difference(keep,skip)
        self.assertEqual(r['components_ns']['command_ns'],8)
        self.assertEqual(r['components_ns']['exec_minus_command_min_ns'],-3)
        self.assertEqual(sum(r['components_ns'].values()),r['window_difference_ns'])
        with self.assertRaises(AssertionError):critical_difference(keep,dict(skip,window_ns=27))

    def test_critical_difference_requires_unique_assignment(self):
        a=path();a['exec_assignment']='BOUNDED';r=dict(paths=[a],critical_role='a',window_ns=21)
        with self.assertRaises(AssertionError):critical_difference(r,r)

    def test_old_sampling_batch_is_not_relabelled(self):
        with self.assertRaises(AssertionError):analyze(dict(status='PASS',stage='complete',comparison='sampling'))

    def test_report_uses_calculated_numbers_not_original_batch_literals(self):
        pairs=[];runs=[]
        for n in (1,2):
            delta=dict.fromkeys(COMPONENTS,0);delta['command_ns']=n*1_000_000_000;delta['exec_minus_command_min_ns']=-100_000_000
            pairs.append(dict(pair=n,critical_comparison=dict(keep_role='late_1',skip_role='late_1',components_ns=delta,window_difference_ns=sum(delta.values())),controller_phase_difference_ns=dict.fromkeys(PHASES,0)))
            for variant in ('keep','skip'):runs.append(dict(pair=n,boundary_vmstat_mode=variant,controller=dict(groups_ns=dict.fromkeys(PHASES,0))))
        value=dict(pairs=pairs,runs=runs,critical_commands=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);report(value,out);text=(out/'REPORT.md').read_text()
            self.assertIn('第1对原命令变化+1.000000秒',text);self.assertIn('第2对原命令变化+2.000000秒',text)
            self.assertIn('调用余量-0.100000秒',text);self.assertNotIn('1.315969',text)

    def test_publish_refuses_existing_output_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);marker=out/'keep.txt';marker.write_text('saved')
            with patch('analyze_grpo_boundary_vmstat.read',return_value={}),patch('analyze_grpo_boundary_vmstat.analyze',return_value={}):
                with self.assertRaises(FileExistsError):publish(out/'source.json',out)
            self.assertEqual(marker.read_text(),'saved')


if __name__=='__main__':unittest.main()
