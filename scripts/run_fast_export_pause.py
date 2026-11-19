"""One paused host-only four-source sweep between two guest publications."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from host_file_monitor import PAGE
import numpy as np
import struct
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
import threading
from host_file_monitor import mincore_file
from host_schedule_cache_layers import capacity
from seven_restored_pages import Reader, prime_tracers
from provider_slot_cache import ProviderCache
from snapshot_layer_metadata import owned_memory_layer
from snapshot_lower_owner import select_config, service_openers
from fast_cache_metadata import decode_array, join_arrays
from guest_cache_exporter import REQUEST, STOP
from provider_slot_cache import account_observed_provider
from host_sandbox_monitor import Binding, UnixHTTP
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE, mapped_device


clock = time.monotonic_ns
ROOT = Path(__file__).resolve().parents[1]


def api(vm, method, endpoint, state=None):
    conn = UnixHTTP(vm['api_socket'])
    conn.timeout = 5
    try:
        conn.request(method, endpoint, json.dumps({'state': state}) if state else None,
                     {'Content-Type': 'application/json'})
        response = conn.getresponse()
        data = response.read()
        if response.status != (204 if state else 200):
            raise RuntimeError(f'{method} {endpoint}: HTTP {response.status}')
        return json.loads(data)['state'] if data else None
    finally:
        conn.close()



def bind_targets(out,binding,vm,stack):
    dev,ident,_=mapped_device(vm)
    proc=Path('/proc')/str(vm['pid'])
    path=proc/'root'/dev.lstrip('/')
    fd=stack.enter_context(path.open('rb',buffering=0))
    assert (os.fstat(fd.fileno()).st_dev,os.fstat(fd.fileno()).st_ino,os.fstat(fd.fileno()).st_rdev)==ident
    env=out.parent.parent/'env'
    cfg=select_config((env/'logs/server.log').read_text(),vm['sandbox_id'],dev,
                      list((env/'snapshot-local-cache/runtime').glob('*/memory/image.json')),env)
    image=json.loads(cfg.read_text());ref,=image['lowers']
    assert not image.get('upper',{}).get('data')
    lower=env/ref['file'].removeprefix('/workspace/env/')
    assert lower.resolve().is_relative_to((env/'snapshot-store/repository/managed-layers').resolve())
    owner=service_openers(binding,vm,lower)[0]
    lf=stack.enter_context(lower.open('rb',buffering=0));ls=os.fstat(lf.fileno())
    assert (ls.st_dev,ls.st_ino,ls.st_size)==(owner['device'],owner['inode'],ref['size'])
    return dev,ident,path,capacity(fd.fileno()),lower,lf,ls


def sample_curve(targets,result,stop):
    dev,ident,path,length,lower,lf,ls=targets
    t0=result['events']['restore_return'];deadline=t0+2_000_000_000
    target=t0
    try:
        while not stop.is_set():
            if stop.wait(max(0,target-clock())/1e9):break
            row=dict(planned_ns=target);result['curve'].append(row)
            for key,file,options in [('direct',path,dict(block_identity=ident,block_length=length)),
                                     ('lower',lower,dict(file_descriptor=lf.fileno()))]:
                a=clock();r=mincore_file(file,**options);b=clock()
                assert r['stable']
                if key=='lower':assert (r['inode'],r['device'],r['size'],r['mtime_ns'])==(ls.st_ino,ls.st_dev,ls.st_size,ls.st_mtime_ns)
                row[key]=dict(start_ns=a,end_ns=b,pages=r['pages'],resident=r['resident_pages'])
            if target>=deadline or clock()>=deadline:break
            target=min(deadline,max(target+100_000_000,clock()))
    except Exception:
        result['curve_failure_ns']=clock()
        stop.set()
        raise


def collect(out,c,sid,vm,binding,result,cli,curve):
    c.expected_binding=vm
    c.start(sid,coordinated=True)
    assert c.provider.segmented
    ev=result['events']; stages=result['stages']={}
    def step(k,fn,*args):
        t=clock()
        try:return fn(*args)
        finally: stages[k]=[t,clock()]
    if curve.done():curve.result()
    import live_task_cache
    class RequestClock:
        def __getattr__(self,k):return getattr(os,k)
        def pwrite(self,*args):
            ev['request_write_start']=clock()
            n=os.pwrite(*args)
            ev['request_write_return']=clock()
            return n
    original=live_task_cache.os
    live_task_cache.os=RequestClock()
    try:
        ev['can_request']=clock()
        before,info=step('guest_before',c._snapshot)
    finally:live_task_cache.os=original
    ev['pause_request']=clock()
    try:
        api(vm,'PATCH','/vm','Paused')
        ev['pause_response']=clock()
        assert api(vm,'GET','/')=='Paused'
        check_vmm(vm,binding.cgroup)
        decoded=decode_array(before); pfns=decoded[1][decoded[1][:,1]!=0,0]
        assert len(pfns)>0
        result['selected']=len(pfns)
        a=step('vmm_first',c._bulk)
        c.backend.regions=step('maps_before',c.backend.ram_regions)
        off=step('direct_offsets_before',c.backend.offsets,pfns)
        d1=step('direct_first',c.backend.query,off)
        lo=step('lower_offsets',c.lower_layer.offsets,off)
        l1=step('lower_first',c.lower_query.query,lo)
        f1=step('provider_first',c.provider.snapshot)
        b=step('vmm_middle',c._bulk)
        f2=step('provider_last',c.provider.snapshot)
        d2=step('direct_last',c.backend.query,off)
        l2=step('lower_last',c.lower_query.query,lo)
        z=step('vmm_last',c._bulk)
        c.backend.regions=step('maps_after',c.backend.ram_regions)
        # Last host read ends at maps_after. Arithmetic is not an additional read.
        ev['host_start'],ev['host_end']=stages['vmm_first'][0],stages['maps_after'][1]
        curve.result(timeout=8)
        check_vmm(vm,binding.cgroup)
        assert api(vm,'GET','/')=='Paused'
    finally:
        check_vmm(vm,binding.cgroup)
        if api(vm,'GET','/')=='Paused':api(vm,'PATCH','/vm','Resumed')
        assert api(vm,'GET','/')=='Running'
        ev['resumed']=clock()
    after,info2=step('guest_after',c._snapshot)
    decoded2=decode_array(after)
    off2=c.backend.offsets(pfns)
    d2[off2!=off]=0; l2[off2!=off]=0
    host=np.column_stack((pfns,*(c._entries(pfns,v) for v in (a,b,z))))
    direct=np.column_stack((pfns,off,d1,d2)); lower=np.column_stack((pfns,lo,l1,l2))
    result['identities']=join_arrays(decoded,decoded2,host)
    measures,union=account_observed_provider(before,after,host,direct,lower,f1,f2,decoded=(decoded,decoded2))
    ev['account_done']=clock()
    result.update(account=measures,union_pages=len(union),guest_publications=[info,info2])
    result['provider_frames']=[dict(lengths=[len(r['pagemap']) for r in f['rows']],stages=f['stage_costs']) for f in (f1,f2)]
    for name,data in [('before',before),('after',after),('host',host),('direct',direct),('lower',lower)]:
        (out/(name+'.bin')).write_bytes(data if isinstance(data,bytes) else data.tobytes())
    (out/'frames.json').write_text(json.dumps([f1,f2]))
    os.pwrite(c.memory.fileno(),struct.pack('<Q',STOP),c.addresses[0]+REQUEST)
    cli('exec',sid,'bash','-lc',f'for i in $(seq 1 100); do test -f {c.guest}/done && exit 0; sleep .1; done; exit 1')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('container','service-url','config-home','output'):
        parser.add_argument('--'+key, required=True)
    for flag in ('provider-cache','restored-lower'):
        parser.add_argument('--'+flag, action='store_true', required=True)
    a = parser.parse_args()
    out = Path(a.output).resolve()
    assert out.parent.name == 'controller' and out.parent.parent.name == 'mount'
    out.mkdir()
    env = clean_environment(dict(os.environ, XDG_CONFIG_HOME=a.config_home))
    rows = []
    result = dict(status='FAIL', scope='single paused restored VMM, four sources, two guest exports outside timed window',
                  page_bytes=PAGE, samples=rows, events={}, cleanup={})
    events = result['events']
    def cli(*args):
        proc = subprocess.run([ROOT/'runtime/bin/aenv', *args], capture_output=True, env=env, timeout=150)
        if proc.returncode:
            (out/'cli-error.log').write_bytes(proc.stderr)
            proc.check_returncode()
        return proc.stdout.decode().strip()
    source = restored = snapshot = vm = None
    reader = None
    stack=ExitStack();pool=ThreadPoolExecutor(3);stop=threading.Event()
    result['curve']=[]
    try:
        binding = Binding(a.container, out)
        source = create_source(Path(a.config_home), a.service_url)
        cli('exec',source,'true')
        cli('exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
        name = 'paused-layer-'+out.parents[2].name
        cli('snapshot','create',source,'--name',name)
        snapshot = name
        cli('delete',source)
        source = None
        def guest_cli(label,*args,timeout=150):
            if stop.is_set():raise RuntimeError('early scan failed')
            t=clock()
            data=cli(*args)
            result.setdefault('guest_cli',{})[label]=[t,clock()]
            return data
        reader=Reader(out,a.container,guest_cli,session='restored',session_name='observed',lower_cache=True)
        events['probe_begin']=clock()
        prime_tracers(out,stack)
        events['both_attached']=clock()
        events['restore_start'] = clock()
        restored = cli('start',snapshot,'--detach','--timeout','1800')
        events['restore_return'] = clock()
        vm = bind_vmm(binding,restored,out,'restored')
        targets=bind_targets(out,binding,vm,stack)
        events['targets_bound']=clock()
        dev,ident,path,length,lower,lf,ls=targets
        result['objects']=dict(device=dev,identity=ident,capacity=length,lower=lower.name,lower_inode=ls.st_ino,lower_size=ls.st_size)
        curve=pool.submit(sample_curve,targets,result,stop)
        cap=pool.submit(reader.capture.ready)
        def install():
            guest_cli('contents','exec',restored,'python3','-c',CONTENT+'assert p.read_bytes()==data; '+WRITE)
            reader._start_guest_exporter(restored)
        install_done=pool.submit(install)
        proc=Path('/proc')/str(vm['pid']);d=out/'device';d.mkdir()
        reader.lower_layer=owned_memory_layer(out,proc,d/'lower-metadata',binding=binding,vm=vm,device_path=dev)
        assert reader.lower_layer.path==lower
        reader.provider=ProviderCache(out,proc,d/'provider',binding=binding,vm=vm,device_path=dev)
        reader.binding_row=dict(device_path=dev,device_identity=ident,lower_device=ls.st_dev,lower_inode=ls.st_ino)
        cap.result();install_done.result()
        assert api(vm,'GET','/')=='Running'
        collect(out,reader,restored,vm,binding,result,cli,curve)
        result['status']='PASS'
    except Exception:
        result['failed_ns']=clock()
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        stop.set();pool.shutdown(wait=True)
        if reader:
            for key,obj in [('provider',reader.provider),('lower',reader.lower_query),('backend',reader.backend),
                            ('memory',getattr(reader,'memory',None)),('pagemap',getattr(reader,'pm',None)),('capture',reader.capture)]:
                if obj: cleanup(key,lambda:obj.close(final_snapshot=False) if key=='provider' else obj.close())
        cleanup('prebound_resources',stack.close)
        for key,sid in [('restored',restored),('source',source)]:
            if sid: cleanup(key,lambda:cli('delete',sid))
        if snapshot: cleanup('snapshot',lambda:cli('template','delete',snapshot))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
