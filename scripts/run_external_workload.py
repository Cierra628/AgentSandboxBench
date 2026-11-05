"""Run one pinned external workload inside the established owned service lifecycle."""
import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

from external_workload import C4,CYTHON,CYTHON_VALIDATION,budgets,load,guest_runner,validate_timings,write_inputs,validate_c4_outputs,validate_cython_outputs
from external_guest_proxy import environment_script,NETWORK_CHECK,allow_guest_relay,capture_relay_counters
from sandbox_runtime_monitor import RuntimeMonitor

ROOT=Path(__file__).resolve().parents[1]


def _write_events(out,events):
    (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')


def logged_call(out,env,events,lock,index,label,argv,timeout=60):
    """Keep the legacy duration; measure command return before host log writes."""
    start=time.monotonic_ns();code=None;command_start=command_end=None
    stdout_start=stdout_end=None;stdout_completed=False
    try:
        with (out/(f'{index:03}-'+label+'.stderr')).open('x') as error:
            command_start=time.monotonic_ns()
            try:
                result=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=error,text=True,timeout=timeout)
                code=result.returncode
            finally:command_end=time.monotonic_ns()
        stdout_start=time.monotonic_ns()
        try:
            (out/(f'{index:03}-'+label+'.stdout')).write_text(result.stdout)
            stdout_completed=True
        finally:stdout_end=time.monotonic_ns()
    finally:
        wait_start=time.monotonic_ns()
        with lock:
            acquired=time.monotonic_ns()
            event=dict(stage=label,start_monotonic_ns=start,duration_ns=time.monotonic_ns()-start,exit_code=code,
                call_timing_version=2,command_start_monotonic_ns=command_start,command_end_monotonic_ns=command_end,
                command_duration_ns=command_end-command_start if command_start is not None else None,
                command_returned=code is not None,stdout_log_start_monotonic_ns=stdout_start,stdout_log_end_monotonic_ns=stdout_end,
                stdout_log_duration_ns=stdout_end-stdout_start if stdout_start is not None else None,
                stdout_log_completed=stdout_completed,event_lock_wait_ns=acquired-wait_start)
            events.append(event);log_start=time.monotonic_ns();logged=False
            try:
                _write_events(out,events);logged=True
            finally:
                log_end=time.monotonic_ns()
                event.update(events_log_duration_ns=log_end-log_start,events_log_completed=logged,
                    call_end_monotonic_ns=log_end,call_elapsed_ns=log_end-start)
    if result.returncode:raise RuntimeError(label+': exit '+str(result.returncode))
    return result.stdout.strip()


def call_timing_summary(events,final_flush_ns):
    replay=next((dict(e) for e in events if e['stage']=='replay'),None)
    return dict(version=2,replay=replay,final_events_flush_ns=final_flush_ns,
        stdout_log_cumulative_ns=sum(e['stdout_log_duration_ns'] for e in events if e['stdout_log_duration_ns'] is not None),
        events_log_cumulative_ns=sum(e['events_log_duration_ns'] for e in events),
        event_lock_wait_cumulative_ns=sum(e['event_lock_wait_ns'] for e in events),
        scope='Command duration includes CLI startup, communication, output capture, wait and exit, before host stdout logging; stderr still streams to its file during the command. Legacy duration retains stdout logging and event-lock wait, but excludes the following events.json write. Cumulative logging spans can overlap other calls; controller_elapsed_ns remains whole-controller wall time before result.json serialization/write.')


def run(args):
    cfg,replay,expected=load(args.config,args.upstream)
    density=None
    if getattr(args,'density_context',None):
        from host_density import context,tools_finished
        args.density_task='c4'
        density=context(args.density_context,args)
        if cfg['task_id']!=C4:raise ValueError('external density supports only C4')
    c4=cfg['task_id']==C4;cython=cfg['task_id']==CYTHON
    sampling=getattr(args,'sampling','on')
    if sampling not in ('on','off') or sampling=='off' and cfg['task_id'] not in (C4,CYTHON,'django__django-16819'):raise ValueError('sampling off is limited to the explicit C4/Django/Cython controls')
    guest_tools='/probe/input' if c4 or cython else '/workspace/asb-external-tools'
    out=args.output;out.mkdir(exist_ok=False)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home))
    state=dict(status='FAIL',task_id=cfg['task_id'],image=cfg['image'],memory_mib=cfg['memory_mib'],cpu=2,
        source_repository=cfg['repository'],source_revision=cfg['revision'],source_run=cfg['run'],
        validation_scope=cfg['validation_scope'],measurement='guest meminfo every 0.1s; no freeze, root scan, PSS or page pinning')
    state['sampling']=sampling
    if sampling=='off':state.update(measurement='Memory sampling disabled; original tool timing and validation retained',memory='NOT_MEASURED')
    events=[];sid=None;runtime=None;begin=time.monotonic_ns();lock=threading.Lock();counter=0
    def call(label,argv,timeout=60):
        nonlocal counter
        with lock:index=counter;counter+=1
        return logged_call(out,env,events,lock,index,label,argv,timeout)
    def cli(label,*argv,timeout=60):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    try:
        revision=call('repository-version',['git','-C',args.upstream,'rev-parse','HEAD'])
        assert revision==cfg['revision'],'external repository version differs'
        bundle=out/'bundle';bundle.mkdir()
        write_inputs(bundle,replay,cfg['task_id'])
        (bundle/'runner.sh').write_text(guest_runner(len(expected),task_id=cfg['task_id']))
        clock=Path(args.upstream)/'experiments/container-screening/harness/clock_ns.c'
        shutil.copy2(clock,out/'clock_ns.c')
        call('compile-clock',['gcc','-O2','-Wall','-Wextra','-Werror','-static',clock,'-o',bundle/'clock-ns'])
        sid=density['sandbox_id'] if density else call('cold-create',[sys.executable,
            ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,
            '--image',cfg['image'],'--memory-mib','8192','--allow-8gib'],610)
        state['sandbox_id']=sid
        if density:
            cli('no-proxy-environment','exec',sid,'bash','-lc',
                'test -z "${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}"')
        # Same source baseline and Conda environment checks as the published run.
        git_env='export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0=/testbed; '
        if c4:
            cli('source-baseline','exec',sid,'bash','-lc','test -d /app/c4_sample && command -v python && command -v python3 && command -v uv')
            state['source_baseline_scope']='Fixed image, sample directory and required tools; no input contents read'
        elif cython:
            cli('source-baseline','exec',sid,'bash','-lc',
                'test ! -e /app/pyknotid && command -v bash && command -v python && command -v python3 && command -v pip && command -v git')
            state['source_baseline_scope']='Fixed image, absent clone destination and required tools; no source or NumPy pre-read'
            (bundle/'guest-env.sh').write_text(environment_script(os.environ['ASB_EXTERNAL_GUEST_RELAY_URL']))
        else:
            source=cli('source-baseline','exec',sid,'bash','-lc',
                git_env+'cd /testbed && command -v python && git hash-object django/db/migrations/operations/models.py && '
                'git merge-base --is-ancestor 0b0998dc151feb77068e2387c34cc50ef6b356ae HEAD && '
                'git diff --exit-code 0b0998dc151feb77068e2387c34cc50ef6b356ae HEAD -- django')
            assert '/envs/testbed/bin/python' in source and cfg['source_blob'] in source
        cli('setup-directory','exec',sid,'mkdir','-p','/workspace',*(['/probe'] if c4 or cython else []))
        cli('upload-tools','upload',sid,bundle,guest_tools)
        # Upload preserves bytes, not executable mode; verify the owned timer before replay.
        clock_ready=cli('prepare-clock','exec',sid,'bash','-lc',
            f'chmod 755 {guest_tools}/clock-ns && {guest_tools}/clock-ns')
        assert int(clock_ready)>0,'guest clock preflight failed'
        if cython:
            relay_state=Path(os.environ['ASB_EXTERNAL_GUEST_RELAY_STATE'])
            state['guest_relay_rule']=allow_guest_relay(relay_state,sid)
            before=capture_relay_counters(relay_state,'before-head')
            try:ready=cli('guest-network','exec',sid,'bash','-lc',NETWORK_CHECK,timeout=55)
            finally:after=capture_relay_counters(relay_state,'after-head')
            assert ready=='guest-network-ready'
            assert before['allowed_packets'] is not None and after['allowed_packets'] is not None and after['allowed_packets']>before['allowed_packets'],'guest HEAD did not use the owned nft relay rule'
            state['guest_network_preflight']='PASS'
        if sampling=='on':
            runtime=RuntimeMonitor(out,cli,light=True,guest_script='/workspace/asb-external-monitor/sandbox_runtime_monitor.py')
            runtime.start(sid,'backbone','external-frozen-tools')
        state['replay_start_monotonic_ns']=time.monotonic_ns()
        cli('replay','exec',sid,'bash','-lc',f'bash {guest_tools}/runner.sh',timeout=budgets(cfg['task_id'])['replay'])
        state['replay_end_monotonic_ns']=time.monotonic_ns()
        if runtime:runtime.stop(sid,'tools-finished')
        cli('download-tools','download',sid,guest_tools,out,timeout=60)
        downloaded=out/Path(guest_tools).name;shutil.copy2(downloaded/'tools.tsv',out/'tools.tsv')
        with (out/'tools.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
        assert len(rows)==len(expected)
        validate_timings(rows)
        differences=[dict(step=i,actual=int(a['exit_code']),expected=int(b['exit_code'])) for i,(a,b) in enumerate(zip(rows,expected),1) if int(a['exit_code'])!=int(b['exit_code'])]
        state['exit_differences']=differences
        assert not differences,'published reference exits differ'
        if c4:
            validate_c4_outputs(downloaded/'logs')
            state['roundtrip_validation']='PASS'
            if density:tools_finished(density,time.monotonic_ns())
            cli('syntax-validation','exec',sid,'bash','-lc',
                'test -s /app/compress.py && test -s /app/decompress.py && python3 -m py_compile /app/compress.py /app/decompress.py')
        elif cython:
            validate_cython_outputs(downloaded/'logs')
            result=cli('extension-validation','exec',sid,'bash','-lc','source /probe/input/guest-env.sh && '+CYTHON_VALIDATION)
            assert 'compiled extensions import successfully' in result and 'source_repo_commit:' in result
            (out/'extension-validation.txt').write_text(result+'\n')
            state.update(functional_validation='PASS',test_validation='PASS',extension_validation='PASS')
        else:
            cli('syntax-validation','exec',sid,'bash','-lc',
                git_env+'cd /testbed && git rev-parse HEAD && git diff --stat && python -m py_compile django/db/migrations/operations/models.py')
        state.update(status='PASS',actions=len(rows),tool_duration_sum_ns=sum(int(r['duration_ns']) for r in rows),
            strict_exit_match=True,syntax_validation='PASS',official_tests='NOT_RUN_ORIGINAL_SAMPLE_ROUNDTRIP_ONLY' if c4 else 'NOT_RUN_ORIGIN_SYNTAX_ONLY',source_baseline='PASS')
        if cython:state.update(syntax_validation='NOT_REQUESTED_ORIGINAL_EXTENSION_CHECK',official_tests='NOT_RUN_ORIGINAL_18_TESTS_TWICE_AND_EXTENSION_CHECK')
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        if sampling=='off':cleanup['memory_sampling_disabled']=True
        if runtime and sid in runtime.active:
            try:runtime.stop(sid,'failure-cleanup')
            except Exception as error:cleanup['collector_error']=repr(error);state['status']='FAIL'
        if density:
            # The coordinator created every guest and owns their sequential deletion.
            cleanup['sandbox_deletion']='DELEGATED_TO_DENSITY_COORDINATOR'
        else:
            if sid:
                try:cli('delete-own-guest','delete',sid);cleanup['sandbox_removed']=True
                except Exception as error:cleanup['sandbox_error']=repr(error);state['status']='FAIL'
            try:
                guests=json.loads(cli('remaining-guests','list','--output','json'))
                assert not guests;cleanup['service_sandbox_list_empty']=True
            except Exception as error:cleanup['list_error']=repr(error);state['status']='FAIL'
        # A row cannot contain the duration of its own JSON write until that write returns.
        # Flush the final in-memory receipts once, after managed collectors have stopped.
        flush_start=time.monotonic_ns()
        try:
            with lock:_write_events(out,events)
        except Exception as error:
            state.update(status='FAIL',events_final_flush_error=repr(error))
        flush_ns=time.monotonic_ns()-flush_start
        state.update(cleanup=cleanup,call_timing=call_timing_summary(events,flush_ns),controller_elapsed_ns=time.monotonic_ns()-begin)
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('config','upstream','output','config-home'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--density-context',type=Path)
    p.add_argument('--service-url',required=True);p.add_argument('--container',required=True)
    p.add_argument('--sampling',choices=('on','off'),default='on',help='C4/Django/Cython: disable memory sampling, not tool timing or validation')
    raise SystemExit(run(p.parse_args()))
