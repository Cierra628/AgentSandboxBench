"""Baseline checks with synthetic proc/sys and CLI values; no live resources."""
import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,mock_open

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guest_cache_capacity as guest
import platform_tasks as platform
import validate_isolated_agentenv as validator
import run_xarray6992 as runner


class PythonCacheBaselineTests(unittest.TestCase):
    def test_baseline_never_calls_reclaim_and_restores_original_state(self):
        for original in ('Y','N'):
            for fail in (False,True):
                with self.subTest(original=original,fail=fail),patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',clean_reclaim_interface=True,damon=dict(enabled=original))), \
                     patch.object(Path,'mkdir'),patch.object(Path,'open',mock_open()),patch.object(Path,'read_text',return_value=''),patch.object(Path,'iterdir',return_value=[]), \
                     patch.object(Path,'exists',side_effect=[False,True]),patch.object(Path,'touch'),patch.object(Path,'write_text') as write, \
                     patch.object(guest.time,'sleep'),patch.object(guest,'counters',side_effect=RuntimeError('counter failed') if fail else None,return_value=dict(cache_bytes=200*2**20)), \
                     patch.object(guest,'reclaim') as reclaim,patch.object(guest,'set_damon',side_effect=[dict(enabled='N',kdamond_pid=-1),dict(enabled=original,kdamond_pid=8 if original=='Y' else -1)]) as damon:
                    self.assertEqual(guest.serve(Path('/workspace/asb-task-capacity'),None),1 if fail else 0)
                    reclaim.assert_not_called();self.assertEqual([c.args for c in damon.call_args_list],[('N',),(original,)])
                    state=json.loads(write.call_args.args[0]);self.assertTrue(state['baseline_no_reclaim']);self.assertIsNone(state['target_bytes'])
                    self.assertEqual(state['status'],'FAIL' if fail else 'PASS')

    def test_new_configs_reach_both_existing_entrypoints_and_old_configs_stay_default(self):
        for task in ('sphinx7590','xarray6992'):
            cfg=platform.configuration(platform.ROOT/f'configs/platform-{task}-cache-baseline.json')
            check,live=platform.commands(cfg,platform.ROOT/'.artifacts/unused-cache')
            self.assertIn('--cache-baseline',check);self.assertIn('--python-cache-baseline',live)
            self.assertIn('vmlinux.bin',cfg['live_cache_kernel'])
            old=platform.configuration(platform.ROOT/f'configs/platform-{task}-live-cache.json')
            check,live=platform.commands(old,platform.ROOT/'.artifacts/unused-cache')
            self.assertNotIn('--cache-baseline',check);self.assertNotIn('--python-cache-baseline',live)

    def test_baseline_config_rejects_non_python_and_missing_live_cache(self):
        for task in ('platform-prettier6604.json','platform-sphinx7590-live-cache.json'):
            cfg=json.loads((platform.ROOT/'configs'/task).read_text());cfg['guest_cache_baseline']=True;cfg.pop('live_cache_kernel',None)
            with tempfile.TemporaryDirectory() as name:
                p=Path(name)/'config.json';p.write_text(json.dumps(cfg))
                with self.assertRaisesRegex(ValueError,'guest_cache_baseline requires'):platform.configuration(p)

    def test_validator_rejects_baseline_without_live_cache_before_allocation(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'Python cache baseline requires'):
                validator.main(SimpleNamespace(python_cache_baseline=True))
            allocate.assert_not_called()

    def test_actual_stop_verifies_restored_state_and_zero_requests(self):
        tree=ast.parse(Path(runner.__file__).read_text())
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='capacity_stop')
        fn.body=[ast.copy_location(ast.Global(names=n.names),n) if isinstance(n,ast.Nonlocal) else n for n in fn.body]
        for bad in (None,'request','restore'):
            with self.subTest(bad=bad),tempfile.TemporaryDirectory() as name:
                state=dict(status='PASS',baseline_no_reclaim=True,target_bytes=None,capabilities_before=dict(damon=dict(enabled='Y')),damon_disabled=dict(enabled='N',kdamond_pid=-1),damon_restored=dict(enabled='Y',kdamond_pid=7))
                row=dict(target_bytes=None,action=dict(requested_pages=0,write_ns=0))
                if bad=='request':row['action']['requested_pages']=1
                if bad=='restore':state['damon_restored']['enabled']='N'
                def cli(label,*args,**kwargs):return json.dumps(state if args[-1].endswith('result.json') else row)
                result=dict(checks={});ns=dict(out=Path(name),guest=lambda *a:None,cli=cli,sid='owned',json=json,result=result,capacity_active=True,cache_baseline=True,cache_target=None)
                exec(compile(ast.Module(body=[fn],type_ignores=[]),'<actual capacity stop>','exec'),ns)
                if bad:
                    with self.assertRaises(AssertionError):ns['capacity_stop']()
                else:
                    ns['capacity_stop']();self.assertEqual(result['checks']['cache_baseline_no_reclaim_and_damon_restored'],'PASS')
                self.assertFalse(ns['capacity_active'])

    def test_wrapper_distinguishes_report_from_run_and_reuses_saved_success(self):
        source=(platform.ROOT/'scripts/run-python-cache-baselines.sh').read_text()
        code=source.split("<<'PY'\n",1)[1].split('\nPY',1)[0]
        with tempfile.TemporaryDirectory() as name:
            out=Path(name);run=out/'20261018-run';run.mkdir()
            (run/'result.json').write_text(json.dumps(dict(status='PASS',storage_result_dir='saved-storage',report_dir='saved-report')))
            (out/'sphinx7590.log').write_text('status=PASS result_dir='+str(out/'report-20261018-run')+'\nstatus=PASS result_dir='+str(run)+'\n')
            for _ in range(2):
                with patch.object(sys,'argv',['receipt',str(out),'sphinx7590']):exec(compile(code,'<actual wrapper receipt>','exec'),{})
            self.assertEqual(json.loads((out/'runs.json').read_text())['sphinx7590']['platform'],str(run))

    def test_start_and_stop_surround_original_replay_not_official_tests(self):
        source=Path(runner.__file__).read_text()
        self.assertLess(source.index('if cache_control:capacity_start()'),source.index('if live:live.start(sid)'))
        self.assertLess(source.index("capacity_boundary('task-before')"),source.index("stage='first-segment-smoke'"))
        self.assertLess(source.index("capacity_boundary('task-after')"),source.index("stage='final-frozen-state'"))
        self.assertIn("if capacity_active and sid:",source)


if __name__=='__main__':unittest.main()
