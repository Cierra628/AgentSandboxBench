"""Offline-only C4 adapter tests: temporary scripts, metadata and saved metric fixtures."""
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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import c4_schedule_inputs as c
import django_schedule_inputs as d
import run_django_schedule_matrix as matrix
import report_python_schedule_matrix as report
import validate_isolated_agentenv as native
from isolated_storage import capacity_budget,GIB
from run_prettier6604_grpo import execution_paths,prefix_tree_plan


class C4ScheduleTests(unittest.TestCase):
    def test_original_paths_save_points_and_actual_final_checks(self):
        for role,path in c.PATHS.items():
            self.assertEqual([i for s in path for i in range(c.RANGES[s][0],c.RANGES[s][1]+1)],list(range(1,37)))
        tree=prefix_tree_plan(c.PATHS)
        self.assertEqual(sorted(sum(c.RANGES[s][1]-c.RANGES[s][0]+1 for s in p['prefix']) for p in tree['forks'].values()),[24,29])
        for schedule,expected in c.EXPECTED.items():
            paths=execution_paths(c.PATHS,schedule)
            self.assertEqual(sum(c.RANGES[s][1]-c.RANGES[s][0]+1 for path in paths.values() for s in path),expected['actual_actions'])
            for path in paths.values():
                for original in (33,34):self.assertTrue(any(c.RANGES[s][0]<=original<=c.RANGES[s][1] for s in path))
        self.assertEqual(c.NONZERO,{3:2,11:1,12:1,16:1,18:1})

    def test_only_explicit_c4_budget_uses_four_gib_margin(self):
        self.assertFalse(capacity_budget(104.5*GIB,80*GIB)['ready'])
        value=capacity_budget(104.5*GIB,80*GIB,margin=4*GIB)
        self.assertTrue(value['ready']);self.assertEqual(value['host_reserve_bytes'],20*GIB)
        self.assertEqual(value['required_available_bytes'],104*GIB)
        self.assertEqual([d.volume_gib(s) for s in d.EXPECTED],[48,80,80])
        self.assertEqual([c.volume_gib(s) for s in c.EXPECTED],[80,80,80])

    def test_final_source_follows_original_last_write_and_sed(self):
        files={'004.payload':'project','023.payload':'final compress','010.payload':'RECORD_STRUCT = struct.Struct("<IIIIIq")\n'}
        fixed=c.final_sources(files)
        self.assertEqual(fixed['compress.py'],'final compress')
        self.assertEqual(fixed['decompress.py'],'RECORD_STRUCT = struct.Struct("<IIIIIIq")\n')
        with self.assertRaises(AssertionError):c.final_sources(dict(files,**{'010.payload':'different'}))

    def test_independent_bash_preserves_payload_and_expected_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);work=root/'work';(work/'original').mkdir(parents=True);(work/'segments/part').mkdir(parents=True)
            monitor=root/'monitor';monitor.mkdir();app=root/'app';app.mkdir()
            (work/'original/001.sh').write_text('carried=wrong; false | true\n')
            (work/'original/003.sh').write_text('test -z "${carried:-}" || exit 99\nexit 2\n')
            (work/'original/004.payload').write_text('exact payload\n')
            (work/'original/004.sh').write_text('cat '+str(work/'original/004.payload')+' > '+str(app/'pyproject.toml')+'\n')
            (work/'segments/part/actions.tsv').write_text('step\toriginal_step\texit_code\n001\t1\t0\n002\t3\t2\n003\t4\t0\n')
            (work/'clock-ns').write_text('#!/bin/sh\ndate +%s%N\n');(work/'clock-ns').chmod(0o755)
            (work/'source-state.py').write_text(c.SOURCE_STATE_CODE.replace('/app',str(app))+'\n')
            runner=c.RUNNER.replace('cd /app','cd '+str(app)).replace('/workspace/asb-runtime-monitor',str(monitor))
            (work/'guest-runner.sh').write_text(runner)
            env=dict(os.environ,OUTPUT_DIR=str(root/'out'),ROUND='part',BRANCH='early_1',ACTIONS_ROOT=str(work/'segments'),HTTP_PROXY='must-be-empty')
            p=subprocess.run(['bash',work/'guest-runner.sh'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            with (root/'out/steps.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            self.assertEqual([int(r['exit_code']) for r in rows],[0,2,0])
            self.assertEqual(json.loads((root/'out/source-state.json').read_text())['pyproject.toml'],'exact payload\n')
            self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))

    def test_final_markers_use_original_step_not_local_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for schedule in c.EXPECTED:
                for role,path in execution_paths(c.PATHS,schedule).items():
                    s=path[-1];directory=root/'rollouts'/role/s/'stdout';directory.mkdir(parents=True,exist_ok=True)
                    for original,text in [(33,'CONSTRAINT VIOLATIONS: NONE\nFINAL VERDICT: BYTE-EXACT ROUNDTRIP ON c4_sample'),(34,'9898 files, structure + content')]:
                        (directory/f'{original-c.RANGES[s][0]+1:03}.log').write_text(text)
                    self.assertEqual(c.validate_role_outputs(root,role,path)['sample_files'],9898)
            (root/'rollouts/backbone/final/stdout/004.log').write_text('no original verdict')
            with self.assertRaises(AssertionError):c.validate_role_outputs(root,'backbone',c.PATHS['backbone'])

    def test_invalid_native_options_fail_before_devices(self):
        for options in [dict(c4_schedule='grpo'),dict(c4_schedule_inputs=Path('/input')),
                        dict(c4_schedule='bpo',c4_schedule_inputs=Path('/input')),
                        dict(c4_schedule='grpo',c4_schedule_inputs=Path('/input'),django_schedule='grpo',django_schedule_inputs=Path('/django'))]:
            with self.subTest(options=options),patch.object(native.os,'geteuid',return_value=0),patch.object(native.os,'posix_fallocate') as alloc,patch.object(native.subprocess,'run') as run:
                with self.assertRaises(ValueError):native.main(SimpleNamespace(**options))
                alloc.assert_not_called();run.assert_not_called()

    def test_c4_native_command_and_margin_are_explicit_only(self):
        source=(c.ROOT/'scripts/validate_isolated_agentenv.py').read_text();tree=ast.parse(source)
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='c4_schedule' and any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='stage' for x in n.body))
        call=next(n.value for n in branch.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='call')
        self.assertIn("'--task-profile', 'c4'",ast.unparse(call))
        budgets=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and ast.unparse(n.func)=='capacity_budget']
        self.assertEqual(len(budgets),1);self.assertIn('if c4_schedule else {}',ast.unparse(budgets[0]))
        compile(c.SOURCE_STATE_CODE,'<source state>','exec')
        compile('import json;from pathlib import Path;print(json.dumps(dict(source='+c.SOURCE_STATE_EXPR+')))', '<boundary>', 'exec')

    def test_collect_c4_counts_layers_and_report(self):
        for schedule,expected in c.EXPECTED.items():
            task=dict(status='PASS',schedule=schedule,actual_action_count=expected['actual_actions'],logical_action_count=252,
                paths={r:dict(exit_differences=[],tool_ns=1,official={'status':'NOT_RUN'}) for r in c.PATHS},
                cleanup={'owned':True},host_collector={'cgroup':'/owned'},strict_replay_status='PASS',
                save_restore=dict(saves=[{}]*expected['saves'],restores=[{}]*expected['restores']),
                schedule_elapsed_ns=20,actions_download_elapsed_ns=10,controller_elapsed_ns=30,
                schedule_start_monotonic_ns=1,original_paths_end_monotonic_ns=2)
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged')},
                c4_schedule_storage_budget={'capacity_bytes':80*GIB},observed_host_available_min_bytes=21*GIB)
            mon=dict(status='PASS',interval_seconds=.1,host_pss_interval_seconds=1,sessions=[dict(status='PASS',collector_reaped=True,host_receipt={'pss_samples':1})])
            metrics=dict(status='OBSERVED',cgroup='/owned',start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1,
                p1=dict(unique_files=9,resident_bytes=3*report.MIB,counters_before=dict(file=4*report.MIB,anon=report.MIB,kernel=report.MIB)),
                pss=dict(processes=[dict(uid=0) for _ in range(7)],total_bytes=7*report.MIB,per_sandbox_bytes=report.MIB),
                expanded_files=dict(status='PARTIAL',query_window_regular=dict(observed_subtotal_bytes=5*report.MIB,observed_objects=12)),
                cache_layers=dict(block_devices=dict(method_totals={'cachestat':{}},fixed_ram_totals={}),outer_loop={},deleted_tmpfs={}))
            def read(path):
                p=Path(path)
                if p.name=='schedule-smoke-metrics.json':return metrics
                if p.name=='runtime-monitor.json':return mon
                return task if p.parent.name=='external-workload' else outer
            with tempfile.TemporaryDirectory() as tmp,patch.object(report,'read',side_effect=read),patch.object(report,'rows',return_value=[dict(monotonic_ns='1',current=str(6*report.MIB))]):
                row=report.collect(Path(tmp)/'raw',dict(task='c4',schedule=schedule,round=1))
                self.assertEqual((row['actual_actions'],row['logical_actions'],row['lower_logical_layers']),(expected['actual_actions'],252,9))
                self.assertEqual(report.repeat_statistics([row])[0]['task'],'c4')
                output=Path(tmp)/'report';matrix.publish(dict(status='PASS',runs=[row],metadata={'profile':'c4'}),output)
                content=(output/'REPORT.md').read_text();self.assertIn('原9层',content);self.assertIn('9898',content);self.assertNotIn('原28步',content)


if __name__=='__main__':unittest.main()
