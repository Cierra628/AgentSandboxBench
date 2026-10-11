from pathlib import Path
import sys,unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from smoke_agentenv_whole_cache import post_checks_after_analysis_error

class PostAnalysisTests(unittest.TestCase):
    def test_analysis_failure_resumes_then_checks_completed_task(self):
        checks={'frozen_exp1':'PASS','official_before':'PASS','whole_cache':'FAIL'}
        pause=Mock();pause.paused=True;verify=Mock()
        post_checks_after_analysis_error(checks,pause,verify)
        pause.resume.assert_called_once();pause.save.assert_called_once_with(False);verify.assert_called_once()
        self.assertEqual(checks['whole_cache'],'FAIL')
    def test_unfinished_task_is_not_claimed_valid(self):
        verify=Mock();post_checks_after_analysis_error({'frozen_exp1':'FAIL'},None,verify);verify.assert_not_called()
    def test_failed_resume_does_not_execute_guest_commands(self):
        pause=Mock();pause.paused=True;pause.resume.side_effect=RuntimeError('resume failed');verify=Mock()
        with self.assertRaises(RuntimeError):post_checks_after_analysis_error({'frozen_exp1':'PASS','official_before':'PASS'},pause,verify)
        verify.assert_not_called()
if __name__=='__main__':unittest.main()
