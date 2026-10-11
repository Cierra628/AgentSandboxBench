from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_git_syscalls import parse_file
import report_git_step_diagnostic as git_report


class SyscallParserTests(unittest.TestCase):
    def parse(self,text):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'trace.12';path.write_text(text)
            return parse_file(path)
    def test_raw_read_and_expected_missing_file(self):
        rows=self.parse('1000.000001 read(0x3, 0xaabb, 0x1000) = 0x40 <0.000010>\n1000.000020 openat(AT_FDCWD, "missing", O_RDONLY) = -1 ENOENT (No such file or directory) <0.000012>\n')
        self.assertEqual([x['syscall'] for x in rows],['read','openat'])
        self.assertEqual(rows[0]['result'],'0x40')
    def test_resumed_call_keeps_original_start(self):
        rows=self.parse('1000.000001 futex(0xaabb, FUTEX_WAIT, 1, NULL <unfinished ...>\n1000.010001 <... futex resumed>) = 0 <0.010000>\n')
        self.assertEqual(rows[0]['timestamp'],1000.000001)
        self.assertEqual(rows[0]['duration_seconds'],.01)
    def test_truncated_call_rejected(self):
        with self.assertRaises(AssertionError):self.parse('1000.000001 read(3, 0xaabb, 10 <unfinished ...>\n')
    def test_git_report_calls_baseline_audit_without_recursion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'result.json').write_text('{"git_diagnostic": true, "runs": []}')
            with patch.object(git_report,'audit_fixed',side_effect=RuntimeError('audit sentinel')) as audit:
                with self.assertRaisesRegex(RuntimeError,'audit sentinel'):git_report.report(root)
                audit.assert_called_once_with(root)


if __name__=='__main__':unittest.main()
