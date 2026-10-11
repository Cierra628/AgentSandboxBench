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
    def cleanup_fixture(self, directory):
        root = Path(directory)/'runs'
        run = root/'owned-run'
        (run/'data').mkdir(parents=True)
        (run/'data/image').write_text('private image')
        (run/'replay').mkdir()
        (run/'replay/steps.tsv').write_text('retain raw results')
        seed = Path(directory)/'fixed-seed'
        seed.write_text('fixed image')
        (run/'config.json').write_text(json.dumps(dict(scenario='exp1', checkpoint_after=None,
            discard_success_images=True, keep_data=False, seed=str(seed), seed_sha256=archive.sha256(seed))))
        (run/'result.json').write_text(json.dumps(dict(run_id=run.name, status='PASS',
            full_trajectory_verified=True, checks={key:'PASS' for key in (
                'private_template_prepare', 'template_build', 'frozen_input_hashes',
                'unfixed_seed_oracle', 'exp1_replay_correctness', 'sdk_cleanup', 'service_shutdown')},
            host_cleanup=dict(cgroup_removed=True, loop_query_exit_code=0, owned_loop_backings=[]))))
        return root, run, seed

    def test_success_image_cleanup_keeps_raw_results_and_hashes(self):
        with tempfile.TemporaryDirectory() as name:
            root, run, seed = self.cleanup_fixture(name)
            digest = archive.sha256(run/'data/image')
            manifest = archive.discard_success_images(run, root=root)
            self.assertTrue(manifest['source_removed'])
            self.assertFalse((run/'data').exists())
            self.assertEqual((run/'replay/steps.tsv').read_text(), 'retain raw results')
            self.assertEqual(manifest['entries'][0]['sha256'], digest)
            self.assertEqual(seed.read_text(), 'fixed image')
            self.assertFalse((run/'data.tar.gz').exists())

    def test_failed_replay_or_cleanup_cannot_discard_images(self):
        for field in ('status', 'full_trajectory_verified', 'service_shutdown', 'cgroup_removed'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as name:
                root, run, _ = self.cleanup_fixture(name)
                result = json.loads((run/'result.json').read_text())
                if field == 'service_shutdown': result['checks'][field] = 'FAIL'
                elif field == 'cgroup_removed': result['host_cleanup'][field] = False
                else: result[field] = 'FAIL' if field == 'status' else False
                (run/'result.json').write_text(json.dumps(result))
                with self.assertRaises(ValueError): archive.discard_success_images(run, root=root)
                self.assertTrue((run/'data/image').exists())

    def test_checkpoint_control_cleanup_requires_restored_state_and_progress(self):
        for missing in (None,'modified_source_preserved','prefix_state_preserved'):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as name:
                root,run,_=self.cleanup_fixture(name)
                cfg=json.loads((run/'config.json').read_text())
                cfg.update(checkpoint_after='022',discard_success_images=False,
                           discard_verified_checkpoint_images=True,monitoring_control='on')
                (run/'config.json').write_text(json.dumps(cfg))
                result=json.loads((run/'result.json').read_text())
                for key in ('checkpoint_after_022','file_restore_interface','modified_source_preserved',
                            'prefix_state_preserved','patch_matches_full_replay','official_task_tests'):
                    result['checks'][key]='PASS'
                if missing:result['checks'][missing]='FAIL'
                (run/'result.json').write_text(json.dumps(result))
                (run/'checkpoint.json').write_text(json.dumps(dict(next_step='023')))
                (run/'official-task-result.json').write_text(json.dumps(dict(status='PASS',parser_results=dict(num_tests_passed=4))))
                if missing:
                    with self.assertRaises(ValueError):archive.discard_success_images(run,root=root,verified_checkpoint=True)
                    self.assertTrue((run/'data/image').exists())
                else:
                    manifest=archive.discard_success_images(run,root=root,verified_checkpoint=True)
                    self.assertEqual(manifest['mode'],'verified-checkpoint-control-images-discarded')
                    self.assertFalse((run/'data').exists())

    def test_cleanup_refuses_checkpoint_unrequested_or_changed_seed(self):
        for change in ('checkpoint_after', 'discard_success_images', 'seed'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                root, run, seed = self.cleanup_fixture(name)
                config = json.loads((run/'config.json').read_text())
                if change == 'seed': seed.write_text('changed')
                else: config[change] = '013' if change == 'checkpoint_after' else False
                (run/'config.json').write_text(json.dumps(config))
                with self.assertRaises(ValueError): archive.discard_success_images(run, root=root)
                self.assertTrue((run/'data/image').exists())

    def test_cleanup_refuses_unexpected_symlinks_and_live_loops(self):
        for change in ('symlink', 'loop'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as name:
                root, run, seed = self.cleanup_fixture(name)
                if change == 'symlink': (run/'data/external').symlink_to(seed)
                loops = subprocess.CompletedProcess([], 0, stdout=str(run/'data/image') if change == 'loop' else '')
                with patch.object(archive.subprocess, 'run', return_value=loops):
                    with self.assertRaises(ValueError): archive.discard_success_images(run, root=root)
                self.assertTrue((run/'data/image').exists())
                self.assertEqual(seed.read_text(), 'fixed image')

    def test_removal_failure_keeps_incomplete_record(self):
        with tempfile.TemporaryDirectory() as name:
            root, run, _ = self.cleanup_fixture(name)
            with patch.object(archive.shutil, 'rmtree', side_effect=OSError('injected cleanup failure')):
                with self.assertRaises(OSError): archive.discard_success_images(run, root=root)
            self.assertFalse(json.loads((run/'image-cleanup.json').read_text())['source_removed'])
            self.assertTrue((run/'data/image').exists())

    def test_outside_result_root_cannot_be_cleaned(self):
        with tempfile.TemporaryDirectory() as name:
            _, run, _ = self.cleanup_fixture(name)
            with self.assertRaisesRegex(ValueError,'immediate child'):
                archive.discard_success_images(run,root=Path(name))
            self.assertTrue((run/'data/image').exists())

    def test_mounted_private_data_cannot_be_cleaned(self):
        with tempfile.TemporaryDirectory() as name:
            root,run,_ = self.cleanup_fixture(name)
            original = Path.read_text
            def read(path,*args,**kwargs):
                if path==Path('/proc/self/mountinfo'):
                    return f'1 2 0:1 / {run}/data rw - ext4 /dev/loop-test rw\n'
                return original(path,*args,**kwargs)
            with patch.object(Path,'read_text',read):
                with self.assertRaisesRegex(ValueError,'mounted filesystem'):
                    archive.discard_success_images(run,root=root)
            self.assertTrue((run/'data/image').exists())

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
