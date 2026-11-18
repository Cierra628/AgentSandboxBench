"""Offline fixtures/mocked syscalls only. Never open an actual device or sandbox."""
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_schedule_cache_layers as m
import host_schedule_files as files


class CacheLayerTests(unittest.TestCase):
    def test_private_run_tmp_closed_files_dedup_and_old_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            for n in ('env','run','tmp'):(base/n).mkdir()
            (base/'run/closed').write_bytes(b'x'*4096)
            (base/'tmp/alias').hardlink_to(base/'run/closed')
            with patch.object(files,'permission_evidence',return_value={}),patch.object(files,'pids',return_value=[]):
                old=files.observe_schedule_files(base/'env',{'guests':{}},[],base/'not-cgroup')
                new=files.observe_schedule_files(base/'env',{'guests':{}},[],base/'not-cgroup',
                    owned_roots=[base/'run',base/'tmp'],capture_layers=True)
            self.assertEqual(old['unique_objects'],0);self.assertNotIn('special_block_references',old)
            self.assertEqual(new['unique_objects'],1);self.assertEqual(new['status'],'OBSERVED')
            self.assertEqual(set(new['files'][0]['categories']),{'owned_run','owned_tmp'})
            self.assertEqual(len(new['files'][0]['aliases']),2)

    def test_devices_dedup_rdev_not_nodes_and_close_all_pins(self):
        refs=[dict(path='/fake/'+str(i),device=1,inode=i,rdev=50,source='test') for i in (10,11)]
        def info(fd):return SimpleNamespace(st_dev=1,st_ino=fd,st_rdev=50,st_mode=stat.S_IFBLK)
        result=dict(stable=True,resident_bytes=8192)
        with patch.object(m,'check_process'),patch.object(m.os,'open',side_effect=[10,11]),\
             patch.object(m.os,'close') as close,patch.object(m.os,'fstat',side_effect=info),\
             patch.object(m,'capacity',return_value=16384),patch.object(m,'mincore_file',return_value=result) as query:
            value=m.observe_devices(refs,{})
        self.assertEqual(value['status'],'OBSERVED');self.assertEqual(len(value['objects']),1)
        self.assertEqual(value['stable_resident_bytes'],8192);query.assert_called_once()
        self.assertEqual(query.call_args.kwargs['block_identity'],(1,10,50))
        self.assertEqual({c.args[0] for c in close.call_args_list},{10,11})

    def test_bad_device_keeps_unknown_without_residency_or_handle_leak(self):
        ref=dict(path='/fake',device=1,inode=10,rdev=50)
        for failure in ('identity','capacity','query'):
            s=SimpleNamespace(st_dev=1,st_ino=99 if failure=='identity' else 10,st_rdev=50,st_mode=stat.S_IFBLK)
            with self.subTest(failure=failure),patch.object(m,'check_process'),patch.object(m.os,'open',return_value=10),\
                 patch.object(m.os,'close') as close,patch.object(m.os,'fstat',return_value=s),\
                 patch.object(m,'capacity',side_effect=OSError('capacity failed') if failure=='capacity' else None,return_value=16384),\
                 patch.object(m,'mincore_file',side_effect=OSError('query failed')):
                value=m.observe_devices([ref],{})
            self.assertEqual(value['status'],'PARTIAL');self.assertIsNone(value['objects'][0]['resident_bytes'])
            close.assert_called_once_with(10)

    def test_loop_effective_flags_abi_and_exact_backing(self):
        def ioctl(fd,op,buf,mutate):
            self.assertEqual((fd,op,mutate),(10,0x4c05,True))
            values=(5,67,0,4096,8192,3,0,0,16,b'',b'',b'',0,0)
            buf[:]=struct.pack('=QQQQQIIII64s64s32sQQ',*values)
        with patch.object(m.fcntl,'ioctl',side_effect=ioctl):v=m.loop_status(10)
        self.assertEqual((v['backing_device'],v['backing_inode']),(5,67))
        self.assertTrue(v['direct_io']);self.assertEqual(v['offset'],4096);self.assertEqual(v['sizelimit'],8192)
        self.assertEqual(struct.calcsize('=QQQQQIIII64s64s32sQQ'),232)

    def test_tmpfs_only_complete_deleted_mapping_reaches_existing_worker(self):
        region=dict(device=1,inode=2,pid=10,start_ticks='20',start=4096,end=8192,
                    permissions='rw-s',offset=0,mapping_path='/dev/zero (deleted)')
        row=dict(object_key=['regular',1,2],status='SEPARATE_SHMEM_OR_HUGETLB',filesystem_magic=0x01021994,
                 aliases=[dict(region=region)])
        with patch.object(m,'query_deleted_backings_bounded',return_value={'status':'OBSERVED','whole_backing_cache_bytes':8192}) as query:
            value=m.deleted_tmpfs({'files':[row]})
        query.assert_called_once_with([region]);self.assertEqual(value['whole_backing_cache_bytes'],8192)
        row['aliases']=[]
        with patch.object(m,'query_deleted_backings_bounded',return_value={'status':'NOT_REQUESTED','whole_backing_cache_bytes':None}):
            value=m.deleted_tmpfs({'files':[row]})
        self.assertEqual(value['status'],'PARTIAL');self.assertEqual(len(value['missing_sources']),1)
        self.assertIsNone(value['whole_backing_cache_bytes'])

    def test_complete_mapping_metadata_saved_for_existing_tmpfs_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);env=base/'env';env.mkdir();proc=base/'proc';root=proc/'101'
            (root/'fd').mkdir(parents=True);(root/'map_files').mkdir()
            (root/'stat').write_text('101 (fake) '+' '.join(['S']+['0']*18+['123']))
            (root/'cgroup').write_text('0::/test\n')
            backing=base/'backing';backing.write_bytes(b'x'*4096);s=backing.stat()
            (root/'maps').write_text(f'1000-2000 rw-s 00000000 {os.major(s.st_dev):x}:{os.minor(s.st_dev):x} {s.st_ino} /dev/zero (deleted)\n')
            (root/'map_files/1000-2000').symlink_to(backing)
            with patch.object(files,'pids',return_value=[101]),patch.object(files,'permission_evidence',return_value={}),\
                 patch.object(files,'filesystem_magic',return_value=0x01021994):
                value=files.observe_schedule_files(env,{'guests':{}},[],Path('/sys/fs/cgroup/test'),proc_root=proc,capture_layers=True)
            region=value['files'][0]['aliases'][0]['region']
            self.assertEqual((region['pid'],region['start_ticks'],region['start'],region['end']),(101,'123',4096,8192))
            self.assertEqual(region['permissions'],'rw-s');self.assertEqual(region['mapping_path'],'/dev/zero (deleted)')
            self.assertIsNone(value['files'][0]['resident_bytes'])

    def test_wrong_loop_backing_not_queried_and_handles_released(self):
        from unittest.mock import Mock
        mount=Mock();mount.stat.return_value=SimpleNamespace(st_dev=os.makedev(7,3))
        image=Path('/fake/volume.ext4');loop=dict(backing_device=5,backing_inode=67)
        info=[SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=os.makedev(7,3)),
              SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=os.makedev(7,3)),
              SimpleNamespace(st_dev=5,st_ino=68)]
        with patch.object(m.Path,'resolve',return_value=Path('/sys/devices/loop3')),patch.object(m,'mount_identity'),\
             patch.object(m.os,'open',side_effect=[10,11]),patch.object(m.os,'close') as close,\
             patch.object(m.os,'fstat',side_effect=info),patch.object(m,'loop_status',return_value=loop),\
             patch.object(m,'mincore_file') as query:
            value=m.observe_loop(mount,image)
        self.assertEqual(value['status'],'UNKNOWN');self.assertIsNone(value['resident_bytes'])
        query.assert_not_called();self.assertEqual([c.args[0] for c in close.call_args_list],[11,10])

    def test_process_or_mapping_change_rejected_before_opening_device(self):
        ref=dict(path='/fake',device=1,inode=2,rdev=50,process=10)
        with patch.object(m,'process_identity',return_value={'start_ticks':'new'}),patch.object(m.os,'open') as opened:
            value=m.observe_devices([ref],{10:{'start_ticks':'old'}})
        opened.assert_not_called();self.assertEqual(value['status'],'PARTIAL')


if __name__=='__main__':unittest.main()
