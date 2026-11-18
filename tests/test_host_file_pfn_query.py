"""Temporary files and fake libc/probe/map calls only; no mincore, BPF or VM."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import host_file_pfn_query as h
import test_snapshot_bpf_batch as batch_fixtures


class HostFilePfnTests(unittest.TestCase):
    def test_folio_with_clear_mincore_bit_is_not_dropped_and_aliases_deduplicate(self):
        rows=np.array([[0,1,1,h.FOLIO|100],[1,0,1,h.FOLIO|101],
                       [2,1,1,h.FOLIO|100],[3,0,1,0]],dtype='<u8')
        r=h.decode_rows(rows)
        self.assertEqual(r['status'],'OBSERVED_NONATOMIC')
        self.assertEqual(r['state_counts'],dict(FOLIO_PRESENT_MINCORE_SET=2,FOLIO_PRESENT_MINCORE_CLEAR=1,NO_FOLIO_OBSERVED=1))
        self.assertEqual(r['mincore_set_pages'],2)
        self.assertEqual(r['confirmed_pfn_union_bytes'],8192)

    def test_missing_exception_invalid_and_null_conflict_are_unknown_not_cold(self):
        rows=np.array([[0,0,0,0],[1,1,0,0],[2,0,1,h.EXCEPTION|3],
                       [3,0,1,h.INVALID],[4,1,1,0],[5,1,1,17]],dtype='<u8')
        r=h.decode_rows(rows);self.assertEqual(r['unknown_pages'],6)
        self.assertEqual(r['state_counts']['MISSING_KERNEL_RETURN'],2)
        self.assertEqual(r['confirmed_pfn_union_bytes'],0)
        self.assertIsNone(r['complete_listed_pfn_union_bytes'])
        self.assertEqual(r['status'],'PARTIAL')

    def test_explicit_valid_pfn_zero_is_different_from_null_return(self):
        r=h.decode_rows(np.array([[0,0,1,h.FOLIO],[1,0,1,0]],dtype='<u8'))
        self.assertEqual(r['confirmed_pfn_union_bytes'],4096)
        self.assertEqual(r['state_counts']['NO_FOLIO_OBSERVED'],1)
        with self.assertRaises(ValueError):h.decode_rows(np.array([[0,0,0,5]],dtype='<u8'))
        with self.assertRaises(ValueError):h.decode_rows(np.array([[0,2,1,0]],dtype='<u8'))

    def test_range_bound_includes_all_scanned_pages_and_partial_last_page(self):
        n=h.MAX_CHUNK_PAGES
        self.assertEqual(list(h.chunks(2,n+1,(n+2)*4096+5)),[(2,n,n*4096),(n+2,1,5)])
        for first,pages,size in ((-1,1,4096),(0,0,4096),(0,2,4096),(True,1,8192),(0,1,0),
                                  (0,h.RAW_BUDGET//32,h.RAW_BUDGET*4096)):
            with self.subTest(args=(first,pages,size)),self.assertRaises(ValueError):list(h.chunks(first,pages,size))

    def test_program_binds_thread_syscall_range_inode_device_and_relative_map_key(self):
        s=h.probe_program(123)
        for guard in ('pid == 123','tid==$c.1','args->start==$c.2','args->len==$c.3',
                      '$m->host->i_ino==$c.6','$m->host->i_sb->s_dev==$c.5',
                      'arg1 >= $c.4','arg1 < $c.4+','@entries[$i-$c.4]',
                      '@active[tid],$encoded','delete(@pending[tid])','clear(@context)'):
            self.assertIn(guard,s)
        self.assertNotIn('bpf_probe_read_user',s)
        with self.assertRaises(ValueError):h.probe_program('1; anything')

    def test_program_context_and_file_offset_arithmetic_are_unsigned(self):
        s=h.probe_program(123)
        self.assertIn('@context[0]=('+','.join(['(uint64)0']*7)+')',s)
        self.assertIn('($c.3+(uint64)4095)/(uint64)4096',s)
        self.assertIn('@pending[tid]=arg1+(uint64)1',s)
        self.assertIn('$i=@pending[tid]-(uint64)1',s)
        self.assertNotIn('@context[0]=(0,0,0,0,0,0,0)',s)

    def synthetic(self,root,*,entries=None,bits=(1,0),mutate=None):
        path=root/'file';path.write_bytes(bytes(4*4096));out=root/'query';out.mkdir()
        q=h.HostFilePfnQuery.__new__(h.HostFilePfnQuery)
        q.path=path;q.directory=out;q.fd=os.open(path,os.O_RDONLY);self.addCleanup(lambda:os.close(q.fd))
        q.initial=h.file_identity(os.fstat(q.fd));q.raw_bytes=0;q.records=[];q.token=0
        q.pid=os.getpid();q.tid=threading.get_native_id();q._permission=Mock(return_value={'thread':{'basis':'HOST_CAP_FOWNER'}})
        q._context=Mock();q.tracer=Mock();q.tracer.poll.return_value=None
        q.batch=Mock();q.batch.read.return_value=entries if entries is not None else {0:h.FOLIO|7,1:h.FOLIO|8}
        q.batch.last_statistics=dict(returncode=-2)
        def mincore(address,length,vec):
            for i in range(len(vec)):vec[i]=bits[i%len(bits)]
            if mutate:mutate(path,q)
            return 0
        q.libc=SimpleNamespace(mmap=Mock(return_value=0x100000),mincore=Mock(side_effect=mincore),munmap=Mock(return_value=0))
        return q

    def test_query_publishes_exact_context_and_never_touches_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp),bits=(0xff,0x80));r=q.query_chunk(1,2)
            info=os.fstat(q.fd);kdev=(os.major(info.st_dev)<<20)|os.minor(info.st_dev)
            self.assertEqual(q._context.call_args_list[0].args[0],(1,q.tid,0x100000,8192,1,kdev,info.st_ino))
            self.assertEqual(q._context.call_args_list[-1].args[0],(0,)*7)
            q.libc.mmap.assert_called_once_with(None,8192,0,1,q.fd,4096)
            q.libc.munmap.assert_called_once_with(0x100000,8192)
            rows=np.fromfile(q.directory/r['binary'],dtype='<u8').reshape(-1,4)
            self.assertEqual(rows[:,:3].tolist(),[[1,1,1],[2,0,1]])
            self.assertEqual(r['raw_summary']['state_counts']['FOLIO_PRESENT_MINCORE_CLEAR'],1)
            self.assertEqual(q.raw_bytes,sum(p.stat().st_size for p in q.directory.iterdir()))
            self.assertEqual(set(r['stages']),{'mincore','map_read','decode'})

    def test_stale_tokens_and_missing_offsets_remain_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));lib=batch_fixtures.FakeBatch([(0,1,h.FOLIO|11),(1,0,h.FOLIO|22)])
            q.batch=h.BatchEntries(lib,123,batch_fixtures.INFO)
            r=q.query_chunk(0,2)
            self.assertEqual(r['status'],'PARTIAL');self.assertEqual(r['raw_summary']['unknown_pages'],1)
            self.assertEqual(r['accepted_pfn_union_bytes'],4096)
            lib.rows=[(0,2,h.FOLIO|33),(1,1,h.FOLIO|11)]
            r=q.query_chunk(3,1)
            rows=np.fromfile(q.directory/r['binary'],dtype='<u8').reshape(-1,4)
            self.assertEqual(rows.tolist(),[[3,1,1,h.FOLIO|33]])

    def test_other_thread_and_changed_file_rejected_before_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp))
            with patch.object(h.threading,'get_native_id',return_value=q.tid+1),self.assertRaisesRegex(ValueError,'thread'):q.query_chunk(0,1)
            q.libc.mmap.assert_not_called()
            q.path.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):q.query_chunk(0,1)
            q.libc.mmap.assert_not_called()

    def test_permission_failure_never_maps_or_returns_false_residency(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q._permission.side_effect=PermissionError('capability missing')
            with self.assertRaises(PermissionError):q.query_chunk(0,1)
            q.libc.mmap.assert_not_called();q._context.assert_not_called()

    def test_changed_size_mtime_and_alias_removal_retain_raw_but_no_physical_value(self):
        for mode in ('size','mtime','remove'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                def mutate(path,q):
                    if mode=='size':path.write_bytes(b'x')
                    elif mode=='mtime':os.utime(path,ns=(1,2))
                    else:path.unlink()
                q=self.synthetic(Path(tmp),mutate=mutate)
                with self.assertRaisesRegex(ValueError,'retained'):q.query_chunk(0,2)
                r=json.loads((q.directory/'chunk-0000.json').read_text())
                self.assertEqual(r['status'],'UNKNOWN_OBJECT_CHANGED');self.assertIsNone(r['accepted_pfn_union_bytes'])
                self.assertEqual((q.directory/'chunk-0000.bin').stat().st_size,64)

    def test_syscall_error_disarms_and_unmaps_without_batch_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q.libc.mincore.side_effect=None;q.libc.mincore.return_value=-1
            with self.assertRaises(OSError):q.query_chunk(0,1)
            self.assertEqual(q._context.call_args_list[-1].args[0],(0,)*7)
            q.libc.munmap.assert_called_once();q.batch.read.assert_not_called()
            self.assertEqual(list(q.directory.iterdir()),[])

    def test_disarm_or_probe_exit_cannot_be_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q._context.side_effect=[None,RuntimeError('disarm failed')]
            with self.assertRaisesRegex(RuntimeError,'disarm failed'):q.query_chunk(0,1)
            q.batch.read.assert_not_called();q.libc.munmap.assert_called_once()
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q.tracer.poll.side_effect=[None,0]
            with self.assertRaisesRegex(RuntimeError,'probe exited'):q.query_chunk(0,1)
            self.assertIsNone(q.records[0]['accepted_pfn_union_bytes'])

    def test_partial_map_read_cannot_be_used_or_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q.batch.read.side_effect=RuntimeError('incomplete BPF batch')
            with self.assertRaisesRegex(RuntimeError,'incomplete BPF batch'):q.query_chunk(0,1)
            self.assertEqual(q._context.call_args_list[-1].args[0],(0,)*7)
            q.libc.munmap.assert_called_once();q.batch.read.assert_called_once()
            self.assertEqual(q.records,[])

    def test_close_timeout_kills_only_owned_mock_process_and_closes_own_fds(self):
        q=h.HostFilePfnQuery.__new__(h.HostFilePfnQuery)
        q.fd=os.open(os.devnull,os.O_RDONLY);q.mapfds={'context':os.open(os.devnull,os.O_RDONLY)}
        fds=[q.fd,*q.mapfds.values()];q.tracer=Mock(pid=99999999,returncode=-9)
        q.tracer.poll.side_effect=[None,-9]
        q.tracer.wait.side_effect=[subprocess.TimeoutExpired('fake owned probe',10),-9]
        with patch.object(h.Path,'read_text',side_effect=FileNotFoundError('fake proc')):q.close()
        q.tracer.send_signal.assert_called_once_with(h.signal.SIGINT);q.tracer.kill.assert_called_once()
        self.assertEqual([c.kwargs for c in q.tracer.wait.call_args_list],[dict(timeout=10),dict(timeout=5)])
        self.assertTrue(q.cleanup['owned_tracer_exited']);self.assertIsNone(q.cleanup['tracer_cpu_before_stop_ns'])
        for fd in fds:
            with self.assertRaises(OSError):os.fstat(fd)

    def test_budget_checked_before_mapping_and_metadata_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));q.raw_bytes=h.RAW_BUDGET-32
            with self.assertRaisesRegex(ValueError,'budget'):q.query_chunk(0,1)
            q.libc.mmap.assert_not_called()
            q.raw_bytes=0
            with patch.object(h,'RAW_BUDGET',100),self.assertRaisesRegex(ValueError,'budget'):q.query_chunk(0,1)
            self.assertEqual(list(q.directory.iterdir()),[])

    def test_batch_record_budget_callback_runs_before_raw_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=self.synthetic(Path(tmp));callback=Mock(side_effect=RuntimeError('global cap'))
            with self.assertRaisesRegex(RuntimeError,'global cap'):q.query_chunk(0,2,before_write=callback)
            callback.assert_called_once();self.assertGreater(callback.call_args.args[0],64)
            self.assertEqual(list(q.directory.iterdir()),[]);self.assertEqual(q.records,[])

    @contextlib.contextmanager
    def constructor(self,root,*,magic=0xef53,permission=True):
        path=root/'file';path.write_bytes(bytes(4096));expected=(path.stat().st_dev,path.stat().st_ino)
        lib=batch_fixtures.FakeBatch();lib.bpf_map_get_fd_by_id=Mock();lib.bpf_map_update_elem=Mock()
        with patch.object(h,'filesystem_magic',return_value=magic),\
             patch.object(h,'permission_evidence',return_value={'host_root_cap_fowner':True}),\
             patch.object(h,'mincore_permission',return_value={'basis':'HOST_CAP_FOWNER' if permission else 'HOST_INODE_OWNER'}),\
             patch.object(h,'kernel_identity',return_value={'release':'fake'}),\
             patch.object(h.ctypes,'CDLL',side_effect=[Mock(),lib]),\
             patch.object(h.subprocess,'Popen') as start:
            yield path,expected,start

    def test_constructor_explicit_permission_identity_and_supported_filesystem(self):
        for mode in ('ok','cap','tmpfs','inode','symlink'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with self.constructor(root,magic=0x01021994 if mode=='tmpfs' else 0xef53,permission=mode!='cap') as (path,expected,start):
                    if mode=='symlink':(root/'alias').symlink_to(path);path=root/'alias'
                    if mode=='inode':expected=(expected[0],expected[1]+1)
                    if mode=='ok':
                        q=h.HostFilePfnQuery(path,root,expected_identity=expected);fd=q.fd;q.close()
                        with self.assertRaises(OSError):os.fstat(fd)
                    else:
                        with self.assertRaises((OSError,ValueError)):h.HostFilePfnQuery(path,root,expected_identity=expected)
                    start.assert_not_called()

    def test_attach_uses_only_own_maps_and_rejects_wrong_context_layout(self):
        for valid in (True,False):
            with self.subTest(valid=valid),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with self.constructor(root) as (path,expected,start):
                    q=h.HostFilePfnQuery(path,root,expected_identity=expected)
                    fds=[os.open(os.devnull,os.O_RDONLY),os.open(os.devnull,os.O_RDONLY)]
                    q.lib.bpf_map_get_fd_by_id=Mock(side_effect=fds);q.lib.bpf_map_update_elem=Mock(return_value=0)
                    def launch(*args,**kw):
                        kw['stdout'].write('READY\n');kw['stdout'].flush()
                        return SimpleNamespace(pid=99999999,poll=lambda:0,returncode=0)
                    start.side_effect=launch
                    infos=[SimpleNamespace(read_text=lambda:'map_id: 1'),SimpleNamespace(read_text=lambda:'map_id: 2')]
                    replies=[json.dumps(dict(batch_fixtures.INFO,name='AT_entries')),json.dumps(dict(type='hash',bytes_key=8,bytes_value=56 if valid else 8,flags=0,name='AT_context'))]
                    with patch.object(h.Path,'iterdir',return_value=infos),patch.object(h.subprocess,'check_output',side_effect=replies):
                        if valid:q.attach();self.assertEqual(q.batch.buffer_bytes,384);q.close()
                        else:
                            with self.assertRaisesRegex(ValueError,'context map'):q.attach()
                    for fd in fds:
                        with self.assertRaises(OSError):os.fstat(fd)

    def test_entry_requires_explicit_live_flag_without_collecting(self):
        args=['query','--file','/fake/file','--expected-device','1','--expected-inode','2',
              '--first-page','0','--pages','1','--output','/fake/output']
        with patch.object(sys,'argv',args),patch.object(h,'observe') as observe,contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):h.main()
        observe.assert_not_called()

    def test_observe_stops_after_first_failure_and_preserves_chunk_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=Mock();q.initial={'size':(h.MAX_CHUNK_PAGES+1)*4096};q.kernel={};q.permission={};q.magic=0xef53
            q.records=[];q.raw_bytes=0;q.cleanup={'owned_tracer_exited':True};q.map_info={};q.batch.buffer_bytes=0
            q.query_chunk.side_effect=RuntimeError('missing query context')
            with patch.object(h,'HostFilePfnQuery',return_value=q):
                r=h.observe('/fake/file',(1,2),0,h.MAX_CHUNK_PAGES+1,Path(tmp)/'new')
            self.assertEqual(r['status'],'FAIL');self.assertIn('missing query context',r['error'])
            self.assertEqual(r['failed_stage'],'query_chunk_0')
            q.query_chunk.assert_called_once();q.close.assert_called_once()
            self.assertIsNone(r['complete_file_physical_bytes'])

    def test_observe_keeps_partial_chunks_and_never_adds_a_whole_file_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            q=Mock();q.initial={'size':(h.MAX_CHUNK_PAGES+1)*4096};q.kernel={};q.permission={};q.magic=0xef53
            q.records=[];q.raw_bytes=0;q.cleanup={'owned_tracer_exited':True};q.map_info={};q.batch.buffer_bytes=384
            def chunk(first,pages):
                q.records.append(dict(status='PARTIAL' if first else 'OBSERVED_NONATOMIC',first_page=first,pages=pages))
            q.query_chunk.side_effect=chunk
            with patch.object(h,'HostFilePfnQuery',return_value=q):
                r=h.observe('/fake/file',(1,2),0,h.MAX_CHUNK_PAGES+1,Path(tmp)/'new')
            self.assertEqual(r['status'],'PARTIAL');self.assertEqual(q.query_chunk.call_count,2)
            self.assertIsNone(r['complete_file_physical_bytes']);self.assertIsNone(r['complete_sandbox_physical_bytes'])


if __name__=='__main__':unittest.main()
