"""Find summaries preserve observed failures and reject mismatched outputs."""
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_sphinx_find_diagnostic import capture_summary


class FindReportTests(unittest.TestCase):
    def row(self):
        return dict(status='CAPTURED',command_sha256='a'*64,original_exit_code=1,
            original_stdout='output',diagnostic_stdout='output',original_stderr='',diagnostic_stderr='',
            pipeline_exit_codes=[1,0],find_stderr='find: /proc/20/net: Invalid argument',
            error_processes_after_find=[dict(pid=20,state='Z',parent_pid=10,owned_control_child=True)])

    def summarize(self,rows):
        return capture_summary(rows,'a'*64,hashlib.sha256(b'output').hexdigest(),hashlib.sha256(b'').hexdigest())

    def test_find_error_keeps_exit_pipe_and_process_evidence(self):
        row=self.row();original=copy.deepcopy(row);summary=self.summarize([row])
        self.assertEqual(summary['original_exit_codes'],[1])
        self.assertEqual(summary['diagnostic_pipeline_exit_codes'],[[1,0]])
        self.assertEqual(summary['captures_with_find_error'],1)
        self.assertTrue(summary['errors'][0]['processes_after_find'][0]['owned_control_child'])
        self.assertEqual(row,original)

    def test_consecutive_commands_need_not_fail_together(self):
        row=self.row();row['original_exit_code']=0
        self.assertEqual(self.summarize([row])['original_exit_codes'],[0])

    def test_invalid_receipt_or_changed_output_is_rejected(self):
        for key,value in [('status','FAIL'),('command_sha256','b'*64),('original_stdout','changed'),
                          ('diagnostic_stdout','changed'),('original_stderr','changed'),('diagnostic_stderr','changed'),('pipeline_exit_codes',[1])]:
            row=self.row();row[key]=value
            with self.assertRaises(AssertionError):self.summarize([row])


if __name__=='__main__':unittest.main()
