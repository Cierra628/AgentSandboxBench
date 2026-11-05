import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from host_expanded_files import observe_owned_files
from host_rollout_shared import SharedRollout


class ExpandedFilesTests(unittest.TestCase):
    def fixture(self,base):
        env=base/'env';vm=env/'vm';vm.mkdir(parents=True)
        for name in ('lower','data','index','log'):(vm/name).write_bytes(b'x'*4096)
        (vm/'alias').hardlink_to(vm/'lower');(vm/'symlink').symlink_to('/not-owned')
        s=(vm/'lower').stat()
        prior=[dict(object_key=['regular',s.st_dev,s.st_ino])]
        guest=dict(pid=987654321,image_config=str(vm/'image.json'),upper=dict(data='data',index='index'))
        return env,dict(guests={'one':guest,'two':dict(guest)}),prior

    def test_upper_and_other_files_dedup_with_previous_lower(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,prior=self.fixture(Path(tmp))
            value=observe_owned_files(env,roots,prior)
            self.assertEqual(value['status'],'OBSERVED')
            self.assertEqual(value['new_unique_file_count'],3)
            self.assertEqual({r['category'] for r in value['files']},{'rootfs_upper_data','rootfs_upper_index','other_owned_vm'})
            self.assertEqual(len({tuple(r['object_key']) for r in value['files']}),3)
            self.assertTrue(all(r['users']==['one','two'] for r in value['files'] if r['category'].startswith('rootfs_upper_')))
            self.assertEqual(value['previous_regular_object_count'],1)
            self.assertGreaterEqual(value['end_monotonic_ns'],value['start_monotonic_ns'])

    def test_changed_and_failed_files_are_partial_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,prior=self.fixture(Path(tmp))
            def query(path,**kw):
                s=path.stat()
                if path.name=='log':raise OSError('gone')
                return dict(device=s.st_dev,inode=s.st_ino,stable=path.name!='data',resident_bytes=4096)
            with patch('host_expanded_files.mincore_file',side_effect=query):value=observe_owned_files(env,roots,prior)
            self.assertEqual(value['status'],'PARTIAL')
            failed=next(r for r in value['files'] if r['path'].endswith('/log'))
            self.assertIsNone(failed['resident_bytes'])
            self.assertEqual(value['stable_new_resident_bytes'],4096)
            changed=next(r for r in value['files'] if r['category']=='rootfs_upper_data')
            self.assertEqual(changed['status'],'CHANGED_DURING_QUERY')
            self.assertEqual(changed['resident_bytes'],4096)

    def test_missing_upper_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            env,roots,prior=self.fixture(Path(tmp));roots['guests']['one']['upper']['data']='absent'
            value=observe_owned_files(env,roots,prior)
            self.assertEqual(value['status'],'PARTIAL')
            self.assertTrue(any(r['resident_bytes'] is None and r.get('role')=='one' for r in value['unresolved']))

    def test_expanded_query_is_opt_in_and_saved_with_original_lower_query(self):
        from types import SimpleNamespace
        import time
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);path=base/'lower';path.write_bytes(b'x'*4096);s=path.stat()
            row=dict(path=str(path),device=s.st_dev,inode=s.st_ino,size=s.st_size,rdev=0,block_device=False,is_read_only=True)
            point=dict(drive_inventory={'one':[row]},backends={},rootfs_inventory=dict(guests={},objects=[]),bindings={})
            for enabled in (False,True):
                out=base/str(enabled);out.mkdir()
                c=SharedRollout(base,SimpleNamespace(cgroup=base),out,'grpo',expanded=enabled);c.opened={}
                extra=dict(status='PARTIAL',new_unique_file_count=1,stable_new_resident_bytes=4096,categories={},files=[],unresolved=[])
                with patch.object(c,'phase',return_value=point),patch('host_rollout_shared.counters',return_value={'current':10}),\
                     patch('host_rollout_shared.device_residency',return_value=([],None)),\
                     patch('host_rollout_shared.validate_rootfs_inventory'),patch('host_expanded_files.observe_owned_files',return_value=extra) as observe:
                    value=c.query({},time.monotonic_ns())
                self.assertEqual(value['status'],'OBSERVED')
                if enabled:
                    observe.assert_called_once();self.assertEqual(value['query']['expanded_files'],extra)
                else:observe.assert_not_called();self.assertNotIn('expanded_files',value['query'])


if __name__=='__main__':unittest.main()
