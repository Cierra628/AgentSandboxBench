"""Tiny temporary files, mocked advice/mincore/services, controlled local threads only."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_cold_file_read as base
import host_grpo_cold_read as cold
import host_full_rootfs_control as control
import host_private_files as files
import run_host_full_rootfs as run
import run_host_file_workflow as workflow
import run_prettier6604_grpo as grpo
import test_host_full_rootfs_control as fixtures


class GrpoColdTests(unittest.TestCase):
    def fixture(self,root,mode='shared'):
        mount=root/'.artifacts/storage-isolated-service/owned/mount'
        helper=fixtures.FullRootfsTests()
        with patch.object(control,'manifest_layers',side_effect=helper.layers):
            preparation=helper.prepare(mount,mode)
        out=mount/'controller/prettier6604';out.mkdir(parents=True)
        reader=cold.GrpoColdRead(control.prepared_objects(preparation),mount,mode,helper.layers(),Path('/mock/group'),out)
        return reader

    def initialize(self,reader):
        with patch.object(base.os,'posix_fadvise'),patch.object(files,'counters',return_value={}),\
             patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=0)):
            return reader.initial()

    def test_exact_19_and_133_targets_zero_proof_and_no_duplicate_query(self):
        for mode,count,users in [('shared',19,7),('private',133,1)]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)),\
                 patch.object(base.os,'posix_fadvise') as advice,patch.object(files,'counters',return_value={}),\
                 patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=0)) as query:
                reader=self.fixture(Path(tmp),mode);p0=reader.initial()
                self.assertEqual(len(p0['objects']),count);self.assertEqual(advice.call_count,count)
                self.assertEqual(query.call_count,count)
                self.assertEqual(len({c.args[0] for c in query.call_args_list}),count)
                self.assertTrue(all(len(r['users'])==users for r in p0['objects']))
                self.assertEqual(reader.state['interval_seconds'],5)
                self.assertEqual(reader.state['initial_residency'],'CONFIRMED_ZERO')
                self.assertEqual(reader.state['eviction']['files'],count)
                self.assertFalse(reader.is_alive())

    def test_wrong_set_alias_other_owned_file_and_symlink_escape_rejected_before_advice(self):
        for problem in ('missing','alias','extra','other','symlink'):
            with self.subTest(problem=problem),tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)),\
                 patch.object(base.os,'posix_fadvise') as advice,patch.object(files,'mincore_file') as query:
                root=Path(tmp);reader=self.fixture(root,'private')
                if problem=='missing':reader.objects.pop()
                if problem=='alias':reader.objects[-1]=reader.objects[0]
                if problem=='extra':reader.objects.append(reader.objects[0])
                if problem=='other':
                    path=reader.mount/'env/image-cache/commits/not-in-manifest';path.write_bytes(b'test')
                    reader.objects[0]=dict(reader.objects[0],path=str(path),identity=files.file_identity(path))
                if problem=='symlink':
                    path=Path(reader.objects[-1]['path']);path.unlink()
                    outside=root/'external';outside.write_bytes(b'test');path.symlink_to(outside)
                with self.assertRaises(ValueError):reader.initial()
                advice.assert_not_called();query.assert_not_called()

    def test_nonzero_p0_and_initial_query_error_stop_before_worker_start(self):
        for error in (None,OSError('query failed')):
            with self.subTest(error=error),tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)),\
                 patch.object(base.os,'posix_fadvise'),patch.object(files,'counters',return_value={}),\
                 patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4096),side_effect=error):
                reader=self.fixture(Path(tmp))
                with self.assertRaises((RuntimeError,OSError)):reader.initial()
                with self.assertRaisesRegex(RuntimeError,'verified zero'):reader.start()
                self.assertFalse(reader.is_alive())

    def test_serial_worker_uses_end_plus_interval_and_does_not_catch_up(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)):
            reader=self.fixture(Path(tmp));self.initialize(reader)
            reader.interval_ns=20_000_000;reader.next_ns=time.monotonic_ns()
            done=threading.Event();calls=[]
            def query(objects,group,label):
                start=time.monotonic_ns();time.sleep(.025);end=time.monotonic_ns()
                calls.append((start,end))
                if len(calls)==2:done.set()
                return dict(label=label,start_monotonic_ns=start,end_monotonic_ns=end,elapsed_ns=end-start,cpu_ns=1,resident_bytes=0,objects=objects)
            with patch.object(base,'query_objects',side_effect=query):
                reader.start()
                try:self.assertTrue(done.wait(3))
                finally:reader.stop()
            self.assertEqual(len(calls),2)
            self.assertGreaterEqual(calls[1][0]-calls[0][1],reader.interval_ns)
            self.assertTrue(reader.state['worker_reaped'])
            self.assertGreater(reader.state['worker_timing']['cpu_ns'],0)
            self.assertGreaterEqual(reader.state['worker_timing']['end_monotonic_ns'],reader.state['worker_timing']['start_monotonic_ns'])

    def test_real_grpo_method_still_dispatches_seven_paths_with_query_in_flight(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)):
            reader=self.fixture(Path(tmp));self.initialize(reader);reader.next_ns=time.monotonic_ns()
            query_entered=threading.Event();query_release=threading.Event()
            def query(objects,group,label):
                start=time.monotonic_ns();query_entered.set()
                if not query_release.wait(3):raise TimeoutError('test query not released')
                end=time.monotonic_ns()
                return dict(label=label,start_monotonic_ns=start,end_monotonic_ns=end,elapsed_ns=end-start,cpu_ns=1,resident_bytes=0,objects=objects)
            # Execute the actual method body with fake guest operations; not a rewritten scheduler.
            tree=ast.parse((ROOT/'scripts/run_prettier6604_grpo.py').read_text())
            method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='do_grpo')
            klass=ast.ClassDef(name='Paths',bases=[],keywords=[],body=[method],decorator_list=[])
            module=ast.fix_missing_locations(ast.Module(body=[klass],type_ignores=[]))
            roles=[f'role-{i}' for i in range(7)];creates=[];paths=[]
            namespace=dict(scaling=None,prefix_count=None,private_control=None,roles=roles,shared_files=None,
                physical=None,executed={r:['original'] for r in roles},joined=grpo.joined,asyncio=asyncio,
                full_rootfs=SimpleNamespace(cold=reader))
            exec(compile(module,'actual-do-grpo','exec'),namespace)
            async def exercise():
                ready=asyncio.Event();running=asyncio.Event();obj=namespace['Paths']()
                async def start(role):
                    creates.append(role)
                    if len(creates)==7:ready.set()
                    await ready.wait();return role+'-sid'
                async def path(sid,role,segments):
                    paths.append((sid,role,segments))
                    if len(paths)==7:running.set()
                    await running.wait()
                obj.start=start;obj.run_path=path;obj.phase=lambda label:None
                return await asyncio.wait_for(obj.do_grpo(),2)
            with patch.object(base,'query_objects',side_effect=query):
                reader.start()
                try:
                    self.assertTrue(query_entered.wait(2));value=asyncio.run(exercise())
                    self.assertEqual(len(value),7);self.assertEqual(len(creates),7);self.assertEqual(len(paths),7)
                    self.assertFalse(query_release.is_set())
                finally:query_release.set();reader.stop()
            self.assertEqual([e['label'] for e in reader.events],['guests-and-setup-ready'])

    def test_stop_timeout_keeps_reader_active_then_confirms_reaping_after_release(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)):
            reader=self.fixture(Path(tmp));self.initialize(reader);reader.next_ns=time.monotonic_ns()
            entered=threading.Event();release=threading.Event()
            def query(*args):
                entered.set();release.wait(3);raise OSError('controlled query failure')
            with patch.object(base,'query_objects',side_effect=query):
                reader.start()
                try:
                    self.assertTrue(entered.wait(2))
                    with self.assertRaises(TimeoutError):reader.stop(timeout=.01)
                    self.assertTrue(reader.is_alive());self.assertNotIn('worker_reaped',reader.state)
                finally:
                    release.set();reader.thread.join(2)
                    with self.assertRaisesRegex(RuntimeError,'worker failed'):reader.stop()
            self.assertFalse(reader.is_alive());self.assertTrue(reader.state['worker_reaped'])
            self.assertTrue(reader.state['stop']['reaped']);self.assertEqual(reader.state['status'],'FAIL')

    def test_complete_records_mixed_points_and_costs_without_querying_p1_again(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)):
            reader=self.fixture(Path(tmp));self.initialize(reader)
            origin=reader.state['points'][0]['end_monotonic_ns']
            reader.mark('guest-creation-begin',monotonic_ns=origin+10)
            reader.mark('guests-and-setup-ready',monotonic_ns=origin+20)
            reader.mark('paths-finished',monotonic_ns=origin+30)
            reader.start();reader.stop()
            mixed=dict(label='periodic',start_monotonic_ns=origin+19,end_monotonic_ns=origin+21,elapsed_ns=2,cpu_ns=1,resident_bytes=0)
            p1=dict(label='all-original-paths-finished',start_monotonic_ns=origin+31,end_monotonic_ns=origin+32,elapsed_ns=1,cpu_ns=1,resident_bytes=0)
            reader.state['points'].append(mixed)
            with patch.object(base,'query_objects') as query:reader.complete(p1)
            query.assert_not_called()
            self.assertEqual(mixed['phase'],'MIXED_PHASES');self.assertEqual(p1['phase'],'after-paths')
            self.assertEqual(reader.state['query_total_ns'],sum(p['elapsed_ns'] for p in reader.state['points']))
            self.assertGreater(reader.state['write_cost']['data_wall_ns_before_final_receipt'],0)
            self.assertGreater(reader.state['write_cost']['event_wall_ns'],0)
            self.assertGreaterEqual(reader.state['stop']['end_monotonic_ns'],reader.state['stop']['start_monotonic_ns'])
            self.assertEqual(files.read(reader.out/'host-full-cold-events.json')['events'],reader.events)
            self.assertEqual(files.read(reader.out/'host-full-cold-read.json')['status'],'PASS')

    def test_full_control_keeps_p1_live_users_reaps_before_inventory_and_exports_report(self):
        helper=fixtures.FullRootfsTests()
        for mode,count in [('shared',19),('private',133)]:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)),\
                 patch.object(control,'manifest_layers',side_effect=helper.layers),patch.object(base.os,'posix_fadvise') as advice,\
                 patch.object(files,'counters',return_value={}),patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=0)) as query:
                mount=Path(tmp)/'.artifacts/storage-isolated-service/owned/mount'
                original,guests,roots=helper.observation_fixture(mount,mode);query.reset_mock()
                observer=control.FullRootfsControl(mode,original.out,original.group,original.binding,cold_read=True)
                observer.before();reader=observer.cold
                reader.mark('guest-creation-begin');reader.start();reader.mark('guests-and-setup-ready')
                def inventory(*args):
                    self.assertFalse(reader.is_alive());return roots
                with patch.object(control,'rootfs_inventory',side_effect=inventory),\
                     patch('host_prefix_compare.drives_for_vm',return_value=[]),\
                     patch('host_rollout_shared.opened_regular_files',return_value={}),\
                     patch.object(control,'validate_rootfs_inventory') as post,\
                     patch.object(control.os,'readlink',side_effect=lambda p:'/runtime/guest-'+p.split('/')[2]):
                    query.return_value=dict(stable=True,resident_bytes=4096)
                    value=observer.after(guests,time.monotonic_ns())
                control.validate_observation(value)
                self.assertEqual(query.call_count,count*2);self.assertEqual(advice.call_count,count)
                self.assertEqual(value['P0']['resident_bytes'],0);self.assertEqual(value['P1']['resident_bytes'],count*4096)
                self.assertEqual(len(value['cold_read']['points']),2);self.assertTrue(value['cold_read']['worker_reaped'])
                self.assertTrue(all(not r['users'][0].startswith('planned-') for r in value['P1']['objects']))
                post.assert_called_once_with(roots)
                for bad in ('worker_reaped','live_identity_checks','initial_residency'):
                    changed=copy.deepcopy(value);changed['cold_read'][bad]=False
                    with self.assertRaises(AssertionError):control.validate_observation(changed)
                if mode=='shared':self.report_fixture(observer.out,value)

    def report_fixture(self,raw,value):
        from report_task_overview import write_report
        files.write(raw/'result.json',dict(status='PASS',roles=['backbone'],paths=dict(backbone=dict(official_task_status='FAIL')),
            rollout_start_monotonic_ns=1,first_tool_start_monotonic_ns=2,rollout_end_monotonic_ns=20,
            official_task_directory='/unused',host_full_rootfs_control=value))
        sample=dict(timestamp_ns=10,MemTotal_kib=100,MemAvailable_kib=50,Cached_kib=20,Shmem_kib=0)
        schedule=dict(status='PASS',schedule='grpo',roles=[],actions=189,segments=[],logical_action_count=189,
            checkpoint_count=0,checkpoint_restore={},cleanup={},timing=dict(checkpoint_call_ns=0,restore_request_per_role_ns={},stages=[]))
        with patch('report_task_overview.samples',return_value=({'sid':dict(role='backbone',chunks=[[sample]],sessions=[])},'fixture')),\
             patch('report_task_overview.tool_rows',return_value=([dict(tool_index=1,duration_ns=1)],1)),\
             patch('report_prettier6604_grpo.audit',return_value=(schedule,None,None)) as audit:
            out=raw.parent/'report';out.mkdir();result=write_report(raw,out)
        audit.assert_called_once();self.assertEqual(result['schedule_audit']['actions'],189)
        self.assertEqual(result['physical_page_cache'],'NOT_MEASURED')
        observed=result['host_full_rootfs_control']['cold_read']
        self.assertTrue(observed['worker_reaped']);self.assertEqual(observed['points'],2)
        self.assertEqual(observed['interval_seconds'],5)

    def test_full_initial_failure_and_cold_warm_comparison_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(base,'ROOT',Path(tmp)),\
             patch.object(control,'manifest_layers',side_effect=fixtures.FullRootfsTests().layers),\
             patch.object(base.os,'posix_fadvise'),patch.object(files,'counters',return_value={}),\
             patch.object(files,'mincore_file',return_value=dict(stable=True,resident_bytes=4096)):
            reader=self.fixture(Path(tmp));observer=control.FullRootfsControl('shared',reader.out,reader.group,None,cold_read=True)
            with self.assertRaisesRegex(RuntimeError,'not zero'):observer.before()
            self.assertEqual(observer.state['status'],'FAIL');self.assertFalse(observer.cold.is_alive())
            with self.assertRaises(RuntimeError):observer.cold.start()
        with self.assertRaisesRegex(ValueError,'initial cache policy'):
            run.full_comparison(dict(cold_read_requested=True),{})

    def test_flags_preserve_old_defaults_and_reject_bad_modes_before_side_effects(self):
        for mode in ('shared','private'):
            with patch.object(run,'commands',return_value=([],['bash','entry'])):
                old=run.member_command({},Path('/seed'),mode,Path('/daemon'))
                new=run.member_command({},Path('/seed'),mode,Path('/daemon'),cold_read=True)
                self.assertEqual(new,old+['--host-full-rootfs-cold-read'])
        for mode,schedule,residency in [(None,'grpo','on'),('shared','bpo','on'),('shared','grpo','off')]:
            args=SimpleNamespace(host_full_rootfs_mode=mode,schedule=schedule,host_full_rootfs_residency=residency,host_full_rootfs_cold_read=True)
            with self.assertRaises(ValueError):control.validate_mode(args)
        with patch.object(run,'available') as available,patch.object(run,'daemon_file') as daemon:
            with self.assertRaises(ValueError):run.group(Path('/unused'),'shared',Path('/unused'),'bpo',cold_read=True)
            with self.assertRaises(ValueError):run.pair(Path('/unused'),Path('/unused'),'bpo',cold_read=True)
            available.assert_not_called();daemon.assert_not_called()

    def test_wrapper_and_both_backend_parameter_rebuilds_forward_explicit_flag(self):
        argv=['entry','/unused','--full-rootfs-group','private','--copy-daemon','/unused','--rootfs-cold-read']
        with patch.object(sys,'argv',argv),patch('platform_tasks.configuration',return_value=dict(host_file_monitor=True)),\
             patch('platform_tasks.preflight'),patch.object(workflow.subprocess,'check_output',return_value='systemd'),\
             patch.object(workflow.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as execute:
            self.assertEqual(workflow.main(),0)
        command=execute.call_args_list[0].args[0]
        self.assertEqual(command[-1],'--rootfs-cold-read');self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=on',command)
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for variable in ('check_args','task_args'):
            begin=code.index(variable+"=['--frozen-export'")
            end=code.index('subprocess.run(' if variable=='check_args' else 'call(stage,[sys.executable',begin)
            self.assertIn("if full_rootfs_cold_read:"+variable+"+=['--host-full-rootfs-cold-read']",code[begin:end])

    def test_bad_wrapper_cli_fails_before_configuration_or_docker(self):
        for extra in ([],['--full-rootfs-group','private','--copy-daemon','/unused','--copy-schedule','bpo'],
                      ['--full-rootfs-group','shared','--copy-daemon','/unused','--rootfs-residency','off']):
            p=subprocess.run([sys.executable,'-B',ROOT/'scripts/run_host_file_workflow.py','/unused','--rootfs-cold-read',*extra],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,2);self.assertIn('cold read requires explicit full GRPO',p.stderr)

    def test_pair_forwards_cold_flag_to_both_groups_without_changing_source_lifecycle(self):
        from run_light_monitor_controls import SOURCE_ID
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);modes=[]
            def execute(command,**kwargs):
                if command[0]=='bash':
                    self.assertIn('--rootfs-cold-read',command)
                    mode=command[command.index('--full-rootfs-group')+1];modes.append(mode)
                    out=root/'.artifacts/host-full-rootfs'/mode;out.mkdir(parents=True)
                    files.write(out/'result.json',dict(status='PASS',observation=dict(cold_read_requested=True)))
                    kwargs['stdout'].write('status=PASS result_dir='+str(out)+'\n')
                return SimpleNamespace(returncode=0)
            opener=MagicMock();opener.open.return_value.__enter__.return_value.status=200
            with patch.object(run,'ROOT',root),patch.object(run,'available',return_value=100*1024**3),\
                 patch.object(run,'daemon_file',return_value=Path('/mock/daemon')),\
                 patch.object(run.subprocess,'run',side_effect=execute),\
                 patch.object(run.subprocess,'check_output',side_effect=[json.dumps([dict(Id=SOURCE_ID,State=dict(Running=False))]),json.dumps(dict(Running=False))]),\
                 patch.object(run.urllib.request,'build_opener',return_value=opener),\
                 patch.object(run,'full_comparison',return_value=dict(scope='mock')):
                self.assertEqual(run.pair(Path('/unused'),Path('/unused'),cold_read=True),0)
            self.assertEqual(modes,['shared','private'])
            result=files.read(next((root/'.artifacts/host-full-rootfs-pairs').glob('*/result.json')))
            self.assertTrue(result['source_returned_to_stopped']);self.assertEqual(result['actual_actions'],378)

    def test_actual_cleanup_guard_precedes_guest_deletion_on_worker_timeout(self):
        source=(ROOT/'scripts/run_prettier6604_grpo.py').read_text()
        begin=source.index('        cold_reaped=True\n');end=source.index('        if light_monitor or not monitoring:cleanup',begin)
        fragment='\n'.join(line[8:] for line in source[begin:end].splitlines())
        for alive in (True,False):
            with self.subTest(alive=alive):
                class Reader:
                    def stop(self):raise TimeoutError('active')
                    def is_alive(self):return alive
                saved=[];observer=SimpleNamespace(cold=Reader(),state={},save=lambda:saved.append(1))
                context=dict(full_rootfs=observer,cleanup={},success=True,result={})
                exec(compile(fragment,'actual-cleanup-guard','exec'),context)
                self.assertFalse(context['success']);self.assertEqual(context['cold_reaped'],not alive)
                self.assertEqual(context['cleanup']['cold_reader_reaped'],not alive)
                self.assertEqual(observer.state['status'],'FAIL');self.assertEqual(saved,[1])
        self.assertIn('        if controller and cold_reaped:\n',source)
        self.assertLess(end,source.index('closed=parallel_guest_cleanup',end))


if __name__=='__main__':unittest.main()
