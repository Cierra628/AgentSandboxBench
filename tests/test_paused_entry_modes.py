"""Exercise the real CLI declarations and main's routing without starting a service."""
import argparse
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/validate_isolated_agentenv.py'
TREE = ast.parse(SOURCE.read_text())
FLAGS = {'--paused-host-cache', '--paused-four-sources'}
NAMES = {flag[2:].replace('-', '_') for flag in FLAGS}


def entry(*flags):
    parser = argparse.ArgumentParser()
    declarations = [s for s in ast.walk(TREE) if isinstance(s, ast.Expr)
                    and isinstance(s.value, ast.Call) and s.value.args
                    and isinstance(s.value.args[0], ast.Constant)
                    and s.value.args[0].value in FLAGS]
    main = next(s for s in TREE.body if isinstance(s, ast.FunctionDef) and s.name == 'main')
    # Run just the option reads, guards, cache-session runner choice and receipt assignment.
    nodes = []
    for s in ast.walk(main):
        if isinstance(s, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NAMES for t in s.targets):
            nodes.append(s)
        elif isinstance(s, ast.If) and any(isinstance(n, ast.Name) and n.id in NAMES for n in ast.walk(s.test)):
            nodes.append(s)
        elif isinstance(s, ast.If) and isinstance(s.test, ast.Name) and s.test.id == 'cache_sessions' and any(
                isinstance(n, ast.Constant) and n.value == 'run_paused_host_cache.py' for n in ast.walk(s)):
            nodes.append(s)
        elif isinstance(s, ast.Assign) and any(isinstance(n, ast.Constant) and n.value == 'paused_host_layer_observation'
                                               for n in ast.walk(s.targets[0])):
            nodes.append(s)
    env = dict(parser=parser, cache_sessions=True, restored_lower=True, provider_binary=True,
               child_cache_count=2, unified_cache_rounds=False, receipt={'checks': {}})
    def execute(statements):
        exec(compile(ast.Module(sorted(statements, key=lambda s: s.lineno), []), str(SOURCE), 'exec'), env)
    execute(declarations)
    env['args'] = parser.parse_args(flags)
    execute(nodes)
    return env['runner'], env['receipt']['checks']


class PausedModes(unittest.TestCase):
    def test_host(self):
        self.assertEqual(entry('--paused-host-cache'), ('run_paused_host_cache.py', {'paused_host_layer_observation': 'PASS'}))

    def test_four(self):
        self.assertEqual(entry('--paused-four-sources'), ('run_paused_four_sources.py', {'paused_host_four_source_observation': 'PASS'}))

    def test_both(self):
        with self.assertRaisesRegex(ValueError, 'independent workflows; select only one'):
            entry('--paused-host-cache', '--paused-four-sources')

    def test_neither(self):
        self.assertEqual(entry(), ('run_rollout_cache_calibration.py', {'guest_cache_metadata_without_pause': 'PASS'}))


if __name__ == '__main__':
    unittest.main()
