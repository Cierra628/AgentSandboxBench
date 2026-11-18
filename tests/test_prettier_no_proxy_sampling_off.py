"""Explicit Prettier off routing and toy workflow checks; no live service, guest or archive."""
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
import prettier_no_proxy as local
import host_persistent_input as persistent
import platform_tasks as platform
import run_host_file_workflow as wrapper
import validate_isolated_agentenv as native
import test_host_persistent_tasks as fixtures
import test_sphinx_no_proxy as source_helpers
from sandbox_runtime_monitor import GUEST_ROOT,boundary_runner
from task_metrics_prefix import without_sampling


def config(off=True):
    return platform.configuration(ROOT/('configs/platform-prettier6604-host-current'+('-off' if off else '')+'.json'))


class LocalSamplingOffTests(unittest.TestCase):
    def test_config_scope_is_explicit_and_old_persistent_off_stays_rejected(self):
        cfg=config();local.require_mode(cfg,True);persistent.single_config(cfg,local_no_proxy=True)
        with self.assertRaises(ValueError):persistent.single_config(cfg)
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):
            platform.commands(cfg,Path('/seed'),persistent_input=True)
        for change in ({'task_id':local.SPHINX_TASK},{'task_id':local.XARRAY_TASK},{'light_monitor':True},
                       {'monitor_mode':'continuous'},{'host_file_monitor':False},{'host_current_only':False},
                       {'rollout':'grpo'},{'checkpoint_after_step':24},{'damon_observe':True},{'damon_disable':True}):
            with self.subTest(change=change):
                for check in (lambda:local.require_mode(dict(cfg,**change),True),
                              lambda:persistent.single_config(dict(cfg,**change),local_no_proxy=True)):
                    with self.assertRaises(ValueError):check()
        with self.assertRaises(ValueError):local.require_mode(cfg,False)

    def test_real_cli_round_trip_accepts_only_full_prettier_off_and_matches_on_task(self):
        helper=source_helpers.SphinxLocalTests()
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'off'},clear=True):
            off=helper.native_args(config());on=helper.native_args(config(False))
            self.assertEqual(persistent.native_option(off),Path('/seed'))
            self.assertEqual(persistent.native_option(on),Path('/seed'))
            delta={k:(vars(on)[k],v) for k,v in vars(off).items() if vars(on)[k]!=v}
            self.assertEqual(delta,{'light_monitor':(True,False),'prettier6604_monitor_mode':('continuous','off')})
            for change in ({'light_monitor':True},{'local_no_proxy_check':True},{'prettier6604_checkpoint':True},
                           {'damon_disable':True},{'host_scaling_mode':'cold'},{'host_file_monitor':False},
                           {'host_current_only':False},{'immutable_cache_seed':Path('/ram')}):
                with self.subTest(change=change),self.assertRaises(ValueError):
                    persistent.native_option(SimpleNamespace(**(vars(off)|change)))
        with patch.dict(os.environ,{},clear=True),self.assertRaises(ValueError):persistent.native_option(off)

    def test_actual_native_local_guard_rejects_short_or_mismatched_task(self):
        nodes=next(n for n in source_helpers.tree('validate_isolated_agentenv.py').body if isinstance(n,ast.FunctionDef) and n.name=='main').body
        start=next(i for i,n in enumerate(nodes) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='local_no_proxy')
        end=next(i for i in range(start,len(nodes)) if isinstance(nodes[i],ast.If) and ast.unparse(nodes[i].test)=='persistent_seed')
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True):
            good=source_helpers.SphinxLocalTests().native_args(config())
            for change,prettier in (({},True),({'local_no_proxy_check':True},True),({},False)):
                ns=dict(os=os,args=SimpleNamespace(**(vars(good)|change)),persistent_seed=Path('/seed'),prettier6604=prettier,current_monitor_off=True)
                if not change and prettier:
                    source_helpers.execute(nodes[start:end],ns);self.assertEqual(ns['local_task_id'],local.PRETTIER_TASK)
                else:
                    with self.assertRaises(ValueError):source_helpers.execute(nodes[start:end],ns)

    def test_outer_off_keeps_preparation_accounting_proxy_and_cleanup_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);events=[]
            def run(argv,**kw):
                if argv[0]=='systemd-run':
                    events.append('group')
                    for flag in ('--setenv=ASB_HOST_FILE_SAMPLING=off','--setenv=ASB_LOCAL_NO_PROXY=on',
                                 '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--persistent-seed'):
                        self.assertIn(flag,argv)
                    for key in local.PROXY_KEYS:
                        self.assertIn('--setenv='+key+'=',argv);self.assertNotIn(key,os.environ)
                else:events.append('stop')
                return SimpleNamespace(returncode=0)
            with patch.dict(os.environ,{k:'http://127.0.0.1:17891' for k in local.PROXY_KEYS},clear=True),\
                 patch.object(wrapper,'ROOT',base),patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-current-off.json'),'--persistent-input','--local-no-proxy']),\
                 patch('platform_tasks.preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),\
                 patch.object(wrapper.subprocess,'run',side_effect=run),patch.object(local,'prepare_metadata',side_effect=lambda *a,**k:events.append('metadata')),\
                 patch.object(persistent,'run_phase',side_effect=lambda action,*a,**k:events.append(action)):
                self.assertEqual(wrapper.main(),0)
            self.assertEqual(events,['metadata','prepare','group','stop','release-source'])

    def test_short_and_conflicting_host_on_fail_before_preparation(self):
        path=ROOT/'configs/platform-prettier6604-host-current-off.json'
        for argv in ([str(path),'--local-no-proxy-check'],[str(path),'--host-sampling','on'],
                     [str(ROOT/'configs/platform-sphinx7590-host-current-off.json'),'--local-no-proxy-check'],
                     [str(ROOT/'configs/platform-xarray6992-host-current-off.json'),'--local-no-proxy-check']):
            with patch.dict(os.environ,{},clear=True),patch.object(sys,'argv',['entry',*argv,'--persistent-input','--local-no-proxy']),\
                 patch.object(wrapper.subprocess,'check_output') as docker,patch('platform_tasks.preflight') as preflight,\
                 patch.object(persistent,'run_phase') as phase,contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises((SystemExit,ValueError)):wrapper.main()
                docker.assert_not_called();preflight.assert_not_called();phase.assert_not_called()

    def test_missing_metadata_does_not_restore_or_start_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            phases=[]
            with patch.dict(os.environ,{},clear=True),patch.object(wrapper,'ROOT',Path(tmp)),\
                 patch.object(sys,'argv',['entry',str(ROOT/'configs/platform-prettier6604-host-current-off.json'),'--persistent-input','--local-no-proxy']),\
                 patch('platform_tasks.preflight'),patch.object(wrapper.subprocess,'check_output',return_value='systemd'),\
                 patch.object(wrapper.subprocess,'run') as run,patch.object(local,'prepare_metadata',side_effect=FileNotFoundError('missing metadata')),\
                 patch.object(persistent,'run_phase',side_effect=lambda action,*a,**k:phases.append(action)):
                with self.assertRaises(FileNotFoundError):wrapper.main()
                run.assert_not_called();self.assertEqual(phases,['release-source'])

    def test_leaf_off_creates_no_sampler_and_preserves_timed_commands(self):
        nodes=next(n for n in source_helpers.tree('run_prettier6604.py').body if isinstance(n,ast.FunctionDef) and n.name=='run').body
        guard=next(n for n in nodes if isinstance(n,ast.If) and ast.unparse(n.test)=='local_no_proxy')
        runtime=next(n for n in nodes if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='runtime')
        collector=Mock()
        values=dict(local_no_proxy=True,light=False,checkpoint=False,monitor_mode='off',env={k:'unused' for k in local.PROXY_KEYS},
                    RuntimeMonitor=collector,out=Path('/toy'),cli=Mock(),args=SimpleNamespace(container='toy'),damon_observe=False)
        source_helpers.execute([guard,runtime],values)
        collector.assert_not_called();self.assertIsNone(values['runtime']);self.assertFalse(values['env'])
        text=(ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/prettier-6604-rl-fork/guest-runner.sh').read_text()
        on=boundary_runner(text,guest_phases=True);off=without_sampling(text)
        timed=lambda s:s[s.index('  start_ns='):s.index('\n',s.index('  end_ns='))]
        self.assertEqual(timed(on),timed(off))
        self.assertIn('sample_memory() { :; }',off);self.assertIn('sample_vmstat() { :; }',off)
        self.assertNotIn('/proc/meminfo',off);self.assertNotIn('/proc/vmstat',off);self.assertNotIn('monitor_pid',off)
        self.assertIn(GUEST_ROOT+'/phase',on);self.assertNotIn(GUEST_ROOT+'/phase',off)

    def test_actual_native_dispatch_preserves_monitor_mode_and_original_audit(self):
        branch=next(n for n in ast.walk(source_helpers.tree('validate_isolated_agentenv.py'))
            if isinstance(n,ast.If) and ast.unparse(n.test)=='prettier6604' and n.body and
            isinstance(n.body[0],ast.Assign) and ast.unparse(n.body[0].targets[0])=='stage')
        end=next(i for i,n in enumerate(branch.body) if isinstance(n,ast.Assign) and ast.unparse(n.targets[0])=='runner')
        for mode in ('continuous','off'):
            values=dict(prettier_export=None,mount=Path('/toy'),prettier_checkpoint=False,prettier_readonly=True,
                prettier_monitor_mode=mode,light_monitor=mode=='continuous',expected_audit='/original-audit',
                damon_observe=False,damon_disable=False,prettier_physical=None,live_task_cache=False,live_task_cache_lower=False)
            source_helpers.execute(branch.body[:end+1],values)
            expected=['--monitor-mode',mode,'--expected-official-audit','/original-audit']
            if mode=='continuous':expected+=['--light-monitor']
            self.assertEqual(values['task_args'],expected);self.assertEqual(values['runner'],'run_prettier6604.py')

    def workflow(self,base,**kw):
        with patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on','ASB_LOCAL_NO_PROXY_CHECK':'off'},clear=True),\
             patch.object(local,'check_cache',return_value=dict(status='PASS')):
            return fixtures.PersistentTaskWorkflowTests().workflow(base,local=True,off=True,**kw)

    def test_off_workflow_keeps_common_query_cost_and_release_order_without_host_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            code,value,cache,events,out=self.workflow(Path(tmp))
            self.assertEqual(code,0);self.assertEqual(events,['import','native','native-cleanup-complete','query','report','release-input'])
            self.assertEqual(value['host_file_monitor'],dict(status='NOT_REQUESTED',host_sampling='off'))
            self.assertFalse((cache/'input').exists());self.assertEqual(value['input_file_check']['thread_cpu_ns'],17)
            self.assertEqual(value['input_file_check']['phase'],'AFTER_ISOLATED_RUNTIME_CLEANUP')
            self.assertGreaterEqual(value['input_file_check']['start_monotonic_ns'],value['isolated_runtime_end_monotonic_ns'])
            self.assertEqual(value['official_task_status'],'FAIL');self.assertEqual(value['local_no_proxy']['guest_check'],'PASS')

    def test_failures_preserve_input_and_do_not_query_or_release_prematurely(self):
        for failure in ('import','native','proxy-proof','query','shortfall','unexpected-baseline','host-on','report','cleanup'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                code,value,cache,events,out=self.workflow(Path(tmp),failure=failure)
                self.assertEqual(code,1);self.assertTrue((cache/'input').exists());self.assertNotIn('collector-start',events)
                self.assertLessEqual(events.count('native'),1)
                if failure in ('import','native','proxy-proof','unexpected-baseline','host-on'):self.assertNotIn('query',events)
                if failure!='cleanup':self.assertNotIn('release-input',events)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{},clear=True):
            code,value,cache,events,out=fixtures.PersistentTaskWorkflowTests().workflow(Path(tmp),failure='missing-baseline')
            self.assertEqual(code,1);self.assertNotIn('query',events);self.assertTrue((cache/'input').exists())

    def test_missing_local_cache_stops_before_native_and_retains_input(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'ASB_LOCAL_NO_PROXY':'on'},clear=True),\
             patch.object(local,'check_cache',side_effect=FileNotFoundError('missing local lower')):
            code,value,cache,events,out=fixtures.PersistentTaskWorkflowTests().workflow(Path(tmp),local=True,off=True)
            self.assertEqual(code,1);self.assertEqual(value['failed_stage'],'persistent-import')
            self.assertEqual(events,['import']);self.assertTrue((cache/'input').exists())

    def test_real_off_report_keeps_final_query_and_null_running_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,receipt,cache,events,workflow=self.workflow(root)
            child=Path(receipt['storage_result_dir']);raw=child/'prettier6604';raw.mkdir()
            fixtures.write(raw/'result.json',dict(status='PASS',task_id=local.PRETTIER_TASK,monitor_mode='off',cleanup=dict(monitoring_disabled=True),
                guest_action_sum_ns=2,official_task_status='FAIL'))
            (raw/'segments.tsv').write_text('rollout\tsegment\tsandbox_id\nbackbone\tread_repo\ttoy\n')
            segment=raw/'rollouts/backbone/read_repo';segment.mkdir(parents=True)
            (segment/'steps.tsv').write_text('step\texit_code\tduration_ns\n001\t0\t2\n')
            for file in ('guest-memory.tsv','guest-vmstat.tsv'):(segment/file).write_text('timestamp_ns\n')
            with patch.object(platform,'ROOT',root),patch('report_host_file.report') as host_report:
                output=platform.report(workflow,root/'.artifacts/real-report')
            host_report.assert_not_called();summary=json.loads((output/'summary.json').read_text())
            self.assertEqual(summary['memory'],'NOT_MEASURED');self.assertIsNone(summary['host_file']['current_peak_bytes'])
            self.assertEqual(summary['persistent_input']['input_file_check'],receipt['input_file_check'])
            self.assertEqual(summary['local_no_proxy']['guest_check'],'PASS');self.assertFalse(list(output.rglob('*.png')))
            self.assertEqual(json.loads((output/'result.json').read_text())['official_task_status'],'FAIL')


if __name__=='__main__':unittest.main()
