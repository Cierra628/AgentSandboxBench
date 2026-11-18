"""Only temporary files and mocks; no service, guest, real cache query or device."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_private_files as files
import run_host_private_files as run
import experiment_disk_guard as guard


class PrivateFileTests(unittest.TestCase):
    def test_pinned_manifest_is_nineteen_distinct_ordered_files(self):
        rows=files.targets()
        self.assertEqual(len(rows),19);self.assertEqual(len({r['relative'] for r in rows}),19)
        self.assertEqual(sum(r['size'] for r in rows),5565854720)

    def test_copies_have_new_inodes_and_shared_member_skips_only_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'seed';(source/'layer').mkdir(parents=True)
            (source/'layer/base').write_bytes(b'1234');(source/'other').write_bytes(b'abcdef')
            def call(label,argv,timeout):shutil.copy2(argv[-2],argv[-1])
            with patch.object(files,'targets',return_value=[dict(relative='layer/base',size=4)]):
                first=root/'first';files.copy_commits(source,first,{},call)
                second=root/'second';files.copy_commits(source,second,{},call)
                self.assertNotEqual((first/'layer/base').stat().st_ino,(second/'layer/base').stat().st_ino)
                shared=root/'shared';value=files.copy_commits(source,shared,dict(reuse_targets_from=str(first)),call)
                self.assertEqual((shared/'layer/base').stat().st_size,0)
                self.assertEqual((shared/'other').read_bytes(),b'abcdef')
                self.assertEqual([r['action'] for r in value['entries']],['READONLY_BIND','COPIED'])

    def test_expected_shared_and_single_user_private_objects_are_all_queried(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=files.targets();services=[]
            for slot in range(2):
                mount=Path(tmp)/str(slot);services.append(dict(mount=str(mount)))
                for row in rows:
                    path=mount/'env/image-cache/commits'/row['relative'];path.parent.mkdir(parents=True,exist_ok=True)
                    # Only metadata identity matters here, so use small mock lengths.
                    path.write_bytes(b'x')
            with patch.object(files,'targets',return_value=[dict(row,size=1) for row in rows]):
                shared=files.prepared_objects(services,'shared');private=files.prepared_objects(services,'private')
                self.assertEqual(len(shared),19);self.assertEqual(len(private),38)
                self.assertTrue(all(len(row['users'])==1 for row in private))
                def query(path):return dict(stable=True,resident_bytes=4096)
                with patch.object(files,'mincore_file',side_effect=query) as query,patch.object(files,'counters',return_value={'file':100}):
                    p0=files.query_objects(private,Path('/mock'),'P0')
                self.assertEqual(query.call_count,38);self.assertEqual(p0['resident_bytes'],38*4096)
                self.assertGreaterEqual(p0['end_monotonic_ns'],p0['start_monotonic_ns'])
                self.assertEqual(p0['elapsed_ns'],p0['end_monotonic_ns']-p0['start_monotonic_ns'])
                private[0]['identity']['inode']+=1
                with patch.object(files,'mincore_file',side_effect=query),patch.object(files,'counters',return_value={}):
                    with self.assertRaises(ValueError):files.query_objects(private,Path('/mock'),'bad')

    def test_readonly_bind_identity_is_required(self):
        rows=[dict(source='/owned/base',destination='/workspace/base')]
        mounted=dict(Source='/owned/base',Destination='/workspace/base',Type='bind',RW=False)
        files.verify_mounts(dict(Mounts=[mounted]),rows)
        for key,value in (('RW',True),('Source','/wrong'),('Type','volume')):
            changed=dict(mounted,**{key:value})
            with self.assertRaises(ValueError):files.verify_mounts(dict(Mounts=[changed]),rows)

    def test_signed_direct_difference_and_zero_denominator(self):
        def stages(a,b):return dict(P0=dict(resident_bytes=a),P1=dict(resident_bytes=b))
        result=files.compare(stages(10,12),stages(20,18))
        self.assertEqual(result['preparation_difference_bytes'],10)
        self.assertEqual(result['endpoint_difference_bytes'],6)
        self.assertEqual(result['difference_change_bytes'],-4)
        self.assertAlmostEqual(result['endpoint_reduction_fraction'],1/3)
        result=files.compare(stages(10,12),stages(0,0))
        self.assertEqual(result['endpoint_difference_bytes'],-12)
        self.assertIsNone(result['endpoint_reduction_fraction'])

    def test_old_entry_is_default_and_new_command_is_prefix_only(self):
        cfg=json.loads((ROOT/'configs/platform-prettier6604-host-file.json').read_text())
        with patch.object(run,'commands',return_value=([],['bash','check','--prettier6604-readonly-replay','--host-file-monitor'])):
            command=run.member_command(cfg,Path('/seed'))
        self.assertNotIn('--prettier6604-readonly-replay',command)
        self.assertEqual(command[-2:],['--host-prefix-count','1'])
        self.assertIn('--light-monitor',command)
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(files.member())

    def test_release_timeout_and_abort_do_not_open_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            value=dict(directory=tmp)
            with self.assertRaises(TimeoutError):files.wait_release(value,'missing',timeout=0)
            run.release(tmp,'ready',success=False)
            with self.assertRaises(RuntimeError):files.wait_release(value,'ready')

    def test_members_wait_without_starting_a_second_path_after_exit(self):
        from types import SimpleNamespace
        worker=SimpleNamespace(poll=lambda:1)
        with self.assertRaises(RuntimeError):run.wait_event(Path('/mock/nonexistent-private-event'),[worker],timeout=0)

    def test_inherited_lock_is_the_same_global_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(guard,'LOCK',Path(tmp)/'writer.lock'):
            with guard.writer_lock() as fd:
                with guard.writer_lock(inherited_fd=fd) as inherited:self.assertEqual(inherited,fd)
                code='import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                completed=subprocess.run([sys.executable,'-c',code,str(guard.LOCK)],capture_output=True)
                self.assertNotEqual(completed.returncode,0)
                wrong=os.open(Path(tmp)/'other',os.O_CREAT|os.O_RDWR,0o600)
                try:
                    with self.assertRaises(ValueError):
                        with guard.writer_lock(inherited_fd=wrong):pass
                finally:os.close(wrong)

    def test_locked_descriptor_survives_the_existing_shell_timeout_entry(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(guard,'LOCK',Path(tmp)/'writer.lock'):
            with guard.writer_lock() as fd:
                code='import sys; sys.path.insert(0,sys.argv[1]); from experiment_disk_guard import writer_lock; ctx=writer_lock(inherited_fd=int(sys.argv[2])); ctx.__enter__(); ctx.__exit__(None,None,None)'
                command=['bash','-c','exec timeout 5s "$@"','entry',sys.executable,'-B','-c',code,str(ROOT/'scripts'),str(fd)]
                env=dict(os.environ,ASB_WRITER_LOCK=str(guard.LOCK))
                completed=subprocess.run(command,env=env,pass_fds=(fd,),capture_output=True,timeout=10)
                self.assertEqual(completed.returncode,0,completed.stderr.decode())

    def test_combined_budget_keeps_existing_reserve(self):
        from isolated_storage import capacity_budget,GIB
        self.assertFalse(capacity_budget(58*GIB,32*GIB)['ready'])
        self.assertTrue(capacity_budget(58*GIB+1,32*GIB)['ready'])

    def test_actual_execution_order_and_original_recovery_default(self):
        self.assertEqual(run.execution_modes(),('shared','private'))
        self.assertEqual(run.execution_modes('private-first'),('private','shared'))
        self.assertEqual(run.execution_modes(resume_shared=Path('/saved')),('private',))
        for order,resume in (('invalid',None),('private-first',Path('/saved'))):
            with patch.object(run.subprocess,'check_output') as docker:
                with self.assertRaises(ValueError):run.pair(Path('/unused'),resume,order)
                docker.assert_not_called()

    def test_reversing_execution_does_not_reverse_the_capacity_difference(self):
        shared=dict(mode='shared',stages=dict(P0=dict(resident_bytes=10),P1=dict(resident_bytes=12)))
        private=dict(mode='private',stages=dict(P0=dict(resident_bytes=20),P1=dict(resident_bytes=18)))
        expected=files.compare(shared['stages'],private['stages'])
        self.assertEqual(run.comparison_for_groups([shared,private]),expected)
        self.assertEqual(run.comparison_for_groups([private,shared]),expected)
        self.assertEqual(expected['endpoint_difference_bytes'],6)
        with self.assertRaises(ValueError):run.comparison_for_groups([private,private])

    def test_private_report_dispatch_preserves_old_prefix_audit(self):
        from report_task_overview import write_report
        sample=dict(timestamp_ns=10,MemTotal_kib=100,MemAvailable_kib=50,Cached_kib=20,Shmem_kib=0)
        group=dict(role='backbone',chunks=[[sample]],sessions=[])
        for mode in ('old','recorded','explicit'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw=root/'raw';raw.mkdir();out=root/'report';out.mkdir()
                task=dict(status='PASS',host_prefix_count=1,task_id='fixture')
                if mode=='recorded':task['host_private_file_control']=dict(mode='shared',slot=0)
                (raw/'result.json').write_text(json.dumps(task))
                with patch('report_task_overview.samples',return_value=({'sid':group},'fixture')),\
                     patch('report_task_overview.tool_rows',return_value=([dict(tool_index=1,duration_ns=1)],1)),\
                     patch('host_private_files.audit_prefix_task',return_value={'status':'PASS'}) as private,\
                     patch('host_prefix_compare.audit_prefix',return_value={'status':'PASS'}) as old:
                    result=write_report(raw,out,private_file_control=mode=='explicit')
                self.assertEqual(private.call_count,int(mode!='old'))
                self.assertEqual(old.call_count,int(mode=='old'))
                self.assertIn('prefix_comparison' if mode=='old' else 'private_file_control_prefix',result)


if __name__=='__main__':unittest.main()
