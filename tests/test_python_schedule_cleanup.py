"""Execute only the saved runner's cleanup block with mocked resources; no live operations."""
import ast
import json
from pathlib import Path
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]


class ScheduleCleanupTests(unittest.TestCase):
    def cleanup(self, *, c4=True, delete_seconds=0, delete_error=None, listed=None, list_error=None, snapshot_error=None,
                runtime_error=None, live_ids=None):
        tree = ast.parse((ROOT/'scripts/run_python_schedule.py').read_text())
        run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='run')
        final = next(n.finalbody for n in run.body if isinstance(n, ast.Try) and n.finalbody)
        branch = next(n for n in final if isinstance(n, ast.If) and ast.unparse(n.test)=='controller is not None')
        controller = SimpleNamespace(live={sid:sid for sid in (live_ids or ['owned-sid'])}, agentenv_templates=['owned-snapshot'])
        runtime = Mock(); snapshot = Mock(side_effect=snapshot_error)
        runtime.stop.side_effect = runtime_error
        def cli(label, *args, **kwargs):
            if label=='delete-guest':
                if delete_error:raise delete_error
                # Model the observed 63.263-second server operation without sleeping.
                if kwargs['timeout'] < delete_seconds:
                    raise subprocess.TimeoutExpired(['aenv', *args], kwargs['timeout'])
                return 'deleted'
            self.assertEqual((label,args), ('list-after', ('list','--output','json')))
            if list_error:raise list_error
            return json.dumps([] if listed is None else listed)
        command = Mock(side_effect=cli)
        ns = dict(controller=controller, runtime=runtime, cli=command, delete_snapshot=snapshot,
                  c4=c4, matplotlib=False, text_editing=False, result={'status':'PASS'}, cleanup={}, json=json)
        exec(compile(ast.Module(body=[branch], type_ignores=[]), '<mock cleanup only>', 'exec'), ns)
        return ns

    def test_c4_waits_ninety_seconds_for_successful_delete_and_checks_empty_list(self):
        ns = self.cleanup(delete_seconds=63.263349)
        self.assertEqual(ns['result']['status'], 'PASS')
        self.assertEqual(ns['result']['cleanup_guest_delete_timeout_seconds'], 90)
        ns['cli'].assert_any_call('delete-guest','delete','owned-sid',timeout=90)
        ns['runtime'].stop.assert_called_once_with('owned-sid','cleanup')
        self.assertTrue(all(ns['cleanup'].values()))
        self.assertEqual(ns['controller'].live, {})
        ns['delete_snapshot'].assert_called_once_with(ns['cli'],'owned-snapshot')

    def test_other_profiles_keep_sixty_second_delete_default(self):
        ns = self.cleanup(c4=False)
        ns['cli'].assert_any_call('delete-guest','delete','owned-sid',timeout=60)
        self.assertEqual(ns['result']['status'], 'PASS')
        self.assertNotIn('cleanup_guest_delete_timeout_seconds', ns['result'])

    def test_timeout_remains_failure_even_when_later_list_is_empty(self):
        ns = self.cleanup(delete_error=subprocess.TimeoutExpired(['aenv','delete','owned-sid'],90))
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['owned_guests_removed'])
        self.assertTrue(ns['cleanup']['service_sandbox_list_empty'])
        self.assertIn('TimeoutExpired', ns['result']['cleanup_errors'][0])
        self.assertIn('owned-sid', ns['controller'].live)

    def test_delete_error_is_not_hidden_by_empty_list(self):
        ns = self.cleanup(delete_error=RuntimeError('delete: exit 1'))
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['owned_guests_removed'])
        self.assertIn('delete: exit 1', ns['result']['cleanup_errors'][0])

    def test_c4_nonempty_final_list_is_failure(self):
        ns = self.cleanup(listed=[{'sandbox_id':'owned-sid'}])
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['service_sandbox_list_empty'])
        self.assertIn('not empty', ns['result']['cleanup_errors'][0])

    def test_list_query_error_is_failure(self):
        ns = self.cleanup(list_error=RuntimeError('list unavailable'))
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['service_sandbox_list_empty'])

    def test_snapshot_error_is_failure(self):
        ns = self.cleanup(snapshot_error=RuntimeError('snapshot delete failed'))
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['snapshots_removed'])
        self.assertIn('snapshot delete failed', ns['result']['cleanup_errors'][0])

    def test_monitor_failure_still_deletes_owned_guest_and_keeps_original_error(self):
        for c4 in (False, True):
            with self.subTest(c4=c4):
                ns = self.cleanup(c4=c4, runtime_error=OSError(24, 'Too many open files'))
                ns['cli'].assert_any_call('delete-guest','delete','owned-sid',timeout=90 if c4 else 60)
                self.assertEqual(ns['controller'].live, {})
                self.assertTrue(ns['cleanup']['owned_guests_removed'])
                self.assertTrue(ns['cleanup']['service_sandbox_list_empty'])
                self.assertEqual(ns['result']['status'], 'FAIL')
                self.assertEqual(ns['result']['cleanup_errors'], ["OSError(24, 'Too many open files')"])
                ns['delete_snapshot'].assert_called_once_with(ns['cli'],'owned-snapshot')

    def test_monitor_and_delete_failure_are_both_retained(self):
        ns = self.cleanup(runtime_error=RuntimeError('monitor failed'), delete_error=RuntimeError('delete failed'))
        self.assertEqual(ns['result']['cleanup_errors'], ["RuntimeError('monitor failed')", "RuntimeError('delete failed')"])
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertFalse(ns['cleanup']['owned_guests_removed'])
        self.assertIn('owned-sid', ns['controller'].live)

    def test_monitor_failure_does_not_skip_other_owned_guests_or_snapshots(self):
        ns = self.cleanup(runtime_error=[RuntimeError('first monitor failed'), None], live_ids=['owned-sid', 'owned-second'])
        self.assertEqual(ns['runtime'].stop.call_count, 2)
        for sid in ('owned-sid', 'owned-second'):
            ns['cli'].assert_any_call('delete-guest','delete',sid,timeout=90)
        self.assertTrue(ns['cleanup']['owned_guests_removed'])
        self.assertEqual(ns['result']['status'], 'FAIL')
        self.assertEqual(ns['result']['cleanup_errors'], ["RuntimeError('first monitor failed')"])
        ns['delete_snapshot'].assert_called_once_with(ns['cli'],'owned-snapshot')


if __name__=='__main__':unittest.main()
