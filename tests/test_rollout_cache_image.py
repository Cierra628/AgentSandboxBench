"""No live service: exercise the exact creation call using a mock."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_rollout_cache_calibration as runner
from prettier_no_proxy import profile

class ImageTests(unittest.TestCase):
    def test_fixed_local_prettier_and_no_proxy(self):
        with patch.object(runner,'create',return_value='owned') as call:
            self.assertEqual(runner.create_source(Path('/unused'),'http://127.0.0.1:1'),'owned')
            call.assert_called_once_with(Path('/unused'),'http://127.0.0.1:1',
                profile('prettier__prettier-6604')['image'],request_timeout=90,local_no_proxy=True)
            self.assertIn('prettier-6604@sha256:',call.call_args.args[2])

if __name__=='__main__':unittest.main()
