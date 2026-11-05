"""Capture configured model/shell tools and replay frozen calls in clean VMs."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import shlex
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
    task_path=getattr(args,'task_config',None)
    task=None
    if task_path:
        from online_task import load,state_request
        task=load(task_path,args.inputs)
    if task and task['profile']=='prettier6604':
        from prettier6604_inputs import validate as prettier_validate
        cfg,_,_=prettier_validate()
    else:cfg,_,_,_=validate(args.inputs)
    memory=task['memory_mib'] if task else 512
    source=getattr(args,'replay_source',None)
    if getattr(args,'light_monitor',False) and not (source and task and not getattr(args,'agent_config',None)):
        raise ValueError('light tool monitoring requires frozen task replay without a model or new capture')
    physical_after=getattr(args,'physical_after',None) or []
    if source:
        from tool_trajectory import validate as validate_trace
        validate_trace(source)
        assert task and not getattr(args,'agent_config',None)
        source_context=json.loads((source/'context.json').read_text())
        assert source_context['source_image']==cfg['source_image'] and source_context['memory_mib']==memory
        if 'task_id' in source_context:assert source_context['task_id']==task['task_id']
        else:
            # The original verified CSV capture predates the task_id context field.
            assert task['task_id']=='csv-category-sum-v1' and source_context['tracked_files']==task['tracked_files']
            assert (source.parent/'task-check-capture.json').is_file()
    if physical_after:
        assert len(set(physical_after))==len(physical_after) and all(0<=i<=validate_trace(source)['action_count'] for i in physical_after)
    if physical_after:assert source and task['profile']=='prettier6604'

    online = getattr(args,'agent_config',None)
    if online:
        from model_tool_agent import configuration
        configuration(online)
    if args.check_only:
        print('status=VALIDATED mode='+('online-agent' if online else 'tool-capture-demo'));return 0
    out=args.output.resolve();out.mkdir(exist_ok=False)
    assert out.parent.name=='controller' and out.parent.parent.name=='mount' and out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    live=set();events=[];lock=threading.Lock();counter=0;stage='setup';started=time.monotonic_ns()
    result=dict(status='FAIL',tool_capture_demo=True,checks={},cpu=2,memory_mib=memory,source_image=cfg['source_image'],
        scope='actual tool-interface collection and frozen replay from the same clean image; fresh shell per call; no model inference, persistent PTY or benchmark task acceptance')
    if online or task:result.update(online_agent=bool(online),task_id=task['task_id'] if task else 'csv-category-sum-v1',scope='model-driven task or frozen replay in clean VM; model wait, tool calls and independent evaluation separate')
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
    light=getattr(args,'light_monitor',False)
    # Directory upload into an existing /workspace appends the bundle name.
    # A new owned destination keeps the script and its helper at known paths.
    runtime=RuntimeMonitor(out,cli,container=args.container,light=light,
        guest_script='/workspace/asb-tool-runtime/asb-runtime-monitor.py' if light else None)
    physical=None
    def interrupted(*_):raise RuntimeError('tool capture interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    def create(role):
        sid=call('cold-'+role,[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image'],'--memory-mib',str(memory)],610)
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
        if online and not task:
            from online_task import load,state_request
            task=load(ROOT/'configs/online-task-csv.json')
        if physical_after:
            from task_physical_cache import TaskPhysicalCache
            physical=TaskPhysicalCache(out,args.container,cli)
        first=create('replay' if source else 'capture');stage='capture'
        if physical:physical.ready()

        catalog=resources(args.config_home,out/'native-catalog')
        assert len(catalog['resources']['instances'])==1 and catalog['resources']['instances'][0]['sandboxID']==first
        result['native_catalog']={k:len(v) for k,v in catalog['resources'].items()}
        context=dict(source_image=cfg['source_image'],cpu=2,memory_mib=memory,tracked_files=['/workspace/asb-tool-session/state.txt'],
            session_semantics='fresh bash per tool call; filesystem persists',model='not used',initial_image_state='new sandbox, no inherited command state')
        def seed(sid):
            if not task:return
            if task.get('repo'):
                head=cli('repository-head','exec',sid,'git','-C',task['repo'],'rev-parse','HEAD')
                assert head==task['base_commit']
                assert cli('repository-initial-status','exec',sid,'git','-C',task['repo'],'status','--porcelain')==task['initial_git_status'].strip()
                assert cli('initial-source-hash','exec',sid,'sha256sum',task['tracked_files'][0]).split()[0]==task['initial_source_sha256']
            for path,content in task['seed_files'].items():
                cli('seed-task','exec',sid,'python3','-c','from pathlib import Path; Path('+repr(path)+').write_text('+repr(content)+')')
        seed(first)
        if task:
            context.update(tracked_files=task['tracked_files'],state_request=state_request(task),task_id=task['task_id'],
                model=configuration(online)['model'] if online else 'not used',output_policy='jest-timing-v1' if task.get('repo') else 'strict')
            shutil.copy2(task_path or ROOT/'configs/online-task-csv.json',out/'task-config.json')
        if online:shutil.copy2(online,out/'agent-config.json')
        initial=request(cli,first,context.get('state_request',dict(kind='state',files=context['tracked_files'])))
        if not (online or task):assert all(v is None for v in initial.values())
        if source:shutil.copytree(source,out/'captured');context=json.loads((out/'captured/context.json').read_text())
        else:begin(out/'captured',context,initial)
        tool_env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8','LC_ALL':'C.UTF-8','ASB_LABEL':'capture-demo','TERM':'dumb','NO_COLOR':'1'}
        commands=["printf '%s:alpha\\n' \"$ASB_LABEL\" > state.txt; cat state.txt",
                  "printf 'exploration failed\\n' >&2; exit 7",
                  "printf 'beta\\n' >> state.txt; cat state.txt",
                  "printf '%s\\n' \"$PWD\" \"$ASB_LABEL\"; sha256sum state.txt"]
        def task_check(sid,role):
            runtime.mark(sid,'independent-task-check')
            if task.get('repo'):
                from exp1_official_task import guest_command
                from audit_prettier6604_official import inspect,signature
                cli('upload-oracle','upload',sid,ROOT/'scripts/prettier6604_check.cjs','/workspace/prettier-check.cjs')
                oracle=request(cli,sid,dict(kind='exec',command='. /usr/local/nvm/nvm.sh && nvm use 20.16.0 >/dev/null && node /workspace/prettier-check.cjs',cwd=task['cwd'],env=tool_env,timeout_seconds=30))
                cli('upload-independent-tests','upload',sid,args.inputs/'test.patch','/workspace/independent-test.patch')
                command='git checkout HEAD -- tests && '+guest_command(args.inputs,'/workspace/independent-test.patch')
                receipt=request(cli,sid,dict(kind='exec',command=command,cwd=task['cwd'],env=tool_env,timeout_seconds=150))
                import base64
                content=base64.b64decode(receipt['stdout_b64']).decode()
                (out/('official-'+role+'.log')).write_text(content)
                evaluation=inspect(args.inputs,content)
                receipt.update(official_signature=signature(evaluation),oracle=oracle,
                    task_outcome='PASS' if evaluation['status']=='PASS' and oracle['exit_code']==0 else 'FAIL')
            else:
                receipt=request(cli,sid,dict(kind='exec',command=task['check_command'],cwd=task['cwd'],env=tool_env,timeout_seconds=task['check_timeout_seconds']))
                receipt['task_outcome']='PASS' if receipt['exit_code']==0 else 'FAIL'
            write(out/('task-check-'+role+'.json'),receipt)
            return receipt['task_outcome']
        if online:
            from model_tool_agent import run as model_run
            stage='online-model-tools'
            def post(step):write(step/'state-after.json',request(cli,first,context['state_request']))
            try:result['online']=model_run(online,out/'online',out/'captured',cli,first,tool_env,lambda phase:runtime.mark(first,phase),task=task,after_tool=post)
            except Exception:
                result['online']=json.loads((out/'online/result.json').read_text())
                steps=list((out/'captured/steps').iterdir())
                if not steps or not all((step/'state-after.json').exists() for step in steps):raise
                result['model_completion']='FAIL; completed tool prefix preserved'
        elif not source:
            for command in commands:capture(out/'captured',cli,first,command,cwd='/workspace/asb-tool-session',env=tool_env)
        if not source:
            final=request(cli,first,context.get('state_request',dict(kind='state',files=context['tracked_files'])))
            manifest=freeze(out/'captured',final)
            if not task:assert manifest['expected_failures']=={'002':7}
            if task:
                stage='independent-task-check'
                result['task_outcome']=task_check(first,'capture')
                result['checks']['independent_task']=result['task_outcome']
            result['checks']['actual_calls_captured_and_frozen']='PASS'
            runtime.stop(first,'capture-complete');cli('delete-capture','delete',first);live.remove(first)
            second=create('replay');seed(second)
        else:second=first
        stage='replay'
        def boundary(step):
            if step in physical_after:
                runtime.mark(second,'physical-after-'+str(step))
                physical.measure(second,'tool-'+f'{step:03}',step,retain_fixture_pages=True)
        if 0 in physical_after:boundary(0)
        proof=replay(out/'captured',out/'replayed',cli,second,after_step=boundary if physical else None,output_policy=task.get('output_policy') if task else None)
        if task:
            replay_outcome=task_check(second,'replay')
            result['checks']['independent_replayed_task']=replay_outcome
            if source:
                original=source.parent/'task-check-capture.json'
                shutil.copy2(original,out/'task-check-capture.json')
                original_check=json.loads(original.read_text())
                result['task_outcome']=original_check.get('task_outcome','PASS' if original_check['exit_code']==0 else 'FAIL')
                result['checks']['independent_task']=result['task_outcome']
            assert replay_outcome==result['task_outcome'],'task evaluation changed after replay'
            a=json.loads((out/'task-check-capture.json').read_text());b=json.loads((out/'task-check-replay.json').read_text())
            assert a['exit_code']==b['exit_code']
            if task.get('repo'):assert a['official_signature']==b['official_signature'] and a['oracle']['exit_code']==b['oracle']['exit_code']
            result['checks']['task_outcome_preserved']='PASS'
        if physical:
            assert len(physical.points)==len(physical_after)
            result['physical_cache_after_steps']=physical_after
        result['checks']['frozen_replay_matches_calls_and_final_file']='PASS'
        runtime.stop(second,'replay-complete');cli('delete-replay','delete',second);live.remove(second)
        result.update(status='PASS',replay=proof)
    except Exception as error:
        result.update(status='FAIL',failed_stage=stage,error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        if physical:
            physical.close();cleanup['physical_tracer_stopped']=physical.tracer.poll() is not None
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
    parser.add_argument('--light-monitor',action='store_true',help='Guest meminfo only; keep tool results and task checks')
    parser.add_argument('--task-config',type=Path)
    parser.add_argument('--replay-source',type=Path)
    parser.add_argument('--physical-after',type=int,nargs='+')
    parser.add_argument('--agent-config',type=Path,help='Host-only model settings for real online capture followed by frozen replay')
    raise SystemExit(run(parser.parse_args()))
