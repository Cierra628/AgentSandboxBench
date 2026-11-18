"""Offline configuration, actual guard branches and saved-log fixtures; no VMs or services."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import platform_tasks as platform
import run_host_file_workflow as entry
from validate_isolated_agentenv import ordinary_current_only
import test_host_current_tasks as current_tests
CLEANUP=current_tests.CLEANUP
PROFILES=current_tests.PROFILES


class HostCurrentSamplingControlTests(unittest.TestCase):
    def test_off_config_changes_only_sampling_for_all_three_tasks(self):
        on=json.loads((ROOT/'configs/platform-prettier6604-host-current.json').read_text())
        off=json.loads((ROOT/'configs/platform-prettier6604-host-current-off.json').read_text())
        self.assertEqual(off,dict(on,monitor_mode='off'))
        helper=current_tests.HostCurrentTaskTests()
        for profile in PROFILES:
            cfg=helper.config(profile,monitor_mode='off')
            self.assertFalse(cfg['light_monitor'])
            check,live=platform.commands(cfg,Path('/unused'))
            old_check,old_live=platform.commands(helper.config(profile),Path('/unused'))
            def without_sampling(argv):
                result=[];i=0
                while i<len(argv):
                    if argv[i]=='--light-monitor':i+=1
                    elif argv[i] in ('--monitor-mode','--prettier6604-monitor-mode','--python-monitor-mode'):
                        self.assertEqual(argv[i+1],'off');i+=2
                    else:result.append(argv[i]);i+=1
                return result
            self.assertEqual(without_sampling(check),without_sampling(old_check))
            self.assertEqual(without_sampling(live),without_sampling(old_live))
            self.assertIn('--host-file-monitor',live);self.assertIn('--host-current-only',live)

    def test_outer_off_disables_only_sampling_and_rejects_conflicting_on(self):
        cfg=ROOT/'configs/platform-prettier6604-host-current-off.json'
        for flag in ([],['--host-sampling','off']):
            with patch.object(sys,'argv',['entry',str(cfg),*flag]),\
                    patch.object(entry.subprocess,'check_output',return_value='systemd'),\
                    patch('platform_tasks.preflight'),\
                    patch.object(entry.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as call:
                self.assertEqual(entry.main(),0)
            cmd=call.call_args_list[0].args[0]
            self.assertIn('--setenv=ASB_HOST_FILE_SAMPLING=off',cmd)
            for value in ('--property=MemoryAccounting=yes','--property=IOAccounting=yes'):
                self.assertIn(value,cmd)
            self.assertEqual(cmd[-4:],['bash',str(ROOT/'scripts/use-platform.sh'),'run',str(cfg)])
        for argv in ([str(cfg),'--host-sampling','on'],
                     [str(ROOT/'configs/platform-prettier6604-host-current.json'),'--host-sampling','off'],
                     [str(ROOT/'configs/platform-prettier6604-host-file.json'),'--host-sampling','off']):
            with patch.object(sys,'argv',['entry',*argv]),patch.object(entry.subprocess,'check_output') as docker,\
                    patch('platform_tasks.preflight') as preflight:
                with self.assertRaises(SystemExit):entry.main()
                docker.assert_not_called();preflight.assert_not_called()

    def test_native_requires_matching_guest_host_and_accounting_modes(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        start=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign) and
                   any(isinstance(t,ast.Name) and t.id=='current_monitor_off' for t in n.targets))
        guard=next(n for n in main.body if isinstance(n,ast.If) and ast.unparse(n.test).startswith('host_current_only and'))
        code=compile(ast.Module(body=main.body[start:start+3]+[guard],type_ignores=[]),'<actual off guards>','exec')
        helper=current_tests.HostCurrentTaskTests()
        for profile in PROFILES:
            mode='prettier6604_monitor_mode' if profile=='prettier6604' else 'python_monitor_mode'
            args=helper.native_args(profile);args.light_monitor=False;setattr(args,mode,'off')
            ns=dict(args=args,host_sampling='off',host_current_only=True,light_monitor=False,
                scaling=False,host_matched=False,host_trace_dual=False,independent_phase=None,
                python_tool_capture=False,tool_source=None,exp1_light=False,ordinary_current_only=ordinary_current_only)
            ns.update(external_off=False,external_config=None)
            exec(code,dict(ns))
            for change in ({'host_file_monitor':False},{'host_current_only':False},{'light_monitor':True},
                           {mode:'continuous'},{'immutable_cache_seed':None},{'python_tool_capture_demo':True},
                           {'damon_observe':True},{'prettier6604_export':'/export'}):
                with self.subTest(profile=profile,change=change),self.assertRaises(ValueError):
                    bad=SimpleNamespace(**dict(vars(args),**change));exec(code,dict(ns,args=bad))
            with self.assertRaises(ValueError):exec(code,dict(ns,host_sampling='on'))

    def test_native_off_records_creation_without_querying_memory_baseline(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
            any(ast.unparse(t)=="receipt['host_file_creation']" for t in n.targets))
        code=compile(ast.Module(body=[node],type_ignores=[]),'<actual creation receipt>','exec')
        for sampling in ('on','off'):
            receipt={};state={'measurement_slice':{'subtree_control':'io memory'}}
            with patch('host_file_monitor.counters',return_value={'current':5}) as counters:
                exec(code,dict(receipt=receipt,host_group=Path('/owned'),actual='/owned/service',
                    host_sampling=sampling,scaling=False,host_counters=counters,io_state=state,time=time))
            row=receipt['host_file_creation']
            self.assertEqual(row['host_sampling'],sampling)
            self.assertEqual(row['io_controller_state'],state)
            if sampling=='off':
                counters.assert_not_called();self.assertIsNone(row['before_guest_counters'])
            else:
                counters.assert_called_once_with(Path('/owned'))
                self.assertEqual(row['before_guest_counters'],{'current':5})

    def test_report_requires_real_absence_of_all_sampling_and_preserves_task_failure(self):
        # Exercise the real tools-only writer, not a mocked report success.
        for contamination in (None,'periodic','boundary','host','mincore','missing-off','host-on','baseline'):
            with self.subTest(contamination=contamination),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);workflow=root/'.artifacts/workflow';child=root/'.artifacts/storage'
                raw=child/'prettier6604';workflow.mkdir(parents=True);raw.mkdir(parents=True)
                cfg=dict(backend='agentenv',task_id='prettier__prettier-6604',rollout='single',
                    host_file_monitor=True,host_current_only=True,monitor_mode='off')
                monitor=dict(status='NOT_REQUESTED',host_sampling='off')
                creation=dict(host_sampling='off',before_guest_counters=None)
                if contamination=='missing-off':monitor['status']='PASS'
                if contamination=='host-on':creation['host_sampling']='on'
                if contamination=='baseline':creation['before_guest_counters']={'current':1}
                (workflow/'effective-config.json').write_text(json.dumps(cfg))
                (workflow/'result.json').write_text(json.dumps(dict(status='PASS',storage_result_dir=str(child),host_file_monitor=monitor)))
                (child/'result.json').write_text(json.dumps(dict(status='PASS',cleanup=CLEANUP,
                    host_file_creation=creation,host_file_residency={'status':'NOT_REQUESTED'})))
                (raw/'result.json').write_text(json.dumps(dict(status='PASS',task_id=cfg['task_id'],
                    monitor_mode='off',cleanup={'monitoring_disabled':True},guest_action_sum_ns=2,
                    official_task_status='FAIL',strict_replay_status='FAIL',diagnostic_status='PASS')))
                (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\towned\n')
                segment=raw/'rollouts/backbone/read_repo';segment.mkdir(parents=True)
                (segment/'steps.tsv').write_text('step\texit_code\tduration_ns\n001\t141\t2\n')
                for name in ('guest-memory.tsv','guest-vmstat.tsv'):(segment/name).write_text('timestamp_ns\n')
                if contamination=='periodic':(raw/'runtime-monitor.json').write_text('{}')
                if contamination=='boundary':(segment/'guest-memory.tsv').write_text('timestamp_ns\n1\n')
                if contamination=='host':(workflow/'host-file').mkdir()
                if contamination=='mincore':(child/'host-file-residency.json').write_text('{}')
                out=root/'.artifacts/report'
                with patch.object(platform,'ROOT',root),patch('report_host_file.report') as host_report:
                    if contamination:
                        with self.assertRaises((AssertionError,ValueError)):platform.report(workflow,out)
                    else:
                        platform.report(workflow,out)
                        summary=json.loads((out/'summary.json').read_text())
                        self.assertEqual(summary['memory'],'NOT_MEASURED')
                        self.assertEqual(summary['host_file']['status'],'NOT_REQUESTED')
                        for key in ('current_peak_bytes','file_peak_bytes','current_before_guest_bytes','current_peak_increase_bytes'):
                            self.assertIsNone(summary['host_file'][key])
                        self.assertEqual(summary['tool_duration_sum_ns'],2)
                        result=json.loads((out/'result.json').read_text())
                        self.assertEqual(result['official_task_status'],'FAIL')
                        self.assertEqual(result['strict_replay_status'],'FAIL')
                        self.assertTrue((out/'tools.tsv').exists())
                        self.assertFalse((out/'memory.tsv').exists())
                        self.assertFalse(list(out.rglob('*.png')))
                    host_report.assert_not_called()


if __name__=='__main__':unittest.main()
