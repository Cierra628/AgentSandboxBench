"""Each worker owns two existing PID-scoped tracers and its own output directory."""
import os
import sys
import multiprocessing as mp
import time
import traceback
from pathlib import Path
from contextlib import ExitStack
import numpy as np
from host_sandbox_monitor import Binding
from light_memory_round import check_vmm
from fast_cache_metadata import decode_array
from provider_slot_cache import ProviderCache
from snapshot_cache_residency import SourceTracer
from seven_restored_pages import Reader, DeviceRound, prime_tracers, dispatch, save
from run_seven_paused_serial import api, sweep

clock=time.monotonic_ns


class Publication(Reader):
    def _start_snapshot_queries(self,proc):
        # Keep start(), exporter, KVM/VMM binding and both publications unchanged.
        # Reader._start_snapshot_queries runs in the worker PID instead.
        pass


def receive(pipe):
    if not pipe.poll(100):
        raise TimeoutError('worker protocol timed out')
    return pipe.recv()


def worker(pipe,out,role,container):
    born=clock()
    try:
        with ExitStack() as stack:
            directory=out/('process-'+role);directory.mkdir()
            attaches={}
            def profile(frame,event,arg):
                if frame.f_code is SourceTracer.__init__.__code__ and event in ('call','return'):
                    source=frame.f_locals['key'][1]
                    attaches.setdefault(source,{})[event]=clock()
            sys.setprofile(profile)
            try:prime_tracers(directory,stack)
            finally:
                sys.setprofile(None)
                save(directory/'attach.json',attaches)
            pipe.send(('ready',dict(born=born,primed=clock(),pid=os.getpid(),attach=attaches)))
            vm,owner,device,proof,receipt,layer,before=receive(pipe)
            binding=Binding(container,out)
            check_vmm(vm,binding.cgroup)
            proc=Path('/proc')/str(vm['pid'])
            c=Reader.__new__(Reader)
            c.proof=proof;c.binding_row=receipt;c.directory=directory;c.lower_layer=layer
            c.pm=stack.enter_context((proc/'pagemap').open('rb',buffering=0))
            c._start_snapshot_queries(proc)
            stack.callback(c.backend.close);stack.callback(c.lower_query.close)
            provider=ProviderCache(out,Path('/proc')/str(owner['pid']),
                out/'shared-device'/('provider-'+role),binding=binding,vm=owner,device_path=device)
            stack.callback(provider.close,final_snapshot=False)
            group=DeviceRound(provider,[role])
            qs=[c.backend,c.lower_query]
            maps=[int(next(l.split()[1] for l in Path('/proc/self/fdinfo',str(q.mapfds['entries'])).read_text().splitlines() if l.startswith('map_id:'))) for q in qs]
            assert all(q.service.key[0]==os.getpid() for q in qs)
            pipe.send(('bound',dict(maps=maps,probes=[q.tracer.pid for q in qs])))
            assert receive(pipe)=='go'
            start=clock();cpu=time.thread_time_ns()
            check_vmm(vm,binding.cgroup);assert api(vm,'GET')=='Paused'
            assert len(list(Path('/proc/self/task').iterdir()))==1
            row=dict(stages={},role=role,sid=vm['sandbox_id'],pid=os.getpid(),maps=maps,start=start)
            def probe_cpu(q):return time.clock_gettime_ns(((~q.tracer.pid)<<3)|2)
            pc0=[probe_cpu(q) for q in qs]
            counters=[dispatch(q) for q in qs]
            left,h,d,l=sweep(c,role,before,group,row)
            count=sum(2*(1+np.count_nonzero(np.diff(u)!=1)) for table in (d,l)
                      if len(u:=np.unique(table[:,1][table[:,1]!=np.uint64(2**64-1)])))
            row['dispatch_delta']=[[v-old for v,old in zip(dispatch(q),prev)] for q,prev in zip(qs,counters)]
            assert count>0 and row['dispatch_delta']==[[count,count],[count,count]],row['dispatch_delta']
            assert api(vm,'GET')=='Paused'
            row['sweep_done']=clock()
            row['probe_cpu_ns']=[probe_cpu(q)-a for q,a in zip(qs,pc0)]
            pipe.send(('swept',None))
            after=receive(pipe)
            check_vmm(vm,binding.cgroup);assert api(vm,'GET')=='Running'
            row['account_start']=clock()
            row['pages']=group.match(role,before,after,h,d,l,left,decode_array(after))
            provider_set,pfn_set=group.matches[role]
            row.update(guest_before=left[0]['records'],guest_after=decode_array(after)[0]['records'],
                       pages_queried=sum(x['requested_pages'] for q in qs for x in q.query_statistics))
            row['write_start']=clock()
            (directory/'before.bin').write_bytes(before);(directory/'after.bin').write_bytes(after)
            np.savez(directory/'evidence.npz',host=h,direct=d,lower=l)
            np.savez(directory/'sets.npz',four=np.array(sorted(pfn_set),dtype='<u8'),
                     provider=np.array(sorted(provider_set),dtype='<u8'))
            save(directory/'frames.json',group.frames)
            row.update(write_end=clock(),cpu_ns=time.thread_time_ns()-cpu)
            save(directory/'result.json',row)
            pipe.send(('done',dict(end=clock(),cpu_ns=time.thread_time_ns()-cpu)))
    except BaseException:
        pipe.send(('error',traceback.format_exc()))
    finally:pipe.close()


class Workers:
    def __init__(self,out,roles,container,resources):
        self.out=out;self.roles=roles;self.ps={};self.pipes={};self.stats={}
        ctx=mp.get_context('fork')
        for role in roles:
            parent,child=ctx.Pipe()
            p=ctx.Process(target=worker,args=(child,out,role,container))
            start=clock();p.start();child.close()
            self.ps[role]=p;self.pipes[role]=parent;self.stats[role]=dict(launch=start)
            resources.callback(self.close,role)
        for role in roles:self.stats[role].update(self.get(role,'ready'))
    def close(self,r):
        p=self.ps[r];self.pipes[r].close()
        p.join(3)
        if p.is_alive():
            # Wake a worker waiting for its next command; let ExitStack detach probes.
            p.join(103)
        if p.is_alive():raise RuntimeError('worker cleanup timed out: '+r)
    def get(self,r,key):
        k,data=receive(self.pipes[r])
        if k!=key:raise RuntimeError(f'{r} expected {key}: {k} {data}')
        return data
    def bind(self,r,vm,owner,path,c,before):
        self.pipes[r].send((vm,owner,path,c.proof,c.binding_row,c.lower_layer,before))
        self.stats[r].update(self.get(r,'bound'))
    def query(self):
        maps=[m for s in self.stats.values() for m in s['maps']]
        assert len(set(maps))==2*len(self.roles)
        self.go=clock()
        for p in self.pipes.values():p.send('go')
        for r in self.roles:self.get(r,'swept')
    def account(self,r,after):
        self.pipes[r].send(after);self.stats[r].update(self.get(r,'done'))
    def merge(self,vms):
        import json
        begin=clock();pfn=set();device=set();rows={}
        for r in self.roles:
            directory=self.out/('process-'+r)
            row=json.loads((directory/'result.json').read_text())
            assert (row['role'],row['pid'],row['sid'])==(r,self.ps[r].pid,vms[r]['sandbox_id'])
            with np.load(directory/'sets.npz',allow_pickle=False) as data:
                assert row['pages']==len(data['four'])
                pfn.update(map(int,data['four']));device.update(map(int,data['provider']))
            rows[r]=row
        return dict(workers=self.stats,query_rows=rows,go=self.go,
                    merge_start=begin,merge_end=clock(),four_unique_pages=len(pfn),
                    provider_unique_pages=len(device),valid=len(rows))
