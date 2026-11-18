"""Actual controller methods with fake VM calls; no services, task inputs or devices."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from platform_tasks import configuration,commands
from run_prettier6604_grpo import create_before_setup_options,joined,parallel_guest_cleanup
from run_grpo_light_pair import pair_configs,summarize
from report_grpo_light_repeats import summarize as repeat_summary
from report_task_overview import grpo_setup_timing

ROLES=['backbone',*[f'early_{i}' for i in range(1,4)],*[f'late_{i}' for i in range(1,4)]]
SOURCE=ROOT/'scripts/run_prettier6604_grpo.py'


def actual_controller(namespace):
    # Exercise the nested production class, not a reimplementation of its scheduler.
    node=copy.deepcopy(next(n for n in ast.walk(ast.parse(SOURCE.read_text())) if isinstance(n,ast.ClassDef) and n.name=='SampledPaths'))
    node.bases=[ast.Name(id='FakeBase',ctx=ast.Load())]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(SOURCE),'exec'),namespace)
    return namespace['SampledPaths']()


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    def harness(self,out,*,enabled=True,sampling=True,fail=None):
        events=[];registered=threading.Event();release=threading.Event();creation_barrier=threading.Barrier(7,timeout=4)
        prepare_barrier=threading.Barrier(7,timeout=4)
        ids={role:f'00000000-0000-0000-0000-{i:012d}' for i,role in enumerate(ROLES)}
        expected_registered=5 if fail=='create' else 6
        class FakeBase:
            def __init__(self):self.live={};self.by_role={};self.agentenv_templates=[]
            def record(self,name,row):
                if name=='sandbox-ids.tsv':
                    events.append(('registered',row.split('\t')[0]))
                    if len(self.live)==expected_registered:registered.set()
            def phase(self,name):events.append(('phase',name))
            async def run_path(self,sid,role,path):
                self.assert_ready()
                events.append(('task',role))
            def assert_ready(self):
                assert sum(kind=='upload-workload' for kind,role in events)==7
        def call(label,argv,timeout):
            role=label.removeprefix('cold-');creation_barrier.wait()
            if role==ROLES[-1]:assert release.wait(4)
            if fail=='create' and role==ROLES[0]:raise ValueError('create failure')
            return ids[role]
        def cli(label,*argv):
            role=label.split('-',2)[-1]
            if label.startswith('upload-check-'):role=label.removeprefix('upload-check-');kind='upload-check'
            else:role=label.removeprefix('upload-workload-');kind='upload-workload'
            events.append((kind,role));return ''
        def guest(label,sid,command):
            if label.startswith('source-'):
                role=label.removeprefix('source-');events.append(('source',role))
                if enabled:assert len(controller.live)==7
                if fail is None:prepare_barrier.wait()
                if fail=='prepare' and role==ROLES[0]:raise ValueError('prepare failure')
                return '{}'
            events.append(('baseline',label.removeprefix('baseline-')))
            return '{"status":"PASS"}'
        def sampler(sid,role,phase):
            events.append(('sample',role))
            if enabled:assert len(controller.live)==7
            if fail=='sample' and role==ROLES[0]:raise ValueError('sample failure')
        ns=dict(FakeBase=FakeBase,asyncio=asyncio,time=time,re=re,json=json,sys=sys,ROOT=ROOT,
            args=SimpleNamespace(config_home=out,service_url='fake'),cfg=dict(source_image='fake',base_commit='fake'),
            call=call,cli=cli,guest=guest,out=out,schedule='grpo',runtime=SimpleNamespace(start=sampler) if sampling else None,
            damon_before_checkpoint=False,guest_cache_policy=None,cache_target=None,scaling=None,prefix_count=None,
            private_control=None,full_rootfs=None,shared_files=None,physical=None,roles=ROLES,
            executed={r:['segment'] for r in ROLES},joined=joined,create_before_setup=enabled,result={})
        controller=actual_controller(ns)
        return controller,ns,events,registered,release,ids

    async def test_all_creates_then_concurrent_preparation_on_and_off(self):
        for sampling in (False,True):
            with self.subTest(sampling=sampling),tempfile.TemporaryDirectory() as tmp:
                c,ns,events,registered,release,ids=self.harness(Path(tmp),sampling=sampling)
                pending=asyncio.create_task(c.do_grpo())
                try:
                    self.assertTrue(await asyncio.to_thread(registered.wait,4))
                    self.assertEqual(len(c.live),6)
                    self.assertFalse(any(kind in ('sample','source','task') for kind,_ in events))
                finally:release.set()
                by_role=await asyncio.wait_for(pending,6)
                self.assertEqual(by_role,ids);self.assertEqual(set(c.live),set(ids.values()))
                self.assertLessEqual(ns['result']['all_created_monotonic_ns'],ns['result']['all_prepared_monotonic_ns'])
                for role in ROLES:
                    steps=[kind for kind,r in events if r==role]
                    self.assertEqual(steps,['registered',*(['sample'] if sampling else []),'source','upload-check','baseline','upload-workload','task'])

    async def test_original_default_still_prepares_before_last_create_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            c,ns,events,registered,release,ids=self.harness(Path(tmp),enabled=False)
            pending=asyncio.create_task(c.do_grpo())
            try:
                self.assertTrue(await asyncio.to_thread(registered.wait,4))
                async def wait_sample():
                    while not any(k=='sample' for k,_ in events):await asyncio.sleep(.001)
                await asyncio.wait_for(wait_sample(),3)
                self.assertEqual(len(c.live),6);self.assertNotIn('all_created_monotonic_ns',ns['result'])
            finally:release.set()
            await asyncio.wait_for(pending,6)
            self.assertEqual(sum(k=='task' for k,_ in events),7)

    async def test_partial_failures_join_and_keep_all_allocations_for_cleanup(self):
        for failure in ('create','prepare','sample'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                c,ns,events,registered,release,ids=self.harness(Path(tmp),fail=failure)
                pending=asyncio.create_task(c.do_grpo())
                try:self.assertTrue(await asyncio.to_thread(registered.wait,4))
                finally:release.set()
                with self.assertRaisesRegex(RuntimeError,failure+' failure'):await asyncio.wait_for(pending,6)
                self.assertFalse(any(k=='task' for k,_ in events))
                self.assertEqual(len(c.live),6 if failure=='create' else 7)
                if failure=='create':self.assertFalse(any(k in ('sample','source') for k,_ in events))
                else:self.assertEqual(sum(k=='upload-workload' for k,_ in events),6)
                deleted=[];closed=parallel_guest_cleanup(list(c.live),lambda sid:None,deleted.append)
                self.assertEqual(set(deleted),set(c.live));self.assertTrue(all(r['deleted'] and not r['errors'] for r in closed))

    async def test_legacy_start_keeps_template_and_parent_arguments(self):
        # Existing BPO/TVCache callers keep start(role, template=..., parent=...).
        with tempfile.TemporaryDirectory() as tmp:
            c,*_=self.harness(Path(tmp))
            from unittest.mock import AsyncMock
            c.allocate=AsyncMock(return_value='restored');c.prepare=AsyncMock(return_value='restored')
            self.assertEqual(await c.start('early_1',template='snapshot',parent='parent'),'restored')
            c.allocate.assert_awaited_once_with('early_1',template='snapshot',parent='parent')
            c.prepare.assert_awaited_once_with('restored','early_1',template='snapshot',parent='parent')

    async def test_actual_restore_keeps_monitor_boundary_process_checks_and_no_new_uploads(self):
        for schedule in ('bpo','tvcache'):
            with self.subTest(schedule=schedule),tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);c,ns,*_=self.harness(out)
                ns.update(schedule=schedule,host_restore=False,verify_boundary=lambda *a:dict(status='PASS'))
                from run_prettier6604_grpo import verify_process
                ns['verify_process']=verify_process
                order=[];sid='00000000-0000-0000-0000-000000000001'
                c.checkpoints={'snapshot':dict(step=11,parent='parent',boundary={},
                    process=dict(pid='4',start_ticks='10',boot_id='same',count='20'))}
                def cli(label,*argv,**kw):order.append(label);return sid
                def guest(label,*argv):
                    order.append(label)
                    return '{}' if label.startswith('boundary-') else 'pid=4\nstart_ticks=10\nboot_id=same\ncount=21'
                ns['cli']=cli;ns['guest']=guest
                ns['runtime']=SimpleNamespace(start=lambda sid,role,phase:order.append('sample:'+phase))
                self.assertEqual(await c.start('early_1',template='snapshot',parent='parent'),sid)
                self.assertEqual(order,['restore-early_1','sample:restored-checks','boundary-early_1','process-early_1'])
                self.assertIn(sid,c.live)
                saved=json.loads((out/'restored-early_1.json').read_text())
                self.assertEqual(saved['status'],'PASS');self.assertEqual(saved['process']['status'],'PASS')
                if schedule=='tvcache':self.assertEqual(c.by_role['early_1'],sid)


class EntryAndReportTests(unittest.TestCase):
    def test_new_field_is_optional_and_generates_own_on_off_configs(self):
        originals={p:p.read_bytes() for p in [ROOT/'configs/platform-prettier6604-grpo-off.json',ROOT/'configs/platform-prettier6604-grpo-light.json']}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);old=pair_configs(out);self.assertFalse((out/'configs').exists())
            for mode,path in old.items():
                cfg=configuration(path);check,live=commands(cfg,Path('/unused'))
                self.assertNotIn('--grpo-create-before-setup',check);self.assertNotIn('--prettier6604-grpo-create-before-setup',live)
            new=pair_configs(out,True)
            for mode,path in new.items():
                cfg=configuration(path);self.assertTrue(cfg['grpo_create_before_setup'])
                check,live=commands(cfg,Path('/unused'))
                self.assertIn('--grpo-create-before-setup',check);self.assertIn('--prettier6604-grpo-create-before-setup',live)
                self.assertEqual('--light-monitor' in check,mode=='on')
                with self.assertRaises(ValueError):commands(cfg,Path('/unused'),tool_capture=True)
        self.assertTrue(all(p.read_bytes()==data for p,data in originals.items()))

    def test_backend_parameter_reconstruction_and_early_validation(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for variable in ('check_args','task_args'):
            begin=code.index(variable+"=['--frozen-export'")
            end=code.index('subprocess.run(' if variable=='check_args' else 'call(stage,[sys.executable',begin)
            self.assertIn("if create_before_setup:"+variable+"+=['--grpo-create-before-setup']",code[begin:end])
        self.assertLess(code.index('create_before_setup_options(args)'),code.index('seed_value=None'))

    def test_options_only_restrict_new_mode(self):
        self.assertFalse(create_before_setup_options(SimpleNamespace(schedule='bpo',host_file_monitor=True)))
        for mode,light in [('continuous',True),('off',False)]:
            args=dict(grpo_create_before_setup=True,monitor_mode=mode,light_monitor=light)
            self.assertTrue(create_before_setup_options(SimpleNamespace(**args)))
            for key,value in [('schedule','bpo'),('schedule','tvcache'),('role','early_1'),('root_count',7),
                              ('snapshot_cache_pair',True),('host_file_monitor',True),('host_full_rootfs_mode','shared'),
                              ('host_scaling_mode','cold'),('damon_disable',True),('prettier6604_checkpoint',True)]:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                    create_before_setup_options(SimpleNamespace(**dict(args,**{key:value})))
        with self.assertRaises(ValueError):create_before_setup_options(SimpleNamespace(grpo_create_before_setup=True))
        with patch.dict('os.environ',ASB_HOST_FILE_SLICE='host.slice'),self.assertRaises(ValueError):
            create_before_setup_options(SimpleNamespace(grpo_create_before_setup=True,light_monitor=True))
        self.assertTrue(create_before_setup_options(SimpleNamespace(prettier6604_grpo_create_before_setup=True,prettier6604_monitor_mode='off')))

    def test_config_rejects_invalid_new_combinations_but_false_does_not_restrict_old(self):
        original=json.loads((ROOT/'configs/platform-prettier6604-grpo-light.json').read_text())
        for change in ({'grpo_create_before_setup':1},{'light_monitor':False},{'host_file_monitor':True},
                       {'rollout':'bpo'},{'rollout':'tvcache'},{'root_count':7,'shared_cache_measurement':True},
                       {'host_prefix_count':7,'host_file_monitor':True}):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'c.json';path.write_text(json.dumps(dict(original,grpo_create_before_setup=True)|change))
                with self.assertRaises(ValueError):configuration(path)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'c.json';path.write_text(json.dumps(dict(original,grpo_create_before_setup=False,rollout='bpo')))
            self.assertEqual(configuration(path)['rollout'],'bpo')

    def timing(self):
        return dict(grpo_setup_schedule='create-all-before-setup',rollout_start_monotonic_ns=1,
            all_created_monotonic_ns=10,all_prepared_monotonic_ns=20,first_tool_start_monotonic_ns=21,
            rollout_end_monotonic_ns=40,light_cleanup_timing=dict(end_monotonic_ns=50),
            controller_end_monotonic_ns=55,created_to_controller_end_ns=45,create_to_setup_ns=10)

    def test_time_window_includes_cleanup_and_does_not_replace_controller_total(self):
        source=self.timing();source['controller_elapsed_ns']=55
        t=grpo_setup_timing(source);self.assertEqual(t['created_to_controller_end_ns'],45)
        self.assertEqual(source['controller_elapsed_ns'],55)
        self.assertEqual(grpo_setup_timing({}),{})
        for change in ({'created_to_controller_end_ns':30},{'all_created_monotonic_ns':22},{'controller_end_monotonic_ns':49}):
            with self.assertRaises(AssertionError):grpo_setup_timing(dict(source,**change))

    def test_both_report_entries_keep_new_schedule_and_actual_time_window(self):
        from report_task_overview import write_report,write_tool_report
        audit=dict(status='PASS',schedule='grpo',roles=ROLES,actions=189,segments=35,logical_action_count=189,
            checkpoint_count=0,checkpoint_restore='NOT_RUN',cleanup={},timing=dict(checkpoint_call_ns=0,restore_request_per_role_ns={},stages=[]))
        tools=[dict(tool_index=i+1,duration_ns=1) for i in range(189)]
        for sampling,skip in ((False,False),(True,False),(True,True)):
            with self.subTest(sampling=sampling,skip=skip),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw=root/'raw';raw.mkdir();out=root/'report';out.mkdir()
                result=dict(self.timing(),status='PASS',schedule='grpo',task_id='synthetic-report-fixture',
                    monitor_mode='continuous' if sampling else 'off',cleanup=dict(monitoring_disabled=not sampling),
                    paths={'backbone':dict(official_task_status='FAIL')},roles=ROLES,official_task_directory='/unused')
                if skip:result.update(light_skip_boundary_vmstat=True,boundary_vmstat=dict(status='NOT_MEASURED',row_count=0,boundary_memory_rows=448))
                (raw/'result.json').write_text(json.dumps(result))
                data=[dict(timestamp_ns=str(i),MemTotal_kib='4096',MemAvailable_kib='1024',Cached_kib='512',Shmem_kib='128') for i in (1,2)]
                group={'fixture':dict(role='backbone',chunks=[data],sessions=[])}
                with patch('report_task_overview.tool_rows',return_value=(tools,189)),patch('report_task_overview.rows',return_value=[]),\
                     patch('report_task_overview.samples',return_value=(group,'synthetic data')),patch('report_prettier6604_grpo.audit',return_value=(audit,{},[])):
                    summary=(write_report if sampling else write_tool_report)(raw,out)
                self.assertEqual(summary['grpo_setup_schedule'],'create-all-before-setup')
                self.assertEqual(summary['grpo_setup_timing']['created_to_controller_end_ns'],45)
                self.assertEqual(summary['official_task_status'],'FAIL')
                self.assertEqual(summary.get('light_skip_boundary_vmstat',False),skip)
                if skip:self.assertEqual(summary['boundary_vmstat'],result['boundary_vmstat'])
                if sampling:self.assertIn('earlier creation interval',summary['notes'][-1])
                else:self.assertEqual(summary['memory'],'NOT_MEASURED')

    def test_pair_rejects_mixed_schedules_and_compares_new_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);schedule='create-all-before-setup'
            runs=[dict(mode=m,grpo_setup_schedule=schedule,paths={},patches={},official={},sessions=[],
                timings_ns=dict(controller=100,created_to_controller_end_ns=v,create_to_setup_ns=10)) for m,v in [('off',40),('on',42)]]
            (out/'plan.json').write_text(json.dumps(dict(order=['off','on'],grpo_setup_schedule=schedule)))
            def save():(out/'runs.json').write_text(json.dumps(runs))
            save();result=summarize(out)
            self.assertEqual(result['grpo_setup_schedule'],schedule)
            self.assertAlmostEqual(result['comparison']['created_to_controller_end_ns']['change_percent'],5)
            runs[0].pop('grpo_setup_schedule');save()
            with self.assertRaises(AssertionError):summarize(out)

    def test_original_repeat_report_rejects_new_schedule_before_reading_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            (out/'result.json').write_text(json.dumps(dict(status='PASS',source_returned_to_stopped=True,completed=2)))
            (out/'plan.json').write_text(json.dumps(dict(order=['off','on'],grpo_setup_schedule='create-all-before-setup')))
            (out/'runs.json').write_text(json.dumps([dict(mode='off'),dict(mode='on')]))
            with self.assertRaisesRegex(AssertionError,'original setup schedule'):repeat_summary([out],expected_pairs=1)


if __name__=='__main__':unittest.main()
