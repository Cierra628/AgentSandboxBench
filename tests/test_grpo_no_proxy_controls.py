"""Only configuration, AST routing and mocked services; no real guests or devices."""
import ast
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import host_persistent_input as persistent
import platform_tasks as platform
import prettier_no_proxy as local
import run_host_file_workflow as wrapper
import validate_isolated_agentenv as native
from run_grpo_local_no_proxy_controls import comparisons
from report_grpo_local_no_proxy_controls import publish,guest_curves
import test_sphinx_no_proxy as helpers


def config(off=False):
    return platform.configuration(ROOT/('configs/platform-prettier6604-grpo-host-current'+('-off' if off else '')+'.json'))


class GrpoLocalTests(unittest.TestCase):
    def test_guest_panels_do_not_clip_a_peak_in_an_earlier_row(self):
        import csv
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=[]
            for role in ('backbone','early_1','early_2','early_3','late_1','late_2','late_3'):
                for i in range(2):
                    data.append(dict(role=role,sandbox_id=role,elapsed_seconds=i*.1,
                        memory_used_bytes=(1000 if role=='late_1' else 100)*2**20,
                        cached_bytes=(300 if role=='late_1' else 50)*2**20))
            with (root/'memory.tsv').open('w') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(data[0]),delimiter='\t');writer.writeheader();writer.writerows(data)
            with patch.object(plt,'close') as close:
                guest_curves(root,root/'curves.png');fig=close.call_args.args[0]
                for i,ax in enumerate(fig.axes):self.assertGreater(ax.get_ylim()[1],300 if i%2 else 1000)
            plt.close(fig)

    def test_runtime_budget_moves_after_import_only_for_new_grpo(self):
        tree=helpers.tree('platform_tasks.py')
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        ready=next(n for n in run.body if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='ready')
        later=next(n for n in ast.walk(run) if isinstance(n,ast.If) and ast.unparse(n.test)=='persistent_grpo')
        for grpo in (False,True):
            preflight=Mock(return_value={'status':'READY'})
            values=dict(persistent_grpo=grpo,cfg={},preflight=preflight,tool_capture=False,
                agent_config=None,task_config=None,replay_source=None,physical_after=None,result={})
            helpers.execute([ready],values)
            self.assertEqual(preflight.call_args.kwargs.get('offline',False),grpo)
            preflight.reset_mock();helpers.execute([later],values)
            self.assertEqual(preflight.call_count,int(grpo))
            if grpo:self.assertEqual(preflight.call_args.kwargs,{})
        text=ast.unparse(run)
        self.assertLess(text.index("run_phase('import'"),text.index("stage = 'runtime-preflight'"))
        self.assertLess(text.index("stage = 'runtime-preflight'"),text.index("stage = 'isolated-runtime'"))

    def test_report_refuses_incomplete_batch_without_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'result.json').write_text('{"status":"FAIL"}')
            with self.assertRaises(AssertionError):publish(root,root/'report')
            self.assertFalse((root/'report').exists())

    def test_signed_comparisons_keep_two_orders_and_zero_off_cost(self):
        runs=[]
        for index,(pair,mode) in enumerate(((1,'off'),(1,'on'),(2,'on'),(2,'off'))):
            runs.append(dict(pair=pair,mode=mode,paths={'backbone':['a']},patches={'backbone':'patch'},
                official={'backbone':{'passed':0,'failed':2}},
                timings_ns={'tool_sum':(10,12,9,10)[index]*10**9,'monitor_control_call_sum':0 if mode=='off' else 10**9}))
        result=comparisons(runs)
        self.assertEqual([p['order'] for p in result],[['off','on'],['on','off']])
        self.assertEqual([p['timings']['tool_sum']['change_seconds'] for p in result],[2,-1])
        self.assertIsNone(result[0]['timings']['monitor_control_call_sum']['change_percent'])

    def test_fixed_configs_differ_only_in_sampling(self):
        on,off=config(),config(True)
        self.assertEqual({k for k in on.keys()|off.keys() if on.get(k)!=off.get(k)}, {'light_monitor','monitor_mode'})
        for cfg in (on,off):
            self.assertTrue(persistent.local_grpo_config(cfg))
            local.require_mode(cfg,True)
            persistent.single_config(cfg,local_no_proxy=True)
            with self.assertRaises(ValueError):persistent.single_config(cfg)
            for change in ({'rollout':'bpo'},{'frozen_export':None},{'host_shared_files':True},
                           {'host_prefix_count':7},{'damon_observe':True},{'host_current_only':False},
                           {'grpo_create_before_setup':True}):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    persistent.single_config(dict(cfg,**change),local_no_proxy=True)
        with self.assertRaises(ValueError):persistent.single_config(dict(off,light_skip_boundary_vmstat=True),local_no_proxy=True)

    def test_real_native_parser_modes_and_old_grpo_stays_unchanged(self):
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            for off in (False,True):
                cfg=config(off)
                check,live=platform.commands(cfg,Path('/seed'),persistent_input=True)
                self.assertIn('--frozen-export',check)
                self.assertIn('--prettier6604-export',live)
                self.assertIn('--host-current-only',live)
                self.assertNotIn('--prettier6604-readonly-replay',live)
                self.assertNotIn('--immutable-cache-seed',live)
                args=helpers.SphinxLocalTests().native_args(cfg)
                self.assertTrue(native.ordinary_current_only(args,monitor_mode='off' if off else 'continuous'))
                self.assertEqual(persistent.native_option(args),Path('/seed'))
                for change in ({'prettier6604_schedule':'bpo'},{'host_scaling_mode':'cold'},
                               {'immutable_cache_seed':Path('/ram')},{'host_shared_files':True},
                               {'local_no_proxy_check':True},{'prettier6604_readonly_replay':True}):
                    with self.subTest(change=change),self.assertRaises(ValueError):
                        persistent.native_option(SimpleNamespace(**(vars(args)|change)))
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(native.ordinary_current_only(args,monitor_mode='off'))
            with self.assertRaises(ValueError):persistent.native_option(args)
            for name in ('light','off'):
                old=platform.configuration(ROOT/f'configs/platform-prettier6604-grpo-{name}.json')
                _,live=platform.commands(old,Path('/old'))
                self.assertIn('--immutable-cache-seed',live)
                self.assertNotIn('--host-current-only',live)

    def test_short_check_rejected_before_docker(self):
        with patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-grpo-host-current.json'),
                '--persistent-input','--local-no-proxy','--local-no-proxy-check']),\
             patch('platform_tasks.preflight') as preflight,patch.object(wrapper.subprocess,'check_output') as docker:
            with self.assertRaises(SystemExit):wrapper.main()
            docker.assert_not_called();preflight.assert_not_called()

    def test_commits_skip_and_readonly_binding_apply_to_both_modes(self):
        tree=helpers.tree('validate_isolated_agentenv.py')
        skip=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                  "part == 'image-cache/commits'" in ast.unparse(n.test) and 'persistent_seed' in ast.unparse(n.test))
        bind=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
                  ast.unparse(n.test).startswith('not durable_bundle and') and 'current_pair' in ast.unparse(n.test))
        values=dict(readonly_fixture=False,python_inputs=False,seed_value=True,prettier_readonly=False,
                    online_task=False,exp1_light=False,current_pair=False,persistent_seed=Path('/seed'),
                    host_current_only=True,part='image-cache/commits',durable_bundle=None)
        for node in (skip,bind):
            expr=compile(ast.Expression(node.test),'<actual commits condition>','eval')
            self.assertTrue(eval(expr,values))
            self.assertFalse(eval(expr,dict(values,persistent_seed=None)))

    def test_grpo_cold_request_uses_explicit_no_proxy_flag(self):
        tree=helpers.tree('run_prettier6604_grpo.py')
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and n.func.__class__ is ast.Attribute and
                  n.func.attr=='to_thread' and any(isinstance(a,ast.List) and 'agentenv_cold_start.py' in ast.unparse(a) for a in n.args))
        request=next(a for a in call.args if isinstance(a,ast.List))
        expr=compile(ast.Expression(request),'<actual cold request>','eval')
        args=SimpleNamespace(config_home='/home',service_url='http://127.0.0.1:1234')
        for local_mode in (False,True):
            command=eval(expr,dict(sys=sys,ROOT=ROOT,args=args,cfg={'source_image':local.IMAGE},local_no_proxy=local_mode))
            self.assertEqual('--local-no-proxy' in command,local_mode)

    def test_pair_only_seed_is_not_forwarded_to_full_grpo_checker(self):
        tree=helpers.tree('validate_isolated_agentenv.py')
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and len(n.body)==1 and
            isinstance(n.body[0],ast.AugAssign) and ast.unparse(n.body[0].target)=='check_args' and
            '--host-persistent-seed' in ast.unparse(n.body[0]))
        for pair in (False,True):
            values=dict(persistent_seed=Path('/seed'),cache_seed=Path('/seed'),current_pair=pair,check_args=[])
            helpers.execute([node],values)
            self.assertEqual('--host-persistent-seed' in values['check_args'],pair)


if __name__=='__main__':unittest.main()
