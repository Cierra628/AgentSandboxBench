"""Small files and owned subprocesses only; never launch a VM or alter devices."""
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from storage_lifecycle import Lifecycle
from storage_observer import directory_sample, filesystem_sample, writer_deltas, StorageObserver
from storage_supervisor import supervised_run, recover_success_images, own_state
import test_trenvx_data_archive as archive_tests

CHILD = '''import json, os, sys, time
from pathlib import Path
from storage_lifecycle import Lifecycle
from archive_trenvx_run_data import sha256
base = Path(sys.argv[1]); mode = sys.argv[2]
raw = base/'runs'/('fixture-'+str(os.getpid())); raw.mkdir(parents=True)
(raw/'data').mkdir(); (raw/'data/image').write_bytes(b'private fixture image')
seed = base/'seed'; seed.write_bytes(b'fixed fixture seed')
checks = {key:'PASS' for key in ('private_template_prepare','template_build','frozen_input_hashes',
    'unfixed_seed_oracle','exp1_replay_correctness','sdk_cleanup','service_shutdown')}
(raw/'config.json').write_text(json.dumps(dict(scenario='exp1', checkpoint_after=None,
    discard_success_images=True, keep_data=False, cgroup='asb-trenvx-'+raw.name,
    seed=str(seed), seed_sha256=sha256(seed))))
(raw/'result.json').write_text(json.dumps(dict(run_id=raw.name,status='PASS',
    full_trajectory_verified=True,checks=checks,
    host_cleanup=dict(cgroup_removed=True,loop_query_exit_code=0,owned_loop_backings=[]))))
life = Lifecycle(raw)
with life.signals():
    if mode in ('cleanup','hang','abrupt'):
        life.update('image-discard', writers_stopped=True)
    else:
        life.update('running')
    (base/'ready').write_text(str(raw))
    if mode == 'abrupt': os._exit(7)
    if mode == 'cleanup':
        time.sleep(.2)
        from archive_trenvx_run_data import discard_success_images
        discard_success_images(raw, root=raw.parent)
        life.update('complete', writers_stopped=True)
    elif mode == 'hang':
        time.sleep(30)
    else:
        try:
            while True:
                (raw/'data/image').write_bytes(b'owned writer')
                time.sleep(.02)
        finally:
            life.update('resource-cleanup', writers_stopped=True)
            result = json.loads((raw/'result.json').read_text()); result['status'] = 'FAIL'
            (raw/'result.json').write_text(json.dumps(result))
            (base/'cleaned').write_text('owned cleanup completed')
            time.sleep(.1)
            life.update('retained', writers_stopped=True)
'''


class StorageSafetyTests(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ.get('ASB_STORAGE_TEST_ROOT', ROOT/'.artifacts'))
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='storage-fixture-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.out = self.base/'driver'; self.out.mkdir()
        self.child = self.base/'child.py'; self.child.write_text(CHILD)
        self.env = dict(os.environ, PYTHONPATH=str(ROOT/'scripts'), PYTHONDONTWRITEBYTECODE='1')

    def supervised(self, mode, **kw):
        return supervised_run([sys.executable, '-B', self.child, self.base, mode],
            out=self.out, label='fixture', env=self.env, filesystem=ROOT,
            raw_root=self.base/'runs', reserve=10, interval=.02, cleanup_timeout=2,
            free_bytes=lambda: 5 if (self.base/'ready').exists() else 100, **kw)

    def record(self):
        return json.loads((self.out/'fixture-invocation.json').read_text())

    def test_stop_writer_finishes_cleanup_and_leaves_unrelated_process(self):
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        try:
            with self.assertRaises(RuntimeError): self.supervised('running')
            self.assertIsNone(other.poll())
            self.assertTrue((self.base/'cleaned').is_file())
            self.assertEqual(self.record()['status'], 'FAIL')
            self.assertTrue(self.record()['term_sent'])
            self.assertFalse(self.record().get('forced_kill', False))
            self.assertEqual(self.record()['recovery']['status'], 'RETAINED')
            raw = Path((self.base/'ready').read_text())
            self.assertTrue((raw/'data/image').exists())
        finally:
            other.terminate(); other.wait(timeout=5)

    def test_low_space_does_not_interrupt_successful_disposal(self):
        with self.assertRaises(RuntimeError): self.supervised('cleanup')
        row = self.record()
        self.assertTrue(row['cleanup_allowed_to_finish'])
        self.assertFalse(row.get('term_sent', False))
        self.assertEqual(row['exit_code'], 0)
        self.assertEqual(row['status'], 'FAIL')  # Disk stop cannot become a successful workflow.
        self.assertEqual(row['recovery']['status'], 'PASS')
        raw = Path((self.base/'ready').read_text())
        self.assertFalse((raw/'data').exists())
        self.assertTrue(json.loads((raw/'image-cleanup.json').read_text())['source_removed'])

    def test_abrupt_exit_has_independent_verified_recovery(self):
        with self.assertRaises(RuntimeError): self.supervised('abrupt')
        self.assertEqual(self.record()['exit_code'], 7)
        self.assertEqual(self.record()['recovery']['status'], 'PASS')
        raw = Path((self.base/'ready').read_text())
        self.assertFalse((raw/'data').exists())
        self.assertTrue((self.base/'seed').exists())
        self.assertEqual(json.loads((raw/'result.json').read_text())['status'], 'PASS')
        self.assertEqual(self.record()['status'], 'FAIL')

    def test_cleanup_hang_is_bounded(self):
        with self.assertRaises(RuntimeError):
            supervised_run([sys.executable, '-B', self.child, self.base, 'hang'],
                out=self.out, label='fixture', env=self.env, filesystem=ROOT,
                raw_root=self.base/'runs', reserve=10, interval=.02, cleanup_timeout=.3,
                free_bytes=lambda:5 if (self.base/'ready').exists() else 100)
        self.assertTrue(self.record()['forced_kill'])
        self.assertLess(self.record()['elapsed_seconds'], 3)

    def test_stop_receipt_write_failure_still_stops_writer(self):
        original = Path.open
        def opened(path, *args, **kwargs):
            if str(path).endswith('.stop.json'): raise OSError('fixture no space')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', opened), self.assertRaises(RuntimeError): self.supervised('running')
        self.assertTrue(self.record()['term_sent'])
        self.assertIn('stop_record_error', self.record())
        self.assertTrue((self.base/'cleaned').exists())

    def test_failed_checkpoint_unrequested_partial_runs_are_retained(self):
        fixture = archive_tests.DataArchiveContract()
        for variant in ('failed', 'checkpoint', 'unrequested', 'partial', 'wrong_group'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=self.base) as name:
                root, raw, seed = fixture.cleanup_fixture(name)
                cfg = json.loads((raw/'config.json').read_text()); cfg['cgroup'] = 'asb-trenvx-'+raw.name
                result = json.loads((raw/'result.json').read_text())
                if variant == 'failed': result['status'] = 'FAIL'
                elif variant == 'checkpoint': cfg['checkpoint_after'] = '022'
                elif variant == 'unrequested': cfg['discard_success_images'] = False
                elif variant == 'partial': (raw/'image-cleanup.json').write_text('{}')
                else: cfg['cgroup'] = 'different-owner'
                (raw/'config.json').write_text(json.dumps(cfg)); (raw/'result.json').write_text(json.dumps(result))
                self.assertEqual(recover_success_images(raw, root)['status'], 'RETAINED')
                self.assertTrue((raw/'data/image').exists()); self.assertTrue(seed.exists())

    def test_wrong_lifecycle_token_or_owner_is_not_trusted(self):
        state = self.base/'state.json'; raw = self.base/'runs/fixture-123'
        for pid, token in ((124,'right'), (123,'wrong')):
            state.write_text(json.dumps(dict(owner_pid=pid, token=token, raw_dir=str(raw))))
            self.assertEqual(own_state(state, 123, 'right', raw.parent), {})

    def test_storage_diagnostics_are_opt_in_and_separate_from_memory_events(self):
        from trenvx_diagnostic_metrics import DiagnosticMetrics
        metrics = DiagnosticMetrics(self.out, False)
        with patch.dict(os.environ, {'ASB_STORAGE_DIAGNOSTICS':'0'}):
            with metrics.phase('without-storage'): pass
        self.assertFalse((self.out/'storage-events.jsonl').exists())
        with patch.dict(os.environ, {'ASB_STORAGE_DIAGNOSTICS':'1'}):
            with metrics.phase('fixture-copy'): pass
        rows = list(map(json.loads, (self.out/'storage-events.jsonl').read_text().splitlines()))
        self.assertEqual([row['event'] for row in rows], ['begin', 'end'])
        self.assertEqual(rows[-1]['status'], 'PASS')
        self.assertFalse((self.out/'diagnostic-events.jsonl').exists())

    def test_supervisor_attaches_async_diagnostics_to_stop_record(self):
        with self.assertRaises(RuntimeError): self.supervised('cleanup', diagnostics=True)
        row = self.record()
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(row['storage_observer']['status'], 'PASS')
        self.assertGreaterEqual(row['storage_observer']['samples'], 2)
        raw = Path((self.base/'ready').read_text())
        self.assertTrue((raw/'storage-events.jsonl').exists())

    def test_signal_ignored_only_in_safe_cleanup_phases(self):
        raw = self.base/'run'; raw.mkdir()
        with patch.dict(os.environ, {'ASB_STORAGE_TOKEN':'fixture'}, clear=True): life = Lifecycle(raw)
        for phase, stopped, raises in (('running', False, True), ('image-archive', True, True),
                                     ('resource-cleanup', True, False), ('image-discard', True, False)):
            life.update(phase, writers_stopped=stopped)
            if raises:
                with self.assertRaises(RuntimeError): life.on_signal(signal.SIGTERM, None)
            else: life.on_signal(signal.SIGTERM, None)

    def test_directory_scan_deduplicates_hardlinks_and_excludes_symlink_targets(self):
        source = self.base/'files'; source.mkdir()
        (source/'one').write_bytes(b'a'*8192); os.link(source/'one', source/'two')
        outside = self.base/'outside'; outside.mkdir(); (outside/'big').write_bytes(b'x'*16384)
        (source/'link').symlink_to(outside, target_is_directory=True)
        row = directory_sample([source, source])
        self.assertEqual(row['logical_bytes'], 8192)
        self.assertEqual(row['inode_count'], 1)
        self.assertEqual(row['allocated_bytes'], (source/'one').stat().st_blocks*512)

    def test_reserved_space_is_separate_from_available(self):
        from types import SimpleNamespace
        info = SimpleNamespace(f_blocks=100, f_frsize=4096, f_bfree=40, f_bavail=30)
        with patch('storage_observer.os.statvfs', return_value=info): row = filesystem_sample(self.base)
        self.assertEqual(row['free_bytes'], 40*4096)
        self.assertEqual(row['available_bytes'], 30*4096)
        self.assertEqual(row['reserved_or_unavailable_bytes'], 10*4096)

    def test_directory_scan_excludes_mounted_filesystem_contents(self):
        from types import SimpleNamespace
        source = self.base/'files'; source.mkdir()
        (source/'one').write_bytes(b'a'*8192)
        mounted = source/'mounted'; mounted.mkdir(); (mounted/'other').write_bytes(b'x'*16384)
        original = Path.lstat
        def info(path, *args, **kwargs):
            if path == mounted: return SimpleNamespace(st_dev=-1, st_mode=original(path).st_mode)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', info): row = directory_sample([source])
        self.assertEqual(row['logical_bytes'], 8192)
        self.assertEqual(row['inode_count'], 1)

    def test_io_deltas_do_not_reuse_pid_identity_or_assign_disk_allocation(self):
        base = dict(pid=1, parent_pid=0, start_ticks=10, comm='fixture', uid=0,
                    write_bytes=100, cancelled_write_bytes=0)
        row = writer_deltas({1:base}, {1:dict(base, write_bytes=200, cancelled_write_bytes=50)}, 1)[0]
        self.assertEqual(row['write_delta_bytes'], 100); self.assertTrue(row['owned_tree'])
        self.assertEqual(row['cancelled_write_delta_bytes'], 50)
        reused = writer_deltas({1:base}, {1:dict(base, start_ticks=11, write_bytes=200)}, 1)[0]
        self.assertIsNone(reused['write_delta_bytes'])

    def test_cached_directory_read_has_timestamp_and_sampler_failure_is_fail(self):
        sampler = StorageObserver(self.base/'observe', ROOT, roots=[self.out], directory_interval=100)
        stream = io.StringIO()
        sampler.sample(stream); sampler.sample(stream)
        a, b = map(json.loads, stream.getvalue().splitlines())
        self.assertTrue(a['directory_scanned_this_sample']); self.assertFalse(b['directory_scanned_this_sample'])
        self.assertEqual(a['directory_scan_monotonic_ns'], b['directory_scan_monotonic_ns'])
        with patch('storage_observer.filesystem_sample', side_effect=OSError('injected read error')):
            sampler.start(); sampler.thread.join(timeout=2); row = sampler.stop()
        self.assertEqual(row['status'], 'FAIL')

    def test_backend_stop_retains_failed_data_without_starting_archive(self):
        self.backend_fixture(stopped=True, successful=False)

    def test_backend_success_disposal_finishes_after_stop_request(self):
        self.backend_fixture(stopped=True, successful=True)

    def backend_fixture(self, *, stopped, successful):
        import smoke_trenvx_service as backend
        import archive_trenvx_run_data as archive
        from types import SimpleNamespace
        class Args(SimpleNamespace):
            def __getattr__(self, name): return None
        args = Args(scenario='exp1', archive_compressor='gzip', discard_success_images=True)
        raw = self.base/'backend'; raw.mkdir(); binaries = self.base/'bin'; binaries.mkdir()
        for name in ('orchestrator','template-manager','bind_mount'): (binaries/name).write_text('fixture')
        seed = self.base/'seed'; seed.write_text('fixed seed')
        with patch.dict(os.environ, {'ASB_STORAGE_TOKEN':'fixture'}, clear=True): life = Lifecycle(raw)
        fixture = archive_tests.DataArchiveContract()
        def command(out, label, argv, timeout=90):
            if label != 'namespace': return
            _, sample, _ = fixture.cleanup_fixture(self.base/'task')
            import shutil
            shutil.copytree(sample/'data', raw/'data')
            result = json.loads((sample/'result.json').read_text()); result['run_id'] = raw.name
            result['status'] = 'PASS' if successful else 'FAIL'
            (raw/'inner-result.json').write_text(json.dumps(result))
            if stopped: life.stop_path.write_text(json.dumps(dict(reason='fixture reserve reached')))
        original_discard = archive.discard_success_images
        def discard(out, **kw):
            self.assertEqual(kw.pop('root',raw.parent),raw.parent)
            return original_discard(out, root=raw.parent, **kw)
        with patch.object(backend, 'SEED', seed), patch.object(backend, 'CH', seed), \
             patch.object(backend, 'KERNEL', seed), patch.object(backend, 'ENVD', seed), \
             patch.object(backend, 'BIN', binaries), patch.object(backend, 'command', side_effect=command), \
             patch.object(backend.shutil, 'disk_usage', return_value=SimpleNamespace(free=100*2**30)), \
             patch.object(backend.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), \
             patch.object(archive, 'archive_run_data') as compress, \
             patch.object(archive, 'discard_success_images', side_effect=discard):
            code = backend._outer_owned(args, raw, raw.name, __import__('time').monotonic_ns(), life)
        compress.assert_not_called()
        row = json.loads((raw/'result.json').read_text())
        if successful:
            self.assertEqual(code, 0); self.assertFalse((raw/'data').exists())
            self.assertEqual(row['data_retention']['status'], 'PASS')
        else:
            self.assertEqual(code, 1); self.assertTrue((raw/'data/image').exists())
            self.assertEqual(row['data_retention']['status'], 'RETAINED')


if __name__ == '__main__': unittest.main()
