import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('archive', ROOT/'scripts/archive_trenvx_run_data.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class DataArchiveContract(unittest.TestCase):
    def test_archive_disk_abort_keeps_source(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)/'runs'
            run=root/'test-run'
            (run/'data').mkdir(parents=True)
            (run/'data/image').write_text('preserve source')
            (run/'result.json').write_text(json.dumps(dict(run_id=run.name,status='PASS',
                host_cleanup=dict(cgroup_removed=True,loop_query_exit_code=0,owned_loop_backings=[]))))
            with patch.object(archive,'guarded_run',side_effect=RuntimeError('disk reserve reached')):
                with self.assertRaisesRegex(RuntimeError,'disk reserve'):
                    archive.archive_run_data(run,root=root)
            self.assertEqual((run/'data/image').read_text(),'preserve source')
            self.assertFalse((run/'data.tar.gz.partial').exists())
            self.assertFalse((run/'data.tar.gz').exists())

    def test_allocated_size_counts_hard_link_once(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source = root/'image'
            source.write_bytes(b'x' * 8192)
            first = archive.allocated_bytes(root)
            os.link(source, root/'image-copy')
            self.assertEqual(archive.allocated_bytes(root), first)

    def test_verified_archive_preserves_other_files_and_external_symlink_target(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)/'runs'
            run = root/'test-run'
            data = run/'data'
            data.mkdir(parents=True)
            (run/'replay.tsv').write_text('raw trajectory\n')
            (data/'image').write_bytes(b'vm image' * 1024)
            external = Path(name)/'external'
            external.write_text('do not remove')
            (data/'external').symlink_to(external)
            (run/'result.json').write_text(json.dumps(dict(run_id=run.name, status='PASS',
                checks={'service_shutdown': 'PASS'}, host_cleanup=dict(cgroup_removed=True,
                loop_query_exit_code=0, owned_loop_backings=[]))))

            manifest = archive.archive_run_data(run, root=root)

            self.assertTrue(manifest['source_removed'])
            self.assertFalse(data.exists())
            self.assertEqual(external.read_text(), 'do not remove')
            self.assertEqual((run/'replay.tsv').read_text(), 'raw trajectory\n')
            self.assertEqual(manifest['archive_sha256'], archive.sha256(run/'data.tar.gz'))
            self.assertEqual(json.loads((run/'archive-manifest.json').read_text()), manifest)
            members = subprocess.check_output(['tar', '-tzf', run/'data.tar.gz'], text=True)
            self.assertIn('data/image', members)

    def test_unclean_resources_prevent_removal(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)/'runs'
            run = root/'test-run'
            data = run/'data'
            data.mkdir(parents=True)
            (data/'image').write_text('keep')
            (run/'result.json').write_text(json.dumps(dict(run_id=run.name, status='FAIL',
                host_cleanup=dict(cgroup_removed=False, loop_query_exit_code=0,
                                  owned_loop_backings=[]))))
            with self.assertRaisesRegex(ValueError, 'cleanup is not confirmed'):
                archive.archive_run_data(run, root=root)
            self.assertEqual((data/'image').read_text(), 'keep')

    @unittest.skipUnless(shutil.which('pigz'), 'pigz unavailable')
    def test_pigz_archive_uses_same_verified_format(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)/'runs'
            run = root/'test-run'
            data = run/'data'
            data.mkdir(parents=True)
            (data/'image').write_bytes(b'checkpoint' * 1024)
            (run/'result.json').write_text(json.dumps(dict(run_id=run.name, status='PASS',
                checks={'service_shutdown': 'PASS'}, host_cleanup=dict(cgroup_removed=True,
                loop_query_exit_code=0, owned_loop_backings=[]))))
            manifest = archive.archive_run_data(run, root=root, compressor='pigz-fast')
            self.assertEqual(manifest['compressor'], 'pigz-fast')
            self.assertTrue(manifest['source_removed'])
            subprocess.run(['gzip', '-t', run/'data.tar.gz'], check=True)


if __name__ == '__main__':
    unittest.main()
