import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from exp1_inputs import checkpoint_step, validate_trajectory, verify_boundary
from use_platform import CONFIG, ROOT, configuration, preflight, run, run_isolated, isolated_item, isolated_backend, source_name, verify_resources, write_json


class PlatformUseTests(unittest.TestCase):
    def test_checkpoint_range_and_padding(self):
        self.assertEqual(checkpoint_step(1), '001')
        self.assertEqual(checkpoint_step('025'), '025')
        self.assertIsNone(checkpoint_step(None))
        for bad in (0, 26, True, 1.5, '022; rm', 'none'):
            with self.assertRaises(ValueError): checkpoint_step(bad)

    def test_progress_and_state_changes_rejected(self):
        state = dict(steps=[f'{i:03d}' for i in range(1, 23)], failures={'008':127,'016':127},
                     task_files={'changed.js':'a'}, replay_sha256={'steps.tsv':'b'}, head='c')
        self.assertEqual(verify_boundary(state,state,'022')['continuation_start_step'], '023')
        for key, value in [('steps',state['steps'][:-1]), ('failures',{}),
                           ('task_files',{'changed.js':'other'}), ('replay_sha256',{})]:
            changed=copy.deepcopy(state);changed[key]=value
            with self.assertRaises(ValueError): verify_boundary(state,changed,'022')
        with self.assertRaises(ValueError): verify_boundary(state,state,'013')

    def test_frozen_input_tampering_and_extra_action_rejected(self):
        import shutil
        source=Path(json.loads(CONFIG.read_text())['trajectory_dir'])
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)/'trace';shutil.copytree(source,directory)
            self.assertEqual(validate_trajectory(directory)['action_count'],26)
            (directory/'actions/027.sh').write_text('echo extra')
            with self.assertRaises(ValueError): validate_trajectory(directory)
            (directory/'actions/027.sh').unlink()
            (directory/'actions/001.sh').write_text('echo tampered')
            with self.assertRaises(ValueError): validate_trajectory(directory)

    def test_config_rejects_unknown_fields_and_wrong_backend_boundary(self):
        cfg=json.loads(CONFIG.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            changed=dict(cfg, drop_host_caches=True);path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError): configuration(path)
            path.write_text(json.dumps(cfg))
            with self.assertRaises(ValueError): configuration(path,'trenvx','025')
            with self.assertRaises(ValueError): configuration(path,'agentenv','026')

    def test_no_child_launched_when_space_insufficient(self):
        cfg=json.loads(CONFIG.read_text());cfg['backends']=['trenvx']
        with patch('use_platform.os.geteuid',return_value=0), patch('use_platform.shutil.disk_usage') as usage, patch('use_platform.query') as query:
            usage.return_value.free=47*2**30
            with self.assertRaisesRegex(RuntimeError,'required>60'): preflight(cfg)
            query.assert_not_called()

    def test_external_trajectory_source_can_be_recorded(self):
        self.assertEqual(source_name(Path('/tmp/external/actions.tsv')), '/tmp/external/actions.tsv')

    def test_existing_output_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'result.json';path.write_text('original')
            with self.assertRaises(FileExistsError): write_json(path,dict(status='PASS'))
            self.assertEqual(path.read_text(),'original')

    def test_only_proven_same_service_idle_pool_can_change(self):
        running=dict(start_ticks='1',cgroup='/other')
        pool=dict(start_ticks='2',cgroup='/project',vm_state='Not started')
        before={'1':running,'2':pool}
        after={'1':running,'3':dict(pool,start_ticks='3')}
        self.assertEqual(verify_resources(before,after,'/project')['new_project_pool_pids'],['3'])
        after['3']['vm_state']='Running'
        with self.assertRaises(ValueError): verify_resources(before,after,'/project')
        after['3']['vm_state']='Not started';after['1']=dict(running,start_ticks='changed')
        with self.assertRaises(ValueError): verify_resources(before,after,'/project')
        with self.assertRaises(ValueError): verify_resources(before,{'1':running},'/project')

    def test_trenvx_result_parsing_preserves_source_hash_inventory(self):
        # Exercise the actual orchestration after a mocked isolated backend; no live service.
        cfg=configuration(CONFIG,'trenvx');cfg['checkpoint_after_step']=None
        def backend(argv,out,label,cfg):
            (out/(label+'.log')).write_text('status=PASS result_dir='+str(ROOT/'.artifacts/trenvx-service-smoke/mock-run')+'\n')
        with tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
            cfg['output_root']=tmp
            with patch('use_platform.preflight',return_value={'service_cgroup':'/project'}), \
                 patch('use_platform.resource_identity',return_value={}), \
                 patch('use_platform.bounded',side_effect=backend), \
                 patch('use_platform.audit_items',return_value=[{}]):
                self.assertEqual(run(cfg),0)
            result=json.loads(next(Path(tmp).glob('*/result.json')).read_text())
            self.assertEqual(result['runs'][0]['mode'],'full-light')
            self.assertEqual(result['cleanup'],'PASS')


class PlatformIsolatedTests(unittest.TestCase):
    def window(self):
        now=datetime.now(timezone.utc)
        return {'start_utc':now.isoformat(),'end_utc':(now+timedelta(minutes=20)).isoformat()}

    def test_shared_default_and_isolated_override(self):
        self.assertEqual(configuration(CONFIG)['storage'],'shared')
        self.assertEqual(configuration(CONFIG,storage='isolated')['storage'],'isolated')
        with self.assertRaises(ValueError):configuration(CONFIG,storage='unknown')
        with self.assertRaisesRegex(ValueError,'022 only'):configuration(CONFIG,'agentenv','025','isolated')
        with self.assertRaisesRegex(ValueError,'022 only'):configuration(CONFIG,'trenvx','013','isolated')

    def test_custom_prepared_runtime_rejected(self):
        cfg=json.loads(CONFIG.read_text());cfg.update(storage='isolated',runtime_dir='/tmp/unprepared-runtime')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError,'prepared runtime_dir'):configuration(path)

    def test_missing_expired_or_too_short_window_launches_nothing(self):
        cfg=configuration(CONFIG,'both',storage='isolated')
        now=datetime.now(timezone.utc)
        expired={'start_utc':(now-timedelta(minutes=21)).isoformat(),'end_utc':(now-timedelta(minutes=1)).isoformat()}
        short={'start_utc':now.isoformat(),'end_utc':(now+timedelta(seconds=30)).isoformat()}
        with patch('use_platform.prepare_run') as prepare,patch('use_platform.isolated_backend') as backend:
            for window in [None,expired,short]:
                with self.assertRaises((RuntimeError,ValueError)):run_isolated(cfg,window)
            prepare.assert_not_called();backend.assert_not_called()

    def test_low_space_rejected_before_device_or_service_queries(self):
        cfg=configuration(CONFIG,'both',storage='isolated')
        with patch('use_platform.os.geteuid',return_value=0),patch('isolated_storage.available',return_value=59*2**30),patch('use_platform.query') as query:
            with self.assertRaisesRegex(RuntimeError,'required>60'):preflight(cfg)
            query.assert_not_called()

    def test_backend_pass_without_cleanup_is_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            child=Path(tmp)
            for cleanup in [{},{'successful_fixture_removed':True,'mount_and_loop_removed':True,'original_firecracker_identities_unchanged':False}]:
                (child/'result.json').write_text(json.dumps({'status':'PASS','cleanup':cleanup}))
                with self.assertRaisesRegex(ValueError,'cleanup'):isolated_item('trenvx',None,child)

    def test_batch_order_reference_and_same_window(self):
        cfg=configuration(CONFIG,'both',storage='isolated');window=self.window();calls=[]
        def backend(name,boundary,reference,cfg,out,current):
            calls.append((name,boundary,reference,current))
            return {'backend':name,'mode':'checkpoint-'+boundary if boundary else 'full','raw':str(out/(name+'-'+str(boundary)))}
        with tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
            cfg['output_root']=tmp
            with patch('use_platform.writer_lock'),patch('use_platform.isolated_preflight',return_value={'status':'READY'}),patch('probe_storage_boundary.identities',return_value={}),patch('use_platform.isolated_backend',side_effect=backend),patch('use_platform.audit_items',return_value=[{}]*4):
                self.assertEqual(run_isolated(cfg,window),0)
            out=next(Path(tmp).iterdir());result=json.loads((out/'result.json').read_text())
            self.assertEqual([(b,s) for b,s,_,_ in calls],[('agentenv',None),('agentenv','022'),('trenvx',None),('trenvx','022')])
            self.assertEqual(calls[-1][2],Path(result['runs'][2]['raw']))
            self.assertTrue(all(w==window for _,_,_,w in calls));self.assertEqual(result['audited_run_count'],4)

    def test_batch_failure_preserves_prior_run_and_skips_next(self):
        cfg=configuration(CONFIG,'both',storage='isolated')
        with tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
            cfg['output_root']=tmp
            with patch('use_platform.writer_lock'),patch('use_platform.isolated_preflight',return_value={}),patch('probe_storage_boundary.identities',return_value={}),patch('use_platform.isolated_backend',side_effect=[{'backend':'agentenv','mode':'full','raw':'owned'},RuntimeError('cleanup failed')]) as backend,patch('use_platform.audit_items') as audit:
                self.assertEqual(run_isolated(cfg,self.window()),1)
            result=json.loads(next(Path(tmp).glob('*/result.json')).read_text())
            self.assertEqual(len(result['runs']),1);self.assertEqual(result['status'],'FAIL')
            self.assertEqual(backend.call_count,2);audit.assert_not_called()
            resource=json.loads(next(Path(tmp).glob('*/resource-check.json')).read_text())
            self.assertEqual(resource['status'],'PASS')

    def test_effective_sampling_mismatch_fails_adapter(self):
        cfg=configuration(CONFIG,'trenvx',storage='isolated')
        with tempfile.TemporaryDirectory(dir=ROOT/'.artifacts') as tmp:
            out=Path(tmp);raw=out/'raw';raw.mkdir()
            (raw/'config.json').write_text(json.dumps({'checkpoint_after':None,'host_sample_interval_seconds':1,'guest_sample_interval_seconds':1}))
            def worker(argv,**kwargs):
                (out/'trenvx-full.log').write_text('status=PASS result_dir='+str(ROOT/'.artifacts/storage-isolated-trenvx/mock-run'))
                self.assertIn('--platform-config',argv);self.assertIn('--confirmed-window',argv)
            with patch('use_platform.supervised_run',side_effect=worker),patch('use_platform.isolated_item',return_value={'raw':str(raw)}):
                with self.assertRaisesRegex(ValueError,'sampling interval'):isolated_backend('trenvx',None,None,cfg,out,self.window())

    def test_trenvx_full_command_forwards_sampling_without_checkpoint(self):
        from validate_isolated_trenvx import backend_command
        cfg=configuration(CONFIG,'trenvx',storage='isolated');cfg['checkpoint_after_step']=None
        cfg['sample_interval_seconds']=.25
        with patch('use_platform.configuration',return_value=cfg):
            argv=backend_command(SimpleNamespace(mode='full',reference=None,platform_config=Path('fixture')))
        self.assertNotIn('--checkpoint-after',argv);self.assertNotIn('--reference',argv)
        self.assertEqual(argv[argv.index('--host-sample-interval')+1],'0.25')
        self.assertEqual(argv[argv.index('--guest-sample-interval')+1],'0.25')
        cfg['checkpoint_after_step']='022'
        with patch('use_platform.configuration',return_value=cfg):
            with self.assertRaisesRegex(ValueError,'mode does not match'):backend_command(SimpleNamespace(mode='full',reference=None,platform_config=Path('fixture')))


    def test_isolated_report_checks_storage_receipt_and_service_scope(self):
        from use_platform import audit_items
        from exp1_inputs import sha
        with tempfile.TemporaryDirectory() as tmp:
            child=Path(tmp)/'child';raw=child/'exp1/raw/run';audit=child/'exp1/audit/run'
            (raw/'replay').mkdir(parents=True);audit.mkdir(parents=True)
            (raw/'replay/actions.tsv').write_text('manifest');(raw/'replay/patch.diff').write_text('patch')
            (audit/'result.json').write_text('{"status":"PASS"}')
            (audit/'official-task-tests.stdout').write_text('task output')
            (audit/'official-task-result.json').write_text('{"status":"PASS"}')
            cleanup={'successful_fixture_removed':True,'mount_and_loop_removed':True,'original_firecracker_identities_unchanged':True}
            receipt={'status':'PASS','cleanup':cleanup,'capacity_bytes':20*2**30,'observed_volume_used_peak_bytes':5*2**30,'observed_host_available_min_bytes':43*2**30}
            (child/'result.json').write_text(json.dumps(receipt))
            cfg={'storage':'isolated','official_task':'unused','trajectory':{'manifest_sha256':sha(raw/'replay/actions.tsv'),'expected_patch_sha256':sha(raw/'replay/patch.diff')}}
            item=isolated_item('agentenv',None,child)
            value={'final_checks':{'independent_svg':'PASS','official_task_tests':'PASS'},'cleanup':{'service':'RETAINED_SHARED_SERVICE'},'host_memory':{'scope':'old'}}
            with patch('report_memory_metrics.report',return_value=value),patch('report_memory_metrics.write_report'),patch('exp1_official_task.grade',return_value={'status':'PASS'}):
                result=audit_items([item],cfg,Path(tmp)/'reports')[0]
                self.assertEqual(result['cleanup']['service'],'OWNED_TEMPORARY_SERVICE_REMOVED; ORIGINAL_SERVICE_RETAINED')
                self.assertFalse(result['storage']['temporary_checkpoint_images_retained'])
                receipt['cleanup']['successful_fixture_removed']=False
                (child/'result.json').write_text(json.dumps(receipt))
                with self.assertRaisesRegex(ValueError,'cleanup'):audit_items([item],cfg,Path(tmp)/'other')


if __name__=='__main__': unittest.main()
