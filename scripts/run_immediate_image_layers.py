"""Observe every fixed rootfs lower after an immediate in-place Firecracker pause."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from host_file_monitor import mincore_permission, count_resident, PAGE
from host_rootfs_layers import lower_file, file_identity
from host_private_files import targets
from contextlib import ExitStack
import ctypes
from host_sandbox_monitor import Binding, UnixHTTP
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE, mapped_device
from snapshot_lower_owner import select_config, service_openers

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


def scan(fd,row):
    """Same 256 MiB PROT_NONE/MAP_SHARED chunks as host_file_monitor, timed separately."""
    st=os.fstat(fd.fileno())
    mincore_permission(fd.fileno(),st)
    lib=ctypes.CDLL(None,use_errno=True)
    lib.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
    lib.mmap.restype=ctypes.c_void_p
    lib.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_ubyte)]
    lib.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    start=clock();calls=[];resident=0
    for off in range(0,st.st_size,256*2**20):
        size=min(256*2**20,st.st_size-off)
        addr=lib.mmap(None,size,0,1,fd.fileno(),off)
        if addr==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'mmap')
        try:
            v=(ctypes.c_ubyte*((size+PAGE-1)//PAGE))()
            t=clock();rc=lib.mincore(addr,size,v);end=clock()
            if rc:raise OSError(ctypes.get_errno(),'mincore')
            resident+=count_resident(v);calls.append([t,end])
        finally:
            if lib.munmap(addr,size):raise OSError(ctypes.get_errno(),'munmap')
    end=clock()
    st=os.fstat(fd.fileno())
    assert (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==tuple(row[k] for k in ('device','inode','size','mtime_ns'))
    return dict(inode=st.st_ino,pages=row['pages'],resident=resident,start_ns=start,end_ns=end,calls=calls)


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
    result = dict(status='FAIL', scope='paused restored VMM; all fixed rootfs lowers, file residency only',
                  page_bytes=PAGE, samples=rows, events={}, cleanup={})
    events = result['events']
    def cli(*args):
        proc = subprocess.run([ROOT/'runtime/bin/aenv', *args], capture_output=True, env=env, timeout=150)
        if proc.returncode:
            (out/'cli-error.log').write_bytes(proc.stderr)
            proc.check_returncode()
        return proc.stdout.decode().strip()
    source = restored = snapshot = vm = None
    paused = False
    handles = ExitStack()
    try:
        binding = Binding(a.container, out)
        source = create_source(Path(a.config_home), a.service_url)
        cli('exec',source,'true')
        cli('exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
        proc = Path('/proc')/str(binding.resolve(source)['pid'])
        config = proc/'cwd/overlaybd/image.json'
        refs = json.loads(config.read_text())['lowers']
        wanted = targets()
        assert len(refs)==len(wanted)
        pinned=[]
        result['inventory']=[]
        for ref,w in zip(refs,wanted):
            path=lower_file(ref,config,proc)
            st=file_identity(path)
            assert ref['file'].endswith('/'+w['relative']) and st['size']==w['size']
            fd=handles.enter_context(path.open('rb',buffering=0))
            assert (os.fstat(fd.fileno()).st_dev,os.fstat(fd.fileno()).st_ino)==(st['device'],st['inode'])
            result['inventory'].append(dict(path='/workspace/env/image-cache/commits/'+w['relative'],
                digest=ref['digest'],pages=(st['size']+PAGE-1)//PAGE,**st))
            pinned.append(fd)
        assert len({(r['device'],r['inode']) for r in result['inventory']})==len(wanted)
        (out/'inventory.json').write_text(json.dumps(result['inventory'],indent=2))
        name = 'paused-layer-'+out.parents[2].name
        cli('snapshot','create',source,'--name',name)
        snapshot = name
        cli('delete',source)
        source = None
        events['restore_start'] = clock()
        restored = cli('start',snapshot,'--detach','--timeout','1800')
        events['restore_return'] = clock()
        vm = bind_vmm(binding,restored,out,'restored')
        assert api(vm,'GET','/') == 'Running'
        events['pause_request'] = clock()
        api(vm,'PATCH','/vm','Paused')
        events['pause_response'] = clock()
        paused = True
        assert api(vm,'GET','/') == 'Paused'
        proc=Path('/proc')/str(vm['pid'])
        cfg=proc/'cwd/overlaybd/image.json'
        active=json.loads(cfg.read_text())['lowers']
        actual=[file_identity(lower_file(ref,cfg,proc)) for ref in active]
        result['restored_rootfs_layers']=[dict(reference=ref,**st) for ref,st in zip(active,actual)]
        assert all(any(all(st[k]==r[k] for k in ('device','inode','size','mtime_ns')) for st in actual) for r in result['inventory'])
        for r,fd in zip(result['inventory'],pinned):
            observed=scan(fd,r)
            rows.append(observed)
        events['query_start']=rows[0]['start_ns'];events['query_end']=rows[-1]['end_ns']
        assert api(vm,'GET','/')=='Paused'
        check_vmm(vm,binding.cgroup)
        # Verify whether the fixed image files actually ARE either PFN source.
        dev,ident,_=mapped_device(vm)
        envdir=out.parent.parent/'env'
        cm=select_config((envdir/'logs/server.log').read_text(),restored,dev,
                         list((envdir/'snapshot-local-cache/runtime').glob('*/memory/image.json')),envdir)
        memory_layers=json.loads(cm.read_text())['lowers']
        lower_ids=[]
        for ref in memory_layers:
            path=envdir/ref['file'].removeprefix('/workspace/env/')
            proof=service_openers(binding,vm,path)[0]
            lower_ids.append([proof['device'],proof['inode']])
        files={(r['device'],r['inode']) for r in result['inventory']}
        result['source_binding']=dict(ram_backend=dev,backend_dev_inode=list(ident[:2]),
            memory_lower_refs=memory_layers,memory_lower_ids=lower_ids,
            backend_matches=sum(x==ident[:2] for x in files),
            lower_matches=sum(tuple(x) in files for x in lower_ids))
        if result['source_binding']['backend_matches'] or result['source_binding']['lower_matches']:
            raise ValueError('unexpected overlap: no validated same-object full PFN path')
        result['four_source_status']='UNAVAILABLE: fixed rootfs files are neither RAM device nor snapshot memory lower; stop'
        result['status'] = 'PASS'
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        def cleanup(key, fn):
            try: fn(); result['cleanup'][key] = 'PASS'
            except Exception as exc: result['cleanup'][key] = repr(exc); result['status'] = 'FAIL'
        handles.close()
        if paused:
            def resume():
                check_vmm(vm,binding.cgroup)
                api(vm,'PATCH','/vm','Resumed')
                assert api(vm,'GET','/') == 'Running'
            cleanup('resume',resume)
        for key,sid in [('restored',restored),('source',source)]:
            if sid: cleanup(key,lambda:cli('delete',sid))
        if snapshot: cleanup('snapshot',lambda:cli('template','delete',snapshot))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return int(result['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())
