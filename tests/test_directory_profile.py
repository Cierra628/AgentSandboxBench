import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DirectoryProfileTest(unittest.TestCase):
    def test_entries_errno_exit_and_both_readdir_interfaces(self):
        self.check_fixture(False)

    def test_open_close_only_preserves_traversal_and_failure_exit(self):
        self.check_fixture(True)

    def test_open_only_preserves_traversal_and_failure_exit(self):
        self.check_fixture(True, True)

    def check_fixture(self, coarse, open_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / 'profile.so'
            fixture = base / 'fixture'
            subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                            *(['-DDIRECTORY_OPEN_ONLY'] if open_only else (['-DDIRECTORY_OPEN_CLOSE_ONLY'] if coarse else [])), str(ROOT / 'scripts/directory_profile.c'), '-ldl', '-pthread', '-o', str(library)], check=True)
            subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'scripts/directory_fixture.c'), '-o', str(fixture)], check=True)
            for binary in (fixture, library):
                versions = subprocess.check_output(['readelf', '--version-info', str(binary)], text=True)
                required = [tuple(map(int, v.split('.'))) for v in re.findall(r'GLIBC_([0-9.]+)', versions)]
                self.assertLessEqual(max(required), (2, 35))
            directories = base / 'directories'
            directories.mkdir()
            for i in range(256):
                d = directories / f'd{i:03d}'
                d.mkdir()
                for j in range(32):
                    (d / f'f{j:03d}').touch()
            env = dict(os.environ)
            env.pop('LD_PRELOAD', None)
            env.pop('DIRECTORY_PROFILE_DIR', None)
            argv = [str(fixture), str(directories), '2', '19']
            plain = subprocess.run(argv, capture_output=True, env=env, timeout=10)
            env.update(LD_PRELOAD=str(library), DIRECTORY_PROFILE_DIR=str(base))
            sampled = subprocess.run(argv, capture_output=True, env=env, timeout=10)
            self.assertEqual((plain.returncode, sampled.returncode), (19, 19))
            self.assertEqual(plain.stdout, sampled.stdout)
            self.assertEqual(sampled.stderr, b'')
            self.assertEqual(json.loads(sampled.stdout)['entries'], 16384)
            files = list(base.glob('directory-*.json'))
            self.assertEqual(len(files), 1)
            calls = json.loads(files[0].read_text())['calls']
            self.assertEqual(calls['opendir']['count'], 513)
            if not open_only:
                self.assertEqual(calls['closedir']['count'], 512)
            if coarse:
                self.assertEqual(set(calls), {'opendir'} if open_only else {'opendir', 'closedir'})
                symbols = subprocess.check_output(['nm', '-D', '--defined-only', str(library)], text=True)
                self.assertNotIn('readdir', symbols)
                if open_only:
                    self.assertNotIn('closedir', symbols)
                self.assertEqual(calls['opendir']['null_with_errno'], 1)
            else:
                self.assertEqual(calls['readdir']['count'], 9472)
                self.assertEqual(calls['readdir64']['count'], 8960)
                self.assertEqual(calls['opendir']['null_with_errno'], 1)
                self.assertEqual(calls['readdir']['null_with_errno'], 512)
                self.assertEqual(calls['readdir']['null_without_errno'], 256)
                self.assertEqual(calls['readdir64']['null_without_errno'], 256)
            for value in calls.values():
                self.assertGreater(value['max_ns'], 0)
                self.assertGreaterEqual(value['total_ns'], value['max_ns'])


if __name__ == '__main__':
    unittest.main()
