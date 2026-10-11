import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_initial_acceptance import load_inputs, main, read_only_audit


class DeliveryTests(unittest.TestCase):
    def test_write_is_blocked_before_existing_file_is_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('keep this result')
            before = path.stat()
            with read_only_audit(), self.assertRaises(PermissionError):
                path.write_text('replacement')
            self.assertEqual(path.read_text(), 'keep this result')
            self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_subprocess_is_blocked_during_audit(self):
        with read_only_audit(), self.assertRaises(PermissionError):
            subprocess.run(['/bin/true'], check=True)

    def test_guard_is_reset_after_exception(self):
        with self.assertRaises(RuntimeError):
            with read_only_audit():
                raise RuntimeError('audit failed')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'new-result'
            path.write_text('new output is allowed after audit')
            self.assertTrue(path.exists())

    def test_existing_output_is_refused_before_loading_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / '.artifacts/initial-acceptance-delivery/existing'
            output.mkdir(parents=True)
            path = output / 'result.json'
            path.write_text('original')
            with patch('verify_initial_acceptance.ROOT', root), patch('verify_initial_acceptance.load_inputs') as load:
                self.assertEqual(main(['--output', str(output)]), 2)
                load.assert_not_called()
            self.assertEqual(path.read_text(), 'original')

    def test_output_cannot_be_created_in_an_old_raw_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / '.artifacts/old-experiment/new-output'
            with patch('verify_initial_acceptance.ROOT', root):
                self.assertEqual(main(['--output', str(output)]), 2)
            self.assertFalse(output.parent.exists())

    def test_changed_fixed_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'input.json').write_text('changed input')
            config = root / 'config.json'
            config.write_text(json.dumps(dict(version=1, expected_report='input.json',
                                              sha256={'expected_report': '0' * 64})))
            with patch('verify_initial_acceptance.ROOT', root):
                with self.assertRaisesRegex(AssertionError, 'fixed input changed'):
                    load_inputs(config)


if __name__ == '__main__':
    unittest.main()
