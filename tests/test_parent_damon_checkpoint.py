"""Offline checkpoint ordering and saved-state restoration; no real proc writes."""
import ast
import asyncio
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch,mock_open

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import guest_cache_capacity as guest
import run_prettier6604_grpo as runner
import validate_isolated_agentenv as validator


class ParentDamonTests(unittest.TestCase):
    def test_prepare_saves_original_and_waits_before_recording_cache(self):
        for original in ('Y','N'):
            with self.subTest(original=original),patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',damon=dict(enabled=original))), \
                 patch.object(guest,'counters',side_effect=[dict(cache_bytes=100),dict(cache_bytes=80)]), \
                 patch.object(guest,'set_damon',return_value=dict(enabled='N',kdamond_pid=-1)) as stop,patch.object(Path,'write_text') as write:
                value=guest.checkpoint_damon(Path('/workspace/asb-damon-before-checkpoint.json'),'prepare')
                stop.assert_called_once_with('N');self.assertEqual(value['original_enabled'],original)
                self.assertEqual(value['before']['cache_bytes'],100);self.assertEqual(value['after']['cache_bytes'],80)
                self.assertEqual(json.loads(write.call_args.args[0]),value)

    def test_prepare_failure_restores_original_state(self):
        with patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',damon=dict(enabled='Y'))), \
             patch.object(guest,'counters',side_effect=[{},RuntimeError('read failed')]),patch.object(guest,'set_damon') as set_state:
            with self.assertRaisesRegex(RuntimeError,'read failed'):guest.checkpoint_damon(Path('/workspace/asb-damon-before-checkpoint.json'),'prepare')
            self.assertEqual([c.args for c in set_state.call_args_list],[('N',),('Y',)])

    def test_observe_rejects_restarted_worker(self):
        for enabled,pid in [('N','-1'),('Y','71'),('N','71')]:
            with self.subTest(enabled=enabled,pid=pid),patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',damon=dict(enabled=enabled,kdamond_pid=pid))), \
                 patch.object(Path,'read_text',return_value='{"status":"PASS","original_enabled":"Y"}'),patch.object(guest,'counters',return_value=dict(cache_bytes=80)):
                if enabled=='N' and pid=='-1':self.assertEqual(guest.checkpoint_damon(Path('/workspace/asb-damon-before-checkpoint.json'),'observe')['original_enabled'],'Y')
                else:
                    with self.assertRaises(AssertionError):guest.checkpoint_damon(Path('/workspace/asb-damon-before-checkpoint.json'),'observe')

    def test_daemon_restores_parent_original_on_success_and_failure(self):
        for fail in (False,True):
            caps=dict(kernel='6.1.175-asb-cache-v3-reclaim',clean_reclaim_interface=True,damon=dict(enabled='N',kdamond_pid='-1'))
            with self.subTest(fail=fail),patch.object(guest,'capabilities',return_value=caps),patch.object(Path,'mkdir'), \
                 patch.object(Path,'open',mock_open()),patch.object(Path,'read_text',return_value=''),patch.object(Path,'iterdir',return_value=[]), \
                 patch.object(Path,'exists',side_effect=[False,True]),patch.object(Path,'touch'),patch.object(Path,'write_text') as write, \
                 patch.object(guest.time,'sleep'),patch.object(guest,'counters',return_value=dict(cache_bytes=1)), \
                 patch.object(guest,'reclaim',side_effect=RuntimeError('cycle failed') if fail else None,return_value=dict(requested_pages=0)), \
                 patch.object(guest,'set_damon',side_effect=[dict(enabled='N',kdamond_pid=-1),dict(enabled='Y',kdamond_pid=9)]) as state:
                self.assertEqual(guest.serve(Path('/workspace/asb-task-capacity'),64,'Y'),1 if fail else 0)
                self.assertEqual([c.args for c in state.call_args_list],[('N',),('Y',)])
                value=json.loads(write.call_args.args[0]);self.assertEqual(value['parent_original_damon_enabled'],'Y')
                self.assertEqual(value['damon_restored']['enabled'],'Y')

    def test_new_mode_rejects_unstopped_inherited_state_before_writes(self):
        with patch.object(guest,'capabilities',return_value=dict(kernel='6.1.175-asb-cache-v3-reclaim',clean_reclaim_interface=True,damon=dict(enabled='Y',kdamond_pid='71'))), \
             patch.object(guest,'set_damon') as set_state:
            with self.assertRaises(AssertionError):guest.serve(Path('/workspace/asb-task-capacity'),64,'Y')
            set_state.assert_not_called()

    def test_validator_new_option_does_not_enable_other_workflows(self):
        with patch.object(validator.os,'geteuid',return_value=0),patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'early DAMON stop requires'):
                validator.main(SimpleNamespace(prettier6604_damon_before_checkpoint=True))
            allocate.assert_not_called()


class RestoredOrderTests(unittest.IsolatedAsyncioTestCase):
    async def test_observe_precedes_checks_and_failed_observe_keeps_guest_registered(self):
        tree=ast.parse(Path(runner.__file__).read_text())
        cls=next(n for n in ast.walk(tree) if isinstance(n,ast.ClassDef) and n.name=='SampledPaths')
        method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='start')
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as name:
                events=[];child='11111111-1111-1111-1111-111111111111';parent='22222222-2222-2222-2222-222222222222'
                def observe(*args):
                    events.append('observe')
                    if fail:raise RuntimeError('inherited worker active')
                def command(label,*args):
                    events.append(label)
                    return '{}' if label.startswith('boundary') else 'pid=1\nstart_ticks=2\nboot_id=b\ncount=4'
                instance=SimpleNamespace(live={},record=Mock(),checkpoints={'template':dict(parent=parent,step=11,boundary={},process=dict(pid='1',start_ticks='2',boot_id='b',count='3'))})
                namespace=dict(asyncio=asyncio,time=time,re=re,json=json,schedule='bpo',cli=Mock(return_value=child),
                    damon_before_checkpoint=True,checkpoint_damon=observe,runtime=SimpleNamespace(start=lambda *args:events.append('runtime')),
                    guest=command,verify_boundary=Mock(return_value=dict(status='PASS')),verify_process=runner.verify_process,
                    out=Path(name),executed=dict(early_1=['inspect']))
                exec(compile(ast.Module(body=[method],type_ignores=[]),'<actual restored start>','exec'),namespace)
                if fail:
                    with self.assertRaisesRegex(RuntimeError,'inherited worker active'):await namespace['start'](instance,'early_1',template='template',parent=parent)
                    self.assertEqual(events,['observe'])
                else:
                    self.assertEqual(await namespace['start'](instance,'early_1',template='template',parent=parent),child)
                    self.assertEqual(events,['observe','runtime','boundary-early_1','process-early_1'])
                self.assertIn(child,instance.live)


if __name__=='__main__':unittest.main()
