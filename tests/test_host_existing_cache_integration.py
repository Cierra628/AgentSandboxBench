"""Offline wiring/worker tests only; never run BPF, services or backing queries."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_existing_cache_owner as h


class BoundedOwnerTests(unittest.TestCase):
    def mocked_query(self,root,*,partial=False,change=None,released=True):
        file=root/'archive';group=root/'group';out=root/'query'
        class Worker:
            pid=12345
            returncode=None
            def wait(worker,timeout=None):
                now=time.monotonic_ns()
                value=dict(status='INCOMPLETE' if partial else 'OBSERVED',path=str(file),reference_group=str(group),
                    reference_group_ids=[22],probe_reaped=True,page_size=4096,resident_pages=1,
                    inside_bytes=0,outside_bytes=0 if partial else 4096,unknown_bytes=4096 if partial else 0,
                    residency_observed=dict(resident_bytes=4096),start_monotonic_ns=now,
                    query_start_monotonic_ns=now,query_end_monotonic_ns=now,end_monotonic_ns=now)
                if change:change(value)
                saved=out/'observation';saved.mkdir();(saved/'result.json').write_text(json.dumps(value))
                worker.returncode=1 if partial else 0
                return worker.returncode
        with patch.object(h.subprocess,'Popen',return_value=Worker()) as create,\
             patch.object(h,'_stop_group',return_value=released):
            result=h.query_bounded(file,group,out)
        self.assertTrue(create.call_args.kwargs['start_new_session'])
        return result

    def test_not_requested_does_not_create_directory_or_worker(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(h.subprocess,'Popen') as create:
            out=Path(tmp)/'none';v=h.query_bounded(None,Path('/no-group'),out)
            create.assert_not_called();self.assertFalse(out.exists())
            self.assertEqual(v['status'],'NOT_REQUESTED');self.assertIsNone(v['unknown_bytes'])

    def test_success_and_partial_keep_real_capacities_and_reference(self):
        for partial in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                value=self.mocked_query(Path(tmp),partial=partial)
                self.assertEqual(value['status'],'INCOMPLETE' if partial else 'OBSERVED')
                self.assertEqual(value['unknown_bytes'],4096 if partial else 0)
                self.assertEqual(value['outside_bytes'],0 if partial else 4096)
                self.assertTrue(value['worker_reaped']);self.assertTrue(value['worker_group_released'])
                self.assertEqual(value['reference_group_ids'],[22])

    def test_bad_identity_capacity_time_or_cleanup_cannot_become_observed(self):
        cases=[lambda v:v.update(path='/wrong-file'),lambda v:v.update(reference_group='/wrong-group'),
               lambda v:v.update(outside_bytes=8192),lambda v:v.update(unknown_bytes=4096),
               lambda v:v.update(end_monotonic_ns=0),lambda v:v.update(probe_reaped=False)]
        for change in cases:
            with tempfile.TemporaryDirectory() as tmp:
                v=self.mocked_query(Path(tmp),change=change)
                self.assertEqual(v['status'],'ERROR');self.assertIsNone(v['inside_bytes']);self.assertIsNone(v['outside_bytes'])
        with tempfile.TemporaryDirectory() as tmp:
            v=self.mocked_query(Path(tmp),released=False)
            self.assertEqual(v['status'],'ERROR');self.assertFalse(v['worker_group_released'])

    def test_timeout_real_owned_process_group_is_reaped_unrelated_child_survives(self):
        # Replace only the query executable with a metadata-only blocking fixture.
        # Its child stands in for bpftrace; no BPF or target file is opened.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ready=root/'ready.json';helper=root/'held.py'
            helper.write_text('''import json,os,signal,subprocess,sys,time
def stop(sig,frame): raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
try:
 from pathlib import Path
 Path(sys.argv[1]).write_text(json.dumps(dict(parent=os.getpid(),child=child.pid)))
 while True: time.sleep(1)
finally:
 if child.poll() is None: child.terminate()
 child.wait(timeout=3)
''')
            real_popen=subprocess.Popen
            unrelated=real_popen([sys.executable,'-c','import time; time.sleep(60)'])
            def fixture(argv,**kwargs):
                return real_popen([sys.executable,'-B',str(helper),str(ready)],**kwargs)
            try:
                with patch.object(h.subprocess,'Popen',side_effect=fixture):
                    value=h.query_bounded(root/'not-opened',root/'no-cgroup-query',root/'query',timeout=1,grace=3)
                self.assertEqual(value['status'],'TIMEOUT');self.assertIsNone(value['outside_bytes'])
                self.assertTrue(value['worker_reaped']);self.assertTrue(value['worker_group_released'])
                self.assertTrue(ready.exists());ids=json.loads(ready.read_text())
                self.assertEqual(ids['parent'],value['worker_pid'])
                with self.assertRaises(ProcessLookupError):os.kill(ids['child'],0)
                with self.assertRaises(ChildProcessError):os.waitpid(value['worker_pid'],os.WNOHANG)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate();unrelated.wait(timeout=5)

    def test_forced_group_cleanup_targets_only_owned_group(self):
        from unittest.mock import MagicMock
        worker=MagicMock(pid=12345)
        worker.wait.side_effect=[subprocess.TimeoutExpired('own',1),0]
        with patch.object(h,'_group_alive',side_effect=[True,False,False]),patch.object(h.os,'killpg') as send:
            self.assertTrue(h._stop_group(worker,1))
        self.assertEqual(send.call_args_list[0].args,(12345,signal.SIGTERM))
        self.assertEqual(send.call_args_list[1].args,(12345,signal.SIGKILL))


if __name__=='__main__':unittest.main()
