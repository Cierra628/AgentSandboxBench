import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from experiment_disk_guard import guarded_run, writer_lock


class DiskGuardContract(unittest.TestCase):
    def test_low_space_never_launches_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            with (out/'log').open('w') as log:
                with self.assertRaisesRegex(RuntimeError, 'before launch'):
                    guarded_run(['touch',out/'unexpected'], directory=out, log=log,
                                timeout=1,reserve=10,free_bytes=lambda:5)
            self.assertFalse((out/'unexpected').exists())

    def test_space_drop_stops_only_owned_process_group(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            other=subprocess.Popen(['sleep','30'],start_new_session=True)
            readings=iter([100,100,5])
            try:
                with (out/'log').open('w') as log:
                    with self.assertRaisesRegex(RuntimeError,'disk reserve reached'):
                        guarded_run(['sh','-c','sleep .15; touch "$1"','guard',out/'late-write'],directory=out,log=log,timeout=2,
                                    reserve=10,interval=.01,free_bytes=lambda:next(readings))
                record=json.loads((out/'disk-guard.jsonl').read_text())
                self.assertTrue(record['owned_process_group_killed'])
                self.assertEqual(record['observed_min_free_bytes'],5)
                self.assertIsNone(other.poll())
                time.sleep(.2)
                self.assertFalse((out/'late-write').exists())
            finally:
                other.terminate()
                other.wait(timeout=5)

    def test_timeout_is_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            with (out/'log').open('w') as log:
                with self.assertRaises(TimeoutError):
                    guarded_run(['sleep','30'],directory=out,log=log,timeout=.03,
                                reserve=10,interval=.01,free_bytes=lambda:100)
            self.assertEqual(json.loads((out/'disk-guard.jsonl').read_text())['status'],'FAIL')

    def test_second_project_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('experiment_disk_guard.LOCK',Path(folder)/'lock'):
                with writer_lock():
                    with self.assertRaises(BlockingIOError):
                        with writer_lock():
                            self.fail('second writer acquired lock')


if __name__=='__main__':
    unittest.main()
