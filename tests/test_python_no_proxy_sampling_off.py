"""Fixed Python-task on/off dispatch, toy reports and mocked lifecycle; no live resources."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import platform_tasks as platform
import host_persistent_input as persistent
import prettier_no_proxy as local
import run_host_file_workflow as wrapper
import run_xarray6992 as leaf
import test_host_persistent_tasks as fixtures
import test_sphinx_no_proxy as shared
from sandbox_runtime_monitor import GUEST_ROOT,boundary_runner
from task_metrics_prefix import without_sampling
PROFILES=('sphinx7590','xarray6992')


def config(profile,off=True):
    return platform.configuration(ROOT/f"configs/platform-{profile}-host-current{'-off' if off else ''}.json")

def leaf_parser():
    entry=next(n for n in shared.tree('run_xarray6992.py').body if isinstance(n,ast.If) and ast.unparse(n.test)=="__name__ == '__main__'")
    end=next(i for i,n in enumerate(entry.body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='args')
    values=dict(vars(leaf));shared.execute(entry.body[:end],values);return values['parser']


class PythonLocalSamplingOffTests(unittest.TestCase):
    def test_explicit_config_scope_and_fixed_backup_leave_legacy_contract_unchanged(self):
        for profile in PROFILES:
            cfg=config(profile);local.require_mode(cfg,True);persistent.single_config(cfg,local_no_proxy=True)
            with self.assertRaises(ValueError):persistent.single_config(cfg)
            with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):platform.commands(cfg,Path('/seed'),persistent_input=True)
            for change in ({'cache_backup_directory':'/wrong'},{'host_file_monitor':False},{'host_current_only':False},
                           {'light_monitor':True},{'monitor_mode':'continuous'},{'rollout':'grpo'},
                           {'checkpoint_after_step':22},{'damon_disable':True},{'task_id':'unknown'}):
                with self.subTest(profile=profile,change=change),self.assertRaises(ValueError):
                    persistent.single_config(dict(cfg,**change),local_no_proxy=True)
            for policy in ('strict','sphinx-pipeline-v2' if profile=='xarray6992' else 'xarray-approved-sigpipe'):
                with self.assertRaises(ValueError):local.require_mode(dict(cfg,replay_policy=policy),True)
            with self.assertRaises(ValueError):local.require_mode(cfg,False)

    def test_actual_native_parser_changes_two_sampling_fields_and_keeps_policies(self):
        helper=shared.SphinxLocalTests()
        for profile in PROFILES:
            with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
                on=helper.native_args(config(profile,False));off=helper.native_args(config(profile))
                delta={k:[getattr(on,k),v] for k,v in vars(off).items() if getattr(on,k)!=v}
                self.assertEqual(delta,{'light_monitor':[True,False],'python_monitor_mode':['continuous','off']})
                self.assertTrue(off.python_persistent_sampling);self.assertEqual(persistent.native_option(off),Path('/seed'))
                self.assertEqual(off.python_sphinx_pipeline_diagnostic,profile=='sphinx7590')
                self.assertEqual(off.python_diagnostic_continuation,profile=='xarray6992')
                if profile=='sphinx7590':self.assertEqual(off.python_sphinx_pipeline_policy_version,2)
                for change in ({'light_monitor':True},{'host_current_only':False},{'host_file_monitor':False},
                               {'python_persistent_sampling':False},{'python_readonly_replay':False},
                               {'local_no_proxy_check':True},{'python_checkpoint':True},{'python_diagnostic_only':True},
                               {'host_scaling_mode':'cold'},{'immutable_cache_seed':'/ram'}):
                    with self.subTest(profile=profile,change=change),self.assertRaises(ValueError):
                        persistent.native_option(SimpleNamespace(**(vars(off)|change)))
            with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):persistent.native_option(off)

    def test_actual_native_local_guard_accepts_off_but_rejects_wrong_policy_or_short(self):
        nodes=next(n for n in shared.tree('validate_isolated_agentenv.py').body if isinstance(n,ast.FunctionDef) and n.name=='main').body
        start=next(i for i,n in enumerate(nodes) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='local_no_proxy')
        end=next(i for i in range(start,len(nodes)) if isinstance(nodes[i],ast.If) and ast.unparse(nodes[i].test)=='persistent_seed')
        for profile in PROFILES:
            with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
                args=shared.SphinxLocalTests().native_args(config(profile))
                changes=[{}, {'local_no_proxy_check':True},{'python_task_profile':'unknown'}]
                changes+=[{'python_sphinx_pipeline_policy_version':1}] if profile=='sphinx7590' else [{'python_diagnostic_continuation':False},{'python_sphinx_pipeline_diagnostic':True}]
                for change in changes:
                    ns=dict(os=os,args=SimpleNamespace(**(vars(args)|change)),persistent_seed=Path('/seed'),prettier6604=False,current_monitor_off=True)
                    if change:
                        with self.assertRaises(ValueError):shared.execute(nodes[start:end],ns)
                    else:
                        shared.execute(nodes[start:end],ns);self.assertEqual(ns['local_task_id'],config(profile)['task_id'])

    def test_actual_native_to_leaf_dispatch_keeps_complete_tasks_and_persistent_flag(self):
        # Select the full-task dispatch, not one of its preparation checks.
        branch=next(n for n in ast.walk(shared.tree('validate_isolated_agentenv.py')) if isinstance(n,ast.If) and ast.unparse(n.test)=='python_inputs' and
                    any(isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='task_command' for n in n.body))
        end=next(i for i,n in enumerate(branch.body) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and ast.unparse(n.value.func)=='call')
        for profile in PROFILES:
            for off in (False,True):
                mode='off' if off else 'continuous'
                values=dict(sphinx=profile=='sphinx7590',mount=Path('/toy'),python_short=False,sys=sys,ROOT=ROOT,
                    python_inputs=Path('/fixed-input'),python_profile=profile,args=SimpleNamespace(container='toy'),container='toy',
                    receipt={'address':'http://127.0.0.1:12345'},python_diagnostic=False,python_continuation=profile=='xarray6992',
                    sphinx_pipeline=profile=='sphinx7590',pipeline_version=2 if profile=='sphinx7590' else 1,
                    python_evaluation=None,python_checkpoint=False,python_persistent=True,python_monitor_mode=mode,
                    python_physical=None,python_live_cache=False,python_cache_baseline=False,python_cache_target=None,light_monitor=not off,
                    task_endpoint_metrics=False,python_schedule=None)
                shared.execute(branch.body[:end],values);args=leaf_parser().parse_args(list(map(str,values['task_command'][3:])))
                self.assertEqual(args.task_profile,profile);self.assertEqual(args.monitor_mode,mode);self.assertEqual(args.light_monitor,not off)
                self.assertTrue(args.persistent_sampling);self.assertFalse(args.checkpoint)
                self.assertEqual(args.sphinx_pipeline_diagnostic,profile=='sphinx7590');self.assertEqual(args.diagnostic_continuation,profile=='xarray6992')

    def leaf_checks(self,profile,changes=None):
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            command,_=platform.commands(config(profile),Path('/seed'),persistent_input=True)
            args=leaf_parser().parse_args(command[3:]);args.check_only=False
            for key,value in (changes or {}).items():setattr(args,key,value)
            nodes=next(n for n in shared.tree('run_xarray6992.py').body if isinstance(n,ast.FunctionDef) and n.name=='run').body
            start=next(i for i,n in enumerate(nodes) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='continuation')
            end=next(i for i,n in enumerate(nodes) if isinstance(n,ast.If) and ast.unparse(n.test).startswith('local_no_proxy and'))
            values=dict(args=args,cfg={'path':['read_repo','core_patch']},os=os)
            shared.execute(nodes[:2],values)  # Task-profile choice and Sphinx-only restrictions, before input validation.
            shared.execute([n for n in nodes[start:end+1] if not (isinstance(n,ast.If) and ast.unparse(n.test)=='args.check_only')],values)
            collector=Mock();runtime=next(n for n in nodes if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='runtime')
            values.update(RuntimeMonitor=collector,out=Path('/toy'),cli=Mock());shared.execute([runtime],values)
            collector.assert_not_called();self.assertIsNone(values['runtime'])

    def test_leaf_complete_off_creates_no_sampler_and_rejects_mixed_modes(self):
        for profile in PROFILES:
            self.leaf_checks(profile)
            for change in ({'persistent_sampling':False},{'light_monitor':True},{'diagnostic_only':True},
                           {'physical_cache_after':'read_repo'},{'live_cache':True},{'cache_baseline':True},
                           {'evaluation_patch':Path('/wrong')},{'task_profile':'unknown'}):
                with self.subTest(profile=profile,change=change),self.assertRaises(ValueError):self.leaf_checks(profile,change)
            change={'sphinx_pipeline_policy_version':1} if profile=='sphinx7590' else {'diagnostic_continuation':False}
            with self.assertRaises(ValueError):self.leaf_checks(profile,change)

    def test_outer_off_uses_task_source_clears_proxy_and_keeps_accounting(self):
        for profile in PROFILES:
            with tempfile.TemporaryDirectory() as tmp:
                events=[]
                def run(argv,**kw):
                    if argv[0]=='systemd-run':
                        events.append('group')
                        for flag in ('--setenv=ASB_HOST_FILE_SAMPLING=off','--setenv=ASB_LOCAL_NO_PROXY=on','--property=MemoryAccounting=yes','--property=IOAccounting=yes','--persistent-seed'):self.assertIn(flag,argv)
                        for key in local.PROXY_KEYS:self.assertIn('--setenv='+key+'=',argv);self.assertNotIn(key,os.environ)
                    else:events.append('stop')
                    return SimpleNamespace(returncode=0)
                def phase(action,*a,**kw):
                    events.append(action)
                    if action=='prepare':self.assertEqual(kw,{'task_profile':profile})
                with patch.dict(os.environ,{k:'http://127.0.0.1:17891' for k in local.PROXY_KEYS},clear=True),patch.object(wrapper,'ROOT',Path(tmp)),\
                     patch.object(sys,'argv',['entry',str(ROOT/f'configs/platform-{profile}-host-current-off.json'),'--persistent-input','--local-no-proxy']),\
                     patch.object(platform,'preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),patch.object(wrapper.subprocess,'run',side_effect=run),\
                     patch.object(local,'prepare_metadata',side_effect=lambda p,**kw:events.append('metadata')),patch.object(persistent,'run_phase',side_effect=phase):
                    self.assertEqual(wrapper.main(),0)
                self.assertEqual(events,['metadata','prepare','group','stop','release-source'])

    def test_short_and_conflicting_host_on_stop_before_docker_or_prepare(self):
        for profile in PROFILES:
            for flags in (['--local-no-proxy-check'],['--host-sampling','on']):
                with patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',['entry',str(ROOT/f'configs/platform-{profile}-host-current-off.json'),'--persistent-input','--local-no-proxy',*flags]),\
                     patch.object(wrapper.subprocess,'check_output') as docker,patch.object(platform,'preflight') as preflight,patch.object(persistent,'run_phase') as prepare,contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):wrapper.main()
                    docker.assert_not_called();preflight.assert_not_called();prepare.assert_not_called()

    def workflow(self,base,profile,**kw):
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'off'},clear=True),patch.object(local,'check_cache',return_value={'status':'PASS'}):
            return fixtures.PersistentTaskWorkflowTests().workflow(base,profile,local=True,off=True,**kw)

    def test_off_workflow_keeps_query_cost_phase_and_release_without_host_thread(self):
        for profile in PROFILES:
            with tempfile.TemporaryDirectory() as tmp:
                code,value,cache,events,out=self.workflow(Path(tmp),profile)
                self.assertEqual(code,0);self.assertEqual(events,['import','native','native-cleanup-complete','query','report','release-input'])
                self.assertEqual(value['host_file_monitor'],{'status':'NOT_REQUESTED','host_sampling':'off'})
                self.assertEqual(value['input_file_check']['phase'],'AFTER_ISOLATED_RUNTIME_CLEANUP')
                self.assertEqual(value['input_file_check']['thread_cpu_ns'],17)
                self.assertGreaterEqual(value['input_file_check']['start_monotonic_ns'],value['isolated_runtime_end_monotonic_ns'])
                self.assertFalse((cache/'input').exists());self.assertEqual(value['official_task_status'],'FAIL')

    def test_failures_preserve_input_and_do_not_repeat_runtime(self):
        for profile in PROFILES:
            for failure in ('proxy-proof','unexpected-baseline','host-on','query','shortfall','report','cleanup'):
                with self.subTest(profile=profile,failure=failure),tempfile.TemporaryDirectory() as tmp:
                    code,value,cache,events,out=self.workflow(Path(tmp),profile,failure=failure)
                    self.assertEqual(code,1);self.assertTrue((cache/'input').exists());self.assertNotIn('collector-start',events)
                    self.assertLessEqual(events.count('native'),1)
                    if failure in ('proxy-proof','unexpected-baseline','host-on'):self.assertNotIn('query',events)
                    if failure!='cleanup':self.assertNotIn('release-input',events)

    def test_missing_cache_stops_before_native_and_preserves_input(self):
        for profile in PROFILES:
            with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True),patch.object(local,'check_cache',side_effect=FileNotFoundError('missing lower')):
                code,value,cache,events,out=fixtures.PersistentTaskWorkflowTests().workflow(Path(tmp),profile,local=True,off=True)
                self.assertEqual(code,1);self.assertEqual(events,['import']);self.assertTrue((cache/'input').exists())

    def test_real_off_report_keeps_original_failures_common_query_and_null_memory(self):
        for profile in PROFILES:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);_,receipt,cache,events,work=self.workflow(root,profile)
                raw=Path(receipt['storage_result_dir'])/'python-backbone';raw.mkdir()
                fixtures.write(raw/'result.json',dict(status='PASS',task_id=config(profile)['task_id'],monitor_mode='off',cleanup={'monitoring_disabled':True},guest_action_sum_ns=2,official_task_status='FAIL',strict_replay_status='FAIL',diagnostic_status='PASS'))
                (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\ttoy\n');segment=raw/'rollouts/backbone/read_repo';segment.mkdir(parents=True)
                (segment/'steps.tsv').write_text('step\texit_code\tduration_ns\n001\t0\t2\n')
                for name in ('guest-memory.tsv','guest-vmstat.tsv'):(segment/name).write_text('timestamp_ns\n')
                with patch.object(platform,'ROOT',root),patch('report_host_file.report') as host_report:output=platform.report(work,root/'.artifacts/report-real')
                host_report.assert_not_called();summary=json.loads((output/'summary.json').read_text())
                self.assertEqual(summary['memory'],'NOT_MEASURED');self.assertIsNone(summary['host_file']['current_peak_bytes'])
                self.assertEqual(summary['persistent_input']['input_file_check'],receipt['input_file_check'])
                self.assertEqual(summary['local_no_proxy']['guest_check'],'PASS');self.assertFalse(list(output.rglob('*.png')))
                result=json.loads((output/'result.json').read_text());self.assertEqual(result['official_task_status'],'FAIL');self.assertEqual(result['strict_replay_status'],'FAIL')
                self.assertEqual(summary['diagnostic_status'],'PASS')

    def test_generated_off_runners_preserve_commands_but_remove_boundary_reads(self):
        for profile,workload in [('sphinx7590','sphinx-7590-rl-fork'),('xarray6992','xarray-6992-rl-fork')]:
            original=(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads'/workload/'guest-runner.sh').read_text()
            on=boundary_runner(original,guest_phases=True);off=without_sampling(original)
            timed=lambda s:s[s.index('  start_ns='):s.index('\n',s.index('  end_ns='))]
            self.assertEqual(timed(on),timed(off));self.assertNotIn('/proc/meminfo',off);self.assertNotIn('/proc/vmstat',off)
            self.assertNotIn('monitor_pid',off);self.assertNotIn(GUEST_ROOT+'/phase',off)


if __name__=='__main__':unittest.main()
