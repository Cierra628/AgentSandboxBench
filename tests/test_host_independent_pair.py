"""Offline only: tiny snapshot fixtures, argument wiring and mocked guest operations."""
import asyncio
import ast
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_independent_task import phase_options, phase_arguments, execution_paths, export_prefix, checked_prefix, import_prefix, verify_repository_archive
from native_checkpoint_bundle import restore, export, IMAGE
from platform_tasks import configuration
from run_host_independent_pair import phase_command
from host_file_matched_task import observe_matched

ROOT=Path(__file__).resolve().parents[1]


class IndependentTests(unittest.TestCase):
    def test_options_do_not_change_old_modes_or_accept_incomplete_handoffs(self):
        self.assertIsNone(phase_options(SimpleNamespace()))
        for phase in ('capture','single','dual'):
            args=SimpleNamespace(host_independent_phase=phase,host_matched_task=True)
            if phase!='capture':args.host_checkpoint_bundle=Path('/bundle');args.host_checkpoint_source=Path('/source')
            self.assertEqual(phase_options(args),phase)
            args.host_trace_dual=True
            with self.assertRaises(ValueError):phase_options(args)
        for args in (SimpleNamespace(host_checkpoint_bundle=Path('/bundle')),
            SimpleNamespace(host_independent_phase='single',host_matched_task=True),
            SimpleNamespace(host_independent_phase='capture',host_matched_task=True,host_checkpoint_source=Path('/source'))):
            with self.assertRaises(ValueError):phase_options(args)

    def test_commands_and_counts_use_original_prefix_once(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-host-file.json')
        for phase in ('capture','single','dual'):
            command=phase_command(cfg,Path('/seed'),phase,Path('/bundle'),Path('/source'))
            self.assertIn('--host-matched-task',command);self.assertIn('--host-file-monitor',command)
            self.assertNotIn('--prettier6604-readonly-replay',command)
            self.assertEqual('--host-checkpoint-bundle' in command,phase!='capture')
            self.assertIn(phase,command)
        rollouts={'backbone':['read_repo','read_typescript','main'], 'early_1':['read_repo','read_typescript','inspect','fix']}
        actions={'read_repo':5,'read_typescript':6,'inspect':4,'fix':6}
        totals=[]
        for phase in ('capture','single','dual'):
            paths=execution_paths(rollouts,phase)
            verified=sum(actions[s] for path in paths.values() for s in path)
            totals.append(verified-(0 if phase=='capture' else 11))
        self.assertEqual(totals,[11,10,20]);self.assertEqual(sum(totals),41)

    def make_prefix(self,root):
        raw=root/'prefix';raw.mkdir()
        checkpoint=dict(status='PASS',step=11,template='snapshot-id',parent='original-parent',boundary={'step':11},process={'count':'1'})
        task=dict(status='PASS',task_id='prettier__prettier-6604',host_independent_phase='capture',actual_action_count=11,reused_action_count=0,
            paths={'backbone':{'action_count':11}},checks={'sampled_actions_order_exit_codes_and_distinct_instances':'PASS','checkpoint_saved_at_step_11':'PASS'},
            cleanup={'snapshots_retained_for_export':['snapshot-id'],'service_sandbox_list_empty':True})
        for name,value in (('result',task),('checkpoint-11',checkpoint)):(raw/(name+'.json')).write_text(json.dumps(value))
        (raw/'rollouts/backbone').mkdir(parents=True);(raw/'rollouts/backbone/evidence').write_text('prefix')
        (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\toriginal-parent\nbackbone\tread_typescript\toriginal-parent\n')
        repo=root/'source/snapshot-store/repository'
        for directory in ('catalog/records','snapshots/snapshot-id','managed-layers'):(repo/directory).mkdir(parents=True)
        (repo/'catalog/records/snapshot-id').write_text('catalog')
        (repo/'snapshots/snapshot-id/state').write_text('vm-state')
        (repo/'managed-layers/memory').write_bytes(b'small fixture only')
        return raw,repo.parent,task

    def test_real_metadata_and_identical_snapshot_in_two_independent_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,repo,task=self.make_prefix(root);bundle=root/'bundle'
            value=export_prefix(repo,bundle,raw)
            self.assertEqual(value['capture']['kind'],'prettier-host-step11-v1')
            self.assertNotIn('phase',value['capture'])
            self.assertEqual(verify_repository_archive(bundle)['verified_files'],3)
            self.assertEqual(checked_prefix(bundle,raw)['template'],'snapshot-id')
            for name in ('single','dual'):
                destination=root/name;destination.mkdir();restore(bundle,destination/'snapshot-store')
                (destination/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\n')
                self.assertEqual(import_prefix(bundle,raw,destination)['step'],11)
                self.assertEqual((destination/'rollouts/backbone/evidence').read_text(),'prefix')
            a=root/'single/snapshot-store/repository/managed-layers/memory'
            b=root/'dual/snapshot-store/repository/managed-layers/memory'
            self.assertNotEqual(a.stat().st_ino,b.stat().st_ino)
            a.write_text('changed by first group');self.assertEqual(b.read_bytes(),b'small fixture only')
            task['paths']['backbone']['action_count']=10;(raw/'result.json').write_text(json.dumps(task))
            with self.assertRaises(AssertionError):checked_prefix(bundle,raw)

    def test_old_demo_contract_and_failed_capture_are_not_relaxed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,repo,task=self.make_prefix(root)
            with self.assertRaises((AssertionError,KeyError)):export(repo,root/'bad',{'status':'PASS','server_image':IMAGE,'kind':'prettier-host-step11-v1'})
            task['status']='FAIL';(raw/'result.json').write_text(json.dumps(task))
            with self.assertRaises(AssertionError):export_prefix(repo,root/'failed',raw)
            self.assertFalse((root/'failed').exists())

    def test_resume_reuses_11_actions_only_after_recorded_export_recovery(self):
        from run_host_independent_pair import resumed_capture
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);storage=root/'.artifacts/storage-isolated-service/run';storage.mkdir(parents=True)
            raw,repo,task=self.make_prefix(storage);raw.rename(storage/'prettier6604');raw=storage/'prettier6604'
            export_prefix(repo,storage/'checkpoint-bundle',raw)
            (storage/'result.json').write_text(json.dumps(dict(status='FAIL')))
            with patch('run_host_independent_pair.ROOT',root):
                with self.assertRaises(FileNotFoundError):resumed_capture(storage)
                archive=json.loads((storage/'checkpoint-bundle/bundle.json').read_text())
                (storage/'checkpoint-export-recovery.json').write_text(json.dumps(dict(status='PASS',source_volume_unchanged=True,original_capture_reused=True,archive_sha256=archive['archive_sha256'])))
                (storage/'checkpoint-archive-verification.json').write_text(json.dumps(verify_repository_archive(storage/'checkpoint-bundle')))
                value=resumed_capture(storage)
            self.assertEqual(value['actual_actions'],11);self.assertEqual(value['new_actions'],0)
            self.assertEqual(value['original_storage_status'],'FAIL')

    def test_backing_query_flushes_after_other_files_and_keeps_rw_mount(self):
        from host_file_monitor import residency_check
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inner=root/'inner';image=root/'image'
            inner.write_bytes(b'file');image.write_bytes(b'volume');events=[]
            def query(path):
                events.append(path.name)
                return dict(path=str(path),resident_bytes=4096,stable=path!=image or 'flush' in events)
            with patch('host_file_monitor.counters',return_value=dict(file=8192)),patch('host_file_monitor.mincore_file',side_effect=query):
                result=residency_check(root,[inner,image],root/'result.json',before_last_file=lambda:events.append('flush'))
            self.assertEqual(events,['inner','flush','image']);self.assertEqual(result['status'],'OBSERVED')
        source=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        final=source.split("            if host_group and mounted and host_sampling=='on':")[1]
        self.assertIn("roots=[path for path in roots if path!=image]+[image]",final)
        self.assertIn("lambda:call('flush-own-filesystem',['sync','-f',mount],60)",final)
        self.assertLess(final.index('try:'),final.index("final_options['before_last_file']="))
        self.assertNotIn('remount,ro',final)

    def test_import_sync_is_owned_before_service_and_only_for_independent_restores(self):
        source=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        node=next(n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.If)
            and ast.unparse(n.test)=='durable_bundle or host_bundle')
        code=compile(ast.Module(body=[node],type_ignores=[]),'<checkpoint-import>','exec')
        self.assertLess(source.index("stage='sync-imported-checkpoint'"),source.index("call('start-server'"))
        for phase in (None,'capture','single','dual'):
            events=[];receipt={}
            def imported(*args):events.append('import');return dict(status='PASS')
            def call(label,argv,timeout):
                self.assertEqual((label,argv,timeout),('sync-imported-checkpoint',['sync','-f',Path('/owned')],60))
                events.append('sync')
            env=dict(durable_bundle=None,host_bundle=Path('/bundle'),mount=Path('/owned'),
                independent_phase=phase,receipt=receipt,call=call,time=__import__('time'))
            with patch('native_checkpoint_bundle.restore',side_effect=imported):exec(code,env)
            self.assertEqual(events,['import','sync'] if phase in ('single','dual') else ['import'])
            self.assertEqual('checkpoint_import_sync' in receipt,phase in ('single','dual'))
        env.update(independent_phase='single',receipt={})
        with patch('native_checkpoint_bundle.restore',return_value=dict(status='PASS')),\
             patch.dict(env,call=lambda *a:(_ for _ in ()).throw(RuntimeError('sync failed'))):
            with self.assertRaisesRegex(RuntimeError,'sync failed'):exec(code,env)
        self.assertNotIn('checkpoint_import_sync',env['receipt'])


class IndependentSequenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_requested_group_runs_and_inventory_precedes_creation(self):
        for phase,n,roles in (('single',1,('N1-a',)),('dual',2,('N2-a','N2-b'))):
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp);events=[];active={};bindings={}
                class Controller:
                    live=active
                    async def start(self,role,**kwargs):
                        events.append('start-'+role);active[role]=role;bindings[role]=dict(pid=len(bindings)+1,start_ticks='1')
                        (out/('restored-'+role+'.json')).write_text('{"status":"PASS"}');return role
                    async def run_path(self,sid,role,path):events.append('task-'+role)
                def inventory(*args,**kwargs):
                    events.append('inventory-'+str(len(active)));return dict(status='OBSERVED')
                def snapshot(group):return dict(status='OBSERVED',processes=[dict(comm='firecracker',pss=1,**bindings[r]) for r in active])
                def cli(label,operation,sid,**kwargs):events.append('delete-'+sid)
                async def check(role,sid):events.append('check-'+role)
                roots=out/'roots.json';roots.write_text(json.dumps([str(out/'image')]))
                with patch.dict('os.environ',{'ASB_HOST_FILE_SAMPLING':'on','ASB_HOST_MEMORY_DETAIL':'off'}),\
                     patch('host_file_monitor.measurement_group',return_value=(out,'owned')),\
                     patch('host_file_monitor.counters',return_value=dict(file=1,current=2,anon=1)),\
                     patch('host_file_monitor.pids',return_value=[]),\
                     patch('host_sandbox_monitor.Binding',return_value=SimpleNamespace(resolve=lambda sid:bindings[sid])),\
                     patch('host_file_monitor.residency_check',side_effect=inventory),\
                     patch('host_file_monitor.pss_snapshot',side_effect=snapshot),\
                     patch('host_file_matched_task.live_residency',return_value=dict(status='OBSERVED',backends=[dict(rdev=0)])),\
                     patch('host_existing_cache_owner.query_bounded') as owner:
                    result=await observe_matched(Controller(),'snapshot','parent',SimpleNamespace(mark=lambda *a:None,stop=lambda *a:None),
                        SimpleNamespace(container='mock',host_file_roots=roots,host_independent_phase=phase),out,cli,{r:['original'] for r in roles},
                        lambda role:dict(action_count=10),check)
                self.assertEqual(result['status'],'PASS');self.assertEqual(result['suffix_actions'],10*n)
                self.assertEqual(len(result['groups']),1);self.assertEqual(events[0],'inventory-0')
                self.assertEqual([e for e in events if e.startswith('task-')],['task-'+r for r in roles])
                self.assertFalse(active)
                owner.assert_not_called()

    async def test_explicit_owner_query_follows_devices_and_failure_preserves_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);events=[];active={};vm=dict(pid=1,start_ticks='1')
            class Controller:
                live=active
                async def start(self,role,**kwargs):
                    active[role]=role;(out/('restored-'+role+'.json')).write_text('{}');return role
                async def run_path(self,*args):events.append('task')
            def inventory(*args,**kwargs):return dict(status='OBSERVED')
            def snapshot(group):return dict(status='OBSERVED',processes=[dict(comm='firecracker',pss=1,**vm)])
            def devices(*args,**kwargs):
                self.assertIn('regular_roots',kwargs);events.append('original-device-query')
                return dict(status='OBSERVED',backends=[dict(rdev=0)],compact_inventory=dict(ordinary=dict(status='OBSERVED')))
            def owner(file,group,directory):
                self.assertEqual(file,out/'bundle/repository.tar.gz');self.assertEqual(group,out)
                self.assertEqual(directory,out/'existing-cache-owner');events.append('old-owner-query')
                return dict(status='TIMEOUT',outside_bytes=None)
            def verify(role):events.append('verify');return dict(action_count=10)
            async def check(*args):events.append('official-check')
            def cli(*args,**kwargs):events.append('delete')
            roots=out/'roots.json';roots.write_text(json.dumps([str(out/'image')]))
            with patch.dict('os.environ',{'ASB_HOST_FILE_SAMPLING':'on','ASB_HOST_MEMORY_DETAIL':'on'}),\
                 patch('host_file_monitor.measurement_group',return_value=(out,'owned')),\
                 patch('host_file_monitor.counters',return_value=dict(file=1,current=2,anon=1)),\
                 patch('host_file_monitor.pids',return_value=[]),\
                 patch('host_sandbox_monitor.Binding',return_value=SimpleNamespace(resolve=lambda sid:vm)),\
                 patch('host_file_monitor.residency_check',side_effect=inventory),\
                 patch('host_file_monitor.pss_snapshot',side_effect=snapshot),\
                 patch('host_file_matched_task.live_residency',side_effect=devices),\
                 patch('host_existing_cache_owner.query_bounded',side_effect=owner):
                result=await observe_matched(Controller(),'snapshot','parent',SimpleNamespace(mark=lambda *a:None,stop=lambda *a:None),
                    SimpleNamespace(container='mock',host_file_roots=roots,host_independent_phase='single',host_checkpoint_bundle=out/'bundle'),
                    out,cli,{'N1-a':['original']},verify,check)
            self.assertEqual(events,['task','original-device-query','old-owner-query','verify','official-check','delete'])
            self.assertEqual(result['status'],'PASS');self.assertFalse(active)
            self.assertEqual(result['groups'][0]['existing_cache_owner']['status'],'TIMEOUT')


if __name__=='__main__':unittest.main()
