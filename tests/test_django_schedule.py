"""Django scheduling metadata and disposable shell scripts, never real services."""
import csv
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import django_schedule_inputs as d
from run_prettier6604_grpo import execution_paths,prefix_tree_plan
from isolated_storage import capacity_budget,GIB
import run_django_schedule_matrix as matrix
import report_python_schedule_matrix as report
import validate_isolated_agentenv as native


class DjangoScheduleTests(unittest.TestCase):
    def test_original_paths_and_prefix_boundaries(self):
        for role,path in d.PATHS.items():
            original=[i for s in path for i in range(d.RANGES[s][0],d.RANGES[s][1]+1)]
            self.assertEqual(original,list(range(1,29)))
        for schedule in d.EXPECTED:
            paths=execution_paths(d.PATHS,schedule)
            actual=sum(d.RANGES[s][1]-d.RANGES[s][0]+1 for path in paths.values() for s in path)
            self.assertEqual(actual,d.EXPECTED[schedule]['actual_actions'])
        tree=prefix_tree_plan(d.PATHS)
        self.assertEqual(sorted(v['offset'] for v in tree['forks'].values()),[2,4])
        self.assertEqual([sum(d.RANGES[s][1]-d.RANGES[s][0]+1 for s in d.BACKBONE[:n]) for n in (2,4)],[18,26])

    def test_eight_gib_snapshot_disk_reservations_do_not_use_python_sizes(self):
        self.assertEqual([d.volume_gib(s) for s in d.EXPECTED],[48,80,80])
        self.assertFalse(capacity_budget(106*GIB,d.volume_gib('bpo')*GIB)['ready'])
        self.assertTrue(capacity_budget(107*GIB,d.volume_gib('bpo')*GIB)['ready'])

    def test_shell_preserves_source_state_expected_failure_and_default_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';work.mkdir();(work/'original').mkdir();(work/'segments/part').mkdir(parents=True)
            monitor=root/'monitor';monitor.mkdir();app=root/'app';app.mkdir()
            (work/'original/001.sh').write_text('carried=present; false | true\n')
            (work/'original/017.sh').write_text('test "$carried" = present || exit 99\nfalse\n')
            (work/'original/028.sh').write_text('test "$carried" = present\n')
            (work/'segments/part/actions.tsv').write_text('step\toriginal_step\texit_code\n001\t1\t0\n002\t17\t1\n003\t28\t0\n')
            (work/'clock-ns').write_text('#!/bin/sh\ndate +%s%N\n');(work/'clock-ns').chmod(0o755)
            runner=d.RUNNER.replace('cd /testbed','cd '+str(app)).replace('/workspace/asb-runtime-monitor',str(monitor))
            runner=runner.replace('git -C /testbed diff --binary','printf patch')
            (work/'guest-runner.sh').write_text(runner)
            import os
            env=dict(os.environ,OUTPUT_DIR=str(root/'out'),ROUND='part',BRANCH='early_1',ACTIONS_ROOT=str(work/'segments'))
            p=subprocess.run(['bash',work/'guest-runner.sh'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with (root/'out/steps.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            self.assertEqual([int(r['exit_code']) for r in rows],[0,1,0])
            self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))
            self.assertEqual((root/'out/patch.diff').read_text(),'patch')

    def test_native_invalid_modes_fail_before_any_allocation(self):
        for options in (dict(django_schedule='bpo'),dict(django_schedule_inputs=Path('/prepared')),
                        dict(django_schedule='grpo',django_schedule_inputs=Path('/prepared')),
                        dict(django_schedule='grpo',django_schedule_inputs=Path('/prepared'),
                             external_workload_config=Path('/fixed'),external_workload_upstream=Path('/upstream'),
                             external_creation_oci_input=Path('/oci'),external_workload_sampling='off')):
            with self.subTest(options=options),patch.object(native.os,'geteuid',return_value=0),\
                    patch.object(native.os,'posix_fallocate') as allocate,\
                    patch.object(native.subprocess,'run') as command:
                with self.assertRaises(ValueError):native.main(SimpleNamespace(**options))
                allocate.assert_not_called();command.assert_not_called()

    def test_native_explicit_route_and_capacity_preserve_default(self):
        tree=ast.parse((d.ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        capacity=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='django_schedule' and
            any(isinstance(c,ast.ImportFrom) for c in n.body))
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='django_schedule' and
            any(isinstance(c,ast.Assign) and ast.unparse(c.targets[0])=='stage' for c in n.body))
        command=next(n for n in branch.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='call')
        assignments=[n for n in branch.body[:branch.body.index(command)] if isinstance(n,ast.Assign)]
        for schedule in (None,'grpo','bpo','tvcache'):
            ns=dict(django_schedule=schedule,django_schedule_inputs=Path('/prepared'),external_value={'task_id':'django__django-16819'},
                capacity=32*GIB,GIB=GIB)
            with patch.object(d,'task_plan',return_value={}):
                exec(compile(ast.Module(body=[capacity],type_ignores=[]),'<capacity only>','exec'),ns)
            self.assertEqual(ns['capacity'],(32 if schedule is None else d.volume_gib(schedule))*GIB)
            if schedule is None:continue
            ns.update(mount=Path('/owned'),ROOT=d.ROOT,sys=sys,container='owned',receipt={'address':'http://127.0.0.1:123'},env={},call=Mock())
            exec(compile(ast.Module(body=assignments+[command],type_ignores=[]),'<command only>','exec'),ns)
            args=ns['call'].call_args.args
            argv=list(map(str,args[1]))
            self.assertEqual(Path(argv[2]).name,'run_python_schedule.py')
            self.assertEqual(argv[argv.index('--task-profile')+1],'django16819')
            self.assertEqual(argv[argv.index('--schedule')+1],schedule)
            self.assertEqual(argv[argv.index('--output')+1],'/owned/controller/external-workload')

    def test_collect_django_saved_fixture_and_publish_failure_or_unknown(self):
        from report_python_schedule_matrix import MIB
        for schedule in ('grpo','bpo','tvcache'):
            expected=d.EXPECTED[schedule]
            task=dict(status='PASS',schedule=schedule,actual_action_count=expected['actual_actions'],logical_action_count=196,
                paths={r:dict(exit_differences=[],tool_ns=1,official={'status':'NOT_RUN'}) for r in d.PATHS},
                cleanup={'owned':True},host_collector={'cgroup':'/owned'},strict_replay_status='PASS',
                save_restore=dict(saves=[{}]*expected['saves'],restores=[{}]*expected['restores']),
                schedule_elapsed_ns=20,actions_download_elapsed_ns=10,controller_elapsed_ns=30,
                schedule_start_monotonic_ns=1,original_paths_end_monotonic_ns=2)
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed',
                'successful_fixture_removed','original_service_unchanged')},
                django_schedule_storage_budget={'capacity_bytes':d.volume_gib(schedule)*GIB},observed_host_available_min_bytes=21*GIB)
            monitor=dict(status='PASS',interval_seconds=.1,host_pss_interval_seconds=1,
                sessions=[dict(status='PASS',collector_reaped=True,host_receipt={'pss_samples':1})])
            metrics=dict(status='OBSERVED',cgroup='/owned',start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1,
                p1=dict(unique_files=10,resident_bytes=3*MIB,counters_before=dict(file=4*MIB,anon=MIB,kernel=MIB)),
                pss=dict(processes=[dict(uid=0) for _ in range(7)],total_bytes=7*MIB,per_sandbox_bytes=MIB),
                expanded_files=dict(status='PARTIAL',query_window_regular=dict(observed_subtotal_bytes=5*MIB,observed_objects=12)),
                cache_layers=dict(block_devices=dict(method_totals={'cachestat':{}},fixed_ram_totals={}),outer_loop={},deleted_tmpfs={}))
            def read(path):
                p=Path(path)
                if p.name=='schedule-smoke-metrics.json':return metrics
                if p.name=='runtime-monitor.json':return monitor
                return task if p.parent.name=='external-workload' else outer
            with tempfile.TemporaryDirectory() as tmp,patch.object(report,'read',side_effect=read),\
                    patch.object(report,'rows',return_value=[dict(monotonic_ns='1',current=str(6*MIB))]):
                row=report.collect(Path(tmp)/'raw',dict(task='django16819',schedule=schedule,round=1))
                self.assertEqual((row['actual_actions'],row['logical_actions']),(expected['actual_actions'],196))
                self.assertEqual((row['current_schedule_peak_mib'],row['ordinary_window_mib']),(6,5))
                self.assertEqual(row['volume_gib'],d.volume_gib(schedule))
                self.assertEqual(report.repeat_statistics([row])[0]['task'],'django16819')
                output=Path(tmp)/'report'
                matrix.publish(dict(status='FAIL',runs=[],error='resource insufficient'),output)
                self.assertIn('有效0／9',(output/'REPORT.md').read_text())
                row.update(current_schedule_peak_mib=None,ordinary_window=None,ordinary_window_mib=None)
                matrix.publish(dict(status='FAIL',runs=[row],error='later resource gate'),output)
                content=(output/'REPORT.md').read_text()
                self.assertIn('未知',content);self.assertIn('PARTIAL',content)
                self.assertIn('原10层',content)

    def test_publish_reused_curve_from_another_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);previous=root/'previous';previous.mkdir()
            curve=previous/'curves.png';curve.write_bytes(b'fixture')
            output=root/'continued'
            row=dict(round=1,schedule='grpo',status='FAIL',curve=str(curve),raw='/saved/raw')
            with patch.object(matrix,'repeat_statistics',return_value=[]):
                matrix.publish(dict(status='FAIL',runs=[row]),output)
            self.assertIn('[逐次曲线](../previous/curves.png)',(output/'REPORT.md').read_text())
            self.assertTrue((output/'../previous/curves.png').is_file())


if __name__=='__main__':unittest.main()
