"""Offline-only Matplotlib adapter tests; no live service, devices or real tasks."""
import ast
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import matplotlib_schedule_inputs as m
import run_django_schedule_matrix as matrix
import report_python_schedule_matrix as report
import validate_isolated_agentenv as native
from isolated_storage import capacity_budget,GIB
from run_prettier6604_grpo import execution_paths,prefix_tree_plan


class MatplotlibScheduleTests(unittest.TestCase):
    def test_original_paths_save_points_counts_and_final_tests(self):
        for path in m.PATHS.values():
            self.assertEqual([i for s in path for i in range(m.RANGES[s][0],m.RANGES[s][1]+1)],list(range(1,96)))
        trie=prefix_tree_plan(m.PATHS)
        self.assertEqual(sorted(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for s in v['prefix']) for v in trie['forks'].values()),[73,87])
        for schedule,wanted in m.EXPECTED.items():
            executed=execution_paths(m.PATHS,schedule)
            self.assertEqual(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for p in executed.values() for s in p),wanted['actual_actions'])
            self.assertEqual(wanted['logical_actions'],665)
            for path in executed.values():
                for original in (88,93,95):self.assertTrue(any(m.RANGES[s][0]<=original<=m.RANGES[s][1] for s in path))

    def test_only_matplotlib_uses_displayed_104_gib_allowance_and_larger_layers(self):
        self.assertFalse(capacity_budget(103.95*GIB,80*GIB)['ready'])
        budget=capacity_budget(103.95*GIB,80*GIB,margin=m.START_MARGIN_BYTES)
        self.assertTrue(budget['ready']);self.assertEqual(budget['host_reserve_bytes'],20*GIB)
        self.assertAlmostEqual(budget['required_available_bytes']/GIB,103.5)
        self.assertTrue(capacity_budget(103.89*GIB,80*GIB,margin=m.START_MARGIN_BYTES)['ready'])
        self.assertFalse(capacity_budget(103.49*GIB,80*GIB,margin=m.START_MARGIN_BYTES)['ready'])
        self.assertEqual(sum(m.storage_components('grpo').values()),40.5)
        self.assertEqual(sum(m.storage_components('bpo').values()),76.5)
        self.assertEqual(m.storage_components('tvcache')['image_layers'],24)

    def test_independent_exec_bash_preserves_failures_and_full_staged_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';repo.mkdir();work=root/'work';(work/'original').mkdir(parents=True)
            (work/'segments/part').mkdir(parents=True);(root/'monitor').mkdir()
            def git(*args):return subprocess.run(['git','-C',repo,*args],check=True,capture_output=True,text=True,timeout=10).stdout
            git('init','-q');(repo/'file').write_text('original\n');git('add','file');git('-c','user.name=Unit','-c','user.email=unit@example.invalid','commit','-qm','initial')
            (work/'original/001.sh').write_text("exec bash -lc 'export carried=wrong; false | true'\n")
            (work/'original/002.sh').write_text("exec bash -lc 'test -z \"${carried:-}\" || exit 99; test -z \"${HTTP_PROXY:-}\" || exit 98; exit 4'\n")
            (work/'original/003.sh').write_text('exec bash -lc '+__import__('shlex').quote('cd '+str(repo)+" && printf 'staged\\n' > file && git add -A")+'\n')
            (work/'segments/part/actions.tsv').write_text('step\toriginal_step\texit_code\n001\t1\t0\n002\t2\t4\n003\t3\t0\n')
            (work/'clock-ns').write_text('#!/bin/sh\ndate +%s%N\n');(work/'clock-ns').chmod(0o755)
            runner=m.RUNNER.replace('/testbed',str(repo)).replace('/workspace/asb-runtime-monitor',str(root/'monitor'))
            (work/'guest-runner.sh').write_text(runner)
            env=dict(os.environ,OUTPUT_DIR=str(root/'out'),ROUND='part',BRANCH='early_1',ACTIONS_ROOT=str(work/'segments'),HTTP_PROXY='must-be-cleared')
            result=subprocess.run(['bash',work/'guest-runner.sh'],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'out/steps.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            self.assertEqual([int(r['exit_code']) for r in rows],[0,4,0]);self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))
            self.assertIn('+staged',(root/'out/patch.diff').read_text());self.assertEqual(git('diff','--binary'),'')

    def test_final_checks_map_original_steps_and_reject_missing_or_failed_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for schedule in m.EXPECTED:
                for role,path in execution_paths(m.PATHS,schedule).items():
                    segment=path[-1];directory=root/'rollouts'/role/segment/'stdout';directory.mkdir(parents=True,exist_ok=True)
                    for original,text in ((88,'60 passed'),(93,'60 passed'),(95,'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT')):
                        (directory/f'{original-m.RANGES[segment][0]+1:03}.log').write_text(text)
                    self.assertEqual(m.validate_role_outputs(root,role,path)['test_pass_counts'],[60,60])
            path=execution_paths(m.PATHS,'bpo')['late_1'];log=root/'rollouts/late_1'/path[-1]/'stdout/001.log'
            for text in ('59 passed','60 passed, 1 failed','no summary'):
                log.write_text(text)
                with self.assertRaises(AssertionError):m.validate_role_outputs(root,'late_1',path)

    def test_boundary_checks_source_index_helpers_backup_and_progress(self):
        code=m.boundary_code(['inspect'],['/tmp/fixed.py']);compile(code,'<boundary>','exec')
        for token in ("git('diff','HEAD','--binary')","git('diff','--cached','--binary')","backup.read_text()","Path(p).read_text()","steps="):
            self.assertIn(token,code)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';source=repo/'lib/matplotlib/axes/_axes.py';source.parent.mkdir(parents=True);source.write_text('base\n')
            def git(*a):subprocess.run(['git','-C',repo,*a],check=True,capture_output=True,timeout=10)
            git('init','-q');git('add','.');git('-c','user.name=Unit','-c','user.email=unit@example.invalid','commit','-qm','initial')
            source.with_name('_axes.py.backup').write_text('base\n');source.write_text('modified\n')
            helper=root/'helper.py';helper.write_text('original helper\n');marker=root/'marker';marker.write_text('saved')
            step=root/'artifacts/inspect/steps.tsv';step.parent.mkdir(parents=True);step.write_text('completed')
            code=m.boundary_code(['inspect'],[str(helper)]).replace('/testbed',str(repo)).replace('/workspace/python-schedule-marker',str(marker)).replace('/workspace/artifacts/exp5/backbone',str(root/'artifacts'))
            a=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10));self.assertIn('+modified',a['patch'])
            helper.write_text('changed helper\n');b=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10));self.assertNotEqual(a,b)
            source.with_name('_axes.py.backup').write_text('wrong backup')
            self.assertNotEqual(subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10).returncode,0)

    def test_invalid_native_flags_fail_before_live_actions(self):
        for options in [dict(matplotlib_schedule='grpo'),dict(matplotlib_schedule_inputs=Path('/prepared')),
                dict(matplotlib_schedule='bpo',matplotlib_schedule_inputs=Path('/prepared')),
                dict(matplotlib_schedule='grpo',matplotlib_schedule_inputs=Path('/prepared'),c4_schedule='grpo',c4_schedule_inputs=Path('/c4'))]:
            with patch.object(native.os,'geteuid',return_value=0),patch.object(native.os,'posix_fallocate') as alloc,patch.object(native.subprocess,'run') as run:
                with self.assertRaises(ValueError):native.main(SimpleNamespace(**options))
                alloc.assert_not_called();run.assert_not_called()

    def test_native_command_and_runner_profile_are_explicit(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='matplotlib_schedule' and any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='stage' for x in n.body))
        self.assertIn("'--task-profile', 'matplotlib24177'",ast.unparse(branch))
        self.assertIn('665 if matplotlib_schedule',ast.unparse(branch))
        source=(ROOT/'scripts/run_python_schedule.py').read_text();self.assertIn("(2 if cython else 1 if text_editing else 8 if matplotlib else 4)*2**30",source)
        self.assertIn("profile.boundary_code(prefix,cfg['checkpoint_helper_paths'])",source)
        self.assertIn("'HEAD ' if matplotlib else ''",source)

    def test_collect_counts_layers_and_matplotlib_report(self):
        for schedule,expected in m.EXPECTED.items():
            task=dict(status='PASS',schedule=schedule,actual_action_count=expected['actual_actions'],logical_action_count=665,
                paths={r:dict(exit_differences=[],tool_ns=1,official={'status':'PASS'}) for r in m.PATHS},
                cleanup={'owned':True},host_collector={'cgroup':'/owned'},strict_replay_status='PASS',
                save_restore=dict(saves=[{}]*expected['saves'],restores=[{}]*expected['restores']),
                schedule_elapsed_ns=20,actions_download_elapsed_ns=10,controller_elapsed_ns=30,
                schedule_start_monotonic_ns=1,original_paths_end_monotonic_ns=2)
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged')},
                matplotlib_schedule_storage_budget={'capacity_bytes':80*GIB},observed_host_available_min_bytes=21*GIB)
            mon=dict(status='PASS',interval_seconds=.1,host_pss_interval_seconds=1,sessions=[dict(status='PASS',collector_reaped=True,host_receipt={'pss_samples':1})])
            metrics=dict(status='OBSERVED',cgroup='/owned',start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1,
                p1=dict(unique_files=10,resident_bytes=3*report.MIB,counters_before=dict(file=4*report.MIB,anon=report.MIB,kernel=report.MIB)),
                pss=dict(processes=[dict(uid=0) for _ in range(7)],total_bytes=7*report.MIB,per_sandbox_bytes=report.MIB),
                expanded_files=dict(status='PARTIAL',query_window_regular=dict(observed_subtotal_bytes=5*report.MIB,observed_objects=12)),
                cache_layers=dict(block_devices=dict(method_totals={'cachestat':{}},fixed_ram_totals={}),outer_loop={},deleted_tmpfs={}))
            def read(path):
                p=Path(path)
                if p.name=='schedule-smoke-metrics.json':return metrics
                if p.name=='runtime-monitor.json':return mon
                return task if p.parent.name=='external-workload' else outer
            with tempfile.TemporaryDirectory() as tmp,patch.object(report,'read',side_effect=read),patch.object(report,'rows',return_value=[dict(monotonic_ns='1',current=str(6*report.MIB))]):
                row=report.collect(Path(tmp)/'raw',dict(task='matplotlib24177',schedule=schedule,round=1))
                self.assertEqual((row['actual_actions'],row['logical_actions'],row['lower_logical_layers']),(expected['actual_actions'],665,10))
                self.assertEqual(report.repeat_statistics([row])[0]['task'],'matplotlib24177')
                matrix.publish(dict(status='PASS',runs=[row],metadata={'profile':'matplotlib24177'}),Path(tmp)/'report')
                content=(Path(tmp)/'report/REPORT.md').read_text();self.assertIn('原95步',content);self.assertIn('原10层',content);self.assertIn('60 passed',content);self.assertNotIn('第17步',content)


if __name__=='__main__':unittest.main()
