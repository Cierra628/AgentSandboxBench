import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from exp1_official_task import grade, guest_command


class OfficialGradeTests(unittest.TestCase):
    def test_command_uses_this_tasks_commit(self):
        head='affa24ce764bd14ac087abeef0603012f5c635d6'
        task=dict(base_commit=head,test_patch='+++ b/tests/fixed.js\n',test_command='true')
        with patch('exp1_official_task.load',return_value=task):
            command=guest_command(Path('/fixture'),'/fixture/test.patch')
        self.assertIn('git rev-parse HEAD)" = '+head,command)
        self.assertNotIn('6905b39ec7bc6ed38732e26ceced5b57677e2600',command)

    def test_recheck_requires_applied_patch_without_applying_it_twice(self):
        task=dict(test_patch='+++ b/tests/fixed.js\n',test_command='true')
        with patch('exp1_official_task.load',return_value=task):
            command=guest_command(Path('/fixture'),'/fixture/test.patch',apply_test_patch=False)
        self.assertIn('git apply --reverse --check /fixture/test.patch',command)
        self.assertNotIn('\ngit apply /fixture/test.patch',command)
        self.assertIn('sha256sum tests/fixed.js',command)

    def test_trenvx_output_uses_existing_guest_directory(self):
        task=dict(test_patch='+++ b/tests/fixed.js\n',test_command='true')
        with patch('exp1_official_task.load',return_value=task):
            command=guest_command(Path('/fixture'),'/opt/asb-exp1/official-test.patch',guest_output='/opt/asb-exp1')
        self.assertNotIn('/workspace/',command)
        self.assertIn('/opt/asb-exp1/official-test.log',command)

    def check(self, passed, failed, code=0, baseline=False, pending=0):
        module=types.ModuleType('poly_bench_evaluation.parsers.javascript_parsers')
        class Parser:
            def __init__(self, text):
                self.text=text
            def parse(self):
                return dict(passed_tests=passed,failed_tests=failed)
        module.JavascriptJestPR=Parser
        task=dict(F2P="['target']",P2P="['control']")
        summary=dict(numFailedTestSuites=bool(failed),wasInterrupted=False,numPendingTests=pending,
                     numTodoTests=0,numTotalTests=len(passed)+len(failed),numFailedTests=len(failed),
                     snapshot=dict(added=0,updated=0,filesRemoved=0,failure=bool(failed)))
        with patch('exp1_official_task.load',return_value=task), patch.dict(sys.modules,{module.__name__:module}), patch.object(Path,'read_text',return_value='{}'):
            return grade(Path('/fixture'),f'ASB_OFFICIAL_EXIT={code}\n'+json.dumps(summary),baseline=baseline)

    def test_required_tests_and_negative_control(self):
        self.assertEqual(self.check(['target','control'],[])['status'],'PASS')
        self.assertEqual(self.check(['control'],['target'],1,True)['status'],'PASS')

    def test_skipped_or_missing_test_is_not_pass(self):
        with self.assertRaises(AssertionError):self.check(['control'],[])
        with self.assertRaises(AssertionError):self.check(['target','control'],[],pending=1)

    def test_duplicate_name_with_mixed_results_rejected(self):
        with self.assertRaises(AssertionError):self.check(['target','control'],['target'])

    def test_older_jest_requires_all_assertions_accounted_for(self):
        original = json.JSONDecoder.raw_decode
        def old_decode(decoder,text,*args,**kwargs):
            summary,end=original(decoder,text,*args,**kwargs)
            if 'numTodoTests' in summary:
                del summary['numTodoTests']
                summary['testResults']=[dict(assertionResults=[dict(status='passed'),dict(status='passed')])]
            return summary,end
        with patch.object(json.JSONDecoder,'raw_decode',old_decode):
            self.assertEqual(self.check(['target','control'],[])['status'],'PASS')
            with self.assertRaises(AssertionError):self.check(['control'],['target'],1)

    def test_older_jest_cannot_hide_pending_assertion(self):
        original = json.JSONDecoder.raw_decode
        def old_decode(decoder,text,*args,**kwargs):
            summary,end=original(decoder,text,*args,**kwargs)
            if 'numTodoTests' in summary:
                del summary['numTodoTests']
                summary['testResults']=[dict(assertionResults=[dict(status='passed'),dict(status='pending')])]
            return summary,end
        with patch.object(json.JSONDecoder,'raw_decode',old_decode):
            with self.assertRaises(AssertionError):self.check(['target','control'],[])

    def test_timeout_changed_tests_and_false_negative_control_rejected(self):
        for code in (124,125,137):
            with self.assertRaises(AssertionError):self.check(['target','control'],[],code)
        with self.assertRaises(AssertionError):self.check(['target','control'],[],0,True)


if __name__=='__main__':unittest.main()
