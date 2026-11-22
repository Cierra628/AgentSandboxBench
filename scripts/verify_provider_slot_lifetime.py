"""Three restore cohorts. Inspect identity metadata only: no residency queries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from host_sandbox_monitor import Binding, UnixHTTP
from light_memory_round import check_vmm
from prettier_no_proxy import clean_environment
from provider_slot_cache import ProviderCache
from run_guest_light_memory import bind_vmm
from run_rollout_cache_calibration import create_source
from seven_restored_pages import CONTENT, WRITE, ROLES, mapped_device
from snapshot_layer_metadata import owned_memory_layer
from snapshot_lower_owner import select_config

def state(vm, target=None):
    conn=UnixHTTP(vm['api_socket']);conn.timeout=5
    try:
        conn.request('PATCH' if target else 'GET','/vm' if target else '/',
                     json.dumps({'state':target}) if target else None,{'Content-Type':'application/json'})
        r=conn.getresponse();raw=r.read()
        if r.status!=(204 if target else 200):raise RuntimeError((r.status,raw))
        return target if target else json.loads(raw)['state']
    finally:conn.close()

def main():
    p=argparse.ArgumentParser()
    for arg in ('container','config-home','service-url','output'):p.add_argument('--'+arg,required=True)
    for arg in ('seven-restored','immediate-restores','restored-lower','unified-rounds','provider-cache'):
        p.add_argument('--'+arg,action='store_true',required=True)
    a=p.parse_args();out=Path(a.output);out.mkdir()
    env=out.parent.parent/'env';slots=env/'provider-slots';log=env/'logs/ublk-daemon.log'
    binding=Binding(a.container,out)
    cli_env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=a.config_home))
    def cli(*args):
        return subprocess.check_output([Path(__file__).resolve().parents[1]/'runtime/bin/aenv',*args],
                    env=cli_env,text=True,stderr=subprocess.PIPE,timeout=150).strip()
    def files():return sorted(p.name for p in slots.glob('*') if p.suffix in ('.json','.bin'))
    result=dict(status='FAIL',scope='three paused restore cohorts; provider slot identity only',attempts=[])
    def save():(out/'result.json').write_text(json.dumps(result,indent=2))
    try:
        with ExitStack() as resources:
            source=create_source(Path(a.config_home),a.service_url);result['source']=source
            with ExitStack() as src:
                src.callback(cli,'delete',source)
                cli('exec',source,'python3','-c',CONTENT+'p.parent.mkdir(exist_ok=True); '+WRITE)
                snap='slots-'+out.parents[2].name;cli('snapshot','create',source,'--name',snap)
                resources.callback(cli,'template','delete',snap);result['snapshot']=snap
            for n in range(1,4):
                rows={r:{} for r in ROLES};rec=dict(n=n,roles=rows,before=files());result['attempts'].append(rec)
                pos=log.stat().st_size;vms={};devices={}
                def conflicts():
                    with log.open('rb') as f:f.seek(pos);return f.read().count(b'File exists')
                try:
                    assert not rec['before'], 'residual slots before restore'
                    with ExitStack() as cleanup, ThreadPoolExecutor(7) as pool:
                        def stage(name,fn):
                            tasks={r:pool.submit(fn,r) for r in ROLES};errors=[]
                            for r,f in tasks.items():
                                try:f.result();rows[r][name]='PASS'
                                except Exception as e:
                                    msg=str(getattr(e,'stderr',None) or e);rows[r][name]=msg;errors.append((r,msg))
                            save()
                            if errors or conflicts():raise RuntimeError((name,errors,conflicts()))
                        def restore(r):
                            sid=cli('start',snap,'--detach','--timeout','1800');rows[r]['sid']=sid
                            cleanup.callback(cli,'delete',sid)
                        stage('restore',restore)
                        stage('content',lambda r:cli('exec',rows[r]['sid'],'python3','-c',CONTENT+'assert p.read_bytes()==data'))
                        def bind(r):
                            vm=bind_vmm(binding,rows[r]['sid'],out,f'{n}-{r}');vms[r]=vm
                            path,ident,_=mapped_device(vm);devices[r]=(path,ident)
                            rows[r].update(path=path,major_minor=f'{os.major(ident[2])}:{os.minor(ident[2])}',inode=ident[1])
                        stage('binding',bind)
                        def pause(r):
                            vm=vms[r];check_vmm(vm,binding.cgroup);state(vm,'Paused')
                            assert state(vm)=='Paused';check_vmm(vm,binding.cgroup)
                        stage('pause',pause)
                        assert len(set(devices.values()))==1,'different live devices'
                        path,ident=devices[ROLES[0]]; s=(env/'logs/server.log').read_text()
                        configs=list((env/'snapshot-local-cache/runtime').glob('*/memory/image.json'))
                        for r,vm in vms.items():
                            try:select_config(s,vm['sandbox_id'],path,configs,env)
                            except ValueError:continue
                            break
                        else:raise ValueError('no matching creation receipt')
                        proc=Path('/proc')/str(vm['pid']);dst=out/f'round-{n}';dst.mkdir()
                        with (proc/'root'/path.lstrip('/')).open('rb',buffering=0) as pin:
                            st=os.fstat(pin.fileno());assert (st.st_dev,st.st_ino,st.st_rdev)==ident
                            owned_memory_layer(out,proc,dst/'lower-metadata',binding=binding,vm=vm,device_path=path)
                            obs=ProviderCache(out,proc,dst/'provider',binding=binding,vm=vm,device_path=path)
                            try:
                                records=obs.identity['slots'];rec['slots']=files();rec['device_ids']=sorted({x['dev_id'] for x in records})
                                assert rec['device_ids']==[int(path.removeprefix('/dev/ublkb'))]
                                assert len(records)==16 and set(rec['slots'])=={str(Path(x['name']).with_suffix(suf)) for x in records for suf in ('.json','.bin')}
                                assert all(state(vms[r])=='Paused' and mapped_device(vms[r])[:2]==devices[r] for r in ROLES)
                                rec['provider_binding']='PASS'
                            finally:obs.close(final_snapshot=False)
                finally:
                    deadline=time.monotonic()+10
                    while files() and time.monotonic()<deadline:time.sleep(.1)
                    rec.update(after=files(),file_exists=conflicts(),remaining=json.loads(cli('list','--output','json')))
                    rec['remaining_vmm_pids']=[p for g in binding.cgroup.glob('vmm-*') for p in (g/'cgroup.procs').read_text().split()]
                    save()
                assert not (rec['after'] or rec['remaining'] or rec['remaining_vmm_pids'] or rec['file_exists'])
                rec['cleanup']='PASS'
        result['status']='PASS'
    except Exception:result['error']=traceback.format_exc()
    save();return int(result['status']!='PASS')
if __name__=='__main__':raise SystemExit(main())
