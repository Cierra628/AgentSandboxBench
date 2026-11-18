"""Offline config, session and report checks; no services, devices or BPF."""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from platform_tasks import configuration, commands, preflight
from run_light_rollout_pair import inspect_run


class LightRolloutTests(unittest.TestCase):
    def test_explicit_configs_and_old_defaults(self):
        for schedule in ('grpo','bpo'):
            cfg=configuration(ROOT/f'configs/platform-prettier6604-{schedule}-light-host.json')
            self.assertTrue(cfg['light_monitor']);self.assertTrue(cfg['host_file_monitor'])
            check,live=commands(cfg,Path('/unused'))
            self.assertIn('--light-monitor',check);self.assertIn('--light-monitor',live)
            self.assertIn('--host-file-monitor',live)
            self.assertEqual(preflight(cfg,offline=True)['status'],'VALIDATED')
            old=configuration(ROOT/f'configs/platform-prettier6604-{schedule}.json')
            self.assertFalse(old['light_monitor']);self.assertNotIn('--light-monitor',commands(old,Path('/unused'))[0])
        cfg=json.loads((ROOT/'configs/platform-prettier6604-bpo-light-host.json').read_text())
        for change in ({'light_monitor':False},{'shared_cache_measurement':True},{'rollout':'tvcache'}):
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'config.json';p.write_text(json.dumps(dict(cfg,**change)))
                with self.assertRaises(ValueError):configuration(p)

    def test_light_flag_survives_both_schedule_argument_resets(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for variable in ('check_args','task_args'):
            start=code.index(variable+"=['--frozen-export'")
            end=code.index('subprocess.run(' if variable=='check_args' else 'call(stage,[sys.executable',start)
            self.assertIn("if light_monitor:"+variable+"+=['--light-monitor']",code[start:end])
        code=(ROOT/'scripts/run_prettier6604_grpo.py').read_text()
        self.assertIn('light=host_restore or light_monitor',code)
        self.assertIn('if monitoring and not (host_restore or light_monitor):',code)
        self.assertLess(code.index("runtime.stop,sid,'checkpoint'"),code.index('super().capture_agentenv_template'))
        self.assertIn("finally:await asyncio.to_thread(runtime.start,sid,'backbone','after-checkpoint')",code)

    def fixture(self,root,schedule):
        def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v))
        workflow=root/'workflow';storage=root/'storage';raw=storage/'prettier6604';report=root/'report'
        write(workflow/'result.json',dict(status='PASS',cache_released=True,storage_result_dir=str(storage),report_dir=str(report)))
        write(storage/'result.json',dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged')}))
        roles=['backbone',*[f'early_{i}' for i in range(1,4)],*[f'late_{i}' for i in range(1,4)]]
        task=dict(status='PASS',actual_action_count=189 if schedule=='grpo' else 84,logical_action_count=189,
            first_tool_start_monotonic_ns=200,host_before_first_tool=dict(start_monotonic_ns=190,end_monotonic_ns=195,counters=dict(file=8192)),
            paths={role:dict(official_passed=0,official_failed=2,official_task_status='FAIL') for role in roles})
        if schedule=='grpo':task.pop('logical_action_count')
        write(raw/'result.json',task)
        sessions=[]
        for i,role in enumerate(roles+(['backbone']*2 if schedule=='bpo' else [])):
            directory=f'runtime/{role}/{i}';(raw/directory).mkdir(parents=True)
            sessions.append(dict(sandbox_id=role,status='PASS',collector_reaped=True,raw_directory=directory,receipt=dict(profile='light',interval_seconds=.1,samples=2)))
        write(raw/'runtime-monitor.json',dict(status='PASS',profile='light',host_vmm_requested=False,guest_performance_version=None,sessions=sessions))
        write(report/'summary.json',dict(tool_duration_sum_ns=10,per_sandbox=[],rollout_timing=dict(start_monotonic_ns=100,end_monotonic_ns=400)))
        write(report/'host-file/summary.json',dict(status='PASS'))
        with self.make_stream(workflow) as f:
            w=csv.DictWriter(f,fieldnames=['monotonic_ns','file'],delimiter='\t');w.writeheader()
            w.writerows(dict(monotonic_ns=t,file=v) for t,v in [(50,8192),(250,12288),(350,4096),(450,4096)])
        return workflow,raw

    def make_stream(self,workflow):
        (workflow/'host-file').mkdir();return (workflow/'host-file/memory.tsv').open('w')

    def test_two_session_shapes_and_signed_delta(self):
        for schedule in ('grpo','bpo'):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);workflow,raw=self.fixture(root,schedule)
                value=inspect_run(workflow,schedule,root/'output')
                self.assertEqual(value['task_window_file_final_delta_bytes'],-4096)
                self.assertEqual(value['physical_savings'],'NOT_MEASURED')
                self.assertEqual(value['official_task_status'],'FAIL')
                self.assertEqual(value['guest_sessions'],7 if schedule=='grpo' else 9)
                session=json.loads((raw/'runtime-monitor.json').read_text())['sessions'][0]
                (raw/session['raw_directory']/'guest-cpu.tsv').write_text('unexpected')
                with self.assertRaises(AssertionError):inspect_run(workflow,schedule,root/'rejected')

    def test_retained_inventory_failure_requires_explicit_separate_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);workflow,raw=self.fixture(root,'grpo')
            def update(path,**changes):
                value=json.loads(path.read_text());value.update(changes);path.write_text(json.dumps(value));return value
            update(workflow/'result.json',status='FAIL')
            outer_path=raw.parent/'result.json'
            outer=json.loads(outer_path.read_text());outer['cleanup'].update(failed_fixture_retained=True,successful_fixture_removed=False,host_file_residency_captured=False)
            update(outer_path,status='FAIL',cleanup=outer['cleanup'],default_runtime_report=dict(status='PASS'),
                host_file_residency=dict(status='INCOMPLETE',errors=[],files=[dict(stable=False,path=str(raw.parent/'volume.ext4'),metadata_changed=True)]))
            before=outer_path.read_text()
            with self.assertRaises(AssertionError):inspect_run(workflow,'grpo',root/'rejected')
            value=inspect_run(workflow,'grpo',root/'accepted',retained_inventory_failure=True)
            self.assertEqual(value['status'],'PASS_LIGHT_METRICS_WITH_INCOMPLETE_INVENTORY')
            self.assertEqual(value['original_workflow_status'],'FAIL')
            self.assertEqual(outer_path.read_text(),before)


if __name__=='__main__':unittest.main()
