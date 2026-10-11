"""Capture four actual shell calls and replay them in a fresh VM of the same image."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

from sphinx7590_inputs import ROOT,validate
from sandbox_runtime_monitor import RuntimeMonitor
from tool_trajectory import begin,capture,freeze,replay,request,write
from platform_management import resources


def run(args):
    cfg,_,_,_=validate(args.inputs)
    if args.check_only:
        print('status=VALIDATED mode=tool-capture-demo actions=4 replay=4');return 0
    out=args.output.resolve();out.mkdir(exist_ok=False)
    assert out.parent.name=='controller' and out.parent.parent.name=='mount' and out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    live=set();events=[];lock=threading.Lock();counter=0;stage='setup';started=time.monotonic_ns()
    result=dict(status='FAIL',tool_capture_demo=True,checks={},cpu=2,memory_mib=512,source_image=cfg['source_image'],
        scope='actual tool-interface collection and frozen replay from the same clean image; fresh shell per call; no model inference, persistent PTY or benchmark task acceptance')
    def call(label,argv,timeout=60):
        nonlocal counter
        with lock:index=counter;counter+=1
        start=time.monotonic_ns();stamp=time.time_ns();code=None
        try:
            with (out/f'{index:04}-{label}.stderr').open('x') as log:
                proc=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
            (out/f'{index:04}-{label}.stdout').write_text(proc.stdout);code=proc.returncode
            if code:raise RuntimeError(label+': exit '+str(code))
            return proc.stdout.strip()
        finally:
            with lock:
                events.append(dict(stage=label,timestamp_ns=stamp,start_monotonic_ns=start,duration_ns=time.monotonic_ns()-start,exit_code=code))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
    def cli(label,*argv,timeout=60):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    runtime=RuntimeMonitor(out,cli,container=args.container)
    def interrupted(*_):raise RuntimeError('tool capture interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    def create(role):
        sid=call('cold-'+role,[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image'],'--memory-mib','512'],610)
        live.add(sid)
        with (out/'sandbox-ids.tsv').open('a') as f:f.write(f'{role}\t{sid}\t\tclean-image\n')
        cli('setup-directory','exec',sid,'mkdir','-p','/workspace/asb-tool-session')
        cli('upload-tool-bridge','upload',sid,ROOT/'scripts/guest_tool_capture.py','/workspace/asb-tool-capture.py')
        runtime.start(sid,role,'tool-calls');return sid
    try:
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container]))
        assert labels.get('agentsandboxbench.run_id')==out.parents[2].name and labels.get('agentenv.experiment')=='isolated-storage-check'
        shutil.copytree(args.inputs,out/'prepared-input')
        (out/'sandbox-ids.tsv').write_text('role\tsandbox_id\tparent_id\tcreated_from\n')
        first=create('capture');stage='capture'
        catalog=resources(args.config_home,out/'native-catalog')
        assert len(catalog['resources']['instances'])==1 and catalog['resources']['instances'][0]['sandboxID']==first
        result['native_catalog']={k:len(v) for k,v in catalog['resources'].items()}
        context=dict(source_image=cfg['source_image'],cpu=2,memory_mib=512,tracked_files=['/workspace/asb-tool-session/state.txt'],
            session_semantics='fresh bash per tool call; filesystem persists',model='not used',initial_image_state='new sandbox, no inherited command state')
        initial=request(cli,first,dict(kind='state',files=context['tracked_files']))
        assert all(v is None for v in initial.values())
        begin(out/'captured',context,initial)
        tool_env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','ASB_LABEL':'capture-demo'}
        commands=["printf '%s:alpha\\n' \"$ASB_LABEL\" > state.txt; cat state.txt",
                  "printf 'exploration failed\\n' >&2; exit 7",
                  "printf 'beta\\n' >> state.txt; cat state.txt",
                  "printf '%s\\n' \"$PWD\" \"$ASB_LABEL\"; sha256sum state.txt"]
        for command in commands:capture(out/'captured',cli,first,command,cwd='/workspace/asb-tool-session',env=tool_env)
        final=request(cli,first,dict(kind='state',files=context['tracked_files']))
        manifest=freeze(out/'captured',final);assert manifest['expected_failures']=={'002':7}
        result['checks']['actual_calls_captured_and_frozen']='PASS'
        runtime.stop(first,'capture-complete');cli('delete-capture','delete',first);live.remove(first)
        second=create('replay');stage='replay'
        proof=replay(out/'captured',out/'replayed',cli,second)
        result['checks']['frozen_replay_matches_calls_and_final_file']='PASS'
        runtime.stop(second,'replay-complete');cli('delete-replay','delete',second);live.remove(second)
        result.update(status='PASS',replay=proof)
    except Exception as error:
        result.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        for sid in list(live):
            try:runtime.stop(sid);cli('cleanup-owned-vm','delete',sid);live.remove(sid)
            except Exception as error:cleanup['owned_'+sid]=False;result.setdefault('cleanup_errors',[]).append(repr(error))
        cleanup['owned_vms_removed']=not live
        cleanup['collector_sessions_complete']=bool(runtime.sessions) and all(s.get('status')=='PASS' for s in runtime.sessions)
        try:cleanup['service_sandbox_list_empty']=json.loads(cli('list-after','list','--output','json'))==[]
        except Exception:cleanup['service_sandbox_list_empty']=False
        result.update(cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        if not all(cleanup.values()):result['status']='FAIL'
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','reference','config-home','output'):parser.add_argument('--'+name,type=Path,required=name=='inputs')
    for name in ('container','service-url'):parser.add_argument('--'+name)
    parser.add_argument('--check-only',action='store_true')
    raise SystemExit(run(parser.parse_args()))
