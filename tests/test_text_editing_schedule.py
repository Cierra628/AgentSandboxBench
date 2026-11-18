"""Text schedule checks: synthetic files and mocked services only, no real workload."""
import ast
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import text_editing_schedule_inputs as m
import report_python_schedule_matrix as report
import run_django_schedule_matrix as matrix
import validate_isolated_agentenv as native
from isolated_storage import capacity_budget,GIB
from run_prettier6604_grpo import execution_paths,prefix_tree_plan


class TextEditingScheduleTests(unittest.TestCase):
    def test_paths_counts_and_original_final_checks_for_every_instance(self):
        for path in m.PATHS.values():
            self.assertEqual([i for s in path for i in range(m.RANGES[s][0],m.RANGES[s][1]+1)],list(range(1,51)))
        self.assertEqual(sorted(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for s in x['prefix']) for x in prefix_tree_plan(m.PATHS)['forks'].values()),[33,46])
        for schedule,wanted in m.EXPECTED.items():
            executed=execution_paths(m.PATHS,schedule)
            self.assertEqual(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for p in executed.values() for s in p),wanted['actual_actions'])
            self.assertEqual(wanted['logical_actions'],350)
            for path in executed.values():
                self.assertTrue({47,48,49,50}<={i for s in path for i in range(m.RANGES[s][0],m.RANGES[s][1]+1)})

    def test_storage_keeps_default_reserves(self):
        for schedule in m.EXPECTED:self.assertEqual(m.volume_gib(schedule),64)
        self.assertFalse(capacity_budget(90*GIB,64*GIB)['ready'])
        budget=capacity_budget(91*GIB,64*GIB);self.assertTrue(budget['ready'])
        self.assertEqual((budget['host_reserve_bytes'],budget['host_margin_bytes']),(20*GIB,6*GIB))
        self.assertEqual(sum(m.storage_components('grpo').values()),33.5)
        self.assertEqual(sum(m.storage_components('bpo').values()),55.5)
        self.assertEqual(m.storage_components('tvcache')['image_layers'],3)

    def test_original_preparation_preserves_all_helpers_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);upstream=root/'upstream';upstream.mkdir()
            cfg=dict(version=1,task_id='large-scale-text-editing',actions=50,memory_mib=8192,image='fixed-image',revision='fixed-revision',run='original')
            (root/'configs').mkdir();(root/'configs/external-text-editing-light.json').write_text(json.dumps(cfg))
            files={f'{i:03}.sh':f'printf step-{i}\n' for i in range(1,51)}
            files.update({n:'original helper\n' for n in m.AUXILIARY})
            (upstream/'original').mkdir();(upstream/'original/replay-inputs.json').write_text(json.dumps({'files':files}))
            ref=[dict(exit_code=m.NONZERO.get(i,0)) for i in range(1,51)]
            with patch.object(m,'ROOT',root),patch.object(m,'load',return_value=(cfg,{'files':files},ref)),patch.object(m.subprocess,'check_output',return_value='fixed-revision\n'),patch.object(m.subprocess,'run'):
                prepared=root/'prepared';m.prepare(upstream,prepared)
                self.assertEqual(m.task_plan(prepared)['initial_lower_reference_count'],8)
                self.assertEqual(len(list((prepared/'workload/original').iterdir())),58)
                changed=prepared/'workload/original/002-read.py';changed.write_text('different')
                with self.assertRaises(AssertionError):m.validate(prepared)

    def test_runner_independent_shell_failure_codes_and_no_git_requirement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';app.mkdir();work=root/'work';(work/'original').mkdir(parents=True);(work/'segments/part').mkdir(parents=True);(root/'monitor').mkdir()
            (work/'original/001.sh').write_text('export carried=wrong; false | true\n')
            (work/'original/002.sh').write_text('test -z "${carried:-}" || exit 99; test -z "${HTTP_PROXY:-}" || exit 98; exit 127\n')
            (work/'original/003.sh').write_text('printf complete\n')
            (work/'segments/part/actions.tsv').write_text('step\toriginal_step\texit_code\n001\t1\t0\n002\t2\t127\n003\t3\t0\n')
            (work/'clock-ns').write_text('#!/bin/sh\ndate +%s%N\n');(work/'clock-ns').chmod(0o755)
            (work/'source-state.py').write_text(m.SOURCE_STATE_CODE.replace('/app',str(app)))
            runner=m.RUNNER.replace('/app',str(app)).replace('/workspace/asb-runtime-monitor',str(root/'monitor'))
            (work/'guest-runner.sh').write_text(runner)
            env=dict(os.environ,OUTPUT_DIR=str(root/'out'),ROUND='part',BRANCH='early_1',ACTIONS_ROOT=str(work/'segments'),HTTP_PROXY='must-be-cleared')
            result=subprocess.run(['bash',work/'guest-runner.sh'],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            rows=list(csv.DictReader((root/'out/steps.tsv').open(),delimiter='\t'))
            self.assertEqual([int(r['exit_code']) for r in rows],[0,127,0]);self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))
            self.assertTrue((root/'out/source-state.json').is_file());self.assertFalse((root/'out/patch.diff').exists())

    def test_boundary_reads_small_files_but_only_stats_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';app.mkdir();probe=root/'probe';probe.mkdir();helpers=root/'tmp';helpers.mkdir()
            (app/'input.csv').write_bytes(b'\xff\x00\xfe');(app/'expected.csv').write_bytes(b'\xff')
            (app/'apply_macros.vim').write_text('small script')
            (probe/'001-read.py').write_text('original read helper')
            marker=root/'marker';marker.write_text('saved');steps=root/'artifacts/explore';steps.mkdir(parents=True);(steps/'steps.tsv').write_text('completed')
            code=m.boundary_code(['explore']).replace('/tmp/',str(helpers)+'/').replace('/app',str(app)).replace('/probe/input',str(probe)).replace('/workspace/python-schedule-marker',str(marker)).replace('/workspace/artifacts/exp5/backbone',str(root/'artifacts'))
            value=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10))
            self.assertEqual(value['source']['metadata'][str(app/'input.csv')]['size'],3)
            self.assertEqual(value['source']['scripts'][str(probe/'001-read.py')],'original read helper')
            (probe/'001-read.py').write_text('changed helper')
            changed=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10));self.assertNotEqual(value,changed)

    def test_result_mapping_and_missing_marker_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for schedule in m.EXPECTED:
                for role,path in execution_paths(m.PATHS,schedule).items():
                    segment=path[-1];out=root/'rollouts'/role/segment/'stdout';out.mkdir(parents=True,exist_ok=True)
                    for original,content in [(47,'=== FULL FILE MATCHES EXPECTED ==='),(50,'=== SOLVE.SH OUTPUT MATCHES EXPECTED ===\n=== -S SCRIPT VARIANT ALSO MATCHES ===')]:
                        (out/f'{original-m.RANGES[segment][0]+1:03}.log').write_text(content)
                    self.assertEqual(m.validate_role_outputs(root,role,path)['status'],'PASS')
            path=execution_paths(m.PATHS,'bpo')['late_1'];log=root/'rollouts/late_1'/path[-1]/'stdout/004.log';log.write_text('=== SOLVE.SH OUTPUT MATCHES EXPECTED ===')
            with self.assertRaises(AssertionError):m.validate_role_outputs(root,'late_1',path)

    def test_invalid_flags_and_explicit_native_runner_path(self):
        for options in [dict(text_editing_schedule='grpo'),dict(text_editing_schedule_inputs=Path('/prepared')),
                dict(text_editing_schedule='bpo',text_editing_schedule_inputs=Path('/prepared')),
                dict(text_editing_schedule='grpo',text_editing_schedule_inputs=Path('/prepared'),matplotlib_schedule='grpo',matplotlib_schedule_inputs=Path('/other'))]:
            with patch.object(native.os,'geteuid',return_value=0),patch.object(native.os,'posix_fallocate') as alloc,patch.object(native.subprocess,'run') as run:
                with self.assertRaises(ValueError):native.main(SimpleNamespace(**options))
                alloc.assert_not_called();run.assert_not_called()
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='text_editing_schedule' and any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='stage' for x in n.body))
        self.assertIn("'--task-profile', 'text_editing'",ast.unparse(branch));self.assertIn('350 if text_editing_schedule',ast.unparse(branch))
        runner=(ROOT/'scripts/run_python_schedule.py').read_text();self.assertIn('timeout=900 if text_editing or cython else 600',runner);self.assertIn('argv, 960 if text_editing or cython else 660',runner)
        self.assertIn('test -x /app/solve.sh && test -f /app/output.csv && cmp -s',runner)

    def test_collect_and_report_task_specific_counts_layers_and_budget(self):
        for schedule,wanted in m.EXPECTED.items():
            task=dict(status='PASS',schedule=schedule,actual_action_count=wanted['actual_actions'],logical_action_count=350,
                paths={r:dict(exit_differences=[],tool_ns=1,official={'status':'NOT_RUN'}) for r in m.PATHS},
                cleanup={'owned':True},host_collector={'cgroup':'/owned'},strict_replay_status='PASS',
                save_restore=dict(saves=[{}]*wanted['saves'],restores=[{}]*wanted['restores']),
                schedule_elapsed_ns=20,actions_download_elapsed_ns=10,controller_elapsed_ns=30,
                schedule_start_monotonic_ns=1,original_paths_end_monotonic_ns=2)
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged')},
                text_editing_schedule_storage_budget={'capacity_bytes':64*GIB},observed_host_available_min_bytes=21*GIB)
            monitor=dict(status='PASS',interval_seconds=.1,host_pss_interval_seconds=1,sessions=[dict(status='PASS',collector_reaped=True,host_receipt={'pss_samples':1})])
            metrics=dict(status='OBSERVED',cgroup='/owned',start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1,
                p1=dict(unique_files=8,resident_bytes=3*report.MIB,counters_before=dict(file=4*report.MIB,anon=report.MIB,kernel=report.MIB)),
                pss=dict(processes=[dict(uid=0) for _ in range(7)],total_bytes=7*report.MIB,per_sandbox_bytes=report.MIB),
                expanded_files=dict(status='PARTIAL',query_window_regular=dict(observed_subtotal_bytes=5*report.MIB,observed_objects=12)),
                cache_layers=dict(block_devices=dict(method_totals={'cachestat':{}},fixed_ram_totals={}),outer_loop={},deleted_tmpfs={}))
            def read(path):
                p=Path(path)
                if p.name=='schedule-smoke-metrics.json':return metrics
                if p.name=='runtime-monitor.json':return monitor
                return task if p.parent.name=='external-workload' else outer
            with tempfile.TemporaryDirectory() as tmp,patch.object(report,'read',side_effect=read),patch.object(report,'rows',return_value=[dict(monotonic_ns='1',current=str(6*report.MIB))]):
                row=report.collect(Path(tmp)/'raw',dict(task='text_editing',round=1,schedule=schedule))
                self.assertEqual((row['actual_actions'],row['logical_actions'],row['lower_logical_layers'],row['volume_gib']),(wanted['actual_actions'],350,8,64))
                self.assertEqual(report.repeat_statistics([row])[0]['task'],'text_editing')
                matrix.publish(dict(status='RUNNING',runs=[row],metadata={'profile':'text_editing'}),Path(tmp)/'report')
                content=(Path(tmp)/'report/REPORT.md').read_text();self.assertIn('原50步',content);self.assertIn('原8层',content);self.assertIn('逐字节',content);self.assertNotIn('第17步',content);self.assertNotIn('60 passed',content)


if __name__=='__main__':unittest.main()
