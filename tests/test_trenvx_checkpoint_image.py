"""No devices/services: contracts for retained captures and private journal copies."""
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('checkpoint_image', ROOT/'backend/trenvx/checkpoint_dax.py')
image = importlib.util.module_from_spec(spec); spec.loader.exec_module(image)

class PrivateJournal(unittest.TestCase):
    def tools(self, fsck_text='', fsck_code=0, replay_code=0):
        def run(argv, **kw):
            if argv[0] == 'cp':
                Path(argv[-1]).write_bytes(Path(argv[-2]).read_bytes())
                return subprocess.CompletedProcess(argv, 0)
            if argv[0] == 'dumpe2fs':
                # Replay modifies only the private image, clearing its pending marker.
                pending = Path(argv[-1]).read_bytes() == b'pending journal'
                kw['stdout'].write('Filesystem features: ' + ('needs_recovery' if pending else 'extents') + '\n')
                return subprocess.CompletedProcess(argv, 0)
            if 'journal_only' in argv:
                Path(argv[-1]).write_bytes(b'replayed journal')
                return subprocess.CompletedProcess(argv, replay_code)
            kw['stdout'].write(fsck_text)
            return subprocess.CompletedProcess(argv, fsck_code)
        def output(argv, **kw):
            if argv[0] == 'losetup': return ''
            import hashlib
            return hashlib.sha256(Path(argv[-1]).read_bytes()).hexdigest() + ' image\n'
        return patch.object(image.subprocess, 'run', side_effect=run), patch.object(image.subprocess, 'check_output', side_effect=output)

    def check_case(self, text='', code=0, replay=0, failure=False):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'captured.ext4';source.write_bytes(b'pending journal')
            a,b=self.tools(text,code,replay)
            with a,b:
                if failure:
                    with self.assertRaises(RuntimeError):image.prepare_upper_copy(source)
                else:
                    clone,audit=image.prepare_upper_copy(source)
                    self.assertNotEqual(clone,source);self.assertFalse(audit['needs_recovery_after'])
                logs=list(source.parent.glob('journal-copy-*/journal.json'))
                self.assertEqual(len(logs),1);audit=json.loads(logs[0].read_text())
                self.assertEqual(source.read_bytes(),b'pending journal')
                self.assertTrue(audit['source_unchanged']);self.assertTrue(Path(audit['private_copy']).exists())
                return audit

    def test_replay_writes_only_retained_private_copy(self):
        self.assertEqual(self.check_case()['status'],'PASS')
    def test_count_warnings_are_not_fully_clean(self):
        audit=self.check_case('Free blocks count wrong (1, counted=2).\nFix? no\n')
        self.assertEqual(audit['status'],'COUNTER_WARNINGS');self.assertFalse(audit['filesystem_fully_clean'])
    def test_fsck_error_preserves_images_and_failure_record(self):
        self.assertEqual(self.check_case('inode checksum invalid\n',4,failure=True)['status'],'FAIL')
    def test_journal_failure_preserves_images(self):
        self.assertEqual(self.check_case(replay=4,failure=True)['status'],'FAIL')
    def test_other_unrepaired_question_rejected_even_exit_zero(self):
        self.check_case('Clear? no\n',failure=True)

class CaptureRetention(unittest.TestCase):
    def test_real_paused_copy_verification(self):
        spec=importlib.util.spec_from_file_location('capture_io_copy', ROOT/'backend/trenvx/quiesced_checkpoint.py')
        cp=importlib.util.module_from_spec(spec);spec.loader.exec_module(cp)
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'upper.ext4';source.write_bytes(b'paused content')
            target=Path(d)/'captured.ext4'
            result=cp.CaptureIO('http://unused',Path('unused')).copy(source,target)
            self.assertEqual(source.read_bytes(),target.read_bytes())
            self.assertEqual(result['source_sha256_before'],result['source_sha256_after'])
            self.assertEqual(result['source_sha256_before'],result['captured_sha256'])
            self.assertTrue(result['verified_while_paused'])

    def test_capture_and_promotion_failures_keep_exact_copy(self):
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        if not (vendor/'controller.py').exists():
            self.skipTest('requires pinned IncrementalDAX checkout with recorded patches')
        sys.path.insert(0,str(vendor))
        try:
            spec=importlib.util.spec_from_file_location('capture_controller',vendor/'controller.py')
            controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)
            for fail_capture in (True,False):
                with self.subTest(fail_capture=fail_capture),tempfile.TemporaryDirectory() as d:
                    obj=controller.Controller.__new__(controller.Controller)
                    obj.data_root=Path(d)/'data';obj.data_root.mkdir();obj.raw=Path(d)/'raw';obj.raw.mkdir()
                    obj.counter=0;obj.args=type('Args',(),{'timeout':30})();obj.phase=lambda name:None
                    obj.sandbox_id=lambda sandbox:'owned';obj._template_of=lambda parent:'base'
                    sb=type('Sandbox',(),{'get_sbx_url':lambda self:'127.0.0.1'})()
                    async def capture(io,upper,target,runtime,audit):
                        target.write_bytes(b'exact paused capture');audit['complete']=not fail_capture
                        if fail_capture:raise RuntimeError('capture failed')
                    async def promote(*args):raise RuntimeError('promotion failed')
                    obj.promote_snapshot=promote
                    with patch.object(controller,'capture_joined',side_effect=capture):
                        with self.assertRaises(RuntimeError):asyncio.run(obj.capture_template(sb,'022'))
                    retained=list(obj.data_root.glob('checkpoint-captures/*/upper.ext4'))
                    self.assertEqual(len(retained),1);self.assertEqual(retained[0].read_bytes(),b'exact paused capture')
                    receipt=json.loads((obj.raw/'checkpoint-1-capture.json').read_text())
                    self.assertEqual(receipt['status'],'FAIL');self.assertTrue(receipt['captured_image_exists'])
        finally:sys.path.remove(str(vendor))

if __name__=='__main__':unittest.main()
