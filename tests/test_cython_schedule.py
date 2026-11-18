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
import cython_schedule_inputs as m
import report_python_schedule_matrix as report
import run_django_schedule_matrix as matrix
import validate_isolated_agentenv as native
from isolated_storage import capacity_budget,GIB
from run_prettier6604_grpo import execution_paths,prefix_tree_plan


class CythonScheduleTests(unittest.TestCase):
    def test_paths_counts_and_original_final_checks_for_every_instance(self):
        for path in m.PATHS.values():
            self.assertEqual([i for s in path for i in range(m.RANGES[s][0],m.RANGES[s][1]+1)],list(range(1,84)))
        self.assertEqual(sorted(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for s in x['prefix']) for x in prefix_tree_plan(m.PATHS)['forks'].values()),[60,75])
        for schedule,wanted in m.EXPECTED.items():
            executed=execution_paths(m.PATHS,schedule)
            self.assertEqual(sum(m.RANGES[s][1]-m.RANGES[s][0]+1 for p in executed.values() for s in p),wanted['actual_actions'])
            self.assertEqual(wanted['logical_actions'],581)
            for path in executed.values():
                self.assertTrue({76,77,78,79,80,81,82,83}<={i for s in path for i in range(m.RANGES[s][0],m.RANGES[s][1]+1)})

    def test_storage_keeps_default_reserves(self):
        for schedule in m.EXPECTED:self.assertEqual(m.volume_gib(schedule),64)
        self.assertFalse(capacity_budget(90*GIB,64*GIB)['ready'])
        budget=capacity_budget(91*GIB,64*GIB);self.assertTrue(budget['ready'])
        self.assertEqual((budget['host_reserve_bytes'],budget['host_margin_bytes']),(20*GIB,6*GIB))
        self.assertEqual(sum(m.storage_components('grpo').values()),34.5)
        self.assertEqual(sum(m.storage_components('bpo').values()),58.5)
        self.assertEqual(m.storage_components('tvcache')['image_layers'],6)

    def test_original_preparation_preserves_all_helpers_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);upstream=root/'upstream';upstream.mkdir()
            cfg=dict(version=1,task_id='build-cython-ext',actions=83,memory_mib=8192,image='fixed-image',revision='fixed-revision',run='original')
            (root/'configs').mkdir();(root/'configs/external-cython-light.json').write_text(json.dumps(cfg))
            (root/'configs/cython-reference-step79.patch').write_text('diff --git a/a b/a\n'*12)
            files={f'{i:03}.sh':f'printf step-{i}\n' for i in range(1,84)}
            files.update({n:'original helper\n' for n in m.AUXILIARY})
            (upstream/'original').mkdir();(upstream/'original/replay-inputs.json').write_text(json.dumps({'files':files}))
            ref=[dict(exit_code=m.NONZERO.get(i,0)) for i in range(1,84)]
            with patch.object(m,'ROOT',root),patch.object(m,'load',return_value=(cfg,{'files':files},ref)),patch.object(m.subprocess,'check_output',return_value='fixed-revision\n'),patch.object(m.subprocess,'run'):
                prepared=root/'prepared';m.prepare(upstream,prepared)
                self.assertEqual(m.task_plan(prepared)['initial_lower_reference_count'],7)
                self.assertEqual(len(list((prepared/'workload/original').iterdir())),105)
                changed=prepared/'workload/original/003-read.py';changed.write_text('different')
                with self.assertRaises(AssertionError):m.validate(prepared)

    def test_runner_keeps_environment_independent_shell_and_pipeline_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';app.mkdir();work=root/'work';(work/'original').mkdir(parents=True);(work/'segments/part').mkdir(parents=True);(root/'monitor').mkdir()
            (work/'original/001.sh').write_text('export carried=wrong; false | true\n')
            (work/'original/002.sh').write_text('test -z "${carried:-}" || exit 99; test "$GIT_CONFIG_VALUE_0" = HTTP/1.1 || exit 98; test "$HTTP_PROXY" = http://approved-relay || exit 97; exit 129\n')
            (work/'original/003.sh').write_text('printf complete\n')
            (work/'segments/part/actions.tsv').write_text('step\toriginal_step\texit_code\n001\t1\t0\n002\t2\t129\n003\t3\t0\n')
            (work/'clock-ns').write_text('#!/bin/sh\ndate +%s%N\n');(work/'clock-ns').chmod(0o755)
            (work/'source-state.py').write_text('print("{}")')
            probe=root/'probe';probe.mkdir();(probe/'guest-env.sh').write_text('export GIT_CONFIG_VALUE_0=HTTP/1.1 HTTP_PROXY=http://approved-relay\n')
            runner=m.RUNNER.replace('/app',str(app)).replace('/probe/input',str(probe)).replace('/workspace/asb-runtime-monitor',str(root/'monitor'))
            (work/'guest-runner.sh').write_text(runner)
            env=dict(os.environ,OUTPUT_DIR=str(root/'out'),ROUND='part',BRANCH='early_1',ACTIONS_ROOT=str(work/'segments'))
            result=subprocess.run(['bash',work/'guest-runner.sh'],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            with (root/'out/steps.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            self.assertEqual([int(r['exit_code']) for r in rows],[0,129,0]);self.assertTrue(all(int(r['duration_ns'])>0 for r in rows))
            self.assertTrue((root/'out/source-state.json').is_file())

    def test_boundary_source_helpers_progress_and_binary_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/'app';repo=app/'pyknotid';repo.mkdir(parents=True)
            subprocess.run(['git','init','-q',repo],check=True,timeout=10)
            (repo/'tracked.py').write_text('original\n')
            subprocess.run(['git','-C',repo,'add','tracked.py'],check=True,timeout=10)
            subprocess.run(['git','-C',repo,'-c','user.name=test','-c','user.email=test@example.invalid','commit','-qm','base'],check=True,timeout=10)
            (repo/'tracked.py').write_text('changed\n');(app/'fix_numpy_compat.py').write_text('small fix')
            (repo/'pyknotid/spacecurves').mkdir(parents=True)
            (repo/'pyknotid/cinvariants.test.so').write_bytes(b'\xff\x00\xfe')
            marker=root/'marker';marker.write_text('saved');steps=root/'artifacts/explore';steps.mkdir(parents=True);(steps/'steps.tsv').write_text('completed')
            code=m.boundary_code(['explore']).replace('/app',str(app)).replace('/workspace/python-schedule-marker',str(marker)).replace('/workspace/artifacts/exp5/backbone',str(root/'artifacts'))
            value=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10))
            self.assertEqual(value['source']['scripts'][str(app/'fix_numpy_compat.py')],'small fix')
            self.assertEqual(value['source']['extensions']['pyknotid/cinvariants'][0]['size'],3)
            self.assertIn('changed',value['source']['patch'])
            (app/'fix_numpy_compat.py').write_text('changed helper')
            changed=json.loads(subprocess.check_output([sys.executable,'-c',code],text=True,timeout=10));self.assertNotEqual(value,changed)

    def test_original_result_mapping_and_reject_wrong_test_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for schedule in m.EXPECTED:
                for role,path in execution_paths(m.PATHS,schedule).items():
                    segment=path[-1];out=root/'rollouts'/role/segment/'stdout';out.mkdir(parents=True,exist_ok=True)
                    tests='=== tests from /app/pyknotid (source-tree context) ===\n18 passed\n=== tests from /tmp (site-packages context) ===\n18 passed\n'
                    for original,content in [(76,'SNIPPET OK - alexander_polynomial(-1) = 1'),(82,tests),(83,'final check: numpy 2.3.0\n')]:
                        (out/f'{original-m.RANGES[segment][0]+1:03}.log').write_text(content)
                    self.assertEqual(m.validate_role_outputs(root,role,path)['status'],'PASS')
            path=execution_paths(m.PATHS,'bpo')['late_1'];log=root/'rollouts/late_1'/path[-1]/'stdout/007.log';log.write_text(tests.replace('18 passed','17 passed',1))
            with self.assertRaises(ValueError):m.validate_role_outputs(root,'late_1',path)

    def test_invalid_flags_and_explicit_native_runner_path(self):
        for options in [dict(cython_schedule='grpo'),dict(cython_schedule_inputs=Path('/prepared')),
                dict(cython_schedule='bpo',cython_schedule_inputs=Path('/prepared')),
                dict(cython_schedule='grpo',cython_schedule_inputs=Path('/prepared'),matplotlib_schedule='grpo',matplotlib_schedule_inputs=Path('/other'))]:
            with patch.object(native.os,'geteuid',return_value=0),patch.object(native.os,'posix_fallocate') as alloc,patch.object(native.subprocess,'run') as run:
                with self.assertRaises(ValueError):native.main(SimpleNamespace(**options))
                alloc.assert_not_called();run.assert_not_called()
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='cython_schedule' and any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='stage' for x in n.body))
        self.assertIn("'--task-profile', 'cython'",ast.unparse(branch));self.assertIn('581 if cython_schedule',ast.unparse(branch))
        runner=(ROOT/'scripts/run_python_schedule.py').read_text();self.assertIn('timeout=900 if text_editing or cython else 600',runner);self.assertIn('argv, 960 if text_editing or cython else 660',runner)
        self.assertIn('CYTHON_VALIDATION',runner);self.assertIn('profile.SOURCE_COMMIT',runner)

    def test_collect_and_report_task_specific_counts_layers_and_budget(self):
        for schedule,wanted in m.EXPECTED.items():
            task=dict(status='PASS',schedule=schedule,actual_action_count=wanted['actual_actions'],logical_action_count=581,
                paths={r:dict(exit_differences=[],tool_ns=1,official={'status':'NOT_RUN'}) for r in m.PATHS},
                cleanup={'owned':True},host_collector={'cgroup':'/owned'},strict_replay_status='PASS',
                save_restore=dict(saves=[{}]*wanted['saves'],restores=[{}]*wanted['restores']),
                schedule_elapsed_ns=20,actions_download_elapsed_ns=10,controller_elapsed_ns=30,
                schedule_start_monotonic_ns=1,original_paths_end_monotonic_ns=2)
            outer=dict(status='PASS',cleanup={k:True for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged')},
                cython_schedule_storage_budget={'capacity_bytes':64*GIB},observed_host_available_min_bytes=21*GIB)
            monitor=dict(status='PASS',interval_seconds=.1,host_pss_interval_seconds=1,sessions=[dict(status='PASS',collector_reaped=True,host_receipt={'pss_samples':1})])
            metrics=dict(status='OBSERVED',cgroup='/owned',start_monotonic_ns=1,end_monotonic_ns=2,cpu_ns=1,
                p1=dict(unique_files=7,resident_bytes=3*report.MIB,counters_before=dict(file=4*report.MIB,anon=report.MIB,kernel=report.MIB)),
                pss=dict(processes=[dict(uid=0) for _ in range(7)],total_bytes=7*report.MIB,per_sandbox_bytes=report.MIB),
                expanded_files=dict(status='PARTIAL',query_window_regular=dict(observed_subtotal_bytes=5*report.MIB,observed_objects=12)),
                cache_layers=dict(block_devices=dict(method_totals={'cachestat':{}},fixed_ram_totals={}),outer_loop={},deleted_tmpfs={}))
            def read(path):
                p=Path(path)
                if p.name=='schedule-smoke-metrics.json':return metrics
                if p.name=='runtime-monitor.json':return monitor
                return task if p.parent.name=='external-workload' else outer
            with tempfile.TemporaryDirectory() as tmp,patch.object(report,'read',side_effect=read),patch.object(report,'rows',return_value=[dict(monotonic_ns='1',current=str(6*report.MIB))]):
                row=report.collect(Path(tmp)/'raw',dict(task='cython',round=1,schedule=schedule))
                self.assertEqual((row['actual_actions'],row['logical_actions'],row['lower_logical_layers'],row['volume_gib']),(wanted['actual_actions'],581,7,64))
                self.assertEqual(report.repeat_statistics([row])[0]['task'],'cython')
                matrix.publish(dict(status='RUNNING',runs=[row],metadata={'profile':'cython'}),Path(tmp)/'report')
                content=(Path(tmp)/'report/REPORT.md').read_text();self.assertIn('原83步',content);self.assertIn('原7层',content);self.assertIn('18 passed',content);self.assertNotIn('第17步',content);self.assertNotIn('60 passed',content)

    def test_matrix_wires_nine_owned_runs_and_stops_at_first_real_failure(self):
        import contextlib
        from unittest.mock import Mock
        import cython_schedule_proxy as proxy_module
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp,contextlib.ExitStack() as stack:
                root=Path(tmp);(root/'.artifacts').mkdir();out=root/'.artifacts/matrix-unit'
                metadata=dict(profile='cython',workload_kind='controlled_same_path_prefix_reuse_not_native_seven_branches')
                stack.enter_context(patch.object(m,'task_plan',return_value=metadata))
                stack.enter_context(patch.object(matrix,'ROOT',root))
                stack.enter_context(patch.object(matrix.shutil,'copy2'))
                stack.enter_context(patch.object(matrix,'capacity_after_cleanup',return_value=({'ready':True},{})))
                stack.enter_context(patch.object(matrix.subprocess,'check_output',return_value=json.dumps([dict(Id=matrix.SOURCE_ID,State={'Running':False},HostConfig={'RestartPolicy':{'Name':'no'}})]).encode()))
                opener=Mock();opener.open.return_value.__enter__=Mock(return_value=Mock(status=200));opener.open.return_value.__exit__=Mock(return_value=False)
                stack.enter_context(patch.object(matrix.urllib.request,'build_opener',return_value=opener))
                relays=[]
                def relay(*args,**kw):
                    x=Mock();x.start.return_value='http://192.0.2.17:19000';relays.append(x);return x
                stack.enter_context(patch.object(proxy_module,'ScheduleProxy',side_effect=relay))
                commands=[]
                storage=root/'.artifacts/storage-isolated-service/unit'
                def run(command,**kw):
                    command=list(map(str,command))
                    if command[0]=='systemd-run':
                        commands.append(command)
                        self.assertIn('--cython-schedule',command);self.assertIn('--external-workload-relay',command)
                        self.assertIn('--external-workload-oci-input',command);self.assertIn('--setenv=HTTP_PROXY=',command)
                        self.assertTrue(any(a.startswith('--setenv=ASB_EXTERNAL_GUEST_RELAY_STATE=') for a in command))
                        self.assertFalse(kw['env'].get('HTTP_PROXY'))
                        kw['stdout'].write('status='+('FAIL' if fail else 'PASS')+' result_dir='+str(storage)+'\n')
                        return Mock(returncode=int(fail))
                    return Mock(returncode=0)
                stack.enter_context(patch.object(matrix.subprocess,'run',side_effect=run))
                stack.enter_context(patch.object(matrix,'collect',side_effect=lambda storage,item:dict(item,status='PASS')))
                stack.enter_context(patch.object(matrix,'plot_run',return_value='/synthetic/curve.png'))
                published=stack.enter_context(patch.object(matrix,'publish'))
                stack.enter_context(patch.object(sys,'argv',['matrix','--inputs',str(root/'input'),'--upstream',str(root/'upstream'),'--oci-input',str(root/'oci'),'--output',str(out),'--guest-relay-proxy','http://127.0.0.1:17891','--execute']))
                code=matrix.main(profile_name='cython')
                self.assertEqual(code,int(fail),(out/'result.json').read_text());self.assertEqual(len(commands),1 if fail else 9)
                self.assertEqual(len(relays),len(commands))
                for relay in relays:relay.start.assert_called_once();relay.stop.assert_called_once()
                state=json.loads((out/'result.json').read_text())
                self.assertEqual(state['runs'][-1]['status'],'FAIL' if fail else 'PASS')
                self.assertTrue(state['source_returned_to_stopped']);published.assert_called_once()


if __name__=='__main__':unittest.main()
