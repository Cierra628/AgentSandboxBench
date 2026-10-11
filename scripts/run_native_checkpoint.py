"""Capture at two real tool steps or restore a native checkpoint in a new private service."""
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
from tool_trajectory import begin,capture,freeze,request,write
from native_checkpoint_bundle import load
from validate_isolated_agentenv import IMAGE


def run(args):
    cfg,_,_,_=validate(args.inputs)
    if args.phase=='restore':bundle=load(args.bundle);assert bundle['capture']['source_image']==cfg['source_image']
    if args.check_only:print('status=VALIDATED mode=native-checkpoint-'+args.phase);return 0
    out=args.output.resolve();out.mkdir(exist_ok=False)
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service' and out.parent.name=='controller'
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    live=set();events=[];lock=threading.Lock();counter=0;stage='setup';started=time.monotonic_ns()
    result=dict(status='FAIL',native_checkpoint_demo=True,phase=args.phase,checks={},cpu=2,memory_mib=512,server_image=IMAGE,source_image=cfg['source_image'],
        scope='native full-VM checkpoint after two actual tool calls; persistent repository export/import; process/anonymous-memory/open-file/progress and next calls; not benchmark task acceptance')
    def call(label,argv,timeout=60):
        nonlocal counter
        with lock:index=counter;counter+=1
        start=time.monotonic_ns();stamp=time.time_ns();code=None
        try:
            with (out/f'{index:04}-{label}.stderr').open('x') as log:
                p=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
            (out/f'{index:04}-{label}.stdout').write_text(p.stdout);code=p.returncode
            if code:raise RuntimeError(label+': exit '+str(code))
            return p.stdout.strip()
        finally:
            with lock:
                events.append(dict(stage=label,timestamp_ns=stamp,start_monotonic_ns=start,duration_ns=time.monotonic_ns()-start,exit_code=code))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
    def cli(label,*argv,timeout=60):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    runtime=RuntimeMonitor(out,cli,container=args.container)
    def cancel(*_):raise RuntimeError('owned native checkpoint interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    snapshot=None;success=False
    def state(sid):return request(cli,sid,dict(kind='state',files=['/workspace/asb-tool-session/state.txt','/workspace/asb-tool-session/progress']))
    def probe(sid):return json.loads(cli('state-probe','exec',sid,'python3','/workspace/asb-checkpoint-probe.py','get'))
    try:
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container]))
        assert labels.get('agentsandboxbench.run_id')==out.parents[2].name and labels.get('agentenv.experiment')=='isolated-storage-check'
        shutil.copytree(args.inputs,out/'prepared-input');(out/'sandbox-ids.tsv').write_text('role\tsandbox_id\tparent_id\tcreated_from\n')
        tool_env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','ASB_LABEL':'durable-demo'}
        if args.phase=='capture':
            sid=call('cold-create',[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image'],'--memory-mib','512'],610);live.add(sid)
            with (out/'sandbox-ids.tsv').open('a') as f:f.write(f'capture\t{sid}\t\tclean-image\n')
            cli('setup-directory','exec',sid,'mkdir','-p','/workspace/asb-tool-session')
            for local,remote in [('guest_tool_capture.py','asb-tool-capture.py'),('guest_checkpoint_probe.py','asb-checkpoint-probe.py')]:cli('upload-helper','upload',sid,ROOT/'scripts'/local,'/workspace/'+remote)
            cli('start-process-probe','exec',sid,'bash','-lc','nohup python3 /workspace/asb-checkpoint-probe.py serve </dev/null >/workspace/asb-checkpoint-probe.log 2>&1 &')
            runtime.start(sid,'capture','prefix-tools')
            context=dict(source_image=cfg['source_image'],cpu=2,memory_mib=512,tracked_files=list(state(sid)),session_semantics='fresh bash per tool call; filesystem persists')
            begin(out/'captured-prefix',context,state(sid));stage='prefix'
            for command in ["printf '%s:alpha\\n' \"$ASB_LABEL\" > state.txt; printf 1 > progress; cat state.txt", "printf 2 > progress; printf 'exploration failed\\n' >&2; exit 7"]:
                capture(out/'captured-prefix',cli,sid,command,cwd='/workspace/asb-tool-session',env=tool_env)
            manifest=freeze(out/'captured-prefix',state(sid));assert manifest['expected_failures']=={'002':7}
            runtime.stop(sid,'checkpoint');before=probe(sid);files=state(sid)
            assert before['open_fd_sha256']!=before['pathname_sha256']
            snapshot='asb-durable-'+out.parents[2].name;stage='checkpoint'
            cli('checkpoint','snapshot','create',sid,'--name',snapshot,timeout=180)
            rows=json.loads(cli('native-snapshot-list','snapshot','list','--output','json'));matches=[v for v in rows if snapshot in v['names']]
            assert len(matches)==1
            result.update(snapshot_alias=snapshot,snapshot_id=matches[0]['snapshotID'],original_sandbox_id=sid,process_before=before,files_at_boundary=files,next_tool_step=3,
                prefix_manifest_sha256=__import__('tool_trajectory').sha(out/'captured-prefix/manifest.json'))
            result['checks']['snapshot_after_two_actual_calls']='PASS'
            cli('delete-original','delete',sid);live.remove(sid)
        else:
            saved=bundle['capture'];snapshot=saved['snapshot_alias'];stage='restore'
            assert json.loads(cli('instances-before','list','--output','json'))==[]
            rows=json.loads(cli('native-snapshot-list','snapshot','list','--output','json'))
            assert any(v['snapshotID']==saved['snapshot_id'] and snapshot in v['names'] for v in rows)
            sid=cli('restore','start',snapshot,'--detach','--timeout','1800',timeout=180);live.add(sid);assert sid!=saved['original_sandbox_id']
            with (out/'sandbox-ids.tsv').open('a') as f:f.write(f'restored\t{sid}\t{saved["original_sandbox_id"]}\timported-native-checkpoint\n')
            after=probe(sid);before=saved['process_before']
            assert all(after[k]==before[k] for k in ('pid','start_ticks','boot_id','anonymous_sha256','open_fd_sha256','pathname_sha256'))
            assert after['count']>before['count'] and state(sid)==saved['files_at_boundary']
            result['checks']['process_memory_open_fd_files_and_boundary']='PASS'
            runtime.start(sid,'restored','next-tools')
            context=dict(source_image=cfg['source_image'],cpu=2,memory_mib=512,tracked_files=list(state(sid)),session_semantics='fresh bash per tool call; filesystem persists')
            begin(out/'captured-continuation',context,state(sid));stage='continue'
            for command in ["test \"$(cat progress)\" = 2; printf 'beta\\n' >> state.txt; printf 3 > progress; cat state.txt", "test \"$(cat progress)\" = 3; printf 4 > progress; sha256sum state.txt; cat progress"]:
                value=capture(out/'captured-continuation',cli,sid,command,cwd='/workspace/asb-tool-session',env=tool_env);assert value['exit_code']==0
            final=state(sid);freeze(out/'captured-continuation',final)
            import hashlib
            assert final['/workspace/asb-tool-session/state.txt']==hashlib.sha256(b'durable-demo:alpha\nbeta\n').hexdigest()
            assert final['/workspace/asb-tool-session/progress']==hashlib.sha256(b'4').hexdigest()
            result.update(snapshot_alias=snapshot,snapshot_id=saved['snapshot_id'],original_sandbox_id=saved['original_sandbox_id'],restored_sandbox_id=sid,
                process_before=before,process_after=after,files_at_boundary=saved['files_at_boundary'],files_after_continuation=final,executed_global_steps=[3,4],next_tool_step=3)
            result['checks']['only_steps_three_and_four_execute_and_final_state']='PASS'
            runtime.stop(sid,'restore-complete');cli('delete-restored','delete',sid);live.remove(sid)
            cli('delete-imported-snapshot','template','delete',snapshot);snapshot=None
        result['status']='PASS';success=True
    except Exception as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        for sid in list(live):
            try:runtime.stop(sid);cli('cleanup-owned-vm','delete',sid);live.remove(sid)
            except Exception as error:result.setdefault('cleanup_errors',[]).append(repr(error))
        cleanup['owned_vms_removed']=not live
        cleanup['collector_sessions_complete']=bool(runtime.sessions) and all(s.get('status')=='PASS' for s in runtime.sessions)
        try:cleanup['service_sandbox_list_empty']=json.loads(cli('list-after','list','--output','json'))==[]
        except Exception:cleanup['service_sandbox_list_empty']=False
        if args.phase=='restore':cleanup['imported_snapshot_removed']=snapshot is None
        result.update(cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        if not all(cleanup.values()):result['status']='FAIL'
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','reference','config-home','output','bundle'):parser.add_argument('--'+name,type=Path,required=name=='inputs')
    for name in ('container','service-url'):parser.add_argument('--'+name)
    parser.add_argument('--phase',choices=['capture','restore'],default='capture');parser.add_argument('--check-only',action='store_true')
    raise SystemExit(run(parser.parse_args()))
