"""Replay syscall/return events, with real query(), native TIDs and fake maps/libc."""
import ctypes
import os
from pathlib import Path
import errno
import json
import tempfile
import sys
import threading
import unittest
from unittest.mock import Mock,patch
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'tests/fixtures')]
import snapshot_cache_residency as fixed
import snapshot_gap_one as old  # identical legacy scalar query and probe
from snapshot_cache_residency import PAGE, PRESENT

U64=ctypes.c_uint64

class Probe:
    """One attachment. Route at syscall entry and at the return-probe write."""
    def __init__(self, legacy=False):
        self.legacy=legacy
        self.owners={};self.active={};self.tables={};self.writes=[]
        if legacy:
            src=Path(old.__file__).read_text()
            assert '/pid == TARGET/ { @active[tid]=1; }' in src
            assert '@entries[@idx[tid]]=(@token[0],$pfn);' in src
        else:
            src=fixed._probe_program(os.getpid())
            assert '$r=@token[(uint64)tid]' in src and 'args->len <= $r.2-args->start' in src
            for bank in range(1,8):
                assert f'if ($bank == {bank}) {{ @entries{bank}[@idx[tid]]=(@active[tid],$pfn); }}' in src
    def enter(self,pid,tid,addr,length=PAGE):
        if pid!=os.getpid():return
        if self.legacy:self.active[tid]=(0,1);return
        r=self.owners.get(tid)
        if r and r[1]<=addr<r[2] and length<=r[2]-addr:self.active[tid]=(r[3],r[0])
    def ret(self,tid,offset,pfn):
        if tid not in self.active:return
        bank,token=self.active[tid]
        if self.legacy:token=self.owners[0][0]
        self.tables.setdefault(bank,{})[offset]=(token,pfn)
        self.writes.append((bank,tid))
    def exit(self,tid):
        self.active.pop(tid,None)

def reader(cls, probe, fd, address, mincore, bank=1,length=16*PAGE):
    q=cls.__new__(cls)
    q.fd=fd;st=os.fstat(fd);q.identity=(st.st_dev,st.st_ino,st.st_rdev)
    q.address=address;q.limit=address+length;q.bank=bank;q.tracer=SimpleNamespace(poll=lambda:None)
    q.mapfds={'token':-1,'entries':bank};q.token=0;q.query_statistics=[];q.query_costs=[]
    q.query_lock=threading.Lock();q.batch_entries=None
    def word(p):return ctypes.cast(p,ctypes.POINTER(U64))[0]
    def update(fd,key,p,flags):
        k=word(key);v=ctypes.cast(p,ctypes.POINTER(U64))
        if not probe.legacy:
            assert flags==1 and k==threading.get_native_id()
            if k in probe.owners:return -1
        probe.owners[k]=tuple(v[:1 if probe.legacy else 4]);return 0
    def disarm(fd,p):
        del probe.owners[word(p)];return 0
    def lookup(fd,key,v):
        table=probe.tables.get(0 if probe.legacy else fd,{})
        if word(key) not in table:return -1
        v[0],v[1]=table[word(key)];return 0
    q.lib=SimpleNamespace(bpf_map_update_elem=update,bpf_map_delete_elem=disarm,bpf_map_lookup_elem=lookup)
    q.libc=SimpleNamespace(mincore=mincore)
    return q

class IsolationTests(unittest.TestCase):
    def exercise(self,legacy):
        bases=[32*PAGE,96*PAGE];pid=os.getpid();offset=7;pfns=[0xa11,0xb22]
        probes=[Probe(True),Probe(True)] if legacy else [Probe()]
        first=threading.Event();both=threading.Event();a_returned=threading.Event();b_returned=threading.Event()
        tids={}
        def syscall(i,addr,length,vec):
            t=tids[i]=threading.get_native_id()
            assert addr==bases[i]+offset*PAGE
            for p in probes:p.enter(pid,t,addr)
            if i==0:first.set();assert both.wait(3)
            else:both.set();assert a_returned.wait(3)
            for p in probes:p.ret(t,offset,pfns[i]);p.exit(t)
            if i==0:a_returned.set();assert b_returned.wait(3)
            else:b_returned.set()
            vec[0]=1;return 0
        with open('/dev/null','rb') as a,open('/dev/zero','rb') as b,ThreadPoolExecutor(2) as pool:
            self.assertNotEqual(os.fstat(a.fileno()).st_rdev,os.fstat(b.fileno()).st_rdev)
            readers=[reader(old.SnapshotCache if legacy else fixed.SnapshotCache,probes[i if legacy else 0],f.fileno(),bases[i],
                       lambda *args,i=i:syscall(i,*args),bank=i+1) for i,f in enumerate((a,b))]
            f=pool.submit(readers[0].query,np.array([offset],dtype='<u8'));self.assertTrue(first.wait(3))
            g=pool.submit(readers[1].query,np.array([offset],dtype='<u8'))
            got=[int(h.result()[0]) for h in (f,g)]
        self.assertNotEqual(tids[0],tids[1])
        self.assertEqual(got,[PRESENT|p for p in ([pfns[1]]*2 if legacy else pfns)])
        self.assertEqual([len(p.writes) for p in probes],[2,2] if legacy else [2])
        if not legacy:self.assertEqual(probes[0].writes,[(1,tids[0]),(2,tids[1])])
    def test_legacy_collision(self):self.exercise(True)
    def test_two_devices_two_threads_no_cross_writes(self):self.exercise(False)
    def test_wrong_tid_same_address_and_other_layer_same_tid(self):
        p=Probe();p.owners[17]=(5,32*PAGE,48*PAGE,1)
        for tid,addr in [(18,33*PAGE),(17,96*PAGE)]:
            p.enter(os.getpid(),tid,addr);p.ret(tid,7,99);p.exit(tid)
        p.enter(os.getpid(),17,47*PAGE,2*PAGE);p.ret(17,7,99)
        self.assertEqual(p.tables,{})
    def test_failure_disarms_and_releases_reader(self):
        p=Probe()
        with open('/dev/null','rb') as f:
            q=reader(fixed.SnapshotCache,p,f.fileno(),32*PAGE,lambda *args:-1)
            with self.assertRaises(AssertionError):q.query(np.array([7],dtype='<u8'))
            self.assertEqual(p.owners,{});self.assertFalse(q.query_lock.locked())
            with q.query_lock,self.assertRaisesRegex(RuntimeError,'overlapping'):q.query(np.array([],dtype='<u8'))

    def test_failed_disarm_detaches_the_shared_probe(self):
        p=Probe()
        with open('/dev/null','rb') as f:
            q=reader(fixed.SnapshotCache,p,f.fileno(),32*PAGE,lambda *args:0)
            q.lib.bpf_map_delete_elem=lambda *args:-1
            q.service=SimpleNamespace(lock=threading.Lock(),detach=Mock())
            with self.assertRaisesRegex(RuntimeError,'shared probe detached'):
                q.query(np.array([],dtype='<u8'))
            q.service.detach.assert_called_once()

class SourceLifetimeTests(unittest.TestCase):
    def test_separate_sources_no_extra_tracer_at_capacity_and_clean_reuse(self):
        key=(os.getpid(),'lower')
        shared=fixed.SourceTracer.__new__(fixed.SourceTracer)
        shared.proc=SimpleNamespace(poll=lambda:None)
        shared.used=set();shared.leased=set();shared.fds={'entries1':42}
        def first(fd,ignored,k):
            self.assertEqual(fd,42)
            if stale:
                ctypes.cast(k,ctypes.POINTER(U64))[0]=next(iter(stale));return 0
            ctypes.set_errno(errno.ENOENT);return -1
        def delete(fd,k):
            stale.remove(ctypes.cast(k,ctypes.POINTER(U64))[0]);return 0
        lib=SimpleNamespace(bpf_map_get_next_key=first,bpf_map_delete_elem=delete)
        stale={1,4}
        with patch.dict(fixed.SourceTracer.sources,{key:shared},clear=True):
            for i in range(1,8):
                service,bank=fixed.SourceTracer.acquire('lower',None,lib)
                self.assertIs(service,shared);self.assertEqual(bank,i)
            with self.assertRaisesRegex(RuntimeError,'7 active readers'):
                fixed.SourceTracer.acquire('lower',None,lib)
            shared.leased.remove(1)
            self.assertEqual(fixed.SourceTracer.acquire('lower',None,lib),(shared,1))
            self.assertFalse(stale)
            with patch.object(fixed.SourceTracer,'__init__',return_value=None) as spawn:
                with patch.object(fixed.SourceTracer,'leased',set(),create=True),patch.object(fixed.SourceTracer,'used',set(),create=True):
                    backend,_=fixed.SourceTracer.acquire('backend',None,lib)
                spawn.assert_called_once()
                self.assertIsNot(backend,shared)

    def test_last_close_publishes_real_failure_code_to_both_receipts(self):
        with tempfile.TemporaryDirectory() as root:
            a,b=(Path(root)/name for name in ('a','b'))
            s=fixed.SourceTracer.__new__(fixed.SourceTracer);s.key=(100,'lower')
            s.leased={1,2};s.pending_receipts=[];s.proc=Mock(returncode=None,pid=321)
            s.proc.poll.return_value=None
            s.stop=lambda:setattr(s.proc,'returncode',7)
            with patch.dict(fixed.SourceTracer.sources,{s.key:s},clear=True):
                s.release(1,a,{'queries':[1]})
                self.assertIsNone(json.loads(a.read_text())['returncode'])
                with patch.object(fixed.time,'clock_gettime_ns',return_value=321):s.release(2,b,{'queries':[2]})
                self.assertNotIn(s.key,fixed.SourceTracer.sources)
            x,y=[json.loads(p.read_text()) for p in (a,b)]
            self.assertEqual([x['returncode'],y['returncode']],[7,7])
            self.assertEqual([x['tracer_cpu_ns'],y['tracer_cpu_ns']],[0,321])
            self.assertEqual([x['queries'],y['queries']],[[1],[2]])
