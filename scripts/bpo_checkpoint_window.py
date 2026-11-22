"""One real BPO boundary: the source and both restored cohorts remain alive."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import struct
import time
import numpy as np
from fast_cache_metadata import decode_array, join_arrays, matching_tags, PHYSICAL_MASK, MASK, PRESENT
from guest_cache_exporter import REQUEST, STOP
from host_sandbox_monitor import UnixHTTP
from light_memory_round import check_vmm
from live_task_cache import LiveTaskCache
from seven_restored_pages import Reader, DeviceRound, prime_tracers, mapped_device
from snapshot_layer_metadata import owned_memory_layer
from snapshot_lower_owner import select_config, service_openers
from provider_slot_cache import ProviderCache, account_observed_provider
from task_physical_cache import TaskPhysicalCache

clock=time.monotonic_ns
def api(vm, method, state=None):
    conn = UnixHTTP(vm['api_socket'])
    conn.timeout = 5
    try:
        conn.request(method, '/vm' if state else '/', json.dumps({'state': state}) if state else None,
                     {'Content-Type': 'application/json'})
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status != (204 if state else 200):
            raise RuntimeError(f'{method} {state}: HTTP {resp.status}')
        return json.loads(raw)['state'] if raw else None
    finally:
        conn.close()


def sweep(c, role, before, group, row):
    def read(name, fn, *args):
        start = clock()
        try:
            return fn(*args)
        finally:
            row['stages'][name] = [start, clock()]
    left = decode_array(before)
    pfns = left[1][left[1][:,1] != 0, 0]
    a = read('vmm_first', c._bulk)
    c.backend.regions = read('maps_before', c.backend.ram_regions)
    off = read('direct_offsets', c.backend.offsets, pfns)
    d1 = read('direct_first', c.backend.query, off)
    lo = read('lower_offsets', c.lower_layer.offsets, off)
    l1 = read('lower_first', c.lower_query.query, lo)
    read('provider_first', group.frame, role, 0)
    b = read('vmm_middle', c._bulk)
    read('provider_last', group.frame, role, 1)
    d2 = read('direct_last', c.backend.query, off)
    l2 = read('lower_last', c.lower_query.query, lo)
    z = read('vmm_last', c._bulk)
    c.backend.regions = read('maps_after', c.backend.ram_regions)
    changed = c.backend.offsets(pfns) != off
    d2[changed] = 0
    l2[changed] = 0
    row.update(host_start=row['stages']['vmm_first'][0], host_end=row['stages']['maps_after'][1],
               direct_queries=c.backend.query_statistics, lower_queries=c.lower_query.query_statistics)
    host = np.column_stack((pfns, *(c._entries(pfns,x) for x in (a,b,z))))
    return left, host, np.column_stack((pfns,off,d1,d2)), np.column_stack((pfns,lo,l1,l2))


class Window:
    def __init__(self,out,container,cli,binding):
        self.out,self.container,self.cli,self.binding=out,container,cli,binding
        self.d=out/'checkpoint-window';self.d.mkdir()
        self.stack=ExitStack()
        try:
            self.capture=TaskPhysicalCache(out,container,cli,directory=self.d/'capture')
            self.stack.callback(self.capture.close)
            prime_tracers(self.d,self.stack)
            self.ready=clock()
        except BaseException:
            self.close()
            raise

    def close(self):
        self.stack.close()

    def measure(self,ids,vms):
        roles=list(ids); assert len(roles)==7 and roles[0]=='backbone' and set(roles)==set(vms)
        records={r:{'stages':{},'sid':ids[r]} for r in roles}; cs={}; devices={}; owners={}; first={}; raw={}; paused=set()
        result=dict(boundary='all-restores-ready-before-any-suffix',tracers_ready=self.ready,rows=records,status='INCOMPLETE')
        def persist(): (self.d/'window.json').write_text(json.dumps(result,indent=2)+'\n')
        self.capture.ready()
        for r in roles:
            cls=LiveTaskCache if r=='backbone' else Reader
            cs[r]=cls(self.out,self.container,self.cli,session_name='window-'+r,session='parent' if r=='backbone' else 'restored',
                expected_binding=vms[r],capture=self.capture,lower_cache=r!='backbone')
            if r!='backbone':devices[r]=mapped_device(vms[r])[:2]
        env=self.out.parent.parent/'env';log=(env/'logs/server.log').read_text()
        configs=list((env/'snapshot-local-cache/runtime').glob('*/memory/image.json'))
        try:
            for r,(path,ident) in devices.items():
                if ident in owners:continue
                vm=vms[r];proc=Path('/proc')/str(vm['pid'])
                try:select_config(log,ids[r],path,configs,env)
                except ValueError:continue
                d=self.d/r;d.mkdir()
                pin=self.stack.enter_context((proc/'root'/path.lstrip('/')).open('rb',buffering=0))
                st=os.fstat(pin.fileno());assert (st.st_dev,st.st_ino,st.st_rdev)==ident
                layer=owned_memory_layer(self.out,proc,d/'lower-metadata',binding=self.binding,vm=vm,device_path=path)
                provider=ProviderCache(self.out,proc,d/'provider',binding=self.binding,vm=vm,device_path=path)
                self.stack.callback(provider.close,final_snapshot=False)
                assert provider.segmented
                owners[ident]=(layer,provider)
            groups={}
            for r,(path,ident) in devices.items():
                layer,provider=owners[ident]
                assert {h['pid'] for h in service_openers(self.binding,vms[r],layer.path)}=={provider.identity['host_pid']}
                c=cs[r];c.lower_layer=layer
                c.binding_row=dict(device_path=path,device_identity=ident,lower_device=layer.stat.st_dev,lower_inode=layer.stat.st_ino)
                records[r].update(device=path,lower_inode=layer.stat.st_ino,provider_pid=provider.identity['host_pid'])
                groups[r]=DeviceRound(provider,[r])
            def export(r):
                c=cs[r];rec=records[r];rec['prepare_start']=clock();c.start(ids[r],coordinated=True)
                rec['export_start']=clock();first[r],info=c._snapshot();rec['export_end']=clock()
            def all_roles(pool,fn,rs):
                fs=[pool.submit(fn,r) for r in rs]
                errs=[]
                for f in fs:
                    try:f.result()
                    except Exception as e:errs.append(e)
                if errs:raise errs[0]
            def pause(r):
                check_vmm(vms[r],self.binding.cgroup)
                records[r]['pause_start']=clock();paused.add(r)
                api(vms[r],'PATCH','Paused');records[r]['pause_end']=clock();assert api(vms[r],'GET')=='Paused'
            def resume(r):
                check_vmm(vms[r],self.binding.cgroup)
                if api(vms[r],'GET')=='Paused':api(vms[r],'PATCH','Resumed')
                assert api(vms[r],'GET')=='Running';records[r]['resume_end']=clock();paused.remove(r)
            with ThreadPoolExecutor(7) as pool:
                all_roles(pool,export,roles)
                try:
                    all_roles(pool,pause,roles)
                    for r in roles:
                        rec=records[r];c=cs[r];assert api(vms[r],'GET')=='Paused'
                        if r=='backbone':
                            left=decode_array(first[r]);p=left[1][left[1][:,1]!=0,0]
                            rec['host_start']=clock();h=np.column_stack((p,*(c._entries(p,c._bulk()) for _ in range(3))))
                            rec['host_end']=clock();raw[r]=(left,h)
                        else:raw[r]=sweep(c,r,first[r],groups[r],rec)
                        assert api(vms[r],'GET')=='Paused'
                finally:all_roles(pool,resume,list(paused))
                def finish(r):
                    rec=records[r];c=cs[r];rec['recheck_start']=clock();after,_=c._snapshot();rec['recheck_end']=clock()
                    os.pwrite(c.memory.fileno(),struct.pack('<Q',STOP),c.addresses[0]+REQUEST)
                    right=decode_array(after);left,h=raw[r][:2]
                    if r=='backbone':
                        m=join_arrays(left,right,h)
                        wanted=left[1][left[1][:,1]!=0]
                        accept=(matching_tags(right[1],wanted[:,0])==wanted[:,1]) & ((((h[:,1]^h[:,2])|(h[:,2]^h[:,3]))&PHYSICAL_MASK)==0) & ((h[:,1]&PRESENT)!=0)
                        u=set(map(int,h[accept,1]&MASK));assert len(u)*4096==m['host_confirmed_cache_bytes']
                        unknown=m['unconfirmed_after_pages'];rec['nonresident_guest_pages']=m['host_nonresident_pages']
                        rec['swapped_guest_pages']=m['host_swapped_pages']
                        rec['scope']='VMM PTE only; source, not restored'
                    else:
                        d,l=raw[r][2:];g=groups[r]
                        m,u=account_observed_provider(first[r],after,h,d,l,*g.frames,decoded=(left,right))
                        unknown=m['host_four_segment_unconfirmed_guest_capacity_bytes']//4096
                        np.savez(c.directory/'backings.npz',direct=d,lower=l)
                        (c.directory/'frames.json').write_text(json.dumps(g.frames))
                        rec['scope']='four-source current PFN union'
                    rec.update(pages=len(u),unknown_guest_pages=unknown,guest_before=left[0]['records'],guest_after=right[0]['records'])
                    np.save(c.directory/'host.npy',h)
                    (c.directory/'before.bin').write_bytes(first[r]);(c.directory/'after.bin').write_bytes(after)
                    return u
                sets=list(pool.map(finish,roles));result['unique_pages']=len(set.union(*sets))
            result['status']='OBSERVED'
        except Exception as exc:
            result["error"]=repr(exc)
            raise
        finally:
            persist()
            for c in cs.values():
                for key in ('lower_query','backend','memory','pm'):
                    obj=getattr(c,key,None)
                    if obj is not None:obj.close()
        return dict(status='OBSERVED',file='checkpoint-window/window.json',unique_pages=result['unique_pages'])

