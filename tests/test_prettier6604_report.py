import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_prettier6604 import audit, report


class ReportBoundaries(unittest.TestCase):
    def test_cold_start_failure_has_no_replay_report(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            (raw/'result.json').write_text(json.dumps(dict(status='FAIL',task_id='fixture',checks={},cleanup={})))
            with self.assertRaises(AssertionError):audit(raw,dict(task_id='fixture'))

    def test_output_cannot_modify_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            with self.assertRaises(AssertionError):report(raw,raw/'report')
            self.assertFalse((raw/'report').exists())

    def test_missing_official_pass_cannot_be_invented(self):
        # A failed run must not satisfy the required replay checks by default.
        with tempfile.TemporaryDirectory() as directory:
            raw=Path(directory)
            (raw/'result.json').write_text(json.dumps(dict(status='PASS',task_id='fixture',checks={'smoke_and_unfixed_oracle':'PASS'},cleanup={})))
            with self.assertRaises(AssertionError):audit(raw,dict(task_id='fixture'))


if __name__=='__main__':unittest.main()
