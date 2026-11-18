"""Offline only: fake proc metadata and mocked device queries, no live devices."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_host_file_restore import shared_device_selection,live_residency
from host_scaling import observe_files


def block(rdev):
    return dict(identity=(6,rdev+100,rdev),path='/metadata/block-'+str(rdev),target='/dev/fake'+str(rdev),owner_pid=100)


def drive(rdev=1):
    return dict(device=6,inode=rdev+100,rdev=rdev,block_device=True,is_read_only=True,drive_id='rootfs',path='/metadata/root')


class SharedFilterTests(unittest.TestCase):
    def test_selection_deduplicates_aliases_and_preserves_skipped_unknown(self):
        devices={i:block(i) for i in (1,2,3)};original=copy.deepcopy(devices)
        selected,excluded=shared_device_selection(devices,devices[3]['identity'],[drive(),dict(drive(),drive_id='alias',inode=999)])
        self.assertEqual(set(selected),{1,3});self.assertEqual(len(excluded),1)
        self.assertEqual(excluded[0]['identity'],[6,102,2]);self.assertEqual(excluded[0]['status'],'NOT_REQUESTED')
        self.assertIsNone(excluded[0]['resident_bytes']);self.assertIsNone(excluded[0]['resident_pages'])
        self.assertEqual(devices,original)

    def test_missing_or_changed_shared_identity_does_not_silently_drop_target(self):
        devices={i:block(i) for i in (1,2,3)}
        for rows in ([],[drive(4)],[dict(drive(),inode=999)],[dict(drive(),block_device=False)]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):shared_device_selection(devices,devices[3]['identity'],rows)
        with self.assertRaises(ValueError):shared_device_selection({1:devices[1]},devices[3]['identity'],[drive()])

    def fixture(self,root,n,filtered):
        out=root/'mount/controller/prettier6604';out.mkdir(parents=True)
        memory=block(3);ids=[1,3,*range(10,10+n)]
        fds=[Path('/proc/100/fd')/str(i) for i in range(len(ids)+2)]
        metadata={str(path):SimpleNamespace(st_mode=stat.S_IFBLK,st_dev=6,st_ino=rdev+100,st_rdev=rdev)
            for path,rdev in zip(fds,ids)}
        # Another fd to the same shared root must not add another query.
        metadata[str(fds[-2])]=metadata[str(fds[0])]
        layer_info=SimpleNamespace(st_mode=stat.S_IFREG,st_dev=2,st_ino=20,st_rdev=0,st_size=4096)
        metadata[str(fds[-1])]=layer_info
        bindings={str(i):dict(pid=100+i,start_ticks=str(i)) for i in range(n)}
        guests=[dict(role=str(i),sandbox_id=str(i),binding=bindings[str(i)]) for i in range(n)]
        binding=SimpleNamespace(cgroup=Path('/metadata/group'),resolve=lambda sid:bindings[sid])
        backend=dict(device=6,inode=103,rdev=3,path=memory['path'],regions=[dict(start=0,end=4096,offset=0,path='/dev/fake3')])
        config={'lowers':[{'file':'/workspace/env/layer.raw','size':4096}]*2,'upper':{}}
        queried=[];original_stat=Path.stat
        def fake_stat(path,*args,**kwargs):
            if str(path) in metadata:return metadata[str(path)]
            if path.name=='layer.raw':return layer_info
            if str(path).startswith('/proc/'):raise AssertionError('unexpected real proc access')
            return original_stat(path,*args,**kwargs)
        def device_queries(devices,memory_identity,group,guests,**kwargs):
            values=[]
            for item in sorted(devices.values(),key=lambda v:v['identity']!=memory_identity):
                key=item['identity'];queried.append(key[2])
                values.append(dict(device=key[0],inode=key[1],rdev=key[2],block_device=True,path=item['path'],stable=True,
                    mapping_length=(4 if key[2]==3 else 64)*2**30,resident_bytes=4096,
                    kind='memory-device' if key==memory_identity else 'other-owned-block-device'))
            return values,None
        def layer_query(path):
            queried.append('layer')
            return dict(device=2,inode=20,rdev=0,block_device=False,path=str(path),stable=True,
                mapping_length=4096,resident_bytes=4096)
        with ExitStack() as stack:
            stack.enter_context(patch('run_host_file_restore.ram_backend',return_value=backend))
            stack.enter_context(patch('host_file_monitor.pids',return_value=[100]))
            stack.enter_context(patch.object(Path,'iterdir',lambda path:iter(fds) if str(path)=='/proc/100/fd' else (_ for _ in ()).throw(AssertionError('unexpected directory access'))))
            stack.enter_context(patch.object(Path,'stat',fake_stat))
            stack.enter_context(patch('run_host_file_restore.os.readlink',side_effect=lambda path:'/dev/fake'+str(metadata[str(path)].st_rdev)))
            stack.enter_context(patch('run_host_file_restore.read',return_value=config))
            stack.enter_context(patch('host_file_monitor.counters',return_value={'file':2**30}))
            stack.enter_context(patch('host_file_monitor.pss_snapshot',side_effect=AssertionError('PSS not requested')))
            stack.enter_context(patch('host_file_monitor.mincore_file',side_effect=layer_query))
            stack.enter_context(patch('run_host_file_restore.device_residency',side_effect=device_queries))
            result=live_residency(Path('/metadata/group'),binding,guests,'template',out,
                **({'shared_readonly_drives':[drive(),drive()]} if filtered else {}))
        return result,queried

    def test_live_helper_filters_only_explicit_new_mode_and_keeps_layer_once(self):
        for n in (1,7):
            for filtered in (False,True):
                with self.subTest(n=n,filtered=filtered),tempfile.TemporaryDirectory() as tmp:
                    result,queried=self.fixture(Path(tmp),n,filtered)
                    self.assertEqual(result['status'],'OBSERVED')
                    self.assertEqual(queried,[3,1,'layer'] if filtered else [3,1,*range(10,10+n),'layer'])
                    self.assertEqual(len(result['files']),3 if filtered else n+3)
                    if filtered:
                        self.assertEqual(result['residency_scope'],'SHARED_FILES_ONLY')
                        self.assertEqual(len(result['excluded_devices']),n)
                        self.assertTrue(all(v['resident_bytes'] is None and v['status']=='NOT_REQUESTED' for v in result['excluded_devices']))
                        self.assertEqual(len(result['selected_block_identities']),2)
                    else:
                        self.assertNotIn('excluded_devices',result);self.assertNotIn('residency_scope',result)

    def test_actual_scaling_caller_supplies_shared_readonly_objects_not_private_drives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);observation,_=self.fixture(root,7,True)
            binding=SimpleNamespace(resolve=lambda sid:dict(pid=int(sid)+100))
            private=dict(drive(10),drive_id='user_rootfs',is_read_only=False)
            def inventory(vm):return [drive(),dict(drive(),drive_id='alias'),dict(private,inode=vm['pid'],rdev=vm['pid'])]
            args=SimpleNamespace(host_scaling_mode='restore',host_checkpoint_bundle='/bundle',host_checkpoint_source='/source')
            with patch('host_prefix_compare.drives_for_vm',side_effect=inventory),\
                 patch('run_host_file_restore.live_residency',return_value=observation) as query,\
                 patch('host_file_monitor.counters',return_value={'file':0}):
                result=observe_files(args,root,{str(i):str(i) for i in range(7)},Path('/metadata/group'),binding,dict(template='template'))
            selected=query.call_args.kwargs['shared_readonly_drives']
            self.assertEqual(len(selected),2);self.assertTrue(all(v['rdev']==1 and v['is_read_only'] for v in selected))
            self.assertEqual({v['category'] for v in result['objects']},{'shared_readonly_backing','snapshot_memory_device','snapshot_memory_layer'})
            self.assertEqual(len(result['objects']),3)
            self.assertEqual(len(result['observation']['excluded_devices']),7)
            self.assertGreaterEqual(result['elapsed_ns'],0);self.assertGreaterEqual(result['cpu_ns'],0)


if __name__=='__main__':unittest.main()
