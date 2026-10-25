"""One private restore calibration for no-fault snapshot residency measurements."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback

from agentenv_cold_start import create
from live_task_cache import LiveTaskCache, save_sessions

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',required=True);p.add_argument('--config-home',type=Path,required=True)
    p.add_argument('--service-url',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--lower-cache',action='store_true')
    p.add_argument('--provider-cache',action='store_true')
    p.add_argument('--batch-lookup',action='store_true',help='Private single-threaded read-only BPF batch queries')
    args=p.parse_args();out=args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=dict(os.environ,XDG_CONFIG_HOME=str(args.config_home))
    sid=live=snapshot=None;success=False;stage='boot';events=[];sessions=[]
    result=dict(status='FAIL',vm_pause=False,task_id='snapshot-cache-calibration',checks={})
    def cli(label,*argv,timeout=60):
        started=time.monotonic_ns()
        process=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(process.stdout);(out/(label+'.stderr')).write_bytes(process.stderr)
        events.append(dict(stage=label,duration_ns=time.monotonic_ns()-started,returncode=process.returncode))
        process.check_returncode();return process.stdout.decode().strip()
    def wait(name):
        cli('wait-'+name,'exec',sid,'bash','-lc',f'for i in $(seq 1 150); do test -f {guest}/{name} && exit 0; sleep .1; done; cat /tmp/asb-snapshot-fixture.log; exit 1',timeout=20)
    def read(name):
        value=json.loads(cli('state-'+name,'exec',sid,'cat',guest+'/'+name+'.json'))
        (out/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n');return value
    try:
        live=LiveTaskCache(out,args.container,cli,session='parent')
        config=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())
        sid=create(args.config_home,args.service_url,config['workload_image'],request_timeout=90)
        cli('ready','exec',sid,'true',timeout=15);live.start(sid)
        tools=out/'tools';tools.mkdir();shutil.copy2(ROOT/'scripts/guest_snapshot_cache_fixture.py',tools)
        cli('fixture-upload','upload',sid,tools,'/workspace/asb-snapshot-tools')
        guest='/dev/shm/asb-snapshot-fixture'
        cli('fixture-start','exec',sid,'bash','-lc',f'nohup timeout 210 python3 /workspace/asb-snapshot-tools/guest_snapshot_cache_fixture.py {guest} >/tmp/asb-snapshot-fixture.log 2>&1 </dev/null &')
        wait('ready.json');before=read('ready');time.sleep(2)
        live.close();sessions.append(json.loads((live.directory/'result.json').read_text()));live=None
        gap=dict(start_ns=time.time_ns(),reason='native checkpoint; no collector pause')
        snapshot='cache-calibration-'+out.parents[2].name
        stage='checkpoint';cli('checkpoint','snapshot','create',sid,'--name',snapshot,timeout=150)
        cli('delete-parent','delete',sid,timeout=90);sid=None
        live=LiveTaskCache(out,args.container,cli,session='restored',lower_cache=args.lower_cache,provider_cache=args.provider_cache,
                           **(dict(batch_lookup=True) if args.batch_lookup else {}))
        stage='restore';sid=cli('restore','start',snapshot,'--detach','--timeout','1800',timeout=150)
        cli('restore-ready','exec',sid,'true',timeout=15);live.start(sid);gap['end_ns']=time.time_ns()
        for command,name in [('observe','waiting'),('read','read-done'),('write','write-done')]:
            stage=command
            cli('request-'+command,'exec',sid,'touch',guest+'/'+command);wait(name+'.json');value=read(name)
            assert all(value[k]==before[k] for k in ('pid','boot_id','start_ticks'))
            left=time.time_ns();time.sleep(3);right=time.time_ns()
            result.setdefault('phases',[]).append(dict(name=name,start_ns=left,end_ns=right))
        live.close();sessions.append(json.loads((live.directory/'result.json').read_text()));live=None
        save_sessions(out/'live-cache',sessions,gap)
        cli('request-finish','exec',sid,'touch',guest+'/finish');wait('done')
        check=read('result');assert check['status']=='PASS'
        result['checks'].update(file_contents='PASS',process_continuity='PASS',guest_page_identity_recorded='PASS',checkpoint_restore='PASS')
        success=True
    except BaseException as exc:
        result.update(error=repr(exc),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        if live:
            try:live.close()
            except BaseException as exc:cleanup['collector_error']=repr(exc);success=False
        cleanup['live_cache_stopped']=live is None or not live.thread or not live.thread.is_alive()
        if sid:
            try:cli('delete-final','delete',sid,timeout=90);cleanup['vm_removed']=True
            except BaseException as exc:cleanup['vm_error']=repr(exc);success=False
        if snapshot and success:
            try:cli('delete-snapshot','template','delete',snapshot,timeout=60);cleanup['snapshot_removed']=True
            except BaseException as exc:cleanup['snapshot_error']=repr(exc);success=False
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup)
        (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1

if __name__=='__main__':raise SystemExit(main())
