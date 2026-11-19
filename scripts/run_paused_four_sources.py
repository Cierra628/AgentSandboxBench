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
from live_task_cache import LiveTaskCache
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



def instrument(q):
    # Instance-local ctypes handles. Keep the original native functions and arguments.
    times = dict(scan_ns=0, lookup_ns=0, lookup_calls=0, lengths=[])
    scan = q.libc.mincore
    lookup = q.lib.bpf_map_lookup_elem
    def mincore(addr, length, vec):
        t = clock()
        rc = scan(addr, length, vec)
        dt = clock()-t
        times['scan_ns'] += dt
        times['lengths'].append(length//PAGE)
        return rc
    def pfn(*args):
        t = clock()
        rc = lookup(*args)
        dt = clock()-t
        times['lookup_ns'] += dt
        times['lookup_calls'] += 1
        return rc
    q.libc.mincore = mincore
    q.lib.bpf_map_lookup_elem = pfn
    return times


def collect(out,c,sid,vm,binding,result,cli):
    c.expected_binding=vm
    c.start(sid,coordinated=True)
    assert c.provider.segmented
    dev,_,maps=mapped_device(vm)
    result['objects']=dict(device=dev,maps=len(maps),lower=c.lower_layer.path.name,
                           slots=[s[0].name for s in c.provider.slots],ram_slots=[s['size']//PAGE for s in c.proof['slots']])
    result['meters']=dict(direct=instrument(c.backend),lower=instrument(c.lower_query))
    ev=result['events']; stages=result['stages']={}
    def step(k,fn,*args):
        t=clock()
        try:return fn(*args)
        finally: stages[k]=[t,clock()]
    before,info=step('guest_before',c._snapshot)
    decoded=decode_array(before); pfns=decoded[1][decoded[1][:,1]!=0,0]
    assert len(pfns)>0
    result['selected']=len(pfns)
    check_vmm(vm,binding.cgroup)
    assert api(vm,'GET','/')=='Running'
    ev['pause_request']=clock()
    try:
        api(vm,'PATCH','/vm','Paused')
        ev['pause_response']=clock()
        assert api(vm,'GET','/')=='Paused'
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
        reader = LiveTaskCache(out,a.container,lambda label,*args,timeout=150:cli(*args),session='restored',
                               session_name='observed',lower_cache=True,provider_cache=True)
        events['restore_start'] = clock()
        restored = cli('start',snapshot,'--detach','--timeout','1800')
        events['restore_return'] = clock()
        vm = bind_vmm(binding,restored,out,'restored')
        cli('exec',restored,'python3','-c', CONTENT+'assert p.read_bytes()==data; '+WRITE)
        collect(out,reader,restored,vm,binding,result,cli)
        result['status']='PASS'
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        if reader:
            for key,obj in [('provider',reader.provider),('lower',reader.lower_query),('backend',reader.backend),
                            ('memory',getattr(reader,'memory',None)),('pagemap',getattr(reader,'pm',None)),('capture',reader.capture)]:
                if obj: cleanup(key,lambda:obj.close(final_snapshot=False) if key=='provider' else obj.close())
        for key,sid in [('restored',restored),('source',source)]:
            if sid: cleanup(key,lambda:cli('delete',sid))
        if snapshot: cleanup('snapshot',lambda:cli('template','delete',snapshot))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
