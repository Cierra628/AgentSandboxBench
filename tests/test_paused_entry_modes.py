"""Exercise the cache-session selector without starting a service."""
from argparse import Namespace
from itertools import combinations
from pathlib import Path
import ast
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_isolated_agentenv import PAUSED_ENTRY_MODES, SEVEN_PAUSED_ENTRY_MODES, paused_entry


def entry(*flags, **overrides):
    settings = dict(cache_sessions=True, restored_lower=True, provider_binary='provider',
                    child_cache_count=2, unified_cache_rounds=False)
    settings.update(overrides)
    return paused_entry(Namespace(**dict.fromkeys(flags, True)), **settings)


class PausedModes(unittest.TestCase):
    def test_single_modes(self):
        for flag, expected in {
            'paused_host_cache': ('run_paused_host_cache.py', 'paused_host_layer_observation'),
            'paused_four_sources': ('run_paused_four_sources.py', 'paused_host_four_source_observation'),
            'immediate_image_layers': ('run_immediate_image_layers.py', 'fixed_image_layer_observation'),
        }.items():
            with self.subTest(flag=flag):
                self.assertEqual(entry(flag), expected)

    def test_mutually_exclusive(self):
        for size in (2, 3):
            for flags in combinations(PAUSED_ENTRY_MODES, size):
                with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'independent workflows; select only one'):
                    entry(*flags)

    def test_neither(self):
        self.assertEqual(entry(cache_sessions=False),
                         ('run_rollout_cache_calibration.py', 'guest_cache_metadata_without_pause'))

    def test_seven_export(self):
        self.assertEqual(entry('seven_paused_export', 'immediate_restores', 'seven_restored',
                               child_cache_count=7, unified_cache_rounds=True),
                         ('run_seven_paused_export.py', 'seven_paused_export'))

    def test_seven_serial(self):
        flags = ('immediate_restores', 'seven_restored')
        self.assertEqual(entry('seven_paused_serial', *flags, child_cache_count=7, unified_cache_rounds=True),
                         ('run_seven_paused_serial.py', 'seven_paused_serial'))
        with self.assertRaisesRegex(ValueError, 'independent workflows; select only one'):
            entry('seven_paused_export', 'seven_paused_serial', *flags)

    def test_seven_without_export(self):
        self.assertEqual(entry('seven_restored', child_cache_count=7, unified_cache_rounds=True),
                         ('run_rollout_cache_calibration.py', 'guest_cache_metadata_without_pause'))

    def test_seven_export_requires_both_flags(self):
        for flags in [(), ('seven_restored',), ('immediate_restores',)]:
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, '^paused export requires parallel seven restores$'):
                entry('seven_paused_export', *flags)

    def test_mixed_classes(self):
        for flag in PAUSED_ENTRY_MODES:
            for seven in SEVEN_PAUSED_ENTRY_MODES:
                with self.subTest(flags=(flag, seven)), self.assertRaisesRegex(ValueError, 'select only one class'):
                    entry(flag, seven, 'immediate_restores', 'seven_restored',
                          child_cache_count=7, unified_cache_rounds=True)

    def test_process_mode(self):
        self.assertEqual(entry('paused_query_processes', 'immediate_restores', 'seven_restored',
                               child_cache_count=7, unified_cache_rounds=True),
                         ('run_paused_process.py', 'paused_query_processes'))
        for flags in [(), ('seven_restored',), ('immediate_restores',)]:
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, '^process benchmark requires seven immediate restores$'):
                entry('paused_query_processes', *flags)

    def test_seven_pairs(self):
        for pair in combinations(SEVEN_PAUSED_ENTRY_MODES, 2):
            with self.subTest(pair=pair), self.assertRaisesRegex(ValueError, 'independent workflows; select only one'):
                entry(*pair, 'immediate_restores', 'seven_restored', child_cache_count=7, unified_cache_rounds=True)

    def test_runner_worker_arguments(self):
        # Exercise the actual starred argument in the host runner call, without launching it.
        source = Path(__file__).resolve().parents[1]/'scripts/validate_isolated_agentenv.py'
        args, = [n.args[1] for n in ast.walk(ast.parse(source.read_text())) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == 'call' and len(n.args) >= 2
                 and isinstance(n.args[1], ast.List) and any(isinstance(x, ast.Name) and x.id == 'runner'
                                                             for x in ast.walk(n.args[1])) and
                 any(isinstance(x, ast.Constant) and x.value == '--workers' for x in ast.walk(n.args[1]))]
        workers, = [x.value for x in args.elts if isinstance(x, ast.Starred)
                    and any(isinstance(k, ast.Constant) and k.value == '--workers' for k in ast.walk(x))]
        code = compile(ast.Expression(workers), str(source), 'eval')
        for count in (None, 1, 7):
            self.assertEqual(eval(code, {'process_count': count}), [] if count is None else ['--workers', str(count)])

    def test_prerequisites(self):
        for flag in PAUSED_ENTRY_MODES:
            for key, bad in dict(cache_sessions=False, restored_lower=False, provider_binary=None,
                                 child_cache_count=7, unified_cache_rounds=True).items():
                with self.subTest(flag=flag, key=key), self.assertRaises(ValueError):
                    entry(flag, **{key: bad})


if __name__ == '__main__':
    unittest.main()
