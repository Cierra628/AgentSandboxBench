import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_trenvx_file_origins import post_scan_proof


class PostScanTests(unittest.TestCase):
    def fixture(self,raw):
        (raw/'replay').mkdir()
        (raw/'replay/patch.diff').write_text('task patch\n')
        values={'config.json':dict(monitoring_acceptance=True,official_task='/fixture'),
                'independent-after.json':dict(stdout='{"svg":true}'),
                'independent-post-scan.json':dict(exit_code=0,stdout='{"svg":true}'),
                'patch-post-scan.json':dict(exit_code=0,stdout='task patch\n'),
                'official-task-post-scan.json':dict(exit_code=0,stdout='fixture output'),
                'official-task-post-scan-result.json':dict(status='PASS'),
                'post-scan-correctness.json':dict(status='PASS')}
        for name,value in values.items():(raw/name).write_text(json.dumps(value))

    def test_recomputes_receipts_and_rejects_changed_state(self):
        with tempfile.TemporaryDirectory() as tmp,patch('exp1_official_task.grade',return_value=dict(status='PASS')):
            raw=Path(tmp);self.fixture(raw)
            self.assertEqual(post_scan_proof(raw)['status'],'PASS')
            for name,key,value in [('patch-post-scan.json','stdout','other patch'),
                                   ('independent-post-scan.json','stdout','{"svg":false}'),
                                   ('official-task-post-scan.json','exit_code',1)]:
                p=raw/name;original=p.read_text();receipt=json.loads(original);receipt[key]=value
                p.write_text(json.dumps(receipt))
                with self.subTest(name=name),self.assertRaises(AssertionError):post_scan_proof(raw)
                p.write_text(original)

    def test_official_failure_is_not_hidden_by_success_receipt(self):
        with tempfile.TemporaryDirectory() as tmp,patch('exp1_official_task.grade',side_effect=AssertionError('failed tests')):
            raw=Path(tmp);self.fixture(raw)
            with self.assertRaises(AssertionError):post_scan_proof(raw)


if __name__=='__main__':unittest.main()
