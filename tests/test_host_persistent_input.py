"""Toy files and mocked device/service commands only. No real archive, cgroup or mincore."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_persistent_input as h
from validate_isolated_agentenv import IMAGE,immutable_manifest


class PersistentInputTests(unittest.TestCase):
    def fixture(self,base,profile='prettier6604'):
        task=h.TASKS[profile]
        out=base/'.artifacts'/task['cache_root']/'host-persistent-test';out.mkdir(parents=True)
        group=base/'group';group.mkdir();src=out/'readonly/env'
        for part in h.PARTS:
            p=src/part;p.mkdir(parents=True);(p/'toy').write_bytes(b'toy input')
        parts={p:immutable_manifest(src/p) for p in h.PARTS}
        h.write(out/'source.json',dict(status='PASS',profile=h.PROFILE,mounted=True,loop_device='/dev/loop99',parts=parts,
            task_id=task['task_id'],server_image=IMAGE,source_backup='toy archive',
            **(dict(task_image=h.task_image(task['task_id'])) if profile!='prettier6604' else {})))
        (out/'source.ext4').write_bytes(b'toy image')
        state=dict(mounted=True,attached=True,events=[],mount=out/'readonly')
        def command(argv,timeout=60):
            state['events'].append(list(map(str,argv)))
            if argv[0]=='cp':shutil.copytree(Path(argv[-2]),Path(argv[-1]));return ''
            if argv[:2]==['losetup','-j']:return '/dev/loop99: fixture' if state['attached'] else ''
            if argv[0]=='findmnt':return json.dumps(dict(filesystems=[dict(source='/dev/loop99',target=str(out/'readonly'))]))
            if argv[0]=='umount':state['mounted']=False;return ''
            if argv[:2]==['losetup','-d']:state['attached']=False;return ''
            raise AssertionError('unexpected command '+str(argv))
        return out,group,state,command

    def context(self,base,group,state,command):
        stack=ExitStack();stack.enter_context(patch.object(h,'ROOT',base));stack.enter_context(patch.object(h,'require_budget',return_value=dict(ready=True)))
        stack.enter_context(patch.object(h,'mount_identity',return_value={}))
        stack.enter_context(patch('host_file_monitor.measurement_group',return_value=(group,'fake.slice')))
        stack.enter_context(patch.object(h,'call',side_effect=command))
        original=Path.read_text
        stack.enter_context(patch.object(Path,'read_text',lambda p,*a,**k:'1\n' if str(p)=='/sys/class/block/loop99/ro' else original(p,*a,**k)))
        stack.enter_context(patch.object(Path,'is_mount',lambda p:p==state['mount'] and state['mounted']))
        return stack

    def test_import_creates_new_inodes_then_releases_scratch_and_validates_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base)
            with self.context(base,group,state,command),patch.object(h.os,'fsync',wraps=os.fsync) as sync:
                h.import_input(out);seed,root=h.validate_seed(out)
                self.assertEqual(seed['status'],'PASS');self.assertEqual(seed['group_id'],group.stat().st_ino)
                self.assertFalse((out/'source.ext4').exists());self.assertFalse(state['mounted']);self.assertFalse(state['attached'])
                self.assertEqual(len(seed['files']),6);self.assertGreater(sync.call_count,6)
                events=state['events'];last_copy=max(i for i,e in enumerate(events) if e[0]=='cp');unmount=next(i for i,e in enumerate(events) if e[0]=='umount')
                self.assertLess(last_copy,unmount);self.assertTrue(all('sync'!=e[0] for e in events))
                (root/'deps/toy').write_bytes(b'changed size')
                with self.assertRaises(AssertionError):h.validate_seed(out)

    def test_repeated_success_cleanup_preserves_first_release_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base)
            with self.context(base,group,state,command):
                h.import_input(out);first=h.read(out/'source.json')
                h.release_source(out)
                self.assertEqual(h.read(out/'source.json'),first)
                self.assertEqual(h.validate_seed(out)[0]['status'],'PASS')

    def test_import_rejects_existing_target_without_touching_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base);(out/'input').mkdir();marker=out/'input/keep';marker.write_text('keep')
            with self.context(base,group,state,command):
                with self.assertRaises(FileExistsError):h.import_input(out)
            self.assertEqual(marker.read_text(),'keep');self.assertTrue((out/'source.ext4').exists());self.assertEqual(state['events'],[])

    def test_failed_copy_retains_input_and_scratch_but_owned_release_removes_only_devices(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base)
            def fail(argv,timeout=60):
                if argv[0]=='cp':raise RuntimeError('copy failure')
                return command(argv,timeout)
            with self.context(base,group,state,fail):
                with self.assertRaisesRegex(RuntimeError,'copy failure'):h.import_input(out)
                self.assertEqual(h.read(out/'seed.json')['status'],'FAIL')
                h.release_source(out)
            self.assertTrue((out/'source.ext4').exists());self.assertTrue((out/'input').exists());self.assertFalse(state['attached']);self.assertFalse(state['mounted'])

    def test_interrupted_attach_is_found_by_exact_backing_file_not_stale_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base);v=h.read(out/'source.json');v['loop_device']=None;h.write(out/'source.json',v)
            with self.context(base,group,state,command):h.release_source(out)
            self.assertFalse(state['attached']);self.assertTrue((out/'source.ext4').exists())
            self.assertEqual(state['events'][0],['losetup','-j',str(out/'source.ext4')])

    def test_success_input_cleanup_requires_valid_seed_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=self.fixture(base)
            with self.context(base,group,state,command):h.import_input(out);h.release_input(out)
            self.assertFalse((out/'input').exists());self.assertTrue(h.read(out/'seed.json')['released']);self.assertTrue((out/'source.json').exists())

    def test_budget_is_strict_and_bad_options_have_no_side_effects(self):
        with patch.object(h,'available',return_value=54*h.GIB):
            with self.assertRaises(RuntimeError):h.require_budget(28*h.GIB)
        with patch.object(h,'available',return_value=54*h.GIB+1):self.assertTrue(h.require_budget(28*h.GIB)['ready'])
        self.assertIsNone(h.native_option(SimpleNamespace()))
        attrs=dict(persistent_cache_seed=Path('/seed'),host_current_pair=True,host_scaling_mode='cold',light_monitor=True,host_file_monitor=True)
        self.assertEqual(h.native_option(SimpleNamespace(**attrs)),Path('/seed'))
        for k,v in [('host_current_pair',False),('host_scaling_mode','restore'),('light_monitor',False),('host_file_monitor',False),('immutable_cache_seed','/ram'),('damon_observe',True)]:
            with self.assertRaises(ValueError):h.native_option(SimpleNamespace(**dict(attrs,**{k:v})))
        with tempfile.TemporaryDirectory() as tmp,patch.object(h,'ROOT',Path(tmp)),patch.object(h,'require_budget',side_effect=RuntimeError('budget')),patch.object(h,'call') as device:
            out=Path(tmp)/'.artifacts/prettier6604/host-persistent-test';out.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError,'budget'):h.prepare(out)
            self.assertFalse((out/'source.ext4').exists());device.assert_not_called()

    def test_prepare_preserves_preallocation_and_restores_outside_group_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out=base/'.artifacts/prettier6604/host-persistent-test';out.mkdir(parents=True);backup=base/'backup';backup.mkdir()
            content=b'archive toy';(backup/'volume.ext4.gz').write_bytes(content);expected=b'image'+bytes(20*512-5)
            h.write(backup/'volume-backup.json',dict(status='PASS',source_bytes=20*512,archive_sha256=hashlib.sha256(content).hexdigest(),source_sha256=hashlib.sha256(expected).hexdigest()))
            def command(argv,timeout=60):
                if argv[0]=='dumpe2fs':return 'Filesystem state:         clean'
                if argv[:2]==['losetup','--find']:return '/dev/loop99'
                if argv[0]=='mount':
                    for p in h.PARTS:
                        d=out/'readonly/env'/p;d.mkdir(parents=True);(d/'toy').write_bytes(b'data')
                    return ''
                raise AssertionError(argv)
            def decompress(argv,**kwargs):kwargs['stdout'].write(b'image');return SimpleNamespace(returncode=0)
            with patch.object(h,'ROOT',base),patch.object(h,'BACKUP',backup),patch.object(h,'GIB',512),patch.object(h,'require_budget',return_value={'ready':True}),\
                 patch.object(h,'call',side_effect=command),patch.object(h.subprocess,'run',side_effect=decompress),patch.dict(os.environ,{},clear=True):
                h.prepare(out)
                self.assertEqual((out/'source.ext4').read_bytes(),expected)
                self.assertGreaterEqual((out/'source.ext4').stat().st_blocks*512,len(expected))
                self.assertEqual(h.read(out/'source.json')['status'],'PASS')
                self.assertEqual(h.read(out/'source.json')['source_logical_bytes'],20*512)
                self.assertGreaterEqual(h.read(out/'source.json')['source_allocated_bytes'],20*512)
            with patch.dict(os.environ,ASB_HOST_FILE_SLICE='existing.slice'),patch.object(h,'require_budget') as budget:
                other=base/'.artifacts/prettier6604/host-persistent-other'
                with patch.object(h,'ROOT',base),self.assertRaisesRegex(ValueError,'outside'):h.prepare(other)
                budget.assert_not_called()

    def test_mincore_check_deduplicates_inodes_and_reports_shortfall_without_claiming_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);group=root/'group';group.mkdir();(root/'deps').mkdir();(root/'deps/a').write_bytes(b'a');os.link(root/'deps/a',root/'deps/b');(root/'deps/c').write_bytes(b'c')
            parts={'deps':{n:{} for n in ('a','b','c')}};files=h.identities(root,parts);seed=dict(parts=parts,files=files)
            def query(p):return dict(h.identities(root,{'deps':{p.name:{}}})['deps/'+p.name],stable=True,resident_bytes=4096)
            with patch.object(h,'validate_seed',return_value=(seed,root)),patch('host_file_monitor.counters',return_value=dict(file=4096)),patch('host_file_monitor.mincore_file',side_effect=query) as mincore:
                value=h.observe_input(root,group,root/'check.json')
            self.assertEqual(value['status'],'OBSERVED');self.assertEqual(mincore.call_count,2);self.assertEqual(value['resident_bytes'],8192)
            self.assertEqual(value['shortfall_bytes'],4096);self.assertEqual(value['ownership'],'NOT_QUERIED')
            with patch.object(h,'validate_seed',return_value=(seed,root)),patch('host_file_monitor.counters',return_value=dict(file=4096)),patch('host_file_monitor.mincore_file',side_effect=RuntimeError('query error')):
                value=h.observe_input(root,group,root/'failure.json')
            self.assertEqual(value['status'],'FAIL');self.assertIsNone(value['resident_bytes']);self.assertGreaterEqual(value['thread_cpu_ns'],0)


class RoutingTests(unittest.TestCase):
    def test_native_command_only_changes_seed_switch(self):
        from platform_tasks import configuration
        from run_host_scaling import scaling_command
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        old=scaling_command(cfg,Path('/seed'),'cold',current_pair=True)
        new=scaling_command(cfg,Path('/seed'),'cold',current_pair=True,persistent_input=True)
        self.assertEqual(new,[('--persistent-cache-seed' if v=='--immutable-cache-seed' else v) for v in old])
        with self.assertRaises(ValueError):scaling_command(cfg,Path('/seed'),'cold',persistent_input=True)

    def test_wrapper_prepares_outside_slice_and_cleans_source_after_service_unit(self):
        import run_host_file_workflow as wrapper
        with tempfile.TemporaryDirectory() as tmp:
            events=[]
            def phase(action,path,timeout):
                events.append(action)
                if action=='prepare':
                    self.assertTrue(path.is_dir());self.assertEqual(path.stat().st_mode & 0o777,0o700)
            def run(argv,**kw):events.append('slice' if argv[0]=='systemd-run' else 'stop');return SimpleNamespace(returncode=0)
            argv=['entry',str(ROOT/'configs/platform-prettier6604-host-file.json'),'--current-pair','--persistent-input']
            with patch.object(wrapper,'ROOT',Path(tmp)),patch.object(sys,'argv',argv),patch('platform_tasks.preflight',return_value={}),\
                 patch.object(wrapper.subprocess,'check_output',return_value='systemd\n'),patch.object(wrapper.subprocess,'run',side_effect=run) as launch,patch.object(h,'run_phase',side_effect=phase):
                self.assertEqual(wrapper.main(),0)
            self.assertEqual(events,['prepare','slice','stop','release-source']);self.assertIn('--persistent-seed',launch.call_args_list[0].args[0])

    def test_invalid_mode_rejected_before_docker_and_prepare_failure_never_creates_slice(self):
        import run_host_file_workflow as wrapper
        with patch.object(sys,'argv',['entry','unused','--persistent-input','--restore-pair']),patch.object(wrapper.subprocess,'check_output') as docker:
            with self.assertRaises(SystemExit):wrapper.main()
            docker.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            events=[]
            def phase(action,path,timeout):
                events.append(action)
                if action=='prepare':raise RuntimeError('prepare failure')
            with patch.object(wrapper,'ROOT',Path(tmp)),patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-file.json'),'--current-pair','--persistent-input']),\
                 patch('platform_tasks.preflight',return_value={}),patch.object(wrapper.subprocess,'check_output',return_value='systemd\n'),patch.object(wrapper.subprocess,'run') as launch,patch.object(h,'run_phase',side_effect=phase):
                with self.assertRaisesRegex(RuntimeError,'prepare failure'):wrapper.main()
                launch.assert_not_called()
            self.assertEqual(events,['prepare','release-source'])

    def test_existing_outer_directory_is_neither_used_nor_cleaned(self):
        import run_host_file_workflow as wrapper
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);old=base/'.artifacts/prettier6604/host-persistent-asbhost-fixed7';old.mkdir(parents=True);(old/'keep').write_text('keep')
            argv=['entry',str(ROOT/'configs/platform-prettier6604-host-file.json'),'--current-pair','--persistent-input']
            with patch.object(wrapper,'ROOT',base),patch.object(sys,'argv',argv),patch('platform_tasks.preflight',return_value={}),\
                 patch.object(wrapper.subprocess,'check_output',return_value='systemd\n'),patch.object(wrapper.subprocess,'run') as launch,\
                 patch.object(wrapper.time,'strftime',return_value='fixed'),patch.object(wrapper.os,'getpid',return_value=7),patch.object(h,'run_phase') as phase:
                with self.assertRaises(FileExistsError):wrapper.main()
                phase.assert_not_called();launch.assert_not_called()
            self.assertEqual((old/'keep').read_text(),'keep')


class WorkflowTests(unittest.TestCase):
    def workflow(self,base,fail=None):
        import run_host_scaling as runner
        from platform_tasks import configuration
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        scripts=base/'scripts';scripts.mkdir()
        for name in ('run_host_scaling.py','host_scaling.py','run_host_file_workflow.py','run_prettier6604_grpo.py',
                'validate_isolated_agentenv.py','host_independent_task.py','host_prefix_compare.py','run_host_file_restore.py','host_current_pair.py','host_persistent_input.py'):
            (scripts/name).write_text('# fixture\n')
        group=base/'group';group.mkdir();cache=base/'.artifacts/prettier6604/host-persistent-test';cache.mkdir(parents=True)
        h.write(cache/'source.json',dict(status='PASS'));storage=base/'.artifacts/storage-isolated-service/test';storage.mkdir(parents=True)
        h.write(storage/'result.json',dict(status='PASS',host_scaling_preparation_sync=dict(status='PASS',end_monotonic_ns=1),
            host_file_creation=dict(verified_before_guest_creation_ns=2),cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed',
                'successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')}))
        events=[]
        class Collector:
            def __init__(self,*a,**kw):events.append('collector-start')
            def stop(self):events.append('collector-stop');return dict(status='PASS')
        def phase(action,path,timeout):
            events.append(action)
            if action=='import':
                h.write(cache/'seed.json',dict(status='FAIL' if fail=='import' else 'PASS'))
                (cache/'input').mkdir()
                if fail=='import':raise RuntimeError('import failure')
            elif action=='release-input':
                shutil.rmtree(cache/'input');h.write(cache/'seed.json',dict(status='PASS',released=True))
            else:raise AssertionError(action)
        def native(argv,**kw):
            self.assertIn('--persistent-cache-seed',argv);self.assertNotIn('--immutable-cache-seed',argv);events.append('native')
            if fail=='native':raise __import__('subprocess').CalledProcessError(1,argv)
            kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n')
            return SimpleNamespace(returncode=0)
        def report(*unused):
            events.append('report')
            if fail=='report':raise RuntimeError('report failure')
            return dict(status='PASS_CURRENT_PAIR',input_file_check=dict(status='FAIL' if fail=='query' else 'OBSERVED',shortfall_bytes=None if fail=='query' else 0))
        with patch.object(runner,'ROOT',base),patch.object(h,'ROOT',base),patch.object(runner,'configuration',return_value=cfg),\
             patch.object(runner,'preflight',return_value={}),patch('isolated_storage.available',return_value=100*h.GIB),\
             patch('host_file_monitor.measurement_group',return_value=(group,'fake.slice')),patch('host_file_monitor.HostFileMonitor',Collector),\
             patch.object(h,'run_phase',side_effect=phase),patch.object(runner.subprocess,'run',side_effect=native),patch('host_current_pair.report',side_effect=report):
            code=runner.workflow(Path('unused'),'cold',current_pair=True,persistent_seed=cache)
        results=list((base/'.artifacts/host-scaling').glob('*/result.json'));self.assertEqual(len(results),1)
        return code,h.read(results[0]),cache,events

    def test_success_imports_then_runs_reports_and_releases_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            code,state,cache,events=self.workflow(Path(tmp))
            self.assertEqual(code,0);self.assertEqual(state['status'],'PASS');self.assertTrue(state['cache_released'])
            self.assertEqual(events,['collector-start','import','native','collector-stop','report','release-input'])
            self.assertFalse((cache/'input').exists());self.assertIn('archive restoration is outside',state['measurement_scope'])

    def test_import_native_and_report_failures_keep_evidence_and_stop_collector(self):
        for fail in ('import','native','report','query'):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                code,state,cache,events=self.workflow(Path(tmp),fail)
                self.assertEqual(code,1);self.assertEqual(state['status'],'FAIL');self.assertIn('collector-stop',events)
                self.assertNotIn('release-input',events);self.assertTrue((cache/'input').exists())
                if fail=='import':self.assertNotIn('native',events)
                if fail=='query':self.assertEqual(state['failed_stage'],'persistent-input-check')


if __name__=='__main__':unittest.main()
