"""Explicit Python targets; mock all guest proc/sys writes and runtime calls."""
import ast,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,mock_open
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guest_cache_capacity as guest
import platform_tasks as platform
import run_xarray6992 as runner
import validate_isolated_agentenv as validator


class PythonTargetTests(unittest.TestCase):
    def test_new_targets_reuse_controller_and_restore_on_success_or_failure(self):
        for target in (65,98,167,251):
            for fail in (False,True):
                with self.subTest(target=target,fail=fail),patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',clean_reclaim_interface=True,damon=dict(enabled='Y'))), \
                     patch.object(Path,'mkdir'),patch.object(Path,'open',mock_open()),patch.object(Path,'read_text',return_value=''),patch.object(Path,'iterdir',return_value=[]), \
                     patch.object(Path,'exists',side_effect=[False,True]),patch.object(Path,'touch'),patch.object(Path,'write_text') as write,patch.object(guest.time,'sleep'), \
                     patch.object(guest,'counters',return_value=dict(cache_bytes=target*2**20+4097)), \
                     patch.object(guest,'reclaim',side_effect=RuntimeError('cycle failure') if fail else None,return_value=dict(requested_pages=2)) as reclaim, \
                     patch.object(guest,'set_damon',side_effect=[dict(enabled='N',kdamond_pid=-1),dict(enabled='Y',kdamond_pid=7)]) as damon:
                    self.assertEqual(guest.serve(Path('/workspace/asb-task-capacity'),target,python_target=True),1 if fail else 0)
                    reclaim.assert_called_once_with(target*2**20+4097,target*2**20)
                    self.assertEqual([c.args for c in damon.call_args_list],[('N',),('Y',)])
                    state=json.loads(write.call_args.args[0]);self.assertEqual(state['target_bytes'],target*2**20)
                    self.assertNotIn('baseline_no_reclaim',state)

    def test_old_function_does_not_adopt_new_choices_or_invalid_python_targets(self):
        for value,enabled in [(65,False),(0,True),(-1,True),(True,True),(None,True)]:
            with self.subTest(value=value),patch.object(guest,'capabilities') as caps:
                with self.assertRaises(AssertionError):guest.serve(Path('/workspace/asb-task-capacity'),value,python_target=enabled)
                caps.assert_not_called()

    def test_four_configs_dispatch_exact_targets_and_baselines_stay_unchanged(self):
        for task,targets in [('sphinx7590',(65,98)),('xarray6992',(167,251))]:
            for target in targets:
                cfg=platform.configuration(platform.ROOT/f'configs/platform-{task}-cache-{target}.json')
                check,live=platform.commands(cfg,platform.ROOT/'.artifacts/unused-cache')
                self.assertEqual(check[check.index('--cache-target-mib')+1],str(target))
                self.assertEqual(live[live.index('--python-cache-target-mib')+1],str(target))
                self.assertNotIn('--cache-baseline',check)

    def test_invalid_config_targets_and_baseline_mix_rejected(self):
        original=json.loads((platform.ROOT/'configs/platform-sphinx7590-cache-baseline.json').read_text())
        for value,baseline in [(0,False),(-1,False),(True,False),('65',False),(65,True)]:
            cfg=dict(original,guest_cache_target_mib=value,guest_cache_baseline=baseline)
            with tempfile.TemporaryDirectory() as temp:
                p=Path(temp)/'config.json';p.write_text(json.dumps(cfg))
                with self.assertRaisesRegex(ValueError,'guest_cache_target_mib requires'):platform.configuration(p)

    def test_validator_rejects_target_without_live_collection_before_allocation(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'Python cache target requires'):
                validator.main(SimpleNamespace(python_cache_target_mib=65))
            allocate.assert_not_called()

    def test_actual_stop_checks_exact_excess_requests_and_original_restore(self):
        tree=ast.parse(Path(runner.__file__).read_text());fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='capacity_stop')
        fn.body=[ast.copy_location(ast.Global(names=n.names),n) if isinstance(n,ast.Nonlocal) else n for n in fn.body]
        for bad in (None,'request','restore'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as temp:
                state=dict(status='PASS',target_bytes=65*2**20,capabilities_before=dict(damon=dict(enabled='Y')),damon_disabled=dict(enabled='N',kdamond_pid=-1),damon_restored=dict(enabled='Y',kdamond_pid=7))
                row=dict(target_bytes=65*2**20,before=dict(cache_bytes=65*2**20+4097),action=dict(requested_pages=2))
                if bad=='request':row['action']['requested_pages']=1
                if bad=='restore':state['damon_restored']['enabled']='N'
                def cli(label,*args,**kwargs):return json.dumps(state if args[-1].endswith('result.json') else row)
                result=dict(checks={});ns=dict(out=Path(temp),guest=lambda *a:None,cli=cli,sid='owned',json=json,result=result,capacity_active=True,cache_baseline=False,cache_target=65)
                exec(compile(ast.Module(body=[fn],type_ignores=[]),'<actual capacity stop>','exec'),ns)
                if bad:
                    with self.assertRaises(AssertionError):ns['capacity_stop']()
                else:
                    ns['capacity_stop']();self.assertEqual(result['checks']['cache_target_and_damon_restored'],'PASS')
                self.assertFalse(ns['capacity_active'])


if __name__=='__main__':unittest.main()
