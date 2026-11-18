"""Mocked metadata/syscalls only; no real process, device, service or cache query."""
import ctypes
import errno
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_deleted_backing as h


class DeletedBackingTests(unittest.TestCase):
    def setUp(self):
        self.region=dict(pid=10,start_ticks='123',start=4096,end=8192,permissions='rw-s',offset=0,
                         device=1,inode=20,mapping_path='/memfd:fixture (deleted)')
        self.metadata=dict(device=1,inode=20,size=32768,mode=stat.S_IFREG|0o600,nlink=0,filesystem_magic=0x01021994)
        self.stats=dict(nr_cache=2,nr_dirty=2,nr_writeback=0,nr_evicted=0,nr_recently_evicted=0)

    def query(self,*,stats=None,metadata=None,validation=None,open_error=None):
        with patch.object(h,'_validate_region',side_effect=validation) as validate,\
             patch.object(h,'_metadata',return_value=metadata or self.metadata),\
             patch.object(h,'cachestat',return_value=stats or self.stats) as query,\
             patch.object(h.os,'open',side_effect=open_error or [10,11]) as opened,\
             patch.object(h.os,'close') as closed:
            result=h.query_deleted_backing(self.region,proc=Path('/synthetic'))
        return result,validate,query,opened,closed

    def test_observed_whole_object_not_mapping_length_and_closes_handles(self):
        v,validate,query,opened,closed=self.query()
        self.assertEqual(v['status'],'OBSERVED');self.assertEqual(v['whole_backing_cache_bytes'],2*v['page_size'])
        self.assertEqual(v['cache_counters'],self.stats);self.assertEqual(v['page_charge_owner'],'NOT_MEASURED')
        self.assertEqual(validate.call_count,2);self.assertEqual(query.call_args.args,(11,))
        self.assertEqual(opened.call_args_list[0].args[1],os.O_PATH|os.O_CLOEXEC)
        self.assertEqual(opened.call_args_list[1].args,(Path('/proc/self/fd/10'),os.O_RDONLY|os.O_CLOEXEC))
        self.assertEqual([c.args[0] for c in closed.call_args_list],[11,10])

    def test_swap_entries_are_partial_not_zero(self):
        v,*_=self.query(stats=dict(self.stats,nr_evicted=1))
        self.assertEqual(v['status'],'PARTIAL_SWAP_ENTRIES');self.assertEqual(v['cached_bytes'],2*v['page_size'])
        self.assertIsNone(v['whole_backing_cache_bytes'])

    def test_pid_or_mapping_changes_do_not_produce_totals(self):
        for side_effect in ([ProcessLookupError('PID reused')],[None,ValueError('mapping changed')]):
            with self.subTest(side_effect=side_effect):
                v,*_=self.query(validation=side_effect)
                self.assertEqual(v['status'],'UNKNOWN');self.assertIsNone(v['whole_backing_cache_bytes'])
                self.assertIsNone(v['cached_bytes'])

    def test_wrong_backing_is_not_queried(self):
        v,_,query,_,closed=self.query(metadata=dict(self.metadata,inode=21))
        self.assertEqual(v['status'],'UNKNOWN');query.assert_not_called();closed.assert_called_once_with(10)

    def test_region_validator_checks_actual_synthetic_start_ticks_and_maps(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc=Path(tmp);root=proc/'10';root.mkdir()
            (root/'stat').write_text('10 (fixture) '+' '.join(['S']+['0']*18+['123']))
            line='1000-2000 rw-s 0 00:01 20 /memfd:fixture (deleted)\n'
            (root/'maps').write_text(line)
            h._validate_region(proc,self.region)
            with self.assertRaises(ProcessLookupError):h._validate_region(proc,dict(self.region,start_ticks='124'))
            (root/'maps').write_text(line.replace('rw-s','rw-p'))
            with self.assertRaises(ValueError):h._validate_region(proc,self.region)

    def test_hugetlb_and_char_device_are_not_reopened(self):
        for m in (dict(self.metadata,filesystem_magic=0x958458f6),dict(self.metadata,mode=stat.S_IFCHR)):
            v,_,query,opened,closed=self.query(metadata=m)
            self.assertEqual(v['status'],'UNSUPPORTED_BACKING');self.assertIsNone(v['whole_backing_cache_bytes'])
            self.assertEqual(opened.call_count,1);query.assert_not_called();closed.assert_called_once_with(10)

    def test_permission_and_reopen_failure_keep_unknown_and_release(self):
        for effects,expected_close in ((PermissionError(errno.EPERM,'denied'),0),([10,PermissionError(errno.EACCES,'denied')],1)):
            v,_,query,_,closed=self.query(open_error=effects)
            self.assertEqual(v['status'],'UNKNOWN');self.assertIsNone(v['whole_backing_cache_bytes'])
            self.assertIn(v['errno'],(errno.EPERM,errno.EACCES));self.assertEqual(closed.call_count,expected_close)
            query.assert_not_called()

    def test_metadata_change_after_syscall_keeps_raw_but_no_capacity(self):
        with patch.object(h,'_validate_region'),patch.object(h,'_metadata',side_effect=[self.metadata,self.metadata,dict(self.metadata,size=0)]),\
             patch.object(h,'cachestat',return_value=self.stats),patch.object(h.os,'open',side_effect=[10,11]),patch.object(h.os,'close') as close:
            v=h.query_deleted_backing(self.region)
        self.assertEqual(v['status'],'UNKNOWN');self.assertEqual(v['cache_counters'],self.stats)
        self.assertIsNone(v['whole_backing_cache_bytes']);self.assertEqual(close.call_count,2)

    def test_dedup_and_incomplete_aggregate(self):
        good=dict(status='OBSERVED',whole_backing_cache_bytes=8192)
        alias=dict(self.region,start=12288,end=16384)
        with patch.object(h,'query_deleted_backing',return_value=good) as query:
            v=h.query_deleted_backings([self.region,alias])
        query.assert_called_once();self.assertEqual(v['whole_backing_cache_bytes'],8192)
        self.assertEqual(v['objects'][0]['mapping_count'],2)
        with patch.object(h,'query_deleted_backing',return_value=dict(status='UNKNOWN',whole_backing_cache_bytes=None)):
            self.assertIsNone(h.query_deleted_backings([self.region])['whole_backing_cache_bytes'])
        self.assertEqual(h.query_deleted_backings([])['status'],'NOT_REQUESTED')

    def test_cachestat_abi_and_errno(self):
        class Call:
            def __call__(self,nr,fd,bounds,value,flags):
                self.args=(nr,fd,bounds._obj.off,bounds._obj.len,flags)
                value._obj.nr_cache=2;return 0
        call=Call()
        with patch.object(h.platform,'machine',return_value='x86_64'),patch.object(h.ctypes,'CDLL',return_value=SimpleNamespace(syscall=call)):
            self.assertEqual(h.cachestat(7)['nr_cache'],2)
        self.assertEqual(call.args,(451,7,0,0,0));self.assertEqual(ctypes.sizeof(h.CacheStat),40)
        def denied(*args):ctypes.set_errno(errno.ENOSYS);return -1
        denied.restype=None;denied.argtypes=None
        with patch.object(h.platform,'machine',return_value='x86_64'),patch.object(h.ctypes,'CDLL',return_value=SimpleNamespace(syscall=denied)):
            with self.assertRaises(OSError) as error:h.cachestat(7)
        self.assertEqual(error.exception.errno,errno.ENOSYS)

    def test_cachestat_explicit_range_abi_and_overflow(self):
        class Call:
            def __call__(self,nr,fd,bounds,value,flags):
                self.args=(nr,fd,bounds._obj.off,bounds._obj.len,flags)
                return 0
        call=Call()
        with patch.object(h.platform,'machine',return_value='x86_64'),patch.object(h.ctypes,'CDLL',return_value=SimpleNamespace(syscall=call)):
            h.cachestat(7,offset=4096,length=64*2**30)
            self.assertEqual(call.args,(451,7,4096,64*2**30,0))
        for off,length in ((-1,0),(0,-1),(0,2**64),(2**64-1,1),(False,1),(0,1.5)):
            with self.subTest(off=off,length=length),patch.object(h.ctypes,'CDLL') as libc:
                with self.assertRaises(ValueError):h.cachestat(7,offset=off,length=length)
                libc.assert_not_called()


class BoundedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.regions=[dict(device=1,inode=20)]
        self.good=dict(status='OBSERVED',objects=[dict(device=1,inode=20,status='OBSERVED',whole_backing_cache_bytes=8192)],
                       whole_backing_cache_bytes=8192,page_charge_owner='NOT_MEASURED')

    def worker(self,value=None,*,timeout=False,returncode=0):
        import json
        from unittest.mock import MagicMock
        worker=MagicMock();worker.pid=123;worker.returncode=returncode
        worker.__enter__.return_value=worker
        if timeout:
            worker.communicate.side_effect=[h.subprocess.TimeoutExpired('owned-worker',4),('','')]
            worker.kill.side_effect=lambda:setattr(worker,'returncode',-9)
        else:worker.communicate.return_value=(json.dumps(value or self.good),'')
        return worker

    def test_success_child_is_reaped_and_empty_input_does_not_spawn(self):
        worker=self.worker()
        with patch.object(h.subprocess,'Popen',return_value=worker) as create:
            value=h.query_deleted_backings_bounded(self.regions)
            self.assertEqual(value['whole_backing_cache_bytes'],8192);self.assertTrue(value['worker_reaped'])
            self.assertEqual(value['worker_pid'],123);self.assertEqual(create.call_args.args[0][-1],'--worker')
            self.assertEqual(worker.communicate.call_args.kwargs['timeout'],4)
        with patch.object(h.subprocess,'Popen') as create:
            value=h.query_deleted_backings_bounded([]);create.assert_not_called()
        self.assertEqual(value['status'],'NOT_REQUESTED');self.assertIsNone(value['whole_backing_cache_bytes'])

    def test_timeout_kills_only_owned_child_and_reaps(self):
        worker=self.worker(timeout=True)
        with patch.object(h.subprocess,'Popen',return_value=worker):value=h.query_deleted_backings_bounded(self.regions)
        worker.kill.assert_called_once_with();self.assertEqual(worker.communicate.call_count,2)
        self.assertEqual(value['status'],'TIMEOUT');self.assertEqual(value['worker_returncode'],-9)
        self.assertTrue(value['worker_reaped']);self.assertIsNone(value['whole_backing_cache_bytes'])

    def test_failed_mismatched_or_bad_total_worker_stays_unknown(self):
        import copy
        for kind in ('exit','identity','total'):
            value=copy.deepcopy(self.good)
            if kind=='identity':value['objects'][0]['inode']=21
            if kind=='total':value['whole_backing_cache_bytes']=4096
            worker=self.worker(value,returncode=1 if kind=='exit' else 0)
            with self.subTest(kind=kind),patch.object(h.subprocess,'Popen',return_value=worker):
                result=h.query_deleted_backings_bounded(self.regions)
            self.assertEqual(result['status'],'ERROR');self.assertIsNone(result['whole_backing_cache_bytes'])
            self.assertTrue(result['worker_reaped'])

    def test_partial_object_is_preserved_not_zero(self):
        value=dict(status='INCOMPLETE',objects=[dict(device=1,inode=20,status='PARTIAL_SWAP_ENTRIES',whole_backing_cache_bytes=None)],
                   whole_backing_cache_bytes=None,page_charge_owner='NOT_MEASURED')
        with patch.object(h.subprocess,'Popen',return_value=self.worker(value)):
            result=h.query_deleted_backings_bounded(self.regions)
        self.assertEqual(result['status'],'INCOMPLETE');self.assertIsNone(result['whole_backing_cache_bytes'])


if __name__=='__main__':unittest.main()
