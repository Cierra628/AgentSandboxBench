"""Preparation order and resumption checks; no service or device access."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from shared_task_cache import measure


class PreparePauseTests(unittest.TestCase):
    def exercise(self, fail_second=False):
        events=[]
        class Pause:
            paused=False
            def __init__(self,sid):self.sid=sid
            def pause(self):self.paused=True;events.append('pause-'+self.sid)
            def resume(self):self.paused=False;events.append('resume-'+self.sid)
            def save(self,success):events.append('save-'+self.sid)
        def prepare(sid,segment,step,*,retain_fixture_pages=False):
            self.assertTrue(retain_fixture_pages)
            events.append('prepare-'+sid)
            if fail_second and sid=='b':raise RuntimeError('second preparation failed')
            return dict(segment=segment,pause=Pause(sid))
        def capture(context):
            events.append('capture');raise RuntimeError('stop before real collection')
        with tempfile.TemporaryDirectory() as tmp:
            probe=SimpleNamespace(out=Path(tmp),prepare=prepare,capture=capture)
            with self.assertRaises(RuntimeError):measure(probe,{'backbone':'a','early_1':'b'},'fixture')
        return events

    def test_each_vm_is_paused_before_next_fixture_is_prepared(self):
        events=self.exercise()
        self.assertEqual(events[:5],['prepare-a','pause-a','prepare-b','pause-b','capture'])
        self.assertIn('resume-a',events);self.assertIn('resume-b',events)

    def test_next_preparation_failure_resumes_already_paused_vm(self):
        events=self.exercise(True)
        self.assertEqual(events,['prepare-a','pause-a','prepare-b','resume-a','save-a'])


if __name__=='__main__':unittest.main()
