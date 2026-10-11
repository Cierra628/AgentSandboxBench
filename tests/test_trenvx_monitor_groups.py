from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from report_trenvx_monitor_groups import report


class MonitorGroupReportTests(unittest.TestCase):
    def test_empty_groups_are_not_evidence(self):
        with self.assertRaises(ValueError): report([])

    def test_duplicate_groups_cannot_inflate_sample_count(self):
        with tempfile.TemporaryDirectory() as name:
            with self.assertRaises(ValueError): report([Path(name),Path(name)])

    def test_above_protocol_limit_is_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            paths=[Path(name)/str(i) for i in range(6)]
            for path in paths: path.mkdir()
            with self.assertRaises(ValueError): report(paths)


if __name__ == '__main__': unittest.main()
