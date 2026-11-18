"""Fake parameter files and actual cleanup control flow; never write host sysfs."""
import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import guest_task_damon as control
from platform_tasks import configuration,commands,preflight


class TaskDamonControlTests(unittest.TestCase):
    def parameters(self,root,enabled='Y'):
        path=root/'parameters';path.mkdir()
        for name,value in dict(enabled=enabled,kdamond_pid='64' if enabled=='Y' else '-1',
                               bytes_reclaimed_regions='4096',bytes_reclaim_tried_regions='8192').items():
            (path/name).write_text(value+'\n')
        return path

    def advance(self,parameters):
        enabled=(parameters/'enabled').read_text().strip()
        (parameters/'kdamond_pid').write_text('-1\n' if enabled=='N' else '123\n')

    def test_prepare_waits_for_thread_exit_and_restores_original_state(self):
        for original in ('Y','N'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);parameters=self.parameters(root,original);state=root/'state.json'
                with patch('guest_task_damon.time.sleep',side_effect=lambda _:self.advance(parameters)):
                    prepared=control.prepare(parameters,state,'owned-boot')
                    self.assertEqual(prepared['after']['enabled'],'N');self.assertEqual(prepared['after']['kdamond_pid'],'-1')
                    saved=state.read_text()
                    with self.assertRaises(FileExistsError):control.prepare(parameters,state,'owned-boot')
                    restored=control.restore(parameters,state,'owned-boot')
                    self.assertEqual(restored['after']['enabled'],original)
                    self.assertEqual(state.read_text(),saved)
                    self.assertEqual((parameters/'bytes_reclaimed_regions').read_text(),'4096\n')

    def test_restore_works_after_prepare_failure_and_rejects_wrong_guest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);parameters=self.parameters(root);state=root/'state.json'
            with patch('guest_task_damon.time.monotonic',side_effect=[0,11]):
                with self.assertRaises(TimeoutError):control.prepare(parameters,state,'owned-boot')
            self.assertEqual((parameters/'enabled').read_text().strip(),'N')
            self.assertEqual(json.loads(state.read_text())['before']['enabled'],'Y')
            with self.assertRaisesRegex(AssertionError,'different guest'):control.restore(parameters,state,'wrong-boot')
            self.assertEqual(control.restore(parameters,state,'owned-boot')['after']['enabled'],'Y')

    def test_new_config_is_explicit_and_keeps_observation(self):
        cfg=configuration(ROOT/'configs/platform-prettier6604-damon-disabled.json')
        for command in commands(cfg,Path('/unused')):
            self.assertIn('--damon-disable',command);self.assertIn('--damon-observe',command);self.assertIn('--light-monitor',command)
        self.assertEqual(preflight(cfg,offline=True)['status'],'VALIDATED')
        original=json.loads((ROOT/'configs/platform-prettier6604.json').read_text())
        for fields in (dict(damon_disable=True),dict(damon_observe=True,damon_disable=1),dict(damon_observe=True,damon_disable=True,monitor_mode='off')):
            with tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/'cfg.json';path.write_text(json.dumps(original|fields))
                with self.assertRaises(ValueError):configuration(path)

    def test_actual_finally_restores_before_delete_even_if_monitor_stop_fails(self):
        tree=ast.parse((ROOT/'scripts/run_prettier6604.py').read_text())
        run=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run')
        final=next(n.finalbody for n in run.body if isinstance(n,ast.Try))
        cleanup=next(n for n in final if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='sid')
        code=compile(ast.Module(body=[cleanup],type_ignores=[]),'<actual cleanup>','exec')
        for stop_failure in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                events=[]
                def stop(_):
                    events.append('stop')
                    if stop_failure:raise RuntimeError('injected sampler failure')
                def cli(label,*args,**kwargs):
                    events.append(label)
                    return json.dumps(dict(status='PASS',original_enabled='Y',after=dict(enabled='Y')))
                result={};flags={}
                scope=dict(sid='owned',runtime=SimpleNamespace(stop=stop),damon_restore_needed=True,
                           cli=cli,json=json,out=Path(temp),result=result,cleanup=flags,success=True)
                exec(code,scope)
                self.assertEqual(events,['stop','damon-restore','delete-owned-sandbox'])
                self.assertTrue(flags['damon_original_setting_restored']);self.assertTrue(flags['sandbox_removed'])
                self.assertEqual(scope['success'],not stop_failure)


if __name__=='__main__':unittest.main()
