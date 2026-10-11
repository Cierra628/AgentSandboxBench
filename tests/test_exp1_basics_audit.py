import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_exp1_metrics import KEY_OUTPUT, check_cleanup, check_final_result, measured, observed_samples


class Exp1BasicsAuditTests(unittest.TestCase):
    def trenvx(self, root):
        (root/'replay/stdout').mkdir(parents=True)
        (root/'replay/stdout/024.log').write_text(KEY_OUTPUT)
        (root/'replay/patch.diff').write_bytes(b'known patch\n')
        digest = hashlib.sha256(b'known patch\n').hexdigest()
        (root/'correctness.json').write_text(json.dumps(dict(action_order='PASS',
            input_hashes='PASS', expected_failures={'008':127,'016':127},
            action_024_oracle='PASS', independent_svg_properties='PASS', patch_sha256=digest)))
        before={'svg':{'javascript_formatted':False,'idempotent':True}}
        after={name:{'javascript_formatted':True,'idempotent':True}
               for name in ('svg','nested_svg','html_control')}
        (root/'independent-before.json').write_text(json.dumps(dict(exit_code=1,stdout=json.dumps(before))))
        (root/'independent-after.json').write_text(json.dumps(dict(exit_code=0,stdout=json.dumps(after))))
        result = dict(checks=dict(exp1_replay_correctness='PASS',frozen_input_hashes='PASS',
            sdk_cleanup='PASS',service_shutdown='PASS'),
            host_cleanup=dict(cgroup_removed=True,loop_query_exit_code=0,owned_loop_backings=[]))
        return result

    def test_final_output_and_patch_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); result=self.trenvx(root)
            self.assertEqual(check_final_result(root,root,result,'trenvx','full')['status'],'PASS')
            (root/'replay/stdout/024.log').write_text('wrong output')
            with self.assertRaises(AssertionError):
                check_final_result(root,root,result,'trenvx','full')
            (root/'replay/stdout/024.log').write_text(KEY_OUTPUT)
            (root/'replay/patch.diff').write_bytes(b'wrong patch')
            with self.assertRaises(AssertionError):
                check_final_result(root,root,result,'trenvx','full')
            (root/'replay/patch.diff').write_bytes(b'known patch\n')
            (root/'independent-after.json').write_text(json.dumps(dict(exit_code=0,
                stdout=json.dumps({'svg':{'javascript_formatted':False,'idempotent':True}}))))
            with self.assertRaises(AssertionError):
                check_final_result(root,root,result,'trenvx','full')

    def test_removed_archive_stays_distinct_from_retained_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); result=self.trenvx(root)
            digest='a'*64
            (root/'archive-manifest.json').write_text(json.dumps(dict(archive_sha256=digest)))
            (root/'archive-removal.json').write_text(json.dumps(dict(archive_sha256=digest)))
            self.assertEqual(check_cleanup(root,root,result,'trenvx','full')['archived_images'],
                             'REMOVED_AFTER_VERIFICATION')
            result['host_cleanup']['cgroup_removed']=False
            with self.assertRaises(AssertionError):check_cleanup(root,root,result,'trenvx','full')
            result['host_cleanup']['cgroup_removed']=True
            (root/'archive-removal.json').write_text(json.dumps(dict(archive_sha256='b'*64)))
            with self.assertRaises(AssertionError):check_cleanup(root,root,result,'trenvx','full')

    def test_missing_timing_has_no_zero_duration(self):
        self.assertEqual(measured(None,None,'old run')['status'],'NOT_MEASURED')
        self.assertIsNone(measured(None,None,'old run')['duration_ns'])

    def test_success_image_cleanup_requires_non_checkpoint_and_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result=self.trenvx(root)
            result['checks']['successful_image_cleanup']='PASS'
            (root/'config.json').write_text(json.dumps(dict(discard_success_images=True,scenario='exp1',checkpoint_after=None)))
            receipt=dict(run_id=root.name,mode='successful-exp1-images-discarded',source_removed=True,
                         entries=[dict(kind='file',sha256='a'*64)])
            path=root/'image-cleanup.json';path.write_text(json.dumps(receipt))
            self.assertEqual(check_cleanup(root,root,result,'trenvx','full')['archived_images'],
                             'SUCCESS_IMAGES_REMOVED; RAW_AND_HASHES_RETAINED')
            receipt['source_removed']=False;path.write_text(json.dumps(receipt))
            with self.assertRaises(AssertionError):check_cleanup(root,root,result,'trenvx','full')

    def test_guest_boundary_sample_retains_its_own_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for index,available in enumerate((80,60)):
                row=dict(status='PASS',page_size=4096,timestamp_ns=index,
                         scope='guest boundary',meminfo_bytes=dict(
                             MemTotal=100*1024,MemAvailable=available*1024,
                             Cached=20*1024,AnonPages=5*1024,Slab=3*1024))
                (root/f'guest-memory-{index}.json').write_text(json.dumps(row))
            guest,host=observed_samples(root,'trenvx')
            self.assertEqual(guest['sample_count'],2)
            self.assertEqual(guest['sampling_mode'],'controller_boundary')
            self.assertEqual(guest['observed_peak_used_estimate_kib'],40)
            self.assertEqual(host['sample_count'],0)


if __name__=='__main__':
    unittest.main()
