"""One AgentENV backbone on an already-owned temporary isolated service."""
import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import threading
from types import SimpleNamespace

from prettier6604_inputs import ROOT, CONFIG, validate, sha
from exp1_official_task import load, guest_command, grade
from prettier6604_boundary import verify as verify_boundary
from audit_prettier6604_official import inspect as inspect_official, reference as official_reference, signature


def run(args):
    cfg, workload, reference = validate()
    task = load(args.official_task)
    assert task['instance_id'] == cfg['task_id'] and task['base_commit'] == cfg['base_commit']
    checkpoint=bool(getattr(args,'checkpoint',False))
    monitor_mode=getattr(args,'monitor_mode','continuous')
    assert monitor_mode in ('continuous','off')
    assert monitor_mode!='off' or not checkpoint,'monitor-off requires complete replay without checkpoint'
    physical_segments = getattr(args, 'physical_cache_after', '').split(',') if getattr(args, 'physical_cache_after', '') else []
    assert not physical_segments or monitor_mode == 'continuous' and not checkpoint
    assert len(set(physical_segments)) == len(physical_segments) and set(physical_segments) <= set(cfg['path'])
    expected=None
    if checkpoint or args.expected_official_audit:
        assert args.expected_official_audit, 'verified full replay official outcome required'
        expected=official_reference(args.expected_official_audit)
        assert expected['F2P']==task['F2P'] and expected['P2P']==task['P2P']
    if args.check_only:
        print('status=VALIDATED task=prettier6604 actions=29');return 0
    out = args.output.resolve()
    assert out.parent.name == 'controller' and out.parent.parent.name == 'mount'
    assert out.parents[3] == ROOT/'.artifacts/storage-isolated-service'
    assert args.config_home.resolve() == out.parent/'config'
    out.mkdir(parents=True,exist_ok=False)
    os.umask(0o077)
    env = dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],
               XDG_CONFIG_HOME=str(args.config_home),HOST_MONITOR_LIGHT='1')
    from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
    events=[];sid=None;monitor=None;success=False;snapshot=None;physical=None
    result=dict(status='FAIL',checks={},scope=cfg['scope'],backend='agentenv',task_id=cfg['task_id'],
                physical_page_cache='NOT_MEASURED',monitoring_impact='NOT_VALIDATED',
                checkpoint_restore='NOT_RUN',full_upstream_suite='NOT_RUN',monitor_mode=monitor_mode)
    stage='preflight';started=time.monotonic_ns()
    event_lock=threading.Lock()
    call_counts={}
    def call(label, argv, timeout=180):
        begin=time.monotonic_ns()
        with event_lock:
            index=call_counts.get(label,0);call_counts[label]=index+1
        file_label=label if index==0 else f'{label}-{index:04}'
        with (out/(file_label+'.stderr')).open('x') as log:
            r=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=log,text=True,timeout=timeout)
        (out/(file_label+'.stdout')).write_text(r.stdout)
        with event_lock:
            events.append(dict(stage=label,start_monotonic_ns=begin,duration_ns=time.monotonic_ns()-begin,exit_code=r.returncode))
            (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+': exit '+str(r.returncode))
        return r.stdout.strip()
    def cli(label,*argv,timeout=180):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def guest(label,command,timeout=180):return cli(label,'exec',sid,'bash','-lc',command,timeout=timeout)
    runtime=RuntimeMonitor(out,cli,container=args.container) if monitor_mode=='continuous' else None
    def cancel(signum,frame):raise RuntimeError('owned backbone interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,cancel)
    try:
        info=json.loads(call('service-state',['docker','inspect','--format','{{json .State}}',args.container],10))
        assert info['Running']
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container],10))
        assert labels.get('agentsandboxbench.run_id') == out.parents[2].name
        assert labels.get('agentenv.experiment') == 'isolated-storage-check'
        # Reuse the pinned controller's segment runner; precise physical scans are not enabled.
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        os.environ['WORKLOAD_NAME']='prettier-6604-rl-fork'
        sys.path.insert(0,str(vendor))
        spec=importlib.util.spec_from_file_location('prettier6604_controller',vendor/'controller.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        controller_calls=0
        def bounded_shell(*argv, capture=True):
            nonlocal controller_calls
            argv=list(argv)
            if argv[0]=='aenv': argv[0]=str(ROOT/'runtime/bin/aenv')
            with event_lock:index=controller_calls;controller_calls+=1
            return call(f'controller-call-{index:04}',argv,timeout=600)
        module.shell=bounded_shell
        class SinglePath(module.Controller):
            async def physical_memory(self,label):
                return None  # Explicit NOT_MEASURED in result; never a synthetic PASS.
        controller=SinglePath(SimpleNamespace(system='agentenv',raw_dir=out,timeout=600))
        controller.phase_file=out/'.phase';controller.phase('prepare')
        result['scope']='24/29 checkpoint and continuation; original task failure preserved' if checkpoint else cfg['scope']
        (out/'effective-config.json').write_text(json.dumps(dict(cfg,container=args.container,official_task=str(args.official_task),checkpoint_after_global_step=24 if checkpoint else None,expected_official_failure=signature(expected) if expected else None,cold_request_timeout_seconds=600,sandbox_ttl_seconds=1800,host_scope='single temporary service cgroup; cache and VMM included; pool disabled',guest_scope='continuous per actual VM ID after monitor setup; explicit snapshot gaps; tool boundary counters retained',physical_memory='NOT_MEASURED',monitoring_impact='NOT_VALIDATED'),indent=2)+'\n')
        if physical_segments:
            effective=json.loads((out/'effective-config.json').read_text())
            effective.update(physical_memory='paused file-LRU points',physical_cache_after_segments=physical_segments,
                             measurement_variant='precision analysis; pause/analysis elapsed times included, not lightweight-only timing')
            (out/'effective-config.json').write_text(json.dumps(effective,indent=2)+'\n')
        code=out/'code';code.mkdir()
        for path in [Path(__file__),ROOT/'scripts/agentenv_cold_start.py',ROOT/'scripts/prettier6604_boundary.py',ROOT/'scripts/audit_prettier6604_official.py',ROOT/'scripts/exp1-process-probe.sh',ROOT/'scripts/prettier6604_inputs.py',ROOT/'scripts/prettier6604_check.cjs',ROOT/'scripts/exp1_official_task.py',vendor/'controller.py',workload/'guest-runner.sh',ROOT/'IncrementalDAX_moti/motivation/experiments/lib/host-memory-monitor.sh',ROOT/'configs/prettier6604-backbone.json']:
            shutil.copy2(path,code/path.name)
        (out/'source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in code.iterdir()},indent=2)+'\n')
        if physical_segments:
            physical_sources = ['task_physical_cache.py', 'agentenv_whole_cache.py', 'measure_file_physical.py',
                'guest_whole_cache_probe.py', 'guest_cache_marker.py', 'trenvx_whole_cache.py', 'paused_agentenv_boundary.py',
                'cached_file_inventory.py', 'guest_cached_file_inventory.py', 'guest_named_file_probe.py',
                'guest_file_type_check.py', 'kernel_file_cache.py', 'report_file_inventory.py']
            physical_code = out/'physical-code'; physical_code.mkdir()
            for name in physical_sources: shutil.copy2(ROOT/'scripts'/name, physical_code/name)
            (out/'physical-source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in physical_code.iterdir()},indent=2)+'\n')
        (out/'reference.patch').write_text(reference)
        frozen=out/'frozen-input';(frozen/'segments').mkdir(parents=True)
        shutil.copy2(workload/'guest-runner.sh',frozen/'guest-runner.original.sh')
        from task_metrics_prefix import without_sampling
        (frozen/'guest-runner.sh').write_text(boundary_runner((workload/'guest-runner.sh').read_text(),guest_phases=True) if runtime else without_sampling((workload/'guest-runner.sh').read_text()))
        (out/'sampling-variant.json').write_text(json.dumps(dict(mode=monitor_mode,phase_updates='guest runner' if runtime else 'none',original_runner_sha256=sha(workload/'guest-runner.sh'),executed_runner_sha256=sha(frozen/'guest-runner.sh')),indent=2)+'\n')
        for segment in cfg['path']:shutil.copytree(workload/'segments'/segment,frozen/'segments'/segment)
        (out/'host').mkdir(exist_ok=True)
        if runtime:
            with (out/'host-monitor.log').open('x') as log:
                monitor=subprocess.Popen(['bash',str(ROOT/'IncrementalDAX_moti/motivation/experiments/lib/host-memory-monitor.sh'),str(out/'host/memory-samples.tsv'),str(controller.phase_file),'0.1',args.container],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        if physical_segments:
            from task_physical_cache import TaskPhysicalCache
            stage='physical-capture'
            physical=TaskPhysicalCache(out,args.container,cli)
        stage='cold-start';controller.phase(stage)
        sid=call('cold-start',[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py',
                 '--config-home',args.config_home,'--service-url',args.service_url,
                 '--image',cfg['source_image']],timeout=610)
        assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid)
        if physical: physical.ready()
        (out/'sandbox-id.txt').write_text(sid+'\n')
        controller.record('sandbox-ids.tsv',f'backbone\t{sid}\t\troot')
        if runtime:runtime.start(sid,'backbone','setup')
        stage='smoke';controller.phase(stage)
        smoke=guest('source-smoke',"cd /testbed && python3 - <<'EOF'\nimport subprocess,hashlib,json\nfrom pathlib import Path\nhead=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()\nassert head=='"+cfg['base_commit']+"'\np=Path('src/language-js/needs-parens.js');base=subprocess.check_output(['git','show','HEAD:'+str(p)])\nassert p.read_bytes()==base\nprint(json.dumps(dict(base_commit=head,source_sha256=hashlib.sha256(base).hexdigest(),initial_git_status=subprocess.check_output(['git','status','--porcelain'],text=True),suite_paths=sorted(str(p) for p in Path('tests/typescript/conformance/types').rglob('jsfmt.spec.js')),node=subprocess.check_output(['node','--version'],text=True).strip())))\nEOF")
        (out/'source-smoke.json').write_text(json.dumps(json.loads(smoke),indent=2)+'\n')
        cli('upload-independent-check','upload',sid,ROOT/'scripts/prettier6604_check.cjs','/workspace/prettier6604-check.cjs')
        baseline=json.loads(guest('unfixed-oracle','node /workspace/prettier6604-check.cjs --baseline'))
        assert baseline['status']=='PASS';result['checks']['smoke_and_unfixed_oracle']='PASS'
        cli('upload-frozen-workload','upload',sid,frozen,'/workspace/mixfs-workload')
        if checkpoint:
            cli('upload-state-probe','upload',sid,ROOT/'scripts/prettier6604_boundary.py','/workspace/prettier6604-boundary.py')
            cli('upload-process-probe','upload',sid,ROOT/'scripts/exp1-process-probe.sh','/workspace/exp1-process-probe.sh')
            first_probe=dict(line.split('=',1) for line in guest('process-start','bash /workspace/exp1-process-probe.sh start').splitlines())
        stage='frozen-backbone';controller.phase(stage)
        replay_begin=time.monotonic_ns()
        global_step = 0
        for segment in cfg['path']:
            begin=time.monotonic_ns();asyncio.run(controller.run_segment(sid,'backbone',segment))
            with event_lock:
                events.append(dict(stage='segment-'+segment,duration_ns=time.monotonic_ns()-begin,exit_code=0))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
            global_step += len(cfg['expected_exit_codes'][segment])
            if segment in physical_segments:
                stage='physical-cache-'+segment;controller.phase(stage)
                if runtime: runtime.mark(sid,stage)
                begin=time.monotonic_ns()
                physical.measure(sid,segment,global_step)
                events.append(dict(stage=stage,start_monotonic_ns=begin,duration_ns=time.monotonic_ns()-begin,exit_code=0))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
            if checkpoint and segment=='core_verify':
                stage='checkpoint-boundary';controller.phase(stage)
                before=json.loads(guest('boundary-before','python3 /workspace/prettier6604-boundary.py'))
                verify_boundary(before,before)
                probe_before=dict(line.split('=',1) for line in guest('process-before','bash /workspace/exp1-process-probe.sh').splitlines())
                assert all(probe_before[k]==first_probe[k] for k in ('pid','start_ticks','boot_id'))
                (out/'boundary-before.json').write_text(json.dumps(before,indent=2)+'\n')
                snapshot='prettier6604-'+out.parents[2].name
                (out/'checkpoint-alias.txt').write_text(snapshot+'\n')
                stage='checkpoint';controller.phase(stage)
                runtime.stop(sid,'checkpoint')
                cli('checkpoint','snapshot','create',sid,'--name',snapshot,timeout=150)
                result['checks']['checkpoint_interface']='PASS'
                parent=sid
                cli('delete-original','delete',sid,timeout=60);sid=None
                stage='restore';controller.phase(stage)
                sid=cli('restore','start',snapshot,'--detach','--timeout','1800',timeout=150)
                assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid) and sid!=parent
                controller.record('sandbox-ids.tsv',f'backbone\t{sid}\t{parent}\trestored')
                guest('restore-ready','true')
                runtime.start(sid,'backbone','restored-checks')
                after=json.loads(guest('boundary-after','python3 /workspace/prettier6604-boundary.py'))
                boundary=verify_boundary(before,after)
                assert after['base_commit']==cfg['base_commit']
                probe_after=dict(line.split('=',1) for line in guest('process-after','sleep 0.3; bash /workspace/exp1-process-probe.sh').splitlines())
                assert all(probe_before[k]==probe_after[k] for k in ('pid','start_ticks','boot_id'))
                assert int(probe_after['count'])>int(probe_before['count'])
                (out/'boundary-after.json').write_text(json.dumps(after,indent=2)+'\n')
                (out/'restored-boundary.json').write_text(json.dumps(boundary,indent=2)+'\n')
                (out/'process-continuity.json').write_text(json.dumps(dict(before=probe_before,after=probe_after),indent=2)+'\n')
                result['checks']['restored_progress_and_files']='PASS'
                result['checks']['process_continuity']='PASS'
                result['checkpoint_restore']='PASS'
                stage='frozen-continuation';controller.phase(stage)
        count=0;guest_durations=0
        for segment in cfg['path']:
            output=out/'rollouts/backbone'/segment
            assert sha(output/'actions.tsv')==cfg['manifest_sha256'][segment]
            with (output/'steps.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            assert [r['step'] for r in rows]==[f'{i:03}' for i in range(1,len(rows)+1)]
            assert [int(r['exit_code']) for r in rows]==cfg['expected_exit_codes'][segment]
            count+=len(rows);guest_durations+=sum(int(r['duration_ns']) for r in rows)
        assert count==29
        final=out/'rollouts/backbone/backbone_finish';patch=(final/'patch.diff').read_bytes()
        assert hashlib.sha256(patch).hexdigest()==cfg['reference_patch_sha256']
        assert 'IDEMPOTENT' in (final/'stdout/004.log').read_text()
        log=(final/'stdout/005.log').read_text()
        assert re.search(r'Test Suites:\s+32 passed, 32 total',log) and re.search(r'Tests:\s+66 passed, 66 total',log)
        result['checks']['frozen_order_hashes_exit_codes_patch_and_key_outputs']='PASS'
        result.update(action_count=count,guest_action_sum_ns=guest_durations,final_patch_sha256=hashlib.sha256(patch).hexdigest(),frozen_replay_elapsed_ns=time.monotonic_ns()-replay_begin)
        final_code="cd /testbed && python3 - <<'EOF'\nimport subprocess,hashlib,json\nfrom pathlib import Path\npaths=['src/language-js/needs-parens.js']+sorted(str(p) for p in Path('tests/typescript/custom/parenthesizedType').rglob('*') if p.is_file())\nprint(json.dumps(dict(base_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),git_status=subprocess.check_output(['git','status','--porcelain'],text=True),files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths})))\nEOF"
        final_state=json.loads(guest('task-final-state',final_code))
        (out/'final-state.json').write_text(json.dumps(final_state,indent=2)+'\n')
        stage='independent-tests';controller.phase(stage)
        independent=json.loads(guest('fixed-independent-check','node /workspace/prettier6604-check.cjs'))
        assert independent['status']=='PASS';result['checks']['independent_properties']='PASS'
        (out/'independent-check.json').write_text(json.dumps(independent,indent=2)+'\n')
        stage='official-task-tests';controller.phase(stage)
        if runtime:runtime.mark(sid,stage)
        cli('upload-official-tests','upload',sid,args.official_task/'test.patch','/workspace/prettier6604-official.patch')
        official=guest('official-task-tests',guest_command(args.official_task,'/workspace/prettier6604-official.patch'),timeout=160)
        official_result=inspect_official(args.official_task,official) if expected else grade(args.official_task,official)
        (out/'official-task-result.json').write_text(json.dumps(official_result,indent=2)+'\n')
        result['checks']['official_task_tests']=official_result['status']
        result['official_task_status']=official_result['status']
        if expected:
            assert signature(official_result)==signature(expected), 'restored official outcome differs from full replay'
            result['checks']['official_outcome_matches_full_replay']='PASS'
            (out/'expected-official-audit.json').write_text(json.dumps(expected,indent=2)+'\n')
        source_after=json.loads(guest('source-after-tests',final_code))
        assert source_after['files']==final_state['files'] and source_after['base_commit']==final_state['base_commit'],'task source changed during evaluation'
        (out/'source-after-tests.json').write_text(json.dumps(source_after,indent=2)+'\n')
        if physical:
            assert [p['after_segment'] for p in physical.points] == [s for s in cfg['path'] if s in physical_segments]
            complete=all(p['status']=='PASS' for p in physical.points)
            result['physical_page_cache']='MEASURED_AT_EXPLICIT_BOUNDARIES' if complete else 'PARTIAL_AT_EXPLICIT_BOUNDARIES'
            result['checks']['physical_cache_probe_and_task_state']='PASS'
            result['checks']['physical_cache_complete']='PASS' if complete else 'PARTIAL'
        success=True
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage)
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={}
        if physical:
            try: physical.close();cleanup['physical_tracer_stopped']=True
            except Exception as e:cleanup['physical_tracer_error']=repr(e);success=False
        if sid:
            try:
                if runtime:runtime.stop(sid)
            except Exception as e:cleanup['runtime_monitor_error']=repr(e);success=False
            try:cli('delete-owned-sandbox','delete',sid,timeout=60);cleanup['sandbox_removed']=True
            except Exception as e:cleanup['error']=repr(e);success=False
        if snapshot and success:
            try:cli('delete-owned-snapshot','template','delete',snapshot,timeout=60);cleanup['snapshot_removed']=True
            except Exception as e:cleanup['snapshot_error']=repr(e);success=False
        elif snapshot:
            cleanup['snapshot_retained_for_failure_diagnosis']=True
        if monitor:
            already=monitor.poll()
            if already is not None:cleanup['monitor_unexpected_exit']=already;success=False
            else:
                os.killpg(monitor.pid,signal.SIGTERM)
                try:monitor.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(monitor.pid,signal.SIGKILL);monitor.wait(timeout=5)
                cleanup['monitor_stopped']=True
        if runtime is None:cleanup['monitoring_disabled']=True
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official-task',type=Path,required=True)
    parser.add_argument('--container');parser.add_argument('--config-home',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--service-url',help='Owned temporary loopback API, checked against its credentials')
    parser.add_argument('--monitor-mode',choices=('continuous','off'),default='continuous')
    parser.add_argument('--physical-cache-after',default='',help='Comma-separated completed segments for paused file-LRU analysis')
    parser.add_argument('--checkpoint',action='store_true',help='Snapshot after core_verify (24/29); resume at backbone_finish')
    parser.add_argument('--expected-official-audit',type=Path,help='Hash-verified official failure from complete original replay')
    parser.add_argument('--check-only',action='store_true');args=parser.parse_args()
    if not args.check_only and not all((args.container,args.config_home,args.output,args.service_url)):parser.error('live run requires owned container, config-home, output, service-url')
    raise SystemExit(run(args))
