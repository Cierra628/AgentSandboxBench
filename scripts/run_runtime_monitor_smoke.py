"""Owned VMs, frozen Sphinx prefix, two snapshot children and continuous performance sampling."""
import argparse
import asyncio
import csv
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import threading
from types import SimpleNamespace

from sphinx7590_inputs import ROOT, validate,sha
from task_metrics_prefix import controller_module, check_prefix
from xarray6992_inputs import frozen_shell
from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
from report_sandbox_runtime import write_report


def check_continuation(out,reference,cfg,role):
    directory=out/'rollouts'/role/'trace_cpp_parser';original=reference/'rollouts/backbone/trace_cpp_parser'
    assert sha(directory/'actions.tsv')==sha(original/'actions.tsv')==cfg['input_sha256']['segments/trace_cpp_parser/actions.tsv']
    with (directory/'steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
    assert [r['step'] for r in steps]==[f'{i:03}' for i in range(1,8)]
    assert all(r['branch']==role and r['round']=='trace_cpp_parser' for r in steps)
    actual=[int(r['exit_code']) for r in steps];expected=cfg['expected_exit_codes']['trace_cpp_parser']
    assert actual==expected, f'{role} frozen continuation exits: expected {expected}, observed {actual}'
    for kind in ('stdout','stderr'):
        for r in steps:assert sha(directory/kind/(r['step']+'.log'))==sha(original/kind/(r['step']+'.log'))
    assert sha(directory/'patch.diff')==sha(original/'patch.diff')
    return dict(status='PASS',action_count=7,global_steps='7-13',expected_nonzero_exits_preserved=True,
        guest_action_sum_ns=sum(int(r['duration_ns']) for r in steps))


def run(args):
    cfg,workload,_,_=validate(args.inputs)
    find_diagnostic=getattr(args,'find_diagnostic',False)
    from report_xarray6992 import audit
    reference,_,_=audit(args.reference.resolve()); assert reference['config']==cfg
    if args.check_only:
        print('status=VALIDATED task=sphinx7590 prefix_actions=6 mode='+('find-diagnostic-only' if find_diagnostic else 'two-child-frozen-continuation'));return 0
    out=args.output.resolve();out.mkdir(exist_ok=False)
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(args.config_home),HOST_MONITOR_LIGHT='1')
    live=set();snapshot=None;monitor=None;events=[];success=False;stage='setup'
    started=time.monotonic_ns()
    result=dict(status='FAIL',runtime_monitor_smoke=True,task_id=cfg['task_id'],checks={},
                cpu=2,memory_mib=512,fixture_bytes=64*1024*1024,
                process_probe_mode='single Python loop; no repeated cat/sleep child processes',
                scope='six frozen prefix actions; two snapshot children each continue original steps 7-13; CPU/file-I/O/idle calibration and parent sampling restart; not full task or formal overhead comparison')
    event_lock=threading.Lock();event_counter=0
    def call(label,argv,timeout=60):
        nonlocal event_counter
        with event_lock:
            index=event_counter;event_counter+=1
        begin=time.monotonic_ns();stamp=time.time_ns();code=None
        try:
            with (out/f'{index:04}-{label}.stderr').open('x') as log:
                proc=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
            (out/f'{index:04}-{label}.stdout').write_text(proc.stdout);code=proc.returncode
            if code:raise RuntimeError(label+': exit '+str(code))
            return proc.stdout.strip()
        finally:
            with event_lock:
                events.append(dict(stage=label,timestamp_ns=stamp,start_monotonic_ns=begin,duration_ns=time.monotonic_ns()-begin,exit_code=code))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
    def cli(label,*argv,timeout=60):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def guest(sid,label,command):return cli(label,'exec',sid,'bash','-lc',command)
    runtime=RuntimeMonitor(out,cli,container=args.container)
    def probe_find(sid,label):
        value=json.loads(guest(sid,label,frozen_shell('python3 /workspace/asb-find-diagnostic.py /workspace/asb-find-command.txt')))
        assert value['status']=='CAPTURED' and value['command_sha256']==find_row['sha256']
        result.setdefault('find_diagnostics',{})[label]=value
        (out/'find-diagnostics.json').write_text(json.dumps(result['find_diagnostics'],indent=2)+'\n')
    if find_diagnostic:
        result['scope']='six strict frozen prefix actions and four VM/five sampling sessions; find errors before/after snapshot with parent/restored sampler on/off; diagnostic capture only, not steps 7-13 replay acceptance'
        result['find_diagnostic_only']=True
    def cancel(signum,frame):raise RuntimeError('owned monitoring smoke interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    try:
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container]))
        assert labels.get('agentsandboxbench.run_id')==out.parents[2].name
        assert labels.get('agentenv.experiment')=='isolated-storage-check'
        shutil.copytree(args.inputs,out/'prepared-input');shutil.copytree(workload,out/'frozen-input')
        runner=out/'frozen-input/guest-runner.sh';shutil.copy2(runner,runner.with_name('guest-runner.original.sh'))
        runner.write_text(boundary_runner(runner.read_text(),guest_phases=True))
        module=controller_module(out/'frozen-input')
        def shell(*argv,capture=True):
            argv=list(argv[1:])
            if argv[0]=='exec' and argv[-1].endswith('bash /workspace/mixfs-workload/guest-runner.sh'):argv[-1]=frozen_shell(argv[-1])
            return cli('prefix-'+argv[0],*argv)
        module.shell=shell
        class Controller(module.Controller):
            async def physical_memory(self,label):return None
        controller=Controller(SimpleNamespace(raw_dir=out,system='agentenv',method='backbone',timeout=90))
        controller.phase('startup')
        with (out/'host-monitor.log').open('x') as log:
            monitor=subprocess.Popen(['bash',ROOT/'IncrementalDAX_moti/motivation/experiments/lib/host-memory-monitor.sh',out/'host/memory-samples.tsv',controller.phase_file,'.1',args.container],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        def create(role):
            sid=call('cold-'+role,[sys.executable,ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,'--service-url',args.service_url,'--image',cfg['source_image'],'--memory-mib','512'],610)
            live.add(sid);controller.record('sandbox-ids.tsv',f'{role}\t{sid}\t\troot');runtime.start(sid,role,'setup');return sid
        first=create('backbone')
        # Match original initialization: importing Sphinx creates its __pycache__.
        from task_metrics_prefix import fingerprint
        before_source=fingerprint(lambda label,*argv,**kw:cli(label,*argv,**kw).encode(),first,'source-before')
        assert before_source['head']==cfg['base_commit']
        assert {k:before_source['files'][k] for k in cfg['base_file_sha256']}==cfg['base_file_sha256']
        cli('upload-oracle','upload',first,ROOT/'scripts/sphinx7590_check.py','/workspace/sphinx7590_check.py')
        baseline=json.loads(guest(first,'negative-control','cd /testbed && /opt/miniconda3/envs/testbed/bin/python /workspace/sphinx7590_check.py --baseline'))
        assert baseline['status']=='PASS'
        cli('upload-frozen','upload',first,out/'frozen-input','/workspace/mixfs-workload')
        stage='frozen-prefix';controller.phase(stage);runtime.mark(first,stage)
        asyncio.run(controller.run_path(first,'backbone',['read_repo']))
        result['prefix']=check_prefix(out,cfg,args.reference,'boundary')
        assert before_source==fingerprint(lambda label,*argv,**kw:cli(label,*argv,**kw).encode(),first,'source-after-prefix')
        result['checks']['six_frozen_actions_match_reference']='PASS'
        runtime.mark(first,'waiting')
        idle=create('idle');runtime.mark(idle,'idle')
        stage='file-cache-workload';controller.phase(stage);runtime.mark(first,stage)
        fixture="python3 - <<'EOF'\nfrom pathlib import Path\nimport hashlib,time,os\np=Path('/workspace/asb-cache-fixture.bin')\nwith p.open('wb') as f:f.write(b'x'*(64*1024*1024));f.flush();os.fsync(f.fileno())\nprint(hashlib.sha256(p.read_bytes()).hexdigest());Path('/workspace/asb-next-step').write_text('1')\nend=time.monotonic()+2\nwhile time.monotonic()<end:hashlib.sha256(b'x'*1024*1024).digest()\ntime.sleep(1)\nEOF"
        before_hash=guest(first,'create-cache-fixture',fixture)
        cli('upload-process-probe','upload',first,ROOT/'scripts/exp1-process-probe.sh','/workspace/exp1-process-probe.sh')
        guest(first,'process-start','nohup python3 /workspace/asb-runtime-monitor.py process-probe </dev/null >/dev/null 2>&1 & sleep 0.3')
        before=dict(line.split('=',1) for line in guest(first,'process-before','bash /workspace/exp1-process-probe.sh').splitlines())
        if find_diagnostic:
            with (workload/'segments/trace_cpp_parser/actions.tsv').open() as f:
                find_row=list(csv.DictReader(f,delimiter='\t'))[4]
            command_file=out/'find-command.txt';command_file.write_text(find_row['command'])
            cli('upload-find-probe','upload',first,ROOT/'scripts/guest_find_diagnostic.py','/workspace/asb-find-diagnostic.py')
            cli('upload-find-command','upload',first,command_file,'/workspace/asb-find-command.txt')
            probe_find(first,'parent-monitor-on')
        stage='checkpoint';controller.phase(stage);runtime.stop(first,stage)
        if find_diagnostic:probe_find(first,'parent-monitor-off')
        snapshot='runtime-monitor-'+out.parents[2].name
        cli('checkpoint','snapshot','create',first,'--name',snapshot,timeout=150)
        runtime.start(first,'backbone','after-checkpoint')
        parent_after=dict(line.split('=',1) for line in guest(first,'parent-after-checkpoint','sleep 1; bash /workspace/exp1-process-probe.sh').splitlines())
        assert all(before[k]==parent_after[k] for k in ('pid','start_ticks','boot_id')) and int(parent_after['count'])>int(before['count'])
        result['checks']['parent_process_and_sampling_continue_after_capture']='PASS'
        runtime.stop(first,'delete-parent')
        cli('delete-original','delete',first);live.remove(first)
        restored=cli('restore','start',snapshot,'--detach','--timeout','1800',timeout=150);live.add(restored)
        assert restored!=first
        controller.record('sandbox-ids.tsv',f'backbone\t{restored}\t{first}\trestored')
        if find_diagnostic:probe_find(restored,'restored-monitor-off')
        runtime.start(restored,'backbone','restored-checks')
        if find_diagnostic:probe_find(restored,'restored-monitor-on')
        after_hash=guest(restored,'verify-restored-fixture',"python3 - <<'EOF'\nfrom pathlib import Path\nimport hashlib,time\nassert Path('/workspace/asb-next-step').read_text()=='1';print(hashlib.sha256(Path('/workspace/asb-cache-fixture.bin').read_bytes()).hexdigest());Path('/workspace/asb-next-step').write_text('2');time.sleep(2)\nEOF")
        assert before_hash==after_hash
        after=dict(line.split('=',1) for line in guest(restored,'process-after','bash /workspace/exp1-process-probe.sh').splitlines())
        assert all(before[k]==after[k] for k in ('pid','start_ticks','boot_id')) and int(after['count'])>int(before['count'])
        (out/'restore-proof.json').write_text(json.dumps(dict(parent=first,restored=restored,fixture_sha256=before_hash,progress_before='1',progress_after='2',process_before=before,process_after=after),indent=2)+'\n')
        result['checks']['full_vm_restore_files_progress_and_process']='PASS'
        branch=cli('restore-branch','start',snapshot,'--detach','--timeout','1800',timeout=150);live.add(branch)
        assert branch not in (first,restored,idle)
        controller.record('sandbox-ids.tsv',f'branch\t{branch}\t{first}\trestored-branch')
        runtime.start(branch,'branch','branch-continuation')
        branch_hash=guest(branch,'branch-continue',"python3 - <<'EOF'\nfrom pathlib import Path\nimport hashlib,time\nassert Path('/workspace/asb-next-step').read_text()=='1'\nprint(hashlib.sha256(Path('/workspace/asb-cache-fixture.bin').read_bytes()).hexdigest())\nPath('/workspace/asb-next-step').write_text('3');Path('/workspace/asb-branch-only').write_text('branch')\nend=time.monotonic()+2\nwhile time.monotonic()<end:hashlib.sha256(b'y'*1024*1024).digest()\nEOF")
        assert branch_hash==before_hash
        branch_process=dict(line.split('=',1) for line in guest(branch,'branch-process','bash /workspace/exp1-process-probe.sh').splitlines())
        assert all(before[k]==branch_process[k] for k in ('pid','start_ticks','boot_id')) and int(branch_process['count'])>int(before['count'])
        assert guest(restored,'restored-branch-isolation','test "$(cat /workspace/asb-next-step)" = 2; test ! -e /workspace/asb-branch-only; echo PASS')=='PASS'
        (out/'branch-proof.json').write_text(json.dumps(dict(parent=first,restored=restored,branch=branch,
            restored_progress='2',branch_progress='3',file_sha256=branch_hash,parent_process_after_capture=parent_after,
            branch_process=branch_process),indent=2)+'\n')
        result['checks']['snapshot_branch_progress_process_and_write_isolation']='PASS'
        if find_diagnostic:
            assert len(result['find_diagnostics'])==4
            for child in (restored,branch):
                assert before_source==fingerprint(lambda label,*argv,**kw:cli(label,*argv,**kw).encode(),child,'source-after-find-diagnostic')
            result['checks']['find_errors_and_pipeline_capture']='PASS'
        else:
            stage='frozen-continuation';controller.phase(stage)
            result['continuations']={}
            for child,role in ((restored,'backbone'),(branch,'branch')):
                runtime.mark(child,'frozen-continuation')
                asyncio.run(controller.run_path(child,role,['trace_cpp_parser']))
                result['continuations'][role]=check_continuation(out,args.reference,cfg,role)
                source=fingerprint(lambda label,*argv,**kw:cli(label,*argv,**kw).encode(),child,'source-after-continuation')
                assert source==before_source
            result['checks']['two_children_continue_original_steps_7_to_13']='PASS'
        # Parent collector is stopped before capture; both live VMs must have exactly one new owned sampler.
        for sid in (restored,idle,branch):
            value=guest(sid,'collector-process-count',"python3 - <<'EOF'\nfrom pathlib import Path\ncount=0\nfor p in Path('/proc').glob('[0-9]*/cmdline'):\n try:parts=p.read_bytes().split(b'\\0')\n except (FileNotFoundError,ProcessLookupError):continue\n if b'/workspace/asb-runtime-monitor.py' in parts and b'collect' in parts:count+=1\nassert count==1,count\nprint(count)\nEOF")
            assert value=='1'
        result['checks']['one_owned_collector_per_live_vm']='PASS'
        success=True
    except BaseException as error:
        import traceback
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={};errors=[]
        for sid in list(live):
            try:runtime.stop(sid)
            except Exception as error:errors.append(repr(error))
            try:cli('delete-owned','delete',sid)
            except Exception as error:errors.append(repr(error))
        try:
            assert json.loads(cli('list-after','list','--output','json'))==[]
            cleanup['service_sandbox_list_empty']=True
        except Exception as error:errors.append(repr(error))
        if snapshot and success and not errors:
            try:cli('delete-snapshot','template','delete',snapshot);cleanup['snapshot_removed']=True
            except Exception as error:errors.append(repr(error))
        if monitor:
            if monitor.poll() is not None:errors.append('host monitor unexpectedly exited')
            else:os.killpg(monitor.pid,signal.SIGTERM);monitor.wait(timeout=5)
        if errors:success=False;cleanup['errors']=errors
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if success:
        try:
            report=out.parent/'runtime-report';report.mkdir()
            result['runtime_metrics']=write_report(out,report)
            assert any(r['phase']=='idle' for g in __import__('report_sandbox_runtime').samples(out)[0].values() for chunk in g['chunks'] for r in chunk)
            result['checks']['continuous_idle_and_per_instance_curves']='PASS'
            performance=result['runtime_metrics']['performance'];assert performance['status']=='MEASURED'
            assert len(performance['per_sandbox'])==4
            assert all(r['guest_cpu_status']==r['guest_io_status']==r['host_vmm_status']=='MEASURED' for r in performance['per_sandbox'])
            sessions=runtime.sessions;parent_sessions=[s for s in sessions if s['sandbox_id']==first]
            assert len(parent_sessions)==2 and len({s['session_id'] for s in sessions})==5
            assert len({s['host_receipt']['identity']['pid'] for s in sessions})==4
            assert parent_sessions[0]['host_receipt']['identity']==parent_sessions[1]['host_receipt']['identity']
            parent_metrics=next(r for r in performance['per_sandbox'] if r['sandbox_id']==first)
            assert parent_metrics['guest_cpu_observed_peak_percent']>0
            assert sum(d['write_bytes'] for v in parent_metrics['guest_devices'].values() if v['is_partition']=='0' for d in v['session_deltas'])>=64*1024*1024
            result['checks']['guest_cpu_io_and_owned_host_vmm_curves']='PASS'
            result['checks']['parent_restart_restore_and_branch_sampling_ids']='PASS'
            (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        except Exception as error:
            import traceback
            success=False;result.update(status='FAIL',failed_stage='runtime-report',error=repr(error))
            (out/'failure.txt').write_text(traceback.format_exc())
            (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out))
    return 0 if success else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','reference','output','config-home'):p.add_argument('--'+name,type=Path,required=name in ('inputs','reference'))
    for name in ('container','service-url'):p.add_argument('--'+name)
    p.add_argument('--find-diagnostic',action='store_true')
    p.add_argument('--check-only',action='store_true')
    raise SystemExit(run(p.parse_args()))
