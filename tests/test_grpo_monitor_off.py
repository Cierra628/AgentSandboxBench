"""Offline option/dispatch/report checks; no services, devices or task execution."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from platform_tasks import configuration,commands
from run_prettier6604_grpo import monitor_options,parallel_guest_cleanup,verify_path
from report_task_overview import write_tool_report


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))


class GrpoMonitorOffTests(unittest.TestCase):
    def test_configs_differ_only_in_sampling_and_old_default_keeps_full_mode(self):
        on=json.loads((ROOT/'configs/platform-prettier6604-grpo-light.json').read_text())
        off=json.loads((ROOT/'configs/platform-prettier6604-grpo-off.json').read_text())
        self.assertEqual({k:v for k,v in on.items() if k not in ('light_monitor','monitor_mode')},
                         {k:v for k,v in off.items() if k not in ('light_monitor','monitor_mode')})
        for mode in ('light','off'):
            cfg=configuration(ROOT/f'configs/platform-prettier6604-grpo-{mode}.json');check,live=commands(cfg,Path('/unused'))
            self.assertIn('--schedule',check);self.assertEqual(check[check.index('--schedule')+1],'grpo')
            self.assertIn('--prettier6604-grpo-capacity-gib',live);self.assertEqual(live[live.index('--prettier6604-grpo-capacity-gib')+1],'16')
            self.assertNotIn('--host-file-monitor',live)
            self.assertEqual('--light-monitor' in check,mode=='light')
            if mode=='off':
                self.assertEqual(check[check.index('--monitor-mode')+1],'off')
                self.assertEqual(live[live.index('--prettier6604-monitor-mode')+1],'off')
        self.assertFalse(configuration(ROOT/'configs/platform-prettier6604-grpo.json')['light_monitor'])

    def test_off_rejects_combined_modes_but_continuous_keeps_existing_options(self):
        self.assertEqual(monitor_options(SimpleNamespace()),'continuous')
        self.assertEqual(monitor_options(SimpleNamespace(monitor_mode='off')),'off')
        for key,value in [('schedule','bpo'),('schedule','tvcache'),('role','early_1'),('light_monitor',True),
            ('host_full_rootfs_mode','shared'),('host_shared_files',True),('host_scaling_mode','cold'),
            ('snapshot_cache_pair',True),('root_count',7),('shared_cache',True),('host_prefix_count',7)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                monitor_options(SimpleNamespace(monitor_mode='off',**{key:value}))
        cfg=json.loads((ROOT/'configs/platform-prettier6604-grpo-off.json').read_text())
        for change in ({'host_file_monitor':True},{'light_monitor':True},{'rollout':'bpo'}):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'config.json';write(path,dict(cfg,**change))
                with self.assertRaises(ValueError):configuration(path)

    def test_backend_check_and_live_argument_resets_keep_off(self):
        code=(ROOT/'scripts/validate_isolated_agentenv.py').read_text()
        for variable in ('check_args','task_args'):
            begin=code.index(variable+"=['--frozen-export'")
            end=code.index('subprocess.run(' if variable=='check_args' else 'call(stage,[sys.executable',begin)
            self.assertIn("if prettier_monitor_mode=='off':"+variable+"+=['--monitor-mode','off']",code[begin:end])

    def test_off_cleanup_does_not_claim_a_sampler_was_stopped(self):
        deleted=[];rows=parallel_guest_cleanup(['a','b'],None,deleted.append)
        self.assertEqual(set(deleted),{'a','b'});self.assertTrue(all(r['stopped'] is None and r['deleted'] for r in rows))

    def fixture(self,root):
        raw=root/'raw';raw.mkdir();roles=['backbone',*[f'early_{i}' for i in range(1,4)],*[f'late_{i}' for i in range(1,4)]]
        result=dict(status='PASS',task_id='fixture',schedule='grpo',monitor_mode='off',official_task_directory='/unused',
            cleanup=dict(monitoring_disabled=True),rollout_start_monotonic_ns=10,rollout_end_monotonic_ns=20,
            paths={role:dict(guest_action_sum_ns=27,official_task_status='FAIL') for role in roles})
        write(raw/'result.json',result);(raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\n'+''.join(f'{r}\tsegment\t{r}\n' for r in roles))
        for role in roles:
            p=raw/'rollouts'/role/'segment';p.mkdir(parents=True)
            (p/'steps.tsv').write_text('step\texit_code\tduration_ns\n'+''.join(f'{i:03}\t{1 if i==1 else 0}\t1\n' for i in range(1,28)))
            for name in ('guest-memory.tsv','guest-vmstat.tsv'):(p/name).write_text('timestamp_ns\n')
        audit=dict(status='PASS',schedule='grpo',roles=roles,actions=189,segments=7,logical_action_count=189,checkpoint_count=0,checkpoint_restore='NOT_RUN',cleanup=result['cleanup'])
        return raw,audit

    def test_off_report_audits_schedule_retains_failures_and_never_writes_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);raw,audit=self.fixture(root);out=root/'report';out.mkdir()
            with patch('report_prettier6604_grpo.audit',return_value=(audit,{},[])) as check:
                result=write_tool_report(raw,out)
            check.assert_called_once();self.assertEqual(result['tool_count'],189);self.assertEqual(result['tool_duration_sum_ns'],189)
            self.assertEqual(result['memory'],'NOT_MEASURED');self.assertEqual(result['official_task_status'],'FAIL')
            self.assertEqual(result['rollout_timing']['elapsed_ns'],10)
            self.assertTrue((out/'tools.tsv').exists());self.assertFalse((out/'memory.tsv').exists());self.assertFalse((out/'overview.png').exists())
            bad=root/'bad';bad.mkdir()
            (raw/'rollouts/backbone/segment/guest-memory.tsv').write_text('timestamp_ns\n1\n')
            with self.assertRaisesRegex(AssertionError,'boundary samples'):write_tool_report(raw,bad)

    def test_off_report_rejects_changed_tool_total_and_schedule_failure(self):
        for case in ('timing','audit'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);raw,audit=self.fixture(root);out=root/'report';out.mkdir()
                if case=='timing':
                    p=raw/'rollouts/backbone/segment/steps.tsv';p.write_text(p.read_text().replace('001\t1\t1','001\t1\t2'))
                with patch('report_prettier6604_grpo.audit',side_effect=AssertionError('changed source/exit/patch')):
                    with self.assertRaises(AssertionError):write_tool_report(raw,out)


if __name__=='__main__':unittest.main()
