"""One source plus one restored guest; exercise cache-session lifecycle once."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import threading
import traceback
from agentenv_cold_start import create
from rollout_cache_sessions import RolloutCacheSessions
from light_prettier_rollout import LightRollout
from prettier_no_proxy import clean_environment, IMAGE

ROOT=Path(__file__).resolve().parents[1]


def create_source(config_home, service_url):
    return create(config_home, service_url, IMAGE, request_timeout=90, local_no_proxy=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('container','service-url'):p.add_argument('--'+key,required=True)
    p.add_argument('--config-home',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--immediate-restores',action='store_true')
    p.add_argument('--seven-restored',action='store_true')
    p.add_argument('--provider-cache',action='store_true')
    p.add_argument('--restored-lower',action='store_true')
    p.add_argument('--unified-rounds',action='store_true')
    a=p.parse_args();out=a.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=str(a.config_home)))
    events=[];sids={};snapshot=None;light=None;cache=None;stage='initialize';ok=False
    value=dict(status='FAIL',scope='one source and one restored guest; one native snapshot; no business replay; cache intervals are not atomic snapshots')
    event_lock=threading.Lock()
    def cli(label,*argv,timeout=60):
        start=time.monotonic_ns()
        r=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        end=time.monotonic_ns()
        with event_lock:
            index=len(events)
            events.append(dict(stage=label,start_monotonic_ns=start,duration_ns=end-start,returncode=r.returncode))
        (out/f'{index:04d}-{label}.stdout').write_bytes(r.stdout);(out/f'{index:04d}-{label}.stderr').write_bytes(r.stderr)
        r.check_returncode();return r.stdout.decode().strip()
    if a.seven_restored:
        if not (a.provider_cache and a.restored_lower and a.unified_rounds):raise ValueError('incomplete seven-restored configuration')
        from seven_restored_pages import run
        return run(a,out,cli,events)
    def observe(role,seconds=4):
        start=time.monotonic_ns();time.sleep(seconds)
        value.setdefault('observations',[]).append(dict(role=role,start_ns=start,end_ns=time.monotonic_ns()))
    try:
        cache=RolloutCacheSessions(out,a.container,cli,unified_rounds=a.unified_rounds,restored_lower=a.restored_lower,restored_provider=a.provider_cache)
        light=LightRollout(a.container,out,['backbone','restored'],page_cache=cache)
        stage='prepare-source';light.before_create('backbone',restored=False)
        value['image']=IMAGE
        stage='create-source';sids['backbone']=create_source(a.config_home,a.service_url)
        cli('source-ready','exec',sids['backbone'],'true')
        stage='bind-source';light.register('backbone',sids['backbone'])
        code="from pathlib import Path; import os; p=Path('/workspace/cache-session-file'); p.parent.mkdir(exist_ok=True); f=p.open('xb'); f.write(b'A'*(16*2**20)); f.flush(); os.fsync(f.fileno()); f.close(); assert p.read_bytes()==b'A'*(16*2**20)"
        stage='write-file';cli('write-file','exec',sids['backbone'],'python3','-c',code);observe('source-before-save')
        stage='stop-before-save';light.saving('backbone')
        snapshot='cache-session-'+out.parents[2].name
        stage='save';cli('save','snapshot','create',sids['backbone'],'--name',snapshot,timeout=150)
        stage='resume-source-collector';light.saved('backbone');observe('source-after-save')
        stage='prepare-restored';light.before_create('restored',restored=True)
        stage='restore';sids['restored']=cli('restore','start',snapshot,'--detach','--timeout','1800',timeout=150)
        assert sids['restored']!=sids['backbone']
        cli('restored-ready','exec',sids['restored'],'true')
        stage='bind-restored';light.register('restored',sids['restored'])
        code="from pathlib import Path; assert Path('/workspace/cache-session-file').read_bytes()==b'A'*(16*2**20); print('16 MiB contents verified')"
        for role,sid in sids.items():cli('verify-'+role,'exec',sid,'python3','-c',code)
        observe('both-guests')
        stage='stop-samplers';value['light']=light.stop();light=None
        sessions={}
        for name in ('backbone-g1','backbone-g2','restored-g1'):
            sessions[name]=json.loads((out/'live-cache'/name/'result.json').read_text())
            assert sessions[name]['status']=='PASS' and sessions[name]['samples']>=2
        value['sessions']=sessions
        value['checks']=dict(source_continued='PASS',one_restore='PASS',file_contents='PASS',three_cache_sessions='PASS')
        ok=True
    except BaseException as error:
        value.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        if light:
            try:light.stop();cleanup['samplers']='PASS'
            except Exception as error:cleanup['samplers_error']=repr(error);ok=False
        elif cache and not ok:
            try:cache.stop()
            except Exception as error:cleanup['cache_error']=repr(error)
        for role,sid in sids.items():
            try:cli('delete-'+role,'delete',sid,timeout=90);cleanup[role]='REMOVED'
            except Exception as error:cleanup[role]=repr(error);ok=False
        if snapshot and ok:
            try:cli('delete-snapshot','template','delete',snapshot,timeout=60);cleanup['snapshot']='REMOVED'
            except Exception as error:cleanup['snapshot_error']=repr(error);ok=False
        value.update(status='PASS' if ok else 'FAIL',cleanup=cleanup,guests=sids)
        (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
        (out/'result.json').write_text(json.dumps(value,indent=2)+'\n')
    print('status='+value['status']+' result_dir='+str(out),flush=True)
    return 0 if ok else 1

if __name__=='__main__':raise SystemExit(main())
