"""One paused round, with two shared tracer processes and two device-level provider frames per serial reader."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import traceback

import numpy as np
from fast_cache_metadata import decode_array
from host_file_monitor import mincore_file
from host_sandbox_monitor import UnixHTTP
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from provider_slot_cache import ProviderCache
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import (CONTENT, WRITE, ROLES, Reader, DeviceRound, prime_tracers,
                                 mapped_device, save, owned_memory_layer, service_openers, select_config)
from task_physical_cache import TaskPhysicalCache

clock = time.monotonic_ns
ROOT = Path(__file__).resolve().parents[1]


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


def main(*,process_mode=False):
    parser = argparse.ArgumentParser(description=__doc__)
    for k in ('container','config-home','service-url','output'):
        parser.add_argument('--'+k, required=True)
    for k in ('seven-restored','immediate-restores','provider-cache','restored-lower','unified-rounds'):
        parser.add_argument('--'+k, action='store_true', required=True)
    if process_mode:
        from paused_process_workers import Workers,Publication
        parser.add_argument('--workers',type=int,choices=(1,7),required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    assert out.parent.name == 'controller' and out.parent.parent.name == 'mount'
    out.mkdir()
    rows = {r:dict(status='PENDING',stages={}) for r in ROLES}
    result = dict(status='FAIL',scope='one paused round; seven independent restore windows; device-level shared provider',rows=rows)
    env = clean_environment(dict(os.environ,XDG_CONFIG_HOME=args.config_home))
    def cli(label,*argv,timeout=90):
        p = subprocess.run([ROOT/'runtime/bin/aenv',*argv],capture_output=True,env=env,timeout=timeout)
        if p.returncode:
            raise RuntimeError(f'{label}: {p.returncode}: {p.stderr.decode(errors="replace")[-1000:]}')
        return p.stdout.decode().strip()
    def failed(r, stage, exc):
        rows[r].update(status='UNKNOWN',failed_stage=stage,error=repr(exc))
    def stage(pool, roles, name, fn):
        futures = {pool.submit(fn,r):r for r in roles}
        for f in as_completed(futures):
            r=futures[f]
            try:f.result()
            except Exception as e:failed(r,name,e)
    def active():return [r for r in ROLES if rows[r]['status']=='PENDING']
    ids={}; vms={}; devices={}; readers={}; first={}; raw={}; paused=set(); installs={}
    try:
        with ExitStack() as resources:
            source=create_source(Path(args.config_home),args.service_url)
            cli('ready','exec',source,'true')
            cli('source write','exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
            snap='seven-paused-'+out.parents[2].name
            cli('save','snapshot','create',source,'--name',snap,timeout=150)
            resources.callback(cli,'delete snapshot','template','delete',snap)
            cli('delete source','delete',source)
            cap=TaskPhysicalCache(out,args.container,cli,directory=out/'kvm-capture')
            resources.callback(cap.close)
            binding=cap.binding
            if process_mode:
                selected=ROLES[:args.workers]
                workers=Workers(out,selected,args.container,resources)
                result['scope']='seven paused; independent PID-scoped readers; per-reader two provider frames'
            else:prime_tracers(out,resources)
            result['tracers_ready']=clock()
            gate=threading.Barrier(7)
            for r in ROLES:
                readers[r]=(Publication if process_mode else Reader)(out,args.container,cli,session='restored',session_name=r+'-pages',capture=cap,lower_cache=True)
            def install(r):
                c=readers[r]; sid=ids[r]
                cli('verify-rewrite-read','exec',sid,'python3','-c',CONTENT+'assert p.read_bytes()==data; '+WRITE)
                rows[r]['content_verified']=clock()
                c._start_guest_exporter(sid)
            with ThreadPoolExecutor(7) as pool, ThreadPoolExecutor(7) as prep:
                def restore(r):
                    gate.wait(timeout=15); rows[r]['restore_start']=clock()
                    try:ids[r]=cli('restore','start',snap,'--detach','--timeout','1800',timeout=150)
                    finally:rows[r]['restore_end']=clock()
                    installs[r]=prep.submit(install,r)
                stage(pool,ROLES,'restore',restore)
                for sid in ids.values():resources.callback(cli,'delete branch','delete',sid)
                assert len(set(ids.values()))==len(ids) and source not in ids.values()
                finish_capture=pool.submit(cap.ready)
                def bind(r):
                    vms[r]=bind_vmm(binding,ids[r],out,r)
                    devices[r]=mapped_device(vms[r])[:2]
                stage(pool,active(),'VMM binding',bind)
                envdir=out.parent.parent/'env'
                log=(envdir/'logs/server.log').read_text()
                configs=list((envdir/'snapshot-local-cache/runtime').glob('*/memory/image.json'))
                for owner in active():
                    path,identity=devices[owner]
                    try:select_config(log,ids[owner],path,configs,envdir)
                    except ValueError:continue
                    break
                else:raise RuntimeError('no shared device creation receipt')
                vm=vms[owner]; proc=Path('/proc')/str(vm['pid'])
                pin=resources.enter_context((proc/'root'/path.lstrip('/')).open('rb',buffering=0))
                st=os.fstat(pin.fileno()); assert (st.st_dev,st.st_ino,st.st_rdev)==identity
                shared=out/'shared-device';shared.mkdir()
                layer=owned_memory_layer(out,proc,shared/'lower-metadata',binding=binding,vm=vm,device_path=path)
                provider=ProviderCache(out,proc,shared/'provider',binding=binding,vm=vm,device_path=path)
                resources.callback(provider.close,final_snapshot=False)
                assert provider.segmented and {s['dev_id'] for s in provider.identity['slots']}=={int(path.removeprefix('/dev/ublkb'))}
                provider._check_owner()
                finish_capture.result()
                def prepare(r):
                    c=readers[r]; vm=vms[r]
                    assert devices[r]==(path,identity)
                    assert {v['pid'] for v in service_openers(binding,vm,layer.path)}=={provider.identity['host_pid']}
                    c.binding_row=dict(device_path=path,device_identity=identity,lower_device=layer.stat.st_dev,lower_inode=layer.stat.st_ino)
                    c.expected_binding=vm;c.lower_layer=layer
                    installs[r].result()
                    c.start(ids[r],coordinated=True)
                    rows[r]['ready']=clock()
                    first[r],info=c._snapshot()
                    rows[r].update(first_end=clock(),first_publication=info)
                    if process_mode and r in selected:
                        workers.bind(r,vm,vms[owner],path,c,first[r])
                stage(pool,active(),'prepare / first export',prepare)
                for c in readers.values():
                    for name in ('memory','pm','backend','lower_query'):
                        obj=getattr(c,name,None)
                        if obj is not None:resources.callback(obj.close)
                for name in (() if process_mode else ('backend','lower_query')):
                    qs=[getattr(readers[r],name) for r in active()]
                    if qs:
                        assert len({q.bank for q in qs})==len(qs) and all(q.tracer is qs[0].tracer and q.tracer.poll() is None for q in qs)
                        result[name+'_tracer_pid']=qs[0].tracer.pid
                def pause(r):
                    vm=vms[r];check_vmm(vm,binding.cgroup)
                    rows[r]['pause_request']=clock()
                    paused.add(r)
                    api(vm,'PATCH','Paused');rows[r]['pause_end']=clock()
                    assert api(vm,'GET')=='Paused'
                def resume(r):
                    vm=vms[r];check_vmm(vm,binding.cgroup)
                    if api(vm,'GET')=='Paused':api(vm,'PATCH','Resumed')
                    assert api(vm,'GET')=='Running'
                    rows[r]['resume_end']=clock();paused.discard(r)
                groups = {r: DeviceRound(provider, [r]) for r in ROLES}
                def lower_bitmap(tag):
                    start = clock()
                    v = mincore_file(layer.path, include_resident_indices=True)
                    end = clock()
                    assert v['stable'] and (v['device'],v['inode']) == (layer.stat.st_dev,layer.stat.st_ino)
                    bits = np.zeros(v['pages'],dtype=np.uint8)
                    bits[v['resident_indices']] = 1
                    (out/(tag+'.bits')).write_bytes(np.packbits(bits,bitorder='little').tobytes())
                    result[tag] = dict(start=start,end=end,pages=v['pages'],resident=v['resident_pages'],inode=v['inode'])
                if len(active()) != 7: raise RuntimeError('incomplete preparation; no serial round')
                try:
                    stage(pool,active(),'pause',pause)
                    if len(active()) != 7: raise RuntimeError('not all seven paused')
                    if process_mode:workers.query()
                    else:lower_bitmap('lower-before')
                    for r in (() if process_mode else ROLES):
                        try:
                            assert api(vms[r],'GET')=='Paused'
                            raw[r]=sweep(readers[r],r,first[r],groups[r],rows[r])
                            assert api(vms[r],'GET')=='Paused'
                        except Exception as exc:
                            failed(r,'serial sweep or paused-state check',exc)
                            raise
                    if not process_mode:lower_bitmap('lower-after')
                finally:
                    stage(pool,list(paused),'resume',resume)
                def account(r):
                    c=readers[r]
                    after,info=c._snapshot()
                    if process_mode:
                        if r in selected:
                            workers.account(r,after)
                            rows[r]['status']='OBSERVED'
                        else:rows[r]['status']='NOT_QUERIED'
                        return
                    left,h,d,l=raw[r];right=decode_array(after)
                    pages=groups[r].match(r,first[r],after,h,d,l,left,right)
                    rows[r].update(status='OBSERVED',pages=pages,guest_before=left[0]['records'],guest_after=right[0]['records'])
                    for name,value in [('before',first[r]),('after',after),('host',h),('direct',d),('lower',l)]:
                        (c.directory/(name+'.bin')).write_bytes(value if isinstance(value,bytes) else value.tobytes())
                stage(pool,active(),'identity recheck / account',account)
                if process_mode:
                    if any(rows[r]['status']=='UNKNOWN' for r in ROLES):
                        raise RuntimeError('incomplete identity check; not merging')
                    result.update(workers.merge(vms))
                else:save(out/'provider-frames.json',{r:g.frames for r,g in groups.items()})
                valid=[r for r in ROLES if rows[r]['status']=='OBSERVED']
                if not process_mode:result.update(valid=len(valid),four_unique_pages=len(set().union(*(groups[r].matches[r][1] for r in valid))) if valid else None,
                              provider_unique_pages=len(set().union(*(groups[r].matches[r][0] for r in valid))) if valid else None)
        result['status']='PASS' if result['valid']==(args.workers if process_mode else 7) else 'INCOMPLETE'
    except Exception:
        result['error']=traceback.format_exc()
    finally:
        save(out/'result.json',result)
    return int(result['status']!='PASS')


if __name__=='__main__':
    raise SystemExit(main())
