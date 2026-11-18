"""Tiny owned paths and mocked cache advice/query only; no services, devices or real eviction."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_cold_file_read as cold
import run_host_private_files as run
from host_rootfs_layers import file_identity


class ColdReadTests(unittest.TestCase):
    def fixture(self,root):
        mount=root/'.artifacts/storage-isolated-service/owned/mount'
        target=mount/'env/image-cache/commits/layer/base';target.parent.mkdir(parents=True);target.write_bytes(b'test')
        return cold.ColdFileRead([dict(path=str(target),relative='layer/base',identity=file_identity(target),users=['0','1'])],
            [dict(mount=str(mount))],Path('/mock/group'),root),target

    def point(self,objects,group,label):
        return dict(status='OBSERVED',label=label,start_monotonic_ns=100,end_monotonic_ns=200,
            elapsed_ns=100,cpu_ns=50,resident_bytes=0,objects=objects)

    def test_initial_advice_is_scoped_and_zero_is_verified_before_guests(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cold,'ROOT',Path(tmp)),\
             patch.object(cold.os,'posix_fadvise') as advise,patch.object(cold,'query_objects',side_effect=self.point) as query:
            observer,target=self.fixture(Path(tmp));point=observer.initial()
            advise.assert_called_once();self.assertEqual(advise.call_args.args[1:],(0,0,cold.os.POSIX_FADV_DONTNEED))
            self.assertEqual(point['resident_bytes'],0);self.assertEqual(observer.state['initial_residency'],'CONFIRMED_ZERO')
            query.assert_called_once();self.assertEqual(target.read_bytes(),b'test')

    def test_advisory_success_with_remaining_pages_is_not_cold_success(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cold,'ROOT',Path(tmp)),patch.object(cold.os,'posix_fadvise'),\
             patch.object(cold,'query_objects',side_effect=lambda *a:dict(self.point(*a),resident_bytes=4096)):
            observer,_=self.fixture(Path(tmp))
            with self.assertRaisesRegex(RuntimeError,'not zero'):observer.initial()
            self.assertEqual(observer.state['status'],'FAIL');self.assertNotIn('initial_residency',observer.state)

    def test_outside_owned_paths_or_changed_identity_never_get_advice(self):
        for problem in ('path','identity'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as tmp,patch.object(cold,'ROOT',Path(tmp)),\
                 patch.object(cold.os,'posix_fadvise') as advise,patch.object(cold,'query_objects') as query:
                observer,target=self.fixture(Path(tmp))
                if problem=='path':observer.services=[dict(mount='/outside')]
                else:observer.objects[0]['identity']['inode']+=1
                with self.assertRaises(ValueError):observer.initial()
                advise.assert_not_called();query.assert_not_called()

    def test_sparse_queries_wait_until_prior_query_end_plus_interval(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(cold,'ROOT',Path(tmp)),patch.object(cold,'query_objects',side_effect=self.point) as query:
            observer,_=self.fixture(Path(tmp));observer.capture('P0')
            with patch.object(cold.time,'monotonic_ns',return_value=observer.next_ns-1):observer.tick()
            self.assertEqual(query.call_count,1)
            with patch.object(cold.time,'monotonic_ns',return_value=observer.next_ns):observer.tick()
            self.assertEqual(query.call_count,2);observer.complete()
            self.assertEqual(observer.state['query_total_ns'],200);self.assertEqual(observer.state['query_cpu_ns'],100)

    def test_existing_wait_can_observe_while_member_runs_without_new_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'ready.json';called=[]
            def observe():called.append(1);p.write_text(json.dumps(dict(status='ready')))
            result=run.wait_event(p,[SimpleNamespace(poll=lambda:None)],observe=observe)
            self.assertEqual(result['status'],'ready');self.assertEqual(called,[1])

    def test_cold_pair_cannot_reuse_warm_recovery_or_start_services(self):
        with patch.object(run.subprocess,'check_output') as inspect:
            with self.assertRaisesRegex(ValueError,'cannot reuse'):run.pair(Path('/unused'),Path('/warm'),cold_read=True)
            inspect.assert_not_called()

    def test_new_cli_is_rejected_without_private_group_before_config_read(self):
        p=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_file_workflow.py','/unused','--private-cold-read'],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,2);self.assertIn('requires private-file-group',p.stderr)


if __name__=='__main__':unittest.main()
