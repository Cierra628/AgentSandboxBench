"""Offline validation of strict test grading; no live service access."""
import sys
from pathlib import Path
import unittest
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from xarray6992_inputs import grade_xml,frozen_shell,SHELL_PATH,check_replay_exit,WORKLOAD
from xarray6992_test_ids import resolve_test_ids
import json


class XarrayTests(unittest.TestCase):
    def test_truncated_parameter_maps_uniquely_without_changing_test_set(self):
        short='xarray/tests/test_dataset.py::TestDataset::test_example[a-Different'
        actual=short+' number of dimensions]'
        resolved,aliases=resolve_test_ids([self.node,short],[actual])
        self.assertEqual(resolved,[self.node,actual]);self.assertEqual(aliases,{actual:short})
        xml=self.xml().replace('test_reset[x]','test_example[a-Different number of dimensions]')
        self.assertEqual(grade_xml(xml,[short],[short],[],0,aliases)['passed'],1)
        for catalog in ([],[actual,short+' data]']):
            with self.assertRaises(AssertionError):resolve_test_ids([short],catalog)
        with self.assertRaises(AssertionError):grade_xml(xml,[short],[short],[],0,{actual:'other-test'})

    def pipeline_sample(self):
        samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments']
        return next(s for s in samples if s['role']=='backbone' and s['segment']=='trace_index_state')['actions'][1]

    def test_exact_output_sigpipe_requires_explicit_diagnostic_mode(self):
        sample=self.pipeline_sample();command=sample['command'];stdout=sample['output'].encode()
        with self.assertRaises(AssertionError):check_replay_exit('trace_index_state','002',0,141,command,stdout)
        difference=check_replay_exit('trace_index_state','002',0,141,command,stdout,True)
        self.assertEqual(difference['actual'],141);self.assertEqual(difference['expected'],0)
        self.assertIsNone(check_replay_exit('trace_index_state','002',0,0,command,stdout,True))

    def test_exception_rejects_other_step_command_output_and_exit(self):
        sample=self.pipeline_sample();command=sample['command'];stdout=sample['output'].encode()
        for segment,step,code,cmd,output in [
            ('trace_index_state','001',141,command,stdout),
            ('read_repo','002',141,command,stdout),
            ('trace_index_state','002',1,command,stdout),
            ('trace_index_state','002',124,command,stdout),
            ('trace_index_state','002',141,command+' ',stdout),
            ('trace_index_state','002',141,command,stdout+b' '),
            ('trace_index_state','002',0,command,stdout+b' '),
        ]:
            with self.assertRaises(AssertionError):check_replay_exit(segment,step,0,code,cmd,output,True)

    node='xarray/tests/test_dataset.py::TestDataset::test_reset[x]'
    def xml(self,child=''):
        return '<testsuite><testcase file="xarray/tests/test_dataset.py" classname="xarray.tests.test_dataset.TestDataset" name="test_reset[x]">'+child+'</testcase></testsuite>'

    def test_exact_parametrized_case_passes(self):
        self.assertEqual(grade_xml(self.xml(),[self.node],[self.node],[],0)['status'],'PASS')

    def test_skipped_and_failed_are_not_pass(self):
        self.assertEqual(grade_xml(self.xml('<skipped/>'),[self.node],[self.node],[],0)['status'],'FAIL')
        self.assertEqual(grade_xml(self.xml('<failure/>'),[self.node],[self.node],[],1)['F2P_passed'],0)

    def test_missing_duplicate_error_timeout_and_wrong_code_rejected(self):
        for xml,code in [('<testsuite/>',0),(self.xml().replace('</testsuite>',self.xml()+'</testsuite>'),0),
                         (self.xml('<error/>'),1),(self.xml(),124),(self.xml(),1)]:
            with self.assertRaises(AssertionError):grade_xml(xml,[self.node],[self.node],[],code)

    def test_mixed_workload_rejected_before_allocation(self):
        from validate_isolated_agentenv import main
        args=SimpleNamespace(python_backbone_inputs=Path('/not-read'),prettier6604_task=Path('/not-read'),exp1_checkpoint=False)
        with patch('validate_isolated_agentenv.os.geteuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'another workload'):main(args)

    def test_readonly_replay_without_verified_seed_is_rejected_before_allocation(self):
        from validate_isolated_agentenv import main
        args=SimpleNamespace(python_backbone_inputs=Path('/not-read'),python_readonly_replay=True,exp1_checkpoint=False)
        with patch('validate_isolated_agentenv.os.geteuid',return_value=0):
            with self.assertRaisesRegex(ValueError,'verified task cache seed'):main(args)

    def test_frozen_shell_restores_image_path_and_removes_activation(self):
        env=dict(os.environ,PATH='/unexpected/testbed/bin',CONDA_PREFIX='/unexpected/testbed',
                 CONDA_DEFAULT_ENV='testbed',CONDA_SHLVL='1')
        command='printf "%s\\n" "$PATH"; test -z "${CONDA_PREFIX+x}"; test -z "${CONDA_DEFAULT_ENV+x}"; test -z "${CONDA_SHLVL+x}"'
        result=subprocess.run(['/bin/sh','-c',frozen_shell(command)],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),SHELL_PATH)


if __name__=='__main__':unittest.main()
