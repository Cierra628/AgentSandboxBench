"""Temporary metadata and mocked commands only; no services, images or devices."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from report_task_overview import tool_rows
from run_host_tool_replay import check_inputs


class HostToolReplayTests(unittest.TestCase):
    def test_tool_timings_use_new_replay_only_not_inherited_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'replayed/steps/001').mkdir(parents=True)
            (root/'replayed/steps/001/result.json').write_text(json.dumps(dict(guest_elapsed_ns=50,exit_code=7)))
            (root/'captured/steps/001').mkdir(parents=True)
            (root/'captured/steps/001/result.json').write_text(json.dumps(dict(guest_elapsed_ns=900,exit_code=7)))
            (root/'runtime-monitor.json').write_text(json.dumps(dict(sessions=[dict(role='replay',sandbox_id='new')])) )
            rows,total=tool_rows(root,dict(tool_capture_demo=True))
            self.assertEqual(total,50);self.assertEqual(rows[0]['sandbox_id'],'new')
            self.assertEqual(rows[0]['exit_code'],7)

    def test_check_command_is_frozen_light_without_model_or_service(self):
        with patch('run_host_tool_replay.subprocess.run') as call:
            cfg,spec,task=check_inputs(ROOT/'configs/platform-sphinx7590-host-file.json',ROOT/'configs/host-csv-light-replay.json')
        command=call.call_args.args[0]
        self.assertIn('--replay-source',command);self.assertIn('--light-monitor',command);self.assertIn('--check-only',command)
        self.assertNotIn('--agent-config',command);self.assertEqual(task['task_id'],'csv-category-sum-v1')
        self.assertEqual(task['memory_mib'],512)

    def test_invalid_image_config_rejected_before_command(self):
        with patch('run_host_tool_replay.subprocess.run') as call:
            with self.assertRaises(ValueError):check_inputs(ROOT/'configs/platform-prettier6604-host-file.json',ROOT/'configs/host-csv-light-replay.json')
        call.assert_not_called()

    def test_worker_records_prepare_failure_without_starting_a_service(self):
        import run_host_tool_replay as worker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=dict(inputs='/input',reference='/reference',cache_backup_directory='/backup')
            spec=dict(task_config=Path('/task'),replay_source=Path('/trace'))
            task=dict(task_id='csv-category-sum-v1',memory_mib=512)
            monitor=SimpleNamespace(stop=lambda:dict(status='PASS'))
            with patch.object(worker,'ROOT',root),patch.object(worker,'check_inputs',return_value=(cfg,spec,task)),\
                 patch.object(worker,'preflight'),patch.object(worker.shutil,'copy2'),\
                 patch('host_file_monitor.measurement_group',return_value=(root/'group','owned')),\
                 patch('host_file_monitor.HostFileMonitor',return_value=monitor),\
                 patch.object(worker.subprocess,'run',side_effect=RuntimeError('mock prepare stop')) as command:
                self.assertEqual(worker.run(Path('/config'),Path('/spec')),1)
            self.assertEqual(command.call_count,1)
            saved=list((root/'.artifacts/host-tool-replay').glob('*/result.json'))
            self.assertEqual(len(saved),1)
            self.assertEqual(json.loads(saved[0].read_text())['failed_stage'],'prepare')

    def test_worker_success_uses_original_python_cleanup_contract(self):
        import run_host_tool_replay as worker
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);storage=root/'.artifacts/storage-isolated-service/mock'
            raw=storage/'python-backbone';raw.mkdir(parents=True)
            cleanup={key:True for key in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
                     'original_firecracker_identities_unchanged','original_service_unchanged')}
            (storage/'result.json').write_text(json.dumps(dict(status='PASS',cleanup=cleanup,host_file_residency={'status':'NOT_REQUESTED'})))
            (raw/'result.json').write_text(json.dumps(dict(status='PASS',cleanup={'owned':True},task_outcome='PASS',
                checks={'task_outcome_preserved':'PASS','frozen_replay_matches_calls_and_final_file':'PASS'},replay={'action_count':3})))
            (raw/'runtime-monitor.json').write_text(json.dumps(dict(profile='light',host_vmm_requested=False,
                sessions=[dict(status='PASS',collector_reaped=True,receipt={'interval_seconds':.1})])))
            cfg=dict(inputs='/input',reference='/reference',cache_backup_directory='/backup')
            spec=dict(task_config=Path('/task'),replay_source=Path('/trace'))
            def call(command,**kwargs):
                if '--host-current-only' in command:kwargs['stdout'].write('status=PASS result_dir='+str(storage)+'\n')
            with patch.object(worker,'ROOT',root),patch.object(worker,'check_inputs',return_value=(cfg,spec,dict(task_id='csv-category-sum-v1',memory_mib=512))),\
                 patch.object(worker,'preflight'),patch.object(worker.shutil,'copy2'),\
                 patch('host_file_monitor.measurement_group',return_value=(root/'group','owned')),\
                 patch('host_file_monitor.HostFileMonitor',return_value=SimpleNamespace(stop=lambda:dict(status='PASS'))),\
                 patch.object(worker.subprocess,'run',side_effect=call),patch('tool_trajectory.report'),patch('report_host_file.report'):
                self.assertEqual(worker.run(Path('/config'),Path('/spec')),0)

    def test_light_tool_flag_rejects_new_capture_before_guest_creation(self):
        from run_tool_capture import run
        with patch('run_tool_capture.validate',return_value=({},None,None,None)):
            with self.assertRaisesRegex(ValueError,'requires frozen task replay'):
                run(SimpleNamespace(inputs=Path('/unused'),light_monitor=True))

    def test_current_only_requires_exact_new_path_before_service_actions(self):
        from validate_isolated_agentenv import ordinary_current_only
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        start=next(i for i,n in enumerate(run.body) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='python_tool_capture' for t in n.targets))
        block=run.body[start:start+5]
        code=compile(ast.Module(body=block,type_ignores=[]),'<actual new mode guards>','exec')
        good=dict(python_tool_capture_demo=True,python_tool_replay_source=Path('/trace'),python_tool_task_config=Path('/task'),host_current_only=True,host_file_monitor=True)
        ns=dict(light_monitor=True,exp1_light=False,ordinary_current_only=ordinary_current_only,current_monitor_off=False)
        exec(code,dict(ns,args=SimpleNamespace(**good)))
        for field in ('python_tool_capture_demo','python_tool_replay_source','python_tool_task_config','host_file_monitor'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                exec(code,dict(ns,args=SimpleNamespace(**dict(good,**{field:None}))))
        with self.assertRaises(ValueError):exec(code,dict(ns,args=SimpleNamespace(**good),light_monitor=False))

    def test_new_mode_does_not_start_pss_or_inventory_and_uses_common_lifecycle(self):
        code=(ROOT/'scripts/run_host_tool_replay.py').read_text()
        self.assertIn('include_pss=False',code);self.assertIn("'--host-current-only'",code)
        self.assertIn("'--python-tool-replay-source'",code)
        self.assertNotIn('docker\',\'run',code)
        backend=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        self.assertIn('not (private_control or full_rootfs_mode or host_current_only)',backend)
        self.assertIn('if light_monitor:check_command+=[\'--light-monitor\']',backend)
        self.assertIn('if light_monitor:task_command+=[\'--light-monitor\']',backend)

    def test_frozen_source_count_is_not_the_four_call_demo_and_bad_results_fail(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                    isinstance(n.test,ast.Name) and n.test.id=='tool_source' and isinstance(n.body[0],ast.Assign))
        code=compile(ast.Module(body=[branch],type_ignores=[]),'<actual source acceptance>','exec')
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp);(source/'manifest.json').write_text(json.dumps(dict(action_count=3,expected_failures={})))
            (source/'task.json').write_text(json.dumps(dict(task_id='csv-category-sum-v1')))
            value=dict(task_id='csv-category-sum-v1',replay=dict(action_count=3,expected_failures={}),task_outcome='PASS',
                       checks=dict(independent_task='PASS',independent_replayed_task='PASS',task_outcome_preserved='PASS'))
            ns=dict(tool_source=source,task_result=value,args=SimpleNamespace(python_tool_task_config=source/'task.json'),json=json)
            exec(code,ns)
            for key,bad in [('action_count',4),('expected_failures',{'001':7})]:
                original=value['replay'][key];value['replay'][key]=bad
                with self.assertRaises(AssertionError):exec(code,ns)
                value['replay'][key]=original
            value['checks']['independent_replayed_task']='FAIL'
            with self.assertRaises(AssertionError):exec(code,ns)
            ns.update(tool_source=None,online_agent=False,task_result={'replay':{'action_count':4}})
            exec(code,ns)
            ns['task_result']['replay']['action_count']=3
            with self.assertRaises(AssertionError):exec(code,ns)

    def test_directory_upload_semantics_and_custom_script_complete_real_local_collector(self):
        from sandbox_runtime_monitor import RuntimeMonitor,MEMFIELDS
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw=root/'raw';raw.mkdir();proc=root/'proc';proc.mkdir()
            values={key:0 for key in MEMFIELDS};values.update(MemTotal=4096,MemAvailable=1024,Cached=512)
            (proc/'meminfo').write_text(''.join(f'{k}: {v} kB\n' for k,v in values.items()))
            script=root/'guest-code/asb-runtime-monitor.py'
            def cli(label,*argv,timeout=15):
                if argv[0]=='upload':
                    source,target=Path(argv[2]),Path(argv[3])
                    # Actual CLI appends the source directory when the target exists.
                    if target.exists():target=target/source.name
                    shutil.copytree(source,target);return ''
                if argv[0]=='download':
                    source,target=Path(argv[2]),Path(argv[3]);shutil.copytree(source,target/source.name);return ''
                command=list(map(str,argv[2:]))
                if 'collect' in command:command+=['--proc-root',str(proc)]
                return subprocess.check_output(command,text=True,timeout=timeout).strip()
            with patch('sandbox_runtime_monitor.GUEST_ROOT',str(root/'state')):
                monitor=RuntimeMonitor(raw,cli,light=True,guest_script=str(script))
                try:
                    monitor.start('owned','replay');self.assertTrue(script.is_file())
                    monitor.stop('owned')
                    self.assertTrue(monitor.sessions[0]['collector_reaped'])
                    self.assertEqual(monitor.sessions[0]['status'],'PASS')
                finally:
                    if monitor.active:monitor.stop('owned')


if __name__=='__main__':unittest.main()
