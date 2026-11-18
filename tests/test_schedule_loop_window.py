"""Mocked loop/mincore syscalls only; never open live services or devices."""
import copy
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_file_monitor as files
import host_schedule_cache_layers as layers


def metadata(mtime=100):
    return dict(device=1,inode=2,rdev=0,size=8192,mtime_ns=mtime)


class LoopWindowTests(unittest.TestCase):
    def setUp(self):
        self.before=dict(backing_device=1,backing_inode=2,backing_rdev=0,offset=0,
            sizelimit=0,number=3,flags=0,direct_io=False,read_only=False)
        self.value=dict(device=1,inode=2,rdev=0,size=8192,mapping_length=8192,
            resident_bytes=4096,stable=False,identity_stable=True,size_stable=True,
            metadata_changed=True,metadata_before=metadata(),metadata_after=metadata(101),
            metadata_changes=dict(mtime_ns=dict(before=100,after=101)))

    def observe(self, *, value=None, after=None, enabled=True, initial_inode=2,
                query_error=None, mount_after_error=False):
        mount=Mock();mount.stat.return_value=SimpleNamespace(st_dev=layers.os.makedev(7,3))
        block=SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=layers.os.makedev(7,3))
        backing=SimpleNamespace(st_dev=1,st_ino=initial_inode,st_rdev=0,st_size=8192)
        with patch.object(layers.Path,'resolve',return_value=Path('/fake/loop3')), \
             patch.object(layers,'mount_identity',side_effect=[None,ValueError('mount changed')] if mount_after_error else None), \
             patch.object(layers.os,'open',side_effect=[10,11]) as opened,patch.object(layers.os,'close') as closed, \
             patch.object(layers.os,'fstat',side_effect=[block,block,backing]), \
             patch.object(layers,'loop_status',side_effect=[self.before,after or self.before]), \
             patch.object(layers,'mincore_file',return_value=value or self.value,side_effect=query_error) as query:
            result=layers.observe_loop(mount,Path('/fake/volume.ext4'),include_query_window=enabled)
        self.assertEqual([c.args[0] for c in closed.call_args_list],[11,10])
        self.assertEqual(opened.call_count,2)
        return result,query

    def test_mtime_change_keeps_old_unknown_and_exposes_new_window(self):
        result,query=self.observe()
        self.assertEqual(result['status'],'UNKNOWN');self.assertIsNone(result['resident_bytes'])
        self.assertEqual(result['error'],"ValueError('loop/backing changed during query')")
        window=result['query_window']
        self.assertEqual(window['status'],'OBSERVED');self.assertEqual(window['resident_bytes'],4096)
        self.assertEqual(window['range'],dict(offset=0,length=8192))
        self.assertEqual(window['strict_rejection_reason'],'MTIME_CHANGED')
        self.assertFalse(result['residency']['stable'])
        query.assert_called_once_with(Path('/fake/volume.ext4'),file_descriptor=11,live=True,include_metadata=True)

    def test_default_call_has_no_new_view_or_metadata_request(self):
        result,query=self.observe(enabled=False)
        self.assertNotIn('query_window',result)
        query.assert_called_once_with(Path('/fake/volume.ext4'),file_descriptor=11,live=True)
        self.assertEqual(result['status'],'UNKNOWN')

    def test_stable_and_real_zero_values_are_not_unknown(self):
        for count in (0,4096):
            value=copy.deepcopy(self.value);value.update(stable=True,metadata_changed=False,resident_bytes=count,
                metadata_after=metadata(),metadata_changes={})
            result,_=self.observe(value=value)
            self.assertEqual(result['status'],'OBSERVED')
            self.assertEqual(result['query_window']['resident_bytes'],count)

    def test_loop_backing_offset_limit_and_direct_io_changes_rejected(self):
        for key,new in dict(backing_device=2,backing_inode=3,offset=4096,sizelimit=8192,
                            direct_io=True,flags=16).items():
            with self.subTest(field=key):
                result,_=self.observe(after=dict(self.before,**{key:new}))
                self.assertEqual(result['query_window']['status'],'UNKNOWN')
                self.assertIsNone(result['query_window']['resident_bytes'])
                self.assertIn('LOOP_CONFIGURATION_CHANGED',result['query_window']['rejected_checks'])

    def test_file_identity_and_size_change_rejected(self):
        for field in ('device','inode','rdev','size'):
            value=copy.deepcopy(self.value)
            value['metadata_after'][field]+=1
            value['metadata_changes'][field]=dict(before=value['metadata_before'][field],after=value['metadata_after'][field])
            value['size_stable' if field=='size' else 'identity_stable']=False
            with self.subTest(field=field):
                result,_=self.observe(value=value)
                self.assertIsNone(result['query_window']['resident_bytes'])
                self.assertIn('FILE_SIZE_CHANGED' if field=='size' else 'FILE_IDENTITY_CHANGED',
                    result['query_window']['rejected_checks'])

    def test_wrong_initial_backing_not_queried(self):
        result,query=self.observe(initial_inode=3)
        query.assert_not_called();self.assertIsNone(result['query_window']['resident_bytes'])
        self.assertEqual(result['query_window']['failure_stage'],'backing_identity_before')

    def test_size_change_between_initial_pin_and_query_does_not_pass_new_window(self):
        value=copy.deepcopy(self.value)
        value.update(size=12288,mapping_length=12288,stable=True,metadata_changed=False,
            metadata_before=dict(metadata(),size=12288),metadata_after=dict(metadata(),size=12288),metadata_changes={})
        result,_=self.observe(value=value)
        self.assertEqual(result['status'],'OBSERVED')  # Preserve the old query-only strict condition.
        self.assertEqual(result['query_window']['status'],'UNKNOWN')
        self.assertEqual(result['query_window']['rejected_checks'],['FILE_SIZE_CHANGED'])

    def test_query_and_post_mount_failures_keep_new_value_unknown(self):
        for kw,stage in ((dict(query_error=OSError('query failed')),'mincore_query'),
                         (dict(mount_after_error=True),'mount_after')):
            with self.subTest(stage=stage):
                result,query=self.observe(**kw);query.assert_called_once()
                self.assertIsNone(result['query_window']['resident_bytes'])
                self.assertEqual(result['query_window']['failure_stage'],stage)

    def test_explicit_cache_layers_select_new_view_without_promoting_strict_status(self):
        expanded=dict(process_identities={},special_block_references=[])
        with patch.object(layers,'counters',return_value={}),patch.object(layers,'pids',return_value=[]), \
             patch.object(layers,'observe_devices',return_value=dict(status='OBSERVED')), \
             patch.object(layers,'deleted_tmpfs',return_value=dict(status='NOT_REQUESTED')), \
             patch.object(layers,'observe_loop',return_value=dict(status='UNKNOWN',
                 query_window=dict(status='OBSERVED',resident_bytes=4096))) as loop:
            value=layers.observe_cache_layers(Path('/fake/mount/env'),{}, {},expanded,Path('/fake/group'))
        loop.assert_called_once_with(Path('/fake/mount'),Path('/fake/volume.ext4'),include_query_window=True)
        self.assertEqual(value['status'],'PARTIAL')


class MincoreMetadataTests(unittest.TestCase):
    def query(self,enabled):
        first=SimpleNamespace(st_mode=stat.S_IFREG,st_dev=1,st_ino=2,st_rdev=0,st_size=8192,st_mtime_ns=100)
        last=SimpleNamespace(**dict(first.__dict__,st_mtime_ns=101))
        libc=Mock();libc.mmap.return_value=4096;libc.munmap.return_value=0
        def mincore(addr,length,vector):vector[0]=1;return 0
        libc.mincore.side_effect=mincore
        with patch.object(files.os,'dup',return_value=12) as duplicate,patch.object(files.os,'open') as opened, \
             patch.object(files.os,'close') as closed,patch.object(files.os,'fstat',side_effect=[first,last]) as fstat, \
             patch.object(files.ctypes,'CDLL',return_value=libc):
            result=files.mincore_file(Path('/fake'),file_descriptor=11,live=True,include_metadata=enabled)
        duplicate.assert_called_once_with(11);opened.assert_not_called();closed.assert_called_once_with(12)
        self.assertEqual(fstat.call_count,2);libc.mincore.assert_called_once();libc.mmap.assert_called_once()
        self.assertFalse(result['stable']);self.assertTrue(result['identity_stable']);self.assertTrue(result['size_stable'])
        return result

    def test_metadata_reuses_existing_fstats_and_does_not_repeat_mincore(self):
        row=self.query(True)
        self.assertEqual(row['metadata_before'],metadata())
        self.assertEqual(row['metadata_after'],metadata(101))
        self.assertEqual(row['metadata_changes'],dict(mtime_ns=dict(before=100,after=101)))

    def test_old_result_schema_unchanged_without_metadata_option(self):
        row=self.query(False)
        for field in ('metadata_before','metadata_after','metadata_changes'):self.assertNotIn(field,row)


if __name__=='__main__':unittest.main()
