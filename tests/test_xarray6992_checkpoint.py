"""Offline checks for restore boundary corruption; no live services."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from xarray6992_boundary import PREFIX,PIPE_STDOUT_SHA,verify
from validate_isolated_agentenv import main


class XarrayCheckpoint(unittest.TestCase):
    def setUp(self):
        self.cfg=json.loads((Path(__file__).resolve().parents[1]/'configs/xarray6992-backbone.json').read_text())

    def state(self):
        cfg=self.cfg
        files={'/testbed/'+p:d for p,d in cfg['base_file_sha256'].items()}
        files.update({'/workspace/artifacts/exp5/backbone/'+s+'/actions.tsv':
                      cfg['input_sha256']['segments/'+s+'/actions.tsv'] for s in PREFIX})
        files['/workspace/artifacts/exp5/backbone/trace_index_state/stdout/002.log']=PIPE_STDOUT_SHA
        return dict(base_commit=cfg['base_commit'],next_segment='core_verify',next_segment_absent=True,
                    completed_steps=[dict(segment=s,step=f'{i:03}',exit_code=code)
                        for s in PREFIX for i,code in enumerate(cfg['expected_exit_codes'][s],1)],files_sha256=files)

    def test_step_22_progress_and_exact_sigpipe_exception(self):
        state=self.state()
        self.assertEqual(verify(state,copy.deepcopy(state),self.cfg)['continuation_global_step'],23)
        state['completed_steps'][7]['exit_code']=141
        self.assertEqual(verify(state,copy.deepcopy(state),self.cfg)['status'],'PASS')
        state['files_sha256']['/workspace/artifacts/exp5/backbone/trace_index_state/stdout/002.log']='changed'
        with self.assertRaises(AssertionError):verify(state,state,self.cfg)

    def test_wrong_progress_missing_files_and_executed_next_segment_rejected(self):
        for mutation in ('order','missing_step','wrong_exit','changed_command','missing_source','next_executed','next_segment','wrong_base'):
            state=self.state()
            if mutation=='order':state['completed_steps'].reverse()
            elif mutation=='missing_step':state['completed_steps'].pop()
            elif mutation=='wrong_exit':state['completed_steps'][1]['exit_code']=141
            elif mutation=='changed_command':state['files_sha256']['/workspace/artifacts/exp5/backbone/core_patch/actions.tsv']='changed'
            elif mutation=='missing_source':del state['files_sha256']['/testbed/xarray/core/dataset.py']
            elif mutation=='next_executed':state['next_segment_absent']=False
            elif mutation=='next_segment':state['next_segment']='backbone_finish'
            else:state['base_commit']='wrong'
            with self.subTest(mutation=mutation),self.assertRaises(AssertionError):verify(state,state,self.cfg)

    def test_restore_file_or_progress_change_rejected(self):
        before=self.state();after=copy.deepcopy(before)
        after['files_sha256']['/testbed/xarray/core/dataset.py']='changed'
        with self.assertRaises(AssertionError):verify(before,after,self.cfg)

    def test_missing_reference_or_diagnostic_mode_rejected_before_allocation(self):
        for options in (dict(python_checkpoint=True),dict(python_expected_evaluation_raw=Path('/not-read')),
                        dict(python_checkpoint=True,python_expected_evaluation_raw=Path('/not-read'),python_backbone_inputs=Path('/not-read'))):
            args=SimpleNamespace(exp1_checkpoint=False,**options)
            with patch('validate_isolated_agentenv.os.geteuid',return_value=0),self.assertRaisesRegex(ValueError,'Python checkpoint requires'):main(args)


if __name__=='__main__':unittest.main()
