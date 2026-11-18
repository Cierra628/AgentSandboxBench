"""Synthetic files only: no service, workload execution, process or device queries."""
import ast
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_python_schedule as runner
import schedule_smoke_metrics as metrics


class PythonScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.workload = self.root / 'input'
        self.output = self.root / 'rollouts/early_1/branch_finish'
        self.output.mkdir(parents=True); self.manifest = self.workload / 'segments/branch_finish/actions.tsv'
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text('step\tcommand\n001\ttrue\n')
        (self.output / 'actions.tsv').write_bytes(self.manifest.read_bytes())
        for name in ('stdout', 'stderr'):
            (self.output / name).mkdir(); (self.output / name / '001.log').write_text('')
        (self.output / 'patch.diff').write_text('')
        (self.output / 'guest-memory.tsv').write_text('phase\tCached_kib\ncomplete\t1\n')
        self.sample = [dict(role='early_1', segment='branch_finish', actions=[dict(exit_code=0)])]
        self.steps(0)

    def steps(self, code, duration=10):
        (self.output / 'steps.tsv').write_text('step\tbranch\tround\texit_code\tduration_ns\n001\tearly_1\tbranch_finish\t' + str(code) + '\t' + str(duration) + '\n')

    def verify(self):
        return runner.verify_segment(self.root, self.workload, 'early_1', 'branch_finish', self.sample, 'sphinx7590')

    def test_original_branch_count_and_time(self):
        self.assertEqual(self.verify(), dict(action_count=1, tool_ns=10, exit_differences=[]))

    def test_snapshot_cleanup_uses_the_existing_template_delete_api(self):
        cli=Mock(return_value='removed')
        self.assertEqual(runner.delete_snapshot(cli,'owned-snapshot'),'removed')
        cli.assert_called_once_with('delete-snapshot','template','delete','owned-snapshot',timeout=90)
        cli.side_effect=RuntimeError('delete failed')
        with self.assertRaises(RuntimeError):runner.delete_snapshot(cli,'owned-snapshot')

    def test_unapproved_branch_exit_is_rejected(self):
        self.steps(1)
        with self.assertRaises(AssertionError):self.verify()

    def test_changed_manifest_and_invalid_duration_are_rejected(self):
        self.steps(0, -1)
        with self.assertRaises(AssertionError):self.verify()
        self.steps(0); (self.output / 'actions.tsv').write_text('different')
        with self.assertRaises(AssertionError):self.verify()

    def test_explicit_thirteen_layers_does_not_weaken_old_nineteen(self):
        roles = runner.ROLES
        objects = [dict(device=1, inode=i, users=list(roles)) for i in range(13)]
        roots = dict(status='IDENTIFIED', objects=objects, guests={r:dict(lowers=[dict(status='IDENTIFIED', **o) for o in objects]) for r in roles})
        self.assertEqual(len(metrics.select_initial(roots, objects, roles, expected_count=13)), 13)
        with self.assertRaises(ValueError):metrics.select_initial(roots, objects, roles)
        roots['objects'][0]['users'] = list(roles[:-1])
        with self.assertRaises(ValueError):metrics.select_initial(roots, objects, roles, expected_count=13)

    def test_native_volume_sizes_preserve_single_and_budget_by_operation(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='python_inputs' and
                    n.body and isinstance(n.body[0],ast.Assign) and ast.unparse(n.body[0].targets[0])=='capacity')
        end=next(i for i,n in enumerate(branch.body) if isinstance(n,ast.If) and ast.unparse(n.test)=='python_oci')
        for sphinx in (False,True):
            for schedule in (None,'grpo','bpo','tvcache'):
                values=dict(python_capacity=32,GIB=2**30,python_diagnostic=False,python_evaluation=None,
                    python_readonly_replay=True,python_runtime_smoke=False,python_checkpoint=False,
                    python_schedule=schedule,sphinx=sphinx)
                exec(compile(ast.Module(body=branch.body[:end],type_ignores=[]),'<volume only>','exec'),values)
                expected=8 if schedule is None else 40 if schedule=='grpo' else 56 if sphinx else 64
                self.assertEqual(values['capacity'],expected*2**30)

    def test_native_command_selects_explicit_runner_and_schedule(self):
        tree=ast.parse((ROOT/'scripts/validate_isolated_agentenv.py').read_text())
        branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='python_inputs' and
                    any(isinstance(x,ast.Assign) and ast.unparse(x.targets[0])=='task_command' for x in n.body))
        end=next(i for i,n in enumerate(branch.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='call')
        for profile in ('sphinx7590','xarray6992'):
            for schedule in ('grpo','bpo','tvcache'):
                values=dict(sphinx=profile=='sphinx7590',mount=Path('/owned'),python_short=False,sys=sys,ROOT=ROOT,
                    python_inputs=Path('/fixed-input'),python_profile=profile,container='owned',
                    receipt={'address':'http://127.0.0.1:12345'},python_diagnostic=False,python_continuation=profile=='xarray6992',
                    sphinx_pipeline=profile=='sphinx7590',pipeline_version=2 if profile=='sphinx7590' else 1,
                    python_evaluation=None,python_checkpoint=False,python_persistent=True,python_monitor_mode='continuous',
                    python_physical=None,python_live_cache=False,python_cache_baseline=False,python_cache_target=None,
                    light_monitor=False,task_endpoint_metrics=False,python_schedule=schedule,python_branch_policy='original',
                    python_schedule_find=False,python_schedule_xarray_find=False)
                exec(compile(ast.Module(body=branch.body[:end],type_ignores=[]),'<command only>','exec'),values)
                command=list(map(str,values['task_command']))
                self.assertEqual(Path(command[2]).name,'run_python_schedule.py')
                self.assertEqual(command[command.index('--schedule')+1],schedule)
                self.assertNotIn('--find-exit-diagnostic',command)
                self.assertNotIn('--xarray-find-exit-diagnostic',command)
                if profile=='sphinx7590':
                    values['python_schedule_find']=True
                    exec(compile(ast.Module(body=branch.body[:end],type_ignores=[]),'<command with explicit find policy>','exec'),values)
                    self.assertIn('--find-exit-diagnostic',list(map(str,values['task_command'])))
                else:
                    values['python_schedule_xarray_find']=True
                    exec(compile(ast.Module(body=branch.body[:end],type_ignores=[]),'<explicit Xarray find policy>','exec'),values)
                    self.assertIn('--xarray-find-exit-diagnostic',list(map(str,values['task_command'])))

    def policy_inputs(self, actual):
        command=runner.EARLY2_COMMAND;prefix='p'*1227;helper='h'*1787
        actions=[{}, {}, {},dict(command=command,exit_code=141,output=prefix),dict(
            command='cd /testbed && sed -n \'1,60p\' tests/test_domain_cpp.py && echo "=== DefinitionParser init ===" && true',
            output=helper+'=== DefinitionParser init ===\nrest')]
        stdout=(prefix if actual==141 else prefix+'=== test helper ===\n'+helper).encode()
        return [command,141,actual,stdout,b'',b'unchanged patch',b'unchanged patch',actions]

    def test_exact_dual_output_policy_keeps_real_exit(self):
        self.assertIsNone(runner.check_early2_pipeline(*self.policy_inputs(141)))
        result=runner.check_early2_pipeline(*self.policy_inputs(0))
        self.assertEqual((result['expected'],result['actual']),(141,0))
        self.assertEqual(result['stdout_bytes'],3034)

    def test_policy_rejects_changed_output_stderr_patch_command_or_exit(self):
        for index,replacement in ((0,'different command'),(2,1),(3,b'wrong output'),(4,b'warning'),(5,b'changed patch')):
            args=self.policy_inputs(0);args[index]=replacement
            with self.subTest(index=index),self.assertRaises(AssertionError):runner.check_early2_pipeline(*args)

    def test_policy_does_not_apply_to_other_roles_or_segments(self):
        self.steps(0);self.sample[0]['actions'][0]['exit_code']=141
        with self.assertRaises(AssertionError):
            runner.verify_segment(self.root,self.workload,'early_1','branch_finish',self.sample,'sphinx7590',
                branch_policy=runner.EARLY2_POLICY,reference_patch=b'')

    def test_find_policy_exact_outputs_and_actual_exit_preserved(self):
        for code in (0,1):
            result=runner.check_find_exit(runner.FIND_COMMAND,0,code,runner.FIND_STDOUT,b'',b'patch',b'patch')
            if code==0:self.assertIsNone(result)
            else:
                self.assertEqual((result['expected'],result['actual']),(0,1))
                self.assertEqual(result['policy'],runner.FIND_EXIT_POLICY)
                self.assertEqual(result['suppressed_find_error'],'NOT_RECORDED')

    def test_find_policy_rejects_changes_and_non_sphinx(self):
        args=[runner.FIND_COMMAND,0,1,runner.FIND_STDOUT,b'',b'patch',b'patch']
        for index,value in [(0,'different'),(1,1),(2,2),(2,141),(3,b'changed'),(4,b'warning'),(5,b'changed'),(6,None)]:
            changed=list(args);changed[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(AssertionError):runner.check_find_exit(*changed)
        with self.assertRaises(ValueError):runner.run(SimpleNamespace(task_profile='xarray6992',find_exit_diagnostic=True))

    def test_find_policy_is_explicit_and_only_exact_action(self):
        segment='trace_cpp_parser';role='backbone'
        directory=self.root/'rollouts'/role/segment;directory.mkdir(parents=True)
        manifest=self.workload/'segments'/segment/'actions.tsv';manifest.parent.mkdir(parents=True)
        def prepare(step):
            text='step\tcommand\n'+step+'\t'+runner.FIND_COMMAND+'\n'
            manifest.write_text(text);(directory/'actions.tsv').write_text(text)
            (directory/'steps.tsv').write_text('step\tbranch\tround\texit_code\tduration_ns\n'+step+'\t'+role+'\t'+segment+'\t1\t10\n')
            for name,data in [('stdout',runner.FIND_STDOUT),('stderr',b'')]:
                (directory/name).mkdir(exist_ok=True);(directory/name/(step+'.log')).write_bytes(data)
            (directory/'patch.diff').write_bytes(b'patch');(directory/'guest-memory.tsv').write_text('sample\n1\n')
        sample=[dict(role=role,segment=segment,actions=[dict(exit_code=0)])]
        prepare('005')
        with self.assertRaises(AssertionError):runner.verify_segment(self.root,self.workload,role,segment,sample,'sphinx7590')
        result=runner.verify_segment(self.root,self.workload,role,segment,sample,'sphinx7590',
            find_diagnostic=True,reference_patch=b'patch')
        self.assertEqual(result['exit_differences'][0]['actual'],1)
        prepare('006')
        with self.assertRaises(AssertionError):runner.verify_segment(self.root,self.workload,role,segment,sample,'sphinx7590',
            find_diagnostic=True,reference_patch=b'patch')

    def test_patch_output_preserves_diff_context_and_scalar_default(self):
        tree=ast.parse((ROOT/'scripts/run_python_schedule.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        nodes=[n for n in run.body if isinstance(n,ast.FunctionDef) and n.name in ('call','cli','guest')]
        self.assertEqual(len(nodes),3)
        output='diff --git a/file b/file\nindex old..new 100644\n@@ -1,2 +1,2 @@\n-old\n+new\n \n'
        process=Mock();process.run.return_value=SimpleNamespace(returncode=0,stdout=output)
        ns=dict(lock=threading.Lock(),events=[],out=self.root,env={},ROOT=self.root,
                subprocess=process,time=time,write=runner.write,django=False,matplotlib=False)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'<mocked output transport>','exec'),ns)
        preserved=ns['guest']('patch','sid','git diff --binary',preserve_output=True)
        self.assertEqual(preserved,output)
        self.assertEqual((self.root/'0000-patch.stdout').read_text(),output)
        self.assertEqual(ns['guest']('scalar','sid','scalar command'),output.strip())
        self.assertNotEqual(runner.normalize_patch(output),runner.normalize_patch(output.strip()+'\n'))
        self.assertNotEqual(runner.normalize_patch(output),runner.normalize_patch(output.replace('+new','+different')))

    def test_both_final_patch_queries_request_unmodified_output(self):
        tree=ast.parse((ROOT/'scripts/run_python_schedule.py').read_text())
        final=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='final_check')
        calls=[n for n in ast.walk(final) if isinstance(n,ast.Call) and ast.unparse(n.func)=='asyncio.to_thread' and
               len(n.args)>3 and ast.unparse(n.args[1]) in ("'patch-' + role", "'main-patch'")]
        self.assertEqual(len(calls),2)
        self.assertTrue(all(any(k.arg=='preserve_output' and isinstance(k.value,ast.Constant) and k.value.value is True
                               for k in call.keywords) for call in calls))

    def test_xarray_find_exact_case_keeps_actual_exit_and_requires_empty_patch(self):
        args=[runner.XARRAY_FIND_COMMAND,0,1,runner.XARRAY_FIND_STDOUT,b'',b'',b'']
        self.assertEqual(len(runner.XARRAY_FIND_STDOUT),128)
        result=runner.check_find_exit(*args,profile='xarray6992')
        self.assertEqual((result['segment'],result['step'],result['expected'],result['actual']),('trace_index_state','001',0,1))
        self.assertEqual(result['policy'],runner.XARRAY_FIND_EXIT_POLICY)
        args[2]=0;self.assertIsNone(runner.check_find_exit(*args,profile='xarray6992'))
        with self.assertRaises(AssertionError):runner.check_find_exit(*args)
        for index,value in [(0,'different'),(1,1),(2,2),(2,141),(3,b'changed'),(4,b'warning'),(5,b'changed'),(6,None)]:
            changed=list(args);changed[index]=value
            with self.subTest(index=index,value=value),self.assertRaises(AssertionError):runner.check_find_exit(*changed,profile='xarray6992')
        with self.assertRaises(ValueError):runner.run(SimpleNamespace(task_profile='sphinx7590',xarray_find_exit_diagnostic=True))

    def test_xarray_find_is_opt_in_and_does_not_cover_another_step(self):
        role='backbone';segment='trace_index_state';directory=self.root/'rollouts'/role/segment;directory.mkdir(parents=True)
        manifest=self.workload/'segments'/segment/'actions.tsv';manifest.parent.mkdir(parents=True)
        def prepare(step):
            text='step\tcommand\n'+step+'\t'+runner.XARRAY_FIND_COMMAND+'\n'
            manifest.write_text(text);(directory/'actions.tsv').write_text(text)
            (directory/'steps.tsv').write_text('step\tbranch\tround\texit_code\tduration_ns\n'+step+'\tbackbone\t'+segment+'\t1\t10\n')
            for name,data in [('stdout',runner.XARRAY_FIND_STDOUT),('stderr',b'')]:
                (directory/name).mkdir(exist_ok=True);(directory/name/(step+'.log')).write_bytes(data)
            (directory/'patch.diff').write_bytes(b'');(directory/'guest-memory.tsv').write_text('sample\n1\n')
        sample=[dict(role=role,segment=segment,actions=[dict(exit_code=0)])]
        prepare('001')
        with self.assertRaises(AssertionError):runner.verify_segment(self.root,self.workload,role,segment,sample,'xarray6992')
        result=runner.verify_segment(self.root,self.workload,role,segment,sample,'xarray6992',xarray_find_diagnostic=True,reference_patch=b'')
        self.assertEqual(result['exit_differences'][0]['actual'],1)
        prepare('003')
        with self.assertRaises(AssertionError):runner.verify_segment(self.root,self.workload,role,segment,sample,'xarray6992',
            xarray_find_diagnostic=True,reference_patch=b'')


if __name__ == '__main__':unittest.main()
