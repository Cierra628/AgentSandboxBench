"""Toy files, saved config metadata and mocked services only; no live workload or device."""
import ast
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import host_persistent_input as h
import platform_tasks as platform
import run_host_file_workflow as wrapper
import use_platform
from validate_isolated_agentenv import ordinary_current_only
import test_host_persistent_input as fixtures

PROFILES=tuple(h.TASKS)
CLEANUP={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
    'original_service_unchanged','original_firecracker_identities_unchanged')}

def config(profile):return platform.configuration(ROOT/f'configs/platform-{profile}-host-current.json')
def write(p,v):p.write_text(json.dumps(v)+'\n')
def args(profile,**changes):
    values=dict(persistent_cache_seed=Path('/seed'),host_current_only=True,host_file_monitor=True,light_monitor=True)
    if profile=='prettier6604':values.update(prettier6604_task=Path('/inputs'),prettier6604_readonly_replay=True)
    else:values.update(python_backbone_inputs=Path('/inputs'),python_task_profile=profile,python_readonly_replay=True,python_persistent_sampling=True)
    values.update(changes);return SimpleNamespace(**values)


class PersistentTaskRoutingTests(unittest.TestCase):
    def test_commands_change_seed_switch_only_and_keep_full_tasks_and_policies(self):
        for profile in PROFILES:
            cfg=config(profile);old_check,old=platform.commands(cfg,Path('/seed'))
            check,new=platform.commands(cfg,Path('/seed'),persistent_input=True)
            self.assertEqual(check,old_check)
            self.assertEqual(new,[('--persistent-cache-seed' if v=='--immutable-cache-seed' else v) for v in old])
            self.assertNotIn('--host-persistent-seed',new);self.assertNotIn('--host-current-pair',new)
            self.assertEqual(cfg['replay_policy'],{'prettier6604':'strict','sphinx7590':'sphinx-pipeline-v2','xarray6992':'xarray-approved-sigpipe'}[profile])
        for change in ({'host_current_only':False},{'monitor_mode':'off'},{'damon_observe':True},{'light_monitor':False},
                       {'rollout':'grpo'},{'checkpoint_after_step':24},{'host_file_monitor':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):platform.commands(dict(config('prettier6604'),**change),Path('/seed'),persistent_input=True)
        with self.assertRaises(ValueError):h.single_config(dict(config('sphinx7590'),cache_backup_directory='/wrong-source'))

    def test_native_options_accept_tasks_and_reject_mixed_or_unsupported_modes(self):
        for profile in PROFILES:
            good=args(profile);self.assertTrue(ordinary_current_only(good));self.assertEqual(h.native_option(good),Path('/seed'))
            for key,value in [('immutable_cache_seed',Path('/ram')),('host_current_only',False),('light_monitor',False),
                              ('host_file_monitor',False),('python_checkpoint',True),('damon_disable',True),
                              ('prettier6604_export','/export'),('host_scaling_mode','restore'),
                              ('prettier6604_monitor_mode' if profile=='prettier6604' else 'python_monitor_mode','off')]:
                with self.subTest(profile=profile,key=key),self.assertRaises(ValueError):h.native_option(args(profile,**{key:value}))

    def test_budgets_are_task_specific_and_strict(self):
        for profile,minimum in zip(PROFILES,(54,56,62)):
            t=h.TASKS[profile]
            with patch.object(h,'available',return_value=minimum*h.GIB):
                with self.assertRaises(RuntimeError):h.require_budget((t['source_gib']+t['input_gib'])*h.GIB)
            with patch.object(h,'available',return_value=minimum*h.GIB+1):self.assertTrue(h.require_budget((t['source_gib']+t['input_gib'])*h.GIB)['ready'])

    def test_outer_routes_three_sources_before_the_group_and_forwards_only_internal_seed(self):
        for profile in PROFILES:
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);events=[]
                def phase(action,out,timeout,**kw):
                    events.append(action)
                    if action=='prepare':
                        self.assertEqual(kw['task_profile'],profile);self.assertEqual(out.parent,base/'.artifacts'/h.TASKS[profile]['cache_root'])
                def run(argv,**kw):
                    events.append('group' if argv[0]=='systemd-run' else 'stop')
                    if argv[0]=='systemd-run':
                        self.assertIn('--persistent-seed',argv);self.assertIn('run',argv);self.assertNotIn('--current-pair',argv)
                    return SimpleNamespace(returncode=0)
                with patch.object(wrapper,'ROOT',base),patch.object(sys,'argv',['entry',str(ROOT/f'configs/platform-{profile}-host-current.json'),'--persistent-input']),\
                     patch('platform_tasks.preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),\
                     patch.object(wrapper.subprocess,'run',side_effect=run),patch.object(h,'run_phase',side_effect=phase):self.assertEqual(wrapper.main(),0)
                self.assertEqual(events,['prepare','group','stop','release-source'])

    def test_wrapper_rejects_unsupported_configs_before_docker(self):
        for name in ('prettier6604-host-file','sphinx7590-host-current-off','xarray6992-host-current-off'):
            with self.subTest(name=name),patch.object(sys,'argv',['entry',str(ROOT/f'configs/platform-{name}.json'),'--persistent-input']),\
                 patch.object(wrapper.subprocess,'check_output') as docker,patch('platform_tasks.preflight') as preflight:
                with self.assertRaises(ValueError):wrapper.main()
                docker.assert_not_called();preflight.assert_not_called()

    def test_internal_cli_forwards_new_option_and_keeps_old_call(self):
        path=ROOT/'configs/platform-prettier6604-host-current.json'
        for seed in (None,Path('/seed')):
            argv=['entry','run',str(path)]+(['--persistent-seed',str(seed)] if seed else [])
            with patch.object(sys,'argv',argv),patch.object(use_platform.signal,'signal'),patch.object(platform,'run',return_value=0) as run:
                self.assertEqual(use_platform.main(),0)
                if seed:self.assertEqual(run.call_args.kwargs,dict(persistent_seed=seed))
                else:self.assertEqual(run.call_args.kwargs,{})


class PersistentTaskFileTests(unittest.TestCase):
    def test_python_prepare_uses_original_metadata_format_and_exact_source_capacity(self):
        for profile in ('sphinx7590','xarray6992'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);task=h.TASKS[profile];out=base/'.artifacts'/task['cache_root']/'host-persistent-toy';out.mkdir(parents=True)
                backup=base/'.artifacts/storage-isolated-service'/task['backup'];backup.mkdir(parents=True)
                archive=b'toy archive';(backup/'volume.ext4.gz').write_bytes(archive);image=b'image'+bytes(task['source_gib']*512-5)
                metadata=dict(status='PASS',source_bytes=len(image),archive_sha256=hashlib.sha256(archive).hexdigest(),source_sha256=hashlib.sha256(image).hexdigest())
                def command(argv,timeout=60):
                    if argv[0]=='dumpe2fs':return 'Filesystem state:         clean'
                    if argv[:2]==['losetup','--find']:return '/dev/loop99'
                    if argv[0]=='mount':
                        for part in h.PARTS:
                            d=out/'readonly/env'/part;d.mkdir(parents=True);(d/'toy').write_bytes(b'toy')
                        return ''
                    raise AssertionError(argv)
                def decompress(argv,**kw):kw['stdout'].write(b'image');return SimpleNamespace(returncode=0)
                with patch.object(h,'ROOT',base),patch.object(h,'GIB',512),patch.object(h,'require_budget',return_value={'ready':True}) as budget,\
                     patch('prepare_prettier6604_cache.xarray_backup_metadata',return_value=metadata) as normalize,\
                     patch.object(h,'call',side_effect=command),patch.object(h.subprocess,'run',side_effect=decompress),patch.dict(h.os.environ,{},clear=True):
                    h.prepare(out,profile)
                normalize.assert_called_once_with(backup,profile);budget.assert_called_once_with((task['source_gib']+task['input_gib'])*512)
                value=h.read(out/'source.json');self.assertEqual(value['task_id'],task['task_id']);self.assertEqual(value['task_image'],h.task_image(task['task_id']))
                self.assertEqual((out/'source.ext4').read_bytes(),image);self.assertGreaterEqual(value['source_allocated_bytes'],len(image))

    def test_python_import_checks_task_image_and_new_files_then_cleans_successfully(self):
        helper=fixtures.PersistentInputTests()
        for profile in ('sphinx7590','xarray6992'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as tmp:
                base=Path(tmp);out,group,state,command=helper.fixture(base,profile);task=h.TASKS[profile]
                with helper.context(base,group,state,command),patch.object(h,'require_budget',return_value={'ready':True}) as budget:
                    h.import_input(out);seed,root=h.validate_seed(out,task_id=task['task_id'])
                    budget.assert_called_once_with(task['input_gib']*h.GIB)
                    self.assertEqual(len(seed['files']),6);self.assertFalse((out/'source.ext4').exists())
                    with self.assertRaises(AssertionError):h.validate_seed(out)
                    seed['task_image']='wrong';h.write(out/'seed.json',seed)
                    with self.assertRaises(AssertionError):h.validate_seed(out,task_id=task['task_id'])
                    seed['task_image']=h.task_image(task['task_id']);h.write(out/'seed.json',seed)
                    h.release_input(out);self.assertFalse((out/'input').exists())

    def test_python_query_deduplicates_records_cost_and_preserves_failure_unknown(self):
        helper=fixtures.PersistentInputTests();profile='xarray6992'
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);out,group,state,command=helper.fixture(base,profile);task=h.TASKS[profile]
            with helper.context(base,group,state,command):
                h.import_input(out);seed,root=h.validate_seed(out,task_id=task['task_id'])
                original=root/'deps/toy';alias=root/'deps/alias';h.os.link(original,alias)
                seed['parts']['deps']['alias']=seed['parts']['deps']['toy'];seed['files']=h.identities(root,seed['parts']);h.write(out/'seed.json',seed)
                def query(p):return dict(device=p.stat().st_dev,inode=p.stat().st_ino,size=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns,stable=True,resident_bytes=4096)
                with patch('host_file_monitor.counters',return_value=dict(file=6*4096)),patch('host_file_monitor.mincore_file',side_effect=query) as mincore:
                    value=h.observe_input(out,group,out/'query.json',task_id=task['task_id'])
                self.assertEqual(mincore.call_count,6);self.assertEqual(value['resident_bytes'],6*4096);self.assertEqual(value['shortfall_bytes'],0)
                self.assertEqual(value['path_count'],7);self.assertEqual(value['unique_file_count'],6)
                self.assertGreaterEqual(value['end_monotonic_ns'],value['start_monotonic_ns']);self.assertGreaterEqual(value['thread_cpu_ns'],0)
                self.assertEqual(value['ownership'],'NOT_QUERIED')
                with patch('host_file_monitor.counters',return_value=dict(file=0)),patch('host_file_monitor.mincore_file',side_effect=RuntimeError('query failure')):
                    value=h.observe_input(out,group,out/'failed-query.json',task_id=task['task_id'])
                self.assertEqual(value['status'],'FAIL');self.assertIsNone(value['resident_bytes']);self.assertIsNone(value['shortfall_bytes'])

    def test_actual_native_sync_branch_only_adds_sync_for_new_single_mode(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='scaling' and
            any(isinstance(x,ast.Assign) and "host_scaling_preparation_sync" in ast.unparse(x) for x in ast.walk(ast.Module(body=n.body,type_ignores=[]))))
        code=compile(ast.Module(body=[node],type_ignores=[]),'<actual preparation sync>','exec')
        with patch('host_scaling.sync_preparation',return_value=dict(status='PASS')) as sync:
            receipt={};exec(code,dict(scaling=None,persistent_seed=Path('/new'),local_no_proxy=False,density=None,receipt=receipt,call='call',mount='owned-volume'))
            self.assertEqual(receipt['persistent_input_preparation_sync']['status'],'PASS');sync.assert_called_once_with('call','owned-volume')
            sync.reset_mock();receipt={};exec(code,dict(scaling=None,persistent_seed=None,external_config=None,receipt=receipt))
            self.assertEqual(receipt,{});sync.assert_not_called()


class PersistentTaskWorkflowTests(unittest.TestCase):
    def workflow(self,base,profile='prettier6604',failure=None,short=False,local=False,off=False):
        cfg=config(profile);cfg['output_root']=str(base/'.artifacts/platform-tasks')
        if off:cfg.update(monitor_mode='off',light_monitor=False)
        if profile!='prettier6604':cfg['cache_backup_directory']=str(base/'.artifacts/storage-isolated-service'/h.TASKS[profile]['backup'])
        task=h.TASKS[profile];cache=base/'.artifacts'/task['cache_root']/'host-persistent-workflow';cache.mkdir(parents=True)
        write(cache/'source.json',dict(status='PASS',task_id=cfg['task_id']))
        group=base/'group';group.mkdir();events=[];seed=dict(profile=h.PROFILE,task_id=cfg['task_id'],group=str(group),group_id=group.stat().st_ino,
            import_start_monotonic_ns=1,import_end_monotonic_ns=2,scratch_removed_before_runtime=True)
        storage=base/'.artifacts/storage-isolated-service/toy';storage.mkdir(parents=True)
        write(storage/'result.json',dict(status='PASS',cleanup=CLEANUP,persistent_cache_seed=seed,
            persistent_input_preparation_sync=dict(status='PASS',start_monotonic_ns=3,end_monotonic_ns=4),
            host_file_creation=dict(cgroup=str(group),verified_before_guest_creation_ns=5,host_sampling='off' if off else 'on',
                before_guest_counters=None if off else dict(current=10)),host_file_residency=dict(status='NOT_REQUESTED')))
        if failure in ('unexpected-baseline','missing-baseline','host-on'):
            outer=json.loads((storage/'result.json').read_text())
            if failure=='host-on':outer['host_file_creation']['host_sampling']='on'
            else:outer['host_file_creation']['before_guest_counters']=dict(current=10) if failure=='unexpected-baseline' else None
            write(storage/'result.json',outer)
        if short or local:
            outer=json.loads((storage/'result.json').read_text());outer['local_no_proxy']=dict(guest_check='PASS')
            if failure=='proxy-proof':outer['local_no_proxy']['guest_check']='FAIL'
            write(storage/'result.json',outer)
        scripts=base/'scripts';scripts.mkdir()
        for name in ('platform_tasks.py','use_platform.py','use-platform.sh','host_file_monitor.py','experiment_memory.py','report_host_file.py',
                     'run_host_file_workflow.py','run-host-file-monitor.sh','host_persistent_input.py'):(scripts/name).write_text('# toy\n')
        if short or local:(scripts/'prettier_no_proxy.py').write_text('# toy\n')
        class Collector:
            def __init__(self,*a,**kw):events.append('collector-start');assert kw['include_pss'] is False
            def stop(self):events.append('collector-stop');return dict(status='FAIL' if failure in ('collector','query-and-collector') else 'PASS',thread_cpu_ns=13,counter_read_total_ns=11)
        def phase(action,path,timeout):
            events.append(action)
            if action=='import':
                (cache/'input').mkdir();write(cache/'seed.json',dict(seed,status='PASS'))
                if failure=='import':raise RuntimeError('import failure')
            elif action=='release-input':
                if failure=='cleanup':raise RuntimeError('cleanup failure')
                if failure=='cleanup-false':
                    write(cache/'seed.json',dict(seed,status='PASS',released=False));return
                shutil.rmtree(cache/'input');write(cache/'seed.json',dict(seed,status='PASS',released=True))
            else:raise AssertionError(action)
        def native(argv,**kw):
            events.append('native');self.assertIn('--persistent-cache-seed',argv);self.assertNotIn('--immutable-cache-seed',argv)
            self.assertEqual('--local-no-proxy-check' in argv,short)
            if failure=='native':raise RuntimeError('native failure')
            events.append('native-cleanup-complete');kw['stdout'].write('status=PASS result_dir='+str(storage)+'\n')
            return SimpleNamespace(returncode=0)
        def query(path,actual_group,output,**kw):
            self.assertEqual(kw['task_id'],cfg['task_id']);self.assertEqual(actual_group,group);self.assertEqual(events[-1],'native-cleanup-complete')
            events.append('query');now=time.monotonic_ns()
            query_failed=failure in ('query','query-and-collector')
            value=dict(status='FAIL' if query_failed else 'OBSERVED',shortfall_bytes=4096 if failure=='shortfall' else None if query_failed else 0,
                resident_bytes=None if query_failed else 4096,thread_cpu_ns=17,start_monotonic_ns=now,end_monotonic_ns=now+1,
                group=str(group),group_id=group.stat().st_ino,ownership='NOT_QUERIED')
            write(output,value);return value
        def report(out,destination):
            events.append('report');self.assertTrue((cache/'input').exists())
            if failure=='report':raise RuntimeError('report failure')
            destination.mkdir();write(destination/'result.json',dict(status='PASS',official_task_status='FAIL',strict_replay_status='ORIGINAL',cleanup=CLEANUP));return destination
        with patch.object(platform,'ROOT',base),patch.object(h,'ROOT',base),patch.object(platform,'preflight',return_value={}),\
             patch('host_file_monitor.measurement_group',return_value=(group,'toy.slice')),patch('host_file_monitor.HostFileMonitor',Collector),\
             patch.object(h,'run_phase',side_effect=phase),patch.object(h,'validate_seed',return_value=(seed,cache/'input/env')),\
             patch.object(h,'observe_input',side_effect=query),patch.object(platform,'report',side_effect=report),\
             patch.object(platform.subprocess,'check_output',return_value='saved-git-version'),patch.object(platform.subprocess,'run',side_effect=native):
            result=platform.run(cfg,persistent_seed=cache)
        out=next((base/'.artifacts/platform-tasks').glob('[0-9]*/result.json'))
        return result,json.loads(out.read_text()),cache,events,out.parent

    def test_short_check_skips_input_query_but_preserves_cleanup_order(self):
        import os
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'on'}),patch('prettier_no_proxy.check_cache',return_value=dict(status='PASS')):
            code,value,cache,events,out=self.workflow(Path(tmp),short=True)
            self.assertEqual(code,0);self.assertEqual(value['input_file_validation'],'NOT_REQUESTED_SHORT_CHECK')
            self.assertEqual(events,['collector-start','import','native','native-cleanup-complete','collector-stop','report','release-input'])
            self.assertTrue(value['local_no_proxy_check']);self.assertFalse((cache/'input').exists())

    def test_python_local_proof_controls_query_report_and_input_cleanup(self):
        import os
        for profile,task_id in (('sphinx7590','sphinx-doc__sphinx-7590'),('xarray6992','pydata__xarray-6992')):
            for failure in (None,'proxy-proof'):
                with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'off'}),patch('prettier_no_proxy.check_cache',return_value=dict(status='PASS')) as cache_check:
                    code,value,cache,events,out=self.workflow(Path(tmp),profile,failure=failure,local=True)
                    cache_check.assert_called_once_with(cache/'input/env',task_id=task_id)
                    if failure:
                        self.assertEqual(code,1);self.assertNotIn('query',events);self.assertNotIn('release-input',events);self.assertTrue((cache/'input').exists())
                    else:
                        self.assertEqual(code,0);self.assertEqual(value['local_no_proxy']['guest_check'],'PASS')
                        self.assertEqual(value['input_file_validation'],'PASS');self.assertEqual(value['official_task_status'],'FAIL')
                        self.assertEqual(events,['collector-start','import','native','native-cleanup-complete','query','collector-stop','report','release-input'])

    def test_python_report_keeps_local_proof_and_separate_original_failures(self):
        import os
        for profile in ('sphinx7590','xarray6992'):
            with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'off'}),patch('prettier_no_proxy.check_cache',return_value=dict(status='PASS')):
                base=Path(tmp);_,receipt,cache,_,workflow=self.workflow(base,profile,local=True)
                child=Path(receipt['storage_result_dir']);raw=child/'python-backbone';raw.mkdir()
                write(raw/'result.json',dict(status='PASS',official_task_status='FAIL',strict_replay_status='FAIL'))
                write(raw/'runtime-monitor.json',dict(profile='light'))
                host=dict(file_peak_bytes=5,scope='grouped',primary_metric='memory.current',current_peak_bytes=8,
                    current=dict(before_guest_bytes=1,after_service_ready_peak_increase_bytes=7))
                with patch.object(platform,'ROOT',base),patch('report_task_overview.write_report',return_value=dict(task_id=receipt['task_id'],strict_replay_status='FAIL')),patch('report_host_file.report',return_value=host):
                    output=platform.report(workflow,base/'.artifacts/checked-python-report')
                summary=json.loads((output/'summary.json').read_text());result=json.loads((output/'result.json').read_text())
                self.assertEqual(summary['local_no_proxy']['guest_check'],'PASS');self.assertEqual(result['status'],'PASS')
                self.assertEqual(result['strict_replay_status'],'FAIL');self.assertEqual(result['official_task_status'],'FAIL')

    def test_three_tasks_follow_import_runtime_query_report_cleanup_order(self):
        for profile in PROFILES:
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as tmp:
                code,value,cache,events,out=self.workflow(Path(tmp),profile)
                self.assertEqual(code,0);self.assertEqual(value['status'],'PASS');self.assertTrue(value['cache_released'])
                self.assertEqual(events,['collector-start','import','native','native-cleanup-complete','query','collector-stop','report','release-input'])
                self.assertFalse((cache/'input').exists());self.assertEqual(value['official_task_status'],'FAIL')
                self.assertEqual(value['input_file_check']['thread_cpu_ns'],17);self.assertEqual(value['host_file_monitor']['thread_cpu_ns'],13)
                self.assertEqual(value['input_file_check']['phase'],'AFTER_ISOLATED_RUNTIME_CLEANUP')
                self.assertGreaterEqual(value['input_file_check']['start_monotonic_ns'],value['isolated_runtime_end_monotonic_ns'])
                self.assertIn('external archive restoration excluded',value['workflow_timing_scope'])

    def test_failures_keep_input_stop_collector_and_do_not_repeat_runtime(self):
        for failure in ('import','native','query','shortfall','collector','report','cleanup','cleanup-false','query-and-collector'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                code,value,cache,events,out=self.workflow(Path(tmp),failure=failure)
                self.assertEqual(code,1);self.assertEqual(value['status'],'FAIL');self.assertTrue((cache/'input').exists())
                self.assertIn('collector-stop',events);self.assertLessEqual(events.count('native'),1)
                if failure not in ('cleanup','cleanup-false'):self.assertNotIn('release-input',events)
                if failure=='collector':self.assertEqual(value['failed_stage'],'host-sampling')
                if failure=='cleanup-false':self.assertEqual(value['failed_stage'],'persistent-cleanup')
                if failure in ('query','shortfall','query-and-collector'):
                    self.assertEqual(value['failed_stage'],'input-file-check');self.assertEqual(value['input_file_validation'],'INCOMPLETE')

    def test_real_report_keeps_input_query_phase_cost_and_original_task_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);_,receipt,cache,_,workflow=self.workflow(base)
            child=Path(receipt['storage_result_dir']);raw=child/'prettier6604';raw.mkdir()
            write(raw/'result.json',dict(status='PASS',official_task_status='FAIL'));write(raw/'runtime-monitor.json',dict(profile='light'))
            host=dict(file_peak_bytes=5,scope='grouped',primary_metric='memory.current',current_peak_bytes=8,
                current=dict(before_guest_bytes=1,after_service_ready_peak_increase_bytes=7))
            with patch.object(platform,'ROOT',base),patch('report_task_overview.write_report',return_value=dict(task_id=receipt['task_id'])),\
                 patch('report_host_file.report',return_value=host):
                output=platform.report(workflow,base/'.artifacts/checked-report')
            summary=json.loads((output/'summary.json').read_text());saved=summary['persistent_input']['input_file_check']
            self.assertEqual(saved['phase'],'AFTER_ISOLATED_RUNTIME_CLEANUP');self.assertEqual(saved['thread_cpu_ns'],17)
            self.assertEqual(json.loads((output/'result.json').read_text())['official_task_status'],'FAIL')
            receipt['input_file_check']['group_id']+=1;write(workflow/'result.json',receipt)
            with patch.object(platform,'ROOT',base),patch('report_task_overview.write_report',return_value=dict(task_id=receipt['task_id'])),\
                 patch('report_host_file.report',return_value=host),self.assertRaises(AssertionError):
                platform.report(workflow,base/'.artifacts/wrong-group-report')


if __name__=='__main__':unittest.main()
