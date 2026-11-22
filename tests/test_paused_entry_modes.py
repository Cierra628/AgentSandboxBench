"""Exercise the cache-session selector without starting a service."""
from argparse import Namespace
from itertools import combinations
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from validate_isolated_agentenv import PAUSED_ENTRY_MODES, paused_entry


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

    def test_prerequisites(self):
        for flag in PAUSED_ENTRY_MODES:
            for key, bad in dict(cache_sessions=False, restored_lower=False, provider_binary=None,
                                 child_cache_count=7, unified_cache_rounds=True).items():
                with self.subTest(flag=flag, key=key), self.assertRaises(ValueError):
                    entry(flag, **{key: bad})


if __name__ == '__main__':
    unittest.main()
