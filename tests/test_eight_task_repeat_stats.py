"""Only descriptive arithmetic, no service, cgroup or residency operations."""
import sys
from pathlib import Path
import unittest
import ast
import json
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report_eight_task_repeats import stats


class RepeatStatistics(unittest.TestCase):
    def test_sample_standard_deviation_and_median(self):
        self.assertEqual(stats([1, 2, 3]), dict(n=3, mean=2, median=2, stdev=1, minimum=1, maximum=3))

    def test_single_run_does_not_invent_zero_uncertainty(self):
        self.assertIsNone(stats([2])['stdev'])

    def test_slow_run_is_retained(self):
        value = stats([1, 2, 30])
        self.assertEqual(value['n'], 3)
        self.assertEqual(value['mean'], 11)
        self.assertEqual(value['median'], 2)
        self.assertEqual(value['maximum'], 30)


class ExplicitNetworkPools(unittest.TestCase):
    def test_explicit_concurrency_keeps_global_change_and_checks_source_subset(self):
        from validate_isolated_agentenv import source_vm_identities_unchanged
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root/'scripts/validate_isolated_agentenv.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and isinstance(n.test, ast.BoolOp)
                    and any(isinstance(c,ast.Call) and isinstance(c.func,ast.Name) and c.func.id=='source_vm_identities_unchanged' for c in ast.walk(n)))
        for enabled, owned_changed in ((False,False),(True,False),(True,True)):
            ns = dict(online_agent=False, online_task=False, rootfs_short=False, private_control=None,
                      full_rootfs_mode=False, success=True, cleanup={'original_firecracker_identities_unchanged':False},
                      receipt={'original_service_cgroup':'0::/source'},
                      before=[dict(pid=1,cgroup='0::/foreign')],
                      after=[dict(pid=2,cgroup='0::/source' if owned_changed else '0::/foreign')],
                      source_vm_identities_unchanged=source_vm_identities_unchanged,
                      os=SimpleNamespace(environ={'ASB_ISOLATED_UNUSED_NETWORK_POOLS':'on'} if enabled else {}))
            exec(compile(ast.Module(body=[node],type_ignores=[]), '<actual source-subset check>', 'exec'), ns)
            self.assertEqual(ns['success'],enabled and not owned_changed)
            self.assertFalse(ns['cleanup']['original_firecracker_identities_unchanged'])

    def test_relay_uses_the_native_selected_pool_not_foreign_default(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root/'scripts/validate_isolated_agentenv.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and "getattr(args, 'external_workload_relay', False) and receipt.get('rootfs_network_pools')" == ast.unparse(n.test))
        for pools in (None, ['10.213.0.0/16','10.214.0.0/16']):
            ns = dict(args=SimpleNamespace(external_workload_relay=True),
                      receipt={'rootfs_network_pools':pools} if pools else {}, env={})
            exec(compile(ast.Module(body=[node],type_ignores=[]), '<native relay pool>', 'exec'), ns)
            self.assertEqual(ns['env'], {'ASB_EXTERNAL_GUEST_RELAY_POOL':pools[0]} if pools else {})

    def test_native_reuses_selector_and_old_default_still_rejects_conflict(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root/'scripts/validate_isolated_agentenv.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                    and ast.unparse(n.test).startswith('rootfs_short or private_control or full_rootfs_mode or density'))
        routes = json.dumps([dict(dst='10.211.0.1'), dict(dst='10.212.0.2/31')])
        for enabled in (False, True):
            ns = dict(rootfs_short=False, private_control=None, full_rootfs_mode=False, density=None,
                      routes=routes, json=json, receipt={}, overrides=[], args=SimpleNamespace(external_workload_relay=False),
                      os=SimpleNamespace(environ={'ASB_ISOLATED_UNUSED_NETWORK_POOLS':'on'} if enabled else {}))
            code = compile(ast.Module(body=[node], type_ignores=[]), '<actual private-pool branch>', 'exec')
            if enabled:
                exec(code, ns)
                self.assertEqual(ns['receipt']['rootfs_network_pools'], ['10.213.0.0/16','10.214.0.0/16'])
            else:
                with self.assertRaisesRegex(AssertionError, 'already in use'): exec(code, ns)

    def test_explicit_flag_crosses_both_systemd_entries(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ('run_host_file_workflow.py','run_host_external_workload.py'):
            tree = ast.parse((root/'scripts'/filename).read_text())
            node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                        and 'ASB_ISOLATED_UNUSED_NETWORK_POOLS' in ast.unparse(n.test))
            for enabled in (False, True):
                ns = dict(command=[], os=SimpleNamespace(environ={'ASB_ISOLATED_UNUSED_NETWORK_POOLS':'on'} if enabled else {}))
                exec(compile(ast.Module(body=[node],type_ignores=[]), '<actual systemd flag>', 'exec'), ns)
                self.assertEqual(ns['command'], ['--setenv=ASB_ISOLATED_UNUSED_NETWORK_POOLS=on'] if enabled else [])


if __name__ == '__main__':
    unittest.main()
