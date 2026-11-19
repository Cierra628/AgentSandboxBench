"""Inventory and query only the drives returned by the paused VMM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from host_file_monitor import mincore_permission, count_resident, PAGE
import stat
import array
import fcntl
from contextlib import ExitStack
import ctypes
from host_sandbox_monitor import Binding, UnixHTTP
from host_file_pfn_query import HostFilePfnQuery
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


def scan(n,row):
    """Same 256 MiB PROT_NONE/MAP_SHARED chunks as host_file_monitor, timed separately."""
    st=os.fstat(n)
    mincore_permission(n,st)
    lib=ctypes.CDLL(None,use_errno=True)
    lib.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
    lib.mmap.restype=ctypes.c_void_p
    lib.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_ubyte)]
    lib.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    start=clock();calls=[];resident=0
    for off in range(0,row["length"],256*2**20):
        size=min(256*2**20,row["length"]-off)
        addr=lib.mmap(None,size,0,1,n,off)
        if addr==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'mmap')
        try:
            v=(ctypes.c_ubyte*((size+PAGE-1)//PAGE))()
            t=clock();rc=lib.mincore(addr,size,v);end=clock()
            if rc:raise OSError(ctypes.get_errno(),'mincore')
            resident+=count_resident(v);calls.append([t,end])
        finally:
            if lib.munmap(addr,size):raise OSError(ctypes.get_errno(),'munmap')
    end=clock()
    st=os.fstat(n)
    assert (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)==tuple(row[k] for k in ('device','inode','st_size','mtime_ns'))
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
    result = dict(status='FAIL', scope='paused restored VMM; configured drive residency; RAM backend identity checked separately',
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
        # Whitelist fields; never archive the full config (may include MMDS).
        conn=UnixHTTP(vm['api_socket'])
        try:
            events['config_start']=clock()
            conn.request('GET','/vm/config');resp=conn.getresponse()
            if resp.status!=200: raise RuntimeError(f'GET /vm/config HTTP {resp.status}')
            drives=json.loads(resp.read())['drives'];events['config_return']=clock()
        finally:conn.close()
        assert drives
        rows=result['inventory']=[]
        fds=[]
        for drive in drives:
            path=Path(drive['path_on_host'])
            host=proc/('root' if path.is_absolute() else 'cwd')/str(path).lstrip('/')
            st=host.stat(); block=stat.S_ISBLK(st.st_mode)
            assert block or stat.S_ISREG(st.st_mode)
            n=os.open(host,os.O_RDONLY|os.O_CLOEXEC|os.O_NONBLOCK)
            handles.callback(os.close,n)
            fds.append(n)
            assert (st.st_dev,st.st_ino,st.st_rdev)==tuple(getattr(os.fstat(n),k) for k in ('st_dev','st_ino','st_rdev'))
            length=st.st_size
            if block:
                cap=array.array('Q',[0]);fcntl.ioctl(n,0x80081272,cap,True);length=cap[0]
            rows.append(dict(drive_id=drive['drive_id'],path_on_host=str(path),is_root_device=drive['is_root_device'],
                is_read_only=drive['is_read_only'],device=st.st_dev,inode=st.st_ino,rdev=st.st_rdev,
                major_minor=f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}' if block else None,
                kind='block' if block else 'regular',st_size=st.st_size,mtime_ns=st.st_mtime_ns,
                length=length,pages=(length+PAGE-1)//PAGE))
        for row in rows:
            if row['kind']=='block':
                backing=Path('/sys/dev/block')/row['major_minor']/'loop/backing_file'
                if backing.is_file():row['loop_backing_file']=backing.read_text().strip()
        check_vmm(vm,binding.cgroup)
        for row,fd in zip(rows,fds):
            result['samples'].append(scan(fd,row))
        # Metadata only; neither rootfs lowers nor a replacement RAM device are scanned.
        dev,ident,_=mapped_device(vm)
        envdir=out.parent.parent/'env'
        cm=select_config((envdir/'logs/server.log').read_text(),restored,dev,
                         list((envdir/'snapshot-local-cache/runtime').glob('*/memory/image.json')),envdir)
        layers=[]
        for ref in json.loads(cm.read_text())['lowers']:
            path=envdir/ref['file'].removeprefix('/workspace/env/')
            proof=service_openers(binding,vm,path)[0]
            layers.append(dict(path=ref['file'],device=proof['device'],inode=proof['inode'],size=path.stat().st_size))
        result['memory']=dict(device_path=dev,identity=ident,config=str(cm.relative_to(envdir)),lowers=layers)
        result['matched_drives']=[r['drive_id'] for r in rows if (r['device'],r['inode'],r['rdev'])==ident]
        if any(r['drive_id']=='user_rootfs' for r in rows):
            cfg=json.loads((proc/'cwd/overlaybd/image.json').read_text())
            result['user_rootfs_config']=dict(path='cwd/overlaybd/image.json',lower_count=len(cfg['lowers']),
                lower_files=[ref.get('file',ref.get('dir')) for ref in cfg['lowers']],upper_keys=list(cfg.get('upper',{})))
        # Test the existing full-file PFN entry on the actual configured block
        # drive. Use no inferred backing-file path and do not attach a fallback probe.
        for row in rows:
            if row['kind']!='block' or row['drive_id'] in result['matched_drives']:continue
            name=Path(row['path_on_host'])
            host=proc/('root' if name.is_absolute() else 'cwd')/str(name).lstrip('/')
            try:
                probe=HostFilePfnQuery(host,out,expected_identity=(row['device'],row['inode']))
            except ValueError as exc:
                if 'pinned regular file' not in str(exc):raise
                result['pfn_rejection']=dict(drive=row['drive_id'],path=str(name),error=str(exc))
                break
            else:
                probe.close();raise RuntimeError('unexpected block support in regular-file probe')
        if not result.get('pfn_rejection'):raise RuntimeError('no confirmed blocker for full drive query')
        result['four_source_status']='UNAVAILABLE: RAM direct backend absent from drives; no replacement PFN query'
        assert api(vm,'GET','/')=='Paused'
        check_vmm(vm,binding.cgroup)
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
