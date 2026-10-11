import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prettier6604_boundary import PREFIX,verify
from audit_prettier6604_official import reference,signature


class CheckpointBoundary(unittest.TestCase):
    def state(self):
        return dict(completed_steps=[dict(segment=s,step=f'{i:03}',exit_code=127 if s=='read_repo' and i==2 else 0)
                 for s,n in zip(PREFIX,(5,6,7,6)) for i in range(1,n+1)],
                 next_segment='backbone_finish',files_sha256={'/testbed/src/language-js/needs-parens.js':'fixed'})
    def test_valid_24_to_25_boundary(self):
        state=self.state();self.assertEqual(verify(state,copy.deepcopy(state))['continuation_global_step'],25)
    def test_valid_11_to_12_boundary_and_wrong_progress_rejected(self):
        state=self.state();state['completed_steps']=state['completed_steps'][:11];state['next_segment']='core_patch'
        self.assertEqual(verify(state,copy.deepcopy(state),11)['continuation_global_step'],12)
        for mutation in ('missing','order','next'):
            wrong=copy.deepcopy(state)
            if mutation=='missing':wrong['completed_steps'].pop()
            elif mutation=='order':wrong['completed_steps'].reverse()
            else:wrong['next_segment']='backbone_finish'
            with self.assertRaises(AssertionError):verify(wrong,wrong,11)
    def test_file_change_rejected(self):
        before=self.state();after=copy.deepcopy(before);after['files_sha256']['/testbed/src/language-js/needs-parens.js']='different'
        with self.assertRaises(AssertionError):verify(before,after)
    def test_missing_progress_rejected(self):
        before=self.state();before['completed_steps'].pop()
        with self.assertRaises(AssertionError):verify(before,before)
    def test_wrong_order_or_failure_rejected(self):
        for mutation in ('order','failure'):
            state=self.state()
            if mutation=='order':state['completed_steps'].reverse()
            else:state['completed_steps'][1]['exit_code']=0
            with self.assertRaises(AssertionError):verify(state,state)
    def test_restoring_beyond_the_boundary_rejected(self):
        state=self.state();state['next_segment']='finished'
        with self.assertRaises(AssertionError):verify(state,state)
    def test_reference_requires_preserved_original_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);log=root/'stdout';log.write_text('original')
            p=root/'audit.json';p.write_text(json.dumps(dict(source=str(log),source_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),status='FAIL',exit_code=1,parser_results=dict(num_tests_failed=2,num_tests_passed=0))))
            self.assertEqual(reference(p)['status'],'FAIL')
            log.write_text('changed')
            with self.assertRaises(AssertionError):reference(p)
    def test_failure_multiset_retains_duplicates(self):
        r=dict(status='FAIL',exit_code=1,parser_results=dict(passed_tests=[],failed_tests=['target','target']),
               summary=dict(numTotalTests=2,numFailedTests=2,numFailedTestSuites=1,numPendingTests=0,wasInterrupted=False,
                            snapshot=dict(added=0,updated=0,filesRemoved=0,failure=True,unmatched=2,matched=0)))
        expected=signature(r);r['parser_results']['failed_tests'].pop()
        self.assertNotEqual(expected,signature(r))


if __name__=='__main__':unittest.main()
