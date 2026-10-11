"""Offline checks only: no containers, mounts, devices or existing data changes."""
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from isolated_storage import Boundary,configured_boundary,GIB,capacity_budget,verify_container_boundary,mount_identity,write_mounts,confirmed_window,execution_window
from validate_isolated_agentenv import config_value,immutable_manifest,prettier_capacity


class IsolatedStorageTests(unittest.TestCase):
    def test_small_grpo_volume_cannot_be_used_for_snapshots_or_unconverted_images(self):
        args=SimpleNamespace(prettier6604_grpo_capacity_gib=16)
        self.assertEqual(prettier_capacity(args,True,None,'grpo',False,True),16*GIB)
        for export,role,schedule,checkpoint,seed in [(False,None,'grpo',False,True),(True,'early_1','grpo',False,True),
                (True,None,'bpo',False,True),(True,None,'tvcache',False,True),(True,None,'grpo',True,True),(True,None,'grpo',False,False)]:
            with self.assertRaises(ValueError):prettier_capacity(args,export,role,schedule,checkpoint,seed)
        self.assertEqual(prettier_capacity(SimpleNamespace(),True,None,'bpo',False,True),32*GIB)

    def test_standing_authorization_sets_runtime_without_exclusive_disk_claim(self):
        now=datetime(2026,10,9,6,59,tzinfo=timezone.utc)
        row=execution_window(now=now)
        self.assertEqual(row['status'],'STANDING_USER_AUTHORIZATION')
        self.assertEqual(row['end_utc'],'2026-10-09T07:19:00+00:00')
        self.assertNotIn('no other large',row['scope'])
        with self.assertRaises(ValueError):execution_window(start=now.isoformat(),now=now)
        explicit=execution_window(row['start_utc'],row['end_utc'],now,automatic=True)
        self.assertEqual(explicit,row)

    def test_repeated_runs_share_the_original_window_end(self):
        now=datetime(2026,10,8,4,1,tzinfo=timezone.utc)
        row=confirmed_window('2026-10-08T03:57:37+00:00','2026-10-08T04:17:37+00:00',now)
        self.assertEqual(row['end_utc'],'2026-10-08T04:17:37+00:00')

    def test_expired_future_and_overlong_windows_rejected(self):
        now=datetime(2026,10,8,4,18,tzinfo=timezone.utc)
        for begin,end in [('2026-10-08T03:57:37+00:00','2026-10-08T04:17:37+00:00'),
                          ('2026-10-08T04:20:00+00:00','2026-10-08T04:40:00+00:00'),
                          ('2026-10-08T04:10:00+00:00','2026-10-08T04:40:00+00:00')]:
            with self.assertRaises(ValueError):confirmed_window(begin,end,now)

    def test_budget_preserves_host_reserve_and_margin(self):
        self.assertFalse(capacity_budget(46*GIB,20*GIB)['ready'])
        self.assertTrue(capacity_budget(47*GIB,20*GIB)['ready'])
        for invalid in [0,-GIB,GIB+1]:
            with self.assertRaises(ValueError):capacity_budget(70*GIB,invalid)

    def test_unmounted_directory_cannot_pass_identity(self):
        with tempfile.TemporaryDirectory() as name:
            row={'target':name,'source':'/dev/loop-test','fstype':'ext4'}
            with patch('isolated_storage.subprocess.check_output',return_value=json.dumps({'filesystems':[row]}).encode()):
                with self.assertRaisesRegex(ValueError,'separate filesystem'):mount_identity(Path(name),'/dev/loop-test')

    def test_wrong_source_or_filesystem_rejected(self):
        for source,fs in [('/dev/loop-other','ext4'),('/dev/loop-test','btrfs')]:
            row={'target':'/tmp/owned-mount','source':source,'fstype':fs}
            with patch('isolated_storage.subprocess.check_output',return_value=json.dumps({'filesystems':[row]}).encode()):
                with self.assertRaisesRegex(ValueError,'identity mismatch'):mount_identity(Path('/tmp/owned-mount'),'/dev/loop-test')

    def info(self):
        return {'HostConfig':{'ReadonlyRootfs':True,'LogConfig':{'Type':'none'}},
                'Mounts':[dict(Type='bind',Source=src,Destination=dst,RW=True) for src,dst in write_mounts('/tmp/owned-mount').items()]}

    @patch('isolated_storage.mount_identity',return_value={})
    def test_unbounded_roots_logs_and_extra_writes_rejected(self,mock):
        verify_container_boundary(self.info(),Path('/tmp/owned-mount'),'/dev/loop-test')
        info=self.info();info['HostConfig']['ReadonlyRootfs']=False
        with self.assertRaises(ValueError):verify_container_boundary(info,Path('/tmp/owned-mount'),'/dev/loop-test')
        info=self.info();info['HostConfig']['LogConfig']['Type']='json-file'
        with self.assertRaises(ValueError):verify_container_boundary(info,Path('/tmp/owned-mount'),'/dev/loop-test')
        info=self.info();info['Mounts'].append(dict(Type='bind',Source='/tmp/other',Destination='/var/lib/cache',RW=True))
        with self.assertRaises(ValueError):verify_container_boundary(info,Path('/tmp/owned-mount'),'/dev/loop-test')
        info=self.info();info['Mounts'][0]['Source']='/tmp/other'
        with self.assertRaises(ValueError):verify_container_boundary(info,Path('/tmp/owned-mount'),'/dev/loop-test')

    def test_config_changes_only_named_section(self):
        text='[one]\nenabled = true\n[two]\nenabled = true\n'
        self.assertEqual(config_value(text,'one','enabled','false'),'[one]\nenabled = false\n[two]\nenabled = true\n')
        with self.assertRaises(AssertionError):config_value(text,'missing','enabled','false')

    def test_input_manifest_detects_content_change_without_following_links(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'input').write_text('original');(root/'link').symlink_to('/unrelated/file')
            first=immutable_manifest(root)
            self.assertEqual(first['link']['target'],'/unrelated/file')
            (root/'input').write_text('changed!')
            self.assertNotEqual(first['input']['sha256'],immutable_manifest(root)['input']['sha256'])


class TrenvxBoundaryTests(unittest.TestCase):
    def boundary(self):
        boundary=Boundary.__new__(Boundary)
        boundary.root=Path('/project');boundary.mountpoint=Mock();boundary.raw_root=Mock();boundary.image=Mock()
        boundary.volume_device=42;boundary.image_identity=(7,8);boundary.capacity_bytes=24*GIB
        boundary.volume_reserve_bytes=512*2**20
        boundary.value={'window':{'start_utc':'start','end_utc':'end'}}
        boundary.mountpoint.stat.return_value=SimpleNamespace(st_dev=42)
        boundary.raw_root.stat.return_value=SimpleNamespace(st_dev=42)
        boundary.image.lstat.return_value=SimpleNamespace(st_dev=7,st_ino=8,st_size=24*GIB,st_blocks=24*GIB//512)
        return boundary

    def check(self,boundary,host=46*GIB,volume=8*GIB,headroom=0):
        with patch('isolated_storage.confirmed_window'),patch('isolated_storage.available',
                side_effect=lambda path:host if path==boundary.root else volume):
            return boundary.check(volume_headroom=headroom)

    def test_small_volume_does_not_inherit_shared_disk_launch_threshold(self):
        self.assertEqual(self.check(self.boundary(),headroom=6*GIB),46*GIB)

    def test_missing_mount_refuses_parent_filesystem_fallback(self):
        for path in ['mountpoint','raw_root']:
            boundary=self.boundary();getattr(boundary,path).stat.return_value=SimpleNamespace(st_dev=7)
            with self.assertRaisesRegex(ValueError,'mount disappeared'):self.check(boundary)

    def test_host_reserve_is_independent_of_volume_free_space(self):
        with self.assertRaisesRegex(RuntimeError,'host disk reserve'):
            self.check(self.boundary(),host=20*GIB,volume=10*GIB)

    def test_volume_cleanup_reserve_is_independent_of_host_free_space(self):
        with self.assertRaisesRegex(RuntimeError,'volume cleanup reserve'):
            self.check(self.boundary(),host=70*GIB,volume=512*2**20)

    def test_archive_headroom_is_checked_inside_the_volume(self):
        boundary=self.boundary()
        self.assertEqual(self.check(boundary,volume=6*GIB),46*GIB)
        with self.assertRaisesRegex(RuntimeError,'volume cleanup reserve'):
            self.check(boundary,volume=6*GIB,headroom=6*GIB)

    def test_sparse_or_replaced_backing_is_rejected(self):
        for change in [{'st_blocks':1},{'st_ino':9},{'st_size':25*GIB}]:
            boundary=self.boundary();stat=boundary.image.lstat.return_value
            for key,value in change.items():setattr(stat,key,value)
            with self.assertRaisesRegex(ValueError,'allocation changed'):self.check(boundary)

    def test_expired_window_stops_writes(self):
        boundary=self.boundary()
        with patch('isolated_storage.confirmed_window',side_effect=ValueError('window expired')):
            with self.assertRaisesRegex(ValueError,'window expired'):boundary.check()

    def test_manifest_rejects_writer_outside_owned_volume_and_lower_launch_budget(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve();out=root/'.artifacts/storage-isolated-trenvx/run';out.mkdir(parents=True)
            mount=out/'mount';raw=mount/'trenvx-runs';raw.mkdir(parents=True)
            manifest=out/'boundary.json'
            value=dict(version=1,workspace=str(root),mountpoint=str(mount),device='/dev/loop-test',
                image=str(out/'volume.ext4'),capacity_bytes=24*GIB,image_device=7,image_inode=8,
                initial_host_available_bytes=60*GIB,raw_root=str(raw),volume_reserve_bytes=512*2**20,window={})
            manifest.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'outside'):Boundary(manifest,root,project_root=root)
            with self.assertRaisesRegex(ValueError,'host launch budget'):Boundary(manifest,raw,project_root=root)

    def test_archive_removal_requires_missing_volume_and_matching_verification(self):
        from validate_isolated_trenvx import record_archive_removal
        with tempfile.TemporaryDirectory() as name:
            run=Path(name)/'run';run.mkdir();volume=Path(name)/'volume.ext4';volume.write_text('fixture')
            manifest=dict(gzip_test='PASS',tar_compare='PASS',source_removed=True,archive_sha256='a'*64,archive_bytes=123)
            (run/'archive-manifest.json').write_text(json.dumps(manifest))
            proof=dict(run_id='run',sha256='a'*64,bytes=123)
            with self.assertRaisesRegex(ValueError,'removal not confirmed'):record_archive_removal(run,volume,proof)
            volume.unlink()
            with self.assertRaisesRegex(ValueError,'proof mismatch'):record_archive_removal(run,volume,dict(proof,sha256='b'*64))
            record_archive_removal(run,volume,proof)
            self.assertEqual(json.loads((run/'archive-removal.json').read_text())['archive_sha256'],manifest['archive_sha256'])
            with self.assertRaises(FileExistsError):record_archive_removal(run,volume,proof)

    def test_checkpoint_without_reference_fails_before_allocation(self):
        import validate_isolated_trenvx as validator
        with patch.object(validator,'verify_runtime',return_value={}),patch.object(validator.os,'geteuid',return_value=0),\
                patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(ValueError,'requires --reference'):
                validator.main(SimpleNamespace(mode='checkpoint',reference=None))
            allocate.assert_not_called()

    def test_reference_hash_and_full_completion_are_required(self):
        import validate_isolated_trenvx as validator
        with tempfile.TemporaryDirectory(dir=validator.ROOT/'.artifacts') as name:
            run=Path(name);(run/'replay').mkdir()
            (run/'result.json').write_text(json.dumps(dict(status='PASS',full_trajectory_verified=True)))
            (run/'config.json').write_text(json.dumps(dict(scenario='exp1',checkpoint_after=None)))
            (run/'replay/actions.tsv').write_bytes(b'frozen fixture')
            (run/'replay/patch.diff').write_bytes(b'patch fixture')
            with patch('exp1_inputs.MANIFEST_SHA256',hashlib.sha256(b'frozen fixture').hexdigest()),\
                    patch('exp1_inputs.PATCH_SHA256',hashlib.sha256(b'patch fixture').hexdigest()):
                args=SimpleNamespace(mode='checkpoint',reference=run)
                command=validator.backend_command(args)
                self.assertEqual(command[command.index('--reference')+1],run)
                (run/'replay/patch.diff').write_bytes(b'changed patch')
                with self.assertRaisesRegex(ValueError,'not a verified'):validator.backend_command(args)
                (run/'replay/patch.diff').write_bytes(b'patch fixture')
                (run/'result.json').write_text(json.dumps(dict(status='PASS',full_trajectory_verified=False)))
                with self.assertRaisesRegex(ValueError,'not a verified'):validator.backend_command(args)

    def test_backend_check_only_rejects_missing_reference_without_launching(self):
        import validate_isolated_trenvx as validator
        command=[sys.executable,'-B',str(validator.ROOT/'scripts/smoke_trenvx_service.py'),
                 '--scenario','exp1','--checkpoint-after','022','--check-only']
        missing=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(missing.returncode,2)
        self.assertIn('checkpoint requires',missing.stderr)
        valid=subprocess.run(command+['--reference','/unused/read-only-reference'],capture_output=True,text=True,timeout=10)
        self.assertEqual(valid.returncode,0)
        self.assertIn('scope=arguments-only',valid.stdout)

    def test_wrong_python_fails_before_volume_allocation(self):
        import validate_isolated_trenvx as validator
        with patch.object(validator.sys,'prefix','/wrong/python'),patch.object(validator.os,'geteuid',return_value=0),\
                patch.object(validator.os,'posix_fallocate') as allocate:
            with self.assertRaisesRegex(RuntimeError,'pinned TrEnv-X Python'):
                validator.main(SimpleNamespace())
            allocate.assert_not_called()

    def test_no_boundary_leaves_the_old_default_unchanged(self):
        with patch.dict('os.environ',{},clear=True):self.assertIsNone(configured_boundary())

    def test_disk_guard_rejects_bad_boundary_before_launch(self):
        from experiment_disk_guard import guarded_run
        boundary=Mock();boundary.value={};boundary.host_free.side_effect=ValueError('mount disappeared')
        with tempfile.TemporaryDirectory() as name,patch('isolated_storage.configured_boundary',return_value=boundary),\
                patch('experiment_disk_guard.subprocess.Popen') as launch:
            with open(Path(name)/'log','wb') as log:
                with self.assertRaisesRegex(ValueError,'mount disappeared'):
                    guarded_run([sys.executable,'-c','pass'],directory=name,log=log,timeout=1)
            launch.assert_not_called()
            record=json.loads((Path(name)/'disk-guard.jsonl').read_text())
            self.assertEqual(record['status'],'FAIL')
            self.assertNotIn('process_group',record)


if __name__=='__main__':unittest.main()
