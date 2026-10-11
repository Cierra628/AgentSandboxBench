"""Replay one pinned Xarray backbone on an owned isolated AgentENV service."""
import argparse
import asyncio
import csv
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

from xarray6992_inputs import ROOT, FILES, SHELL_PATH, frozen_shell, sha, validate, normalize_patch, grade_xml,verify_diagnostic_replay
from run_prettier6604_grpo import verify_path


def run(args):
    sphinx=getattr(args,'task_profile','xarray6992')=='sphinx7590'
    if sphinx:
        import sphinx7590_inputs as profile
        if any(getattr(args,k,False) for k in ('diagnostic_continuation','diagnostic_only','evaluation_patch')):
            raise ValueError('Sphinx profile supports strict frozen replay only')
    else:
        import xarray6992_inputs as profile
    files=profile.FILES
    cfg,workload,samples,names=profile.validate(args.inputs)
    assert cfg['frozen_shell_environment']['PATH']==SHELL_PATH,'explicit frozen shell environment required'
    continuation=getattr(args,'diagnostic_continuation',False)
    pipeline=getattr(args,'sphinx_pipeline_diagnostic',False)
    pipeline_version=getattr(args,'sphinx_pipeline_policy_version',1)
    if pipeline_version!=1 and not pipeline:raise ValueError('pipeline policy version requires explicit diagnostic mode')
    if pipeline and (not sphinx or getattr(args,'checkpoint',False) or not getattr(args,'persistent_sampling',False)):
        raise ValueError('Sphinx pipeline diagnostic requires complete persistent replay without checkpoint')
    checkpoint=getattr(args,'checkpoint',False)
    monitoring=getattr(args,'monitor_mode','continuous')=='continuous'
    physical_segments=getattr(args,'physical_cache_after','').split(',') if getattr(args,'physical_cache_after','') else []
    if physical_segments and (checkpoint or not monitoring or not getattr(args,'persistent_sampling',False) or getattr(args,'diagnostic_only',False) or getattr(args,'evaluation_patch',None)):
        raise ValueError('physical boundaries require a complete continuously sampled replay without restore')
    if len(set(physical_segments))!=len(physical_segments) or not set(physical_segments)<=set(cfg['path']):
        raise ValueError('unknown or duplicate physical cache boundary')
    if not monitoring and (not sphinx or checkpoint or any(getattr(args,k,False) for k in ('diagnostic_only','diagnostic_continuation','evaluation_patch'))):
        raise ValueError('monitor-off control is limited to one complete strict Sphinx replay without checkpoint')
    expected=None
    if checkpoint:
        if (not continuation and not sphinx) or not getattr(args,'persistent_sampling',False) or getattr(args,'evaluation_patch',None) or not getattr(args,'expected_evaluation_raw',None):
            raise ValueError('checkpoint requires diagnostic frozen replay and verified complete evaluation raw')
        from report_xarray6992 import audit
        reference=args.expected_evaluation_raw.resolve()
        original=json.loads((reference/'result.json').read_text())
        reference_summary,_,_=audit(reference) if sphinx else audit((ROOT/Path(original['archived_replay_raw'])).resolve(),reference)
        assert not reference_summary.get('checkpoint_restore')=='PASS'
        assert reference_summary['config']==cfg
        expected=reference_summary['official']
    if continuation and (not getattr(args,'persistent_sampling',False) or getattr(args,'diagnostic_only',False)):
        raise ValueError('complete diagnosis requires persistent sampling and frozen replay')
    if getattr(args,'evaluation_patch',None):
        captured=args.evaluation_patch.resolve();source_result=json.loads((captured.parent/'result.json').read_text())
        assert captured.name=='final.patch' and captured.parent.name=='python-backbone'
        assert captured.parents[2]==ROOT/'.artifacts/storage-isolated-service'
        assert source_result['replay']['action_count']==35 and source_result['checks']['final_patch_and_source_hashes']=='PASS'
        assert normalize_patch(captured.read_text())==normalize_patch((args.inputs/'reference.patch').read_text())
        if continuation or getattr(args,'diagnostic_only',False):raise ValueError('evaluation-only cannot replay or run short probes')
    if args.check_only:
        print('status=VALIDATED task='+cfg['task_id']+' actions=35');return 0
    out=args.output.resolve()
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    assert out.parents[3]==ROOT/'.artifacts/storage-isolated-service'
    assert args.config_home.resolve()==out.parent/'config'
    out.mkdir(exist_ok=False)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],
             XDG_CONFIG_HOME=str(args.config_home),HOST_MONITOR_LIGHT='1')
    from sandbox_runtime_monitor import RuntimeMonitor, boundary_runner
    events=[];controller=None;sid=None;monitor=None;success=False;stage='preflight';snapshot=None;parent=None;physical=None
    started=time.monotonic_ns()
    result=dict(status='FAIL',task_id=cfg['task_id'],schedule='single backbone',checks={},
                physical_page_cache='NOT_MEASURED',VMM_PSS='NOT_MEASURED',monitoring_impact='NOT_VALIDATED')
    if continuation:
        result.update(diagnostic_continuation=True,diagnostic_status='INCOMPLETE',strict_replay_status='FAIL',
            strict_replay_scope='historical strict failure retained; this mode never awards strict replay PASS',
            scope='complete diagnostic continuation and independent task tests; only pinned pipeline SIGPIPE allowed with exact original stdout')
    if pipeline:
        result.update(sphinx_pipeline_diagnostic=True,diagnostic_status='INCOMPLETE',strict_replay_status='FAIL',
            strict_replay_scope='diagnostic variant; historical strict FAIL retained, never awards strict replay PASS')
    if pipeline and pipeline_version==2:result['sphinx_pipeline_policy_version']=2
    if checkpoint:
        result.update(checkpoint_restore='INCOMPLETE',checkpoint_after_global_step=29 if sphinx else 22,
            recovery_semantics='full VM snapshot; files/progress and one running process checked',
            scope='Sphinx step-29 full VM checkpoint/restore and original steps 30-35; strict replay and task outcome separate' if sphinx else 'step-22 full VM checkpoint/restore and original steps 23-35; strict replay FAIL retained')

    def verify(path):
        if pipeline:return profile.verify_pipeline_replay(out,cfg,path,samples,names,sampling=monitoring,policy_version=pipeline_version)
        return verify_diagnostic_replay(out,cfg,path) if continuation else verify_path(out,'backbone',path,module.WORKLOAD,samples,names,sampling=monitoring)

    event_lock=threading.Lock();event_counter=0
    def call(label,argv,timeout=180):
        nonlocal event_counter
        with event_lock:
            index=event_counter;event_counter+=1
        begin=time.monotonic_ns();wall=time.time_ns();code=None
        filename=f'{index:04}-{label}'
        try:
            with (out/(filename+'.stderr')).open('x') as log:
                proc=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=subprocess.PIPE,
                                    stderr=log,text=True,timeout=timeout)
            (out/(filename+'.stdout')).write_text(proc.stdout);code=proc.returncode
            if code:raise RuntimeError(filename+': exit '+str(code))
            return proc.stdout.strip()
        finally:
            with event_lock:
                events.append(dict(index=index,stage=label,start_monotonic_ns=begin,timestamp_ns=wall,
                               duration_ns=time.monotonic_ns()-begin,exit_code=code))
                (out/'events.json').write_text(json.dumps(events,indent=2)+'\n')

    def cli(label,*argv,timeout=180):return call(label,[ROOT/'runtime/bin/aenv',*argv],timeout)
    def guest(label,command,timeout=180):return cli(label,'exec',sid,'bash','-lc',command,timeout=timeout)
    runtime=RuntimeMonitor(out,cli,container=args.container) if monitoring else None
    def interrupted(signum,frame):raise RuntimeError('owned run interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    try:
        info=json.loads(call('service-state',['docker','inspect','--format','{{json .State}}',args.container],10))
        labels=json.loads(call('service-labels',['docker','inspect','--format','{{json .Config.Labels}}',args.container],10))
        assert info['Running'] and labels.get('agentsandboxbench.run_id')==out.parents[2].name
        assert labels.get('agentenv.experiment')=='isolated-storage-check'
        shutil.copytree(workload,out/'frozen-input')
        shutil.copytree(args.inputs,out/'prepared-input')
        instrument=lambda text:boundary_runner(text,guest_phases=True)
        if not monitoring:
            from task_metrics_prefix import without_sampling
            instrument=without_sampling
        runner=out/'frozen-input/guest-runner.sh';original_hash=sha(runner)
        shutil.copy2(runner,runner.with_name('guest-runner.original.sh'))
        runner.write_text(instrument(runner.read_text()))
        result['sampling_variant']=dict(mode='continuous per sandbox; boundary samples per segment' if monitoring else 'off',original_runner_sha256=original_hash,
            executed_runner_sha256=sha(runner),frozen_actions='unchanged',phase_updates='guest runner' if monitoring else 'disabled')
        result['monitor_mode']='continuous' if monitoring else 'off'
        (out/'sampling-variant.json').write_text(json.dumps(result['sampling_variant'],indent=2)+'\n')
        vendor=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork'
        os.environ['WORKLOAD_NAME']='sphinx-7590-rl-fork' if sphinx else 'xarray-6992-rl-fork';sys.path.insert(0,str(vendor))
        spec=importlib.util.spec_from_file_location('xarray6992_controller',vendor/'controller.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.WORKLOAD=out/'frozen-input'
        def shell(*argv,capture=True):
            argv=list(argv)
            if argv[0]=='aenv':argv[0]=ROOT/'runtime/bin/aenv'
            if argv[1]=='exec' and argv[-1].endswith('bash /workspace/mixfs-workload/guest-runner.sh'):
                argv[-1]=frozen_shell(argv[-1])
            return call('controller-'+argv[1],argv,180)
        module.shell=shell
        class Backbone(module.Controller):
            async def physical_memory(self,label):return None
        controller=Backbone(SimpleNamespace(raw_dir=out,system='agentenv',method='backbone',timeout=180))
        controller.phase(stage)
        if physical_segments:
            code=out/'physical-code';code.mkdir()
            for name in ('task_physical_cache.py','agentenv_whole_cache.py','measure_file_physical.py',
                         'guest_whole_cache_probe.py','guest_cache_marker.py','trenvx_whole_cache.py','paused_agentenv_boundary.py',
                         'cached_file_inventory.py','guest_cached_file_inventory.py','guest_named_file_probe.py',
                         'guest_file_type_check.py','kernel_file_cache.py','report_file_inventory.py'):
                shutil.copy2(ROOT/'scripts'/name,code/name)
            (out/'physical-source-sha256.json').write_text(json.dumps({p.name:sha(p) for p in code.iterdir()},indent=2)+'\n')
            result['physical_cache_after_segments']=physical_segments
            from task_physical_cache import TaskPhysicalCache
            physical=TaskPhysicalCache(out,args.container,cli,source_files=files)
        if monitoring:
            with (out/'host-monitor.log').open('x') as log:
                monitor=subprocess.Popen(['bash',vendor.parent/'lib/host-memory-monitor.sh',out/'host/memory-samples.tsv',
                        controller.phase_file,'0.1',args.container],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        stage='cold-start';controller.phase(stage)
        sid=call(stage,[sys.executable,'-B',ROOT/'scripts/agentenv_cold_start.py','--config-home',args.config_home,
                 '--service-url',args.service_url,'--image',cfg['source_image']],610)
        assert re.fullmatch(r'[a-fA-F0-9-]{36}',sid)
        if physical:physical.ready()
        controller.live[sid]=sid
        controller.record('sandbox-ids.tsv',f'backbone\t{sid}\t\troot')
        if runtime:runtime.start(sid,'backbone','setup')
        stage='source-and-tool-smoke';controller.phase(stage)
        source_extra=',image_background='+profile.background_probe() if sphinx else ''
        source=json.loads(guest('source',"cd /testbed && python3 - <<'EOF'\nimport json,hashlib,subprocess\nfrom pathlib import Path\nprint(json.dumps(dict(head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in "+repr(list(files))+"},git_status=subprocess.check_output(['git','status','--porcelain'],text=True)"+source_extra+")))\nEOF"))
        assert source['head']==cfg['base_commit'] and source['files']==cfg['base_file_sha256']
        (out/'source-before.json').write_text(json.dumps(source,indent=2)+'\n')
        if sphinx:
            assert source['git_status']==' M setup.py\n M tox.ini\n'
            profile.verify_image_background(source['image_background'])
        probe="python -c \"import os,sys,json; print(json.dumps(dict(python=sys.executable,PATH=os.environ['PATH'],CONDA_PREFIX=os.environ.get('CONDA_PREFIX'))))\""
        before_env=json.loads(guest('login-environment',probe))
        frozen_env=json.loads(guest('frozen-environment',frozen_shell(probe)))
        assert frozen_env['python']=='/opt/miniconda3/bin/python' and frozen_env['PATH']==SHELL_PATH and frozen_env['CONDA_PREFIX'] is None
        (out/'shell-environment.json').write_text(json.dumps(dict(login=before_env,frozen=frozen_env,
            scope='PATH and conda activation reset at each original runner invocation; other inherited environment not claimed identical'),indent=2)+'\n')
        oracle_name='sphinx7590_check.py' if sphinx else 'xarray6992_check.py'
        oracle_path='/workspace/'+oracle_name
        cli('upload-oracle','upload',sid,ROOT/'scripts'/oracle_name,oracle_path)
        cli('upload-test-id-resolver','upload',sid,ROOT/'scripts/xarray6992_test_ids.py','/workspace/xarray6992_test_ids.py')
        python='/opt/miniconda3/envs/testbed/bin/python'
        baseline=json.loads(guest('negative-control','cd /testbed && '+python+' '+oracle_path+' --baseline'))
        assert baseline['status']=='PASS';result['baseline']=baseline
        cli('upload-frozen','upload',sid,out/'frozen-input','/workspace/mixfs-workload')
        if checkpoint:
            if sphinx:
                from sphinx7590_boundary import configuration,verify as verify_boundary
                boundary_cfg=configuration(cfg,args.inputs/'base-files',workload/'agent-sampling.json',source['image_background']['files'])
                (out/'boundary-config.json').write_text(json.dumps(boundary_cfg,indent=2)+'\n')
                cli('upload-sphinx-boundary','upload',sid,ROOT/'scripts/sphinx7590_boundary.py','/workspace/sphinx7590-boundary.py')
            else:
                from xarray6992_boundary import verify as verify_boundary
                boundary_cfg=cfg
            cli('upload-boundary','upload',sid,ROOT/'scripts/xarray6992_boundary.py','/workspace/xarray6992_boundary.py' if sphinx else '/workspace/xarray6992-boundary.py')
            cli('upload-boundary-config','upload',sid,out/'boundary-config.json' if sphinx else args.inputs/'config.json','/workspace/xarray6992-boundary-config.json')
            cli('upload-process-probe','upload',sid,ROOT/'scripts/exp1-process-probe.sh','/workspace/exp1-process-probe.sh')
        if getattr(args,'evaluation_patch',None):
            stage='archived-replay-patch';controller.phase(stage)
            shutil.copy2(args.evaluation_patch,out/'archived-input.patch')
            result.update(evaluation_only=True,strict_replay_status='FAIL',
                archived_replay_raw=str(args.evaluation_patch.parent),
                archived_patch_sha256=sha(args.evaluation_patch),
                scope='independent tests on archived frozen patch in fresh VM; no trajectory replay or VM restore')
            cli('upload-archived-patch','upload',sid,out/'archived-input.patch','/workspace/archived-input.patch')
            guest('apply-archived-patch','cd /testbed && git apply --check /workspace/archived-input.patch && git apply /workspace/archived-input.patch')
        elif getattr(args,'diagnostic_only',False):
            stage='find-diagnostic';controller.phase(stage)
            cli('upload-find-probe','upload',sid,ROOT/'scripts/xarray6992_find_probe.py','/workspace/xarray6992-find-probe.py')
            diagnosis=json.loads(guest('find-probe',frozen_shell(python+' /workspace/xarray6992-find-probe.py --runner /workspace/mixfs-workload/guest-runner.sh --output /workspace/find-probe --probe '+getattr(args,'diagnostic_probe','find')),timeout=180))
            assert diagnosis['status']=='RECORDED' and len(diagnosis['cases'])==12
            cli('download-find-probe','download',sid,'/workspace/find-probe',out/'diagnosis')
            result['diagnosis']=diagnosis;result['checks']['find_diagnostic_capture']='PASS'
            result['scope']='short '+getattr(args,'diagnostic_probe','find')+' diagnosis only; no frozen trajectory or benchmark acceptance'
            success=True
        else:
            replay_started_ns=time.monotonic_ns()
            stage='first-segment-smoke';controller.phase(stage)
            asyncio.run(controller.run_path(sid,'backbone',cfg['path'][:1]))
            verify(cfg['path'][:1])
            result['checks']['first_segment_smoke']='PASS'
            def measure_boundary(segment):
                nonlocal stage
                if physical and segment in physical_segments:
                    completed=cfg['path'][:cfg['path'].index(segment)+1]
                    step=sum(len(next(s for s in samples if s['role']=='backbone' and s['segment']==name)['actions']) for name in completed)
                    previous=stage;stage='physical-cache-'+segment;controller.phase(stage)
                    physical.measure(sid,segment,step,retain_fixture_pages=True)
                    stage=previous
                    controller.phase(stage)
            measure_boundary(cfg['path'][0])
            stage='remaining-frozen-segments';controller.phase(stage)
            for segment in cfg['path'][1:]:
                asyncio.run(controller.run_path(sid,'backbone',[segment]))
                verify([segment])
                measure_boundary(segment)
                if checkpoint and segment==('core_verify' if sphinx else 'core_patch'):
                    from run_prettier6604_grpo import verify_process
                    stage='checkpoint-boundary';controller.phase(stage)
                    boundary_command='python3 /workspace/'+('sphinx7590-boundary.py' if sphinx else 'xarray6992-boundary.py')+' --config /workspace/xarray6992-boundary-config.json'
                    before=json.loads(guest('boundary-before',boundary_command))
                    verify_boundary(before,before,boundary_cfg)
                    (out/'boundary-before.json').write_text(json.dumps(before,indent=2)+'\n')
                    first=dict(line.split('=',1) for line in guest('process-start','bash /workspace/exp1-process-probe.sh start').splitlines())
                    probe_before=dict(line.split('=',1) for line in guest('process-before','bash /workspace/exp1-process-probe.sh').splitlines())
                    assert all(first[k]==probe_before[k] for k in ('pid','start_ticks','boot_id'))
                    snapshot=('sphinx7590-' if sphinx else 'xarray6992-')+out.parents[2].name
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
                    after=json.loads(guest('boundary-after',boundary_command))
                    boundary=verify_boundary(before,after,boundary_cfg)
                    probe_after=dict(line.split('=',1) for line in guest('process-after','sleep 0.3; bash /workspace/exp1-process-probe.sh').splitlines())
                    process=verify_process(probe_before,probe_after)
                    for name,data in [('boundary-after',after),('restored-boundary',boundary),('process-continuity',process)]:
                        (out/(name+'.json')).write_text(json.dumps(data,indent=2)+'\n')
                    result['checks']['restored_progress_and_files']='PASS'
                    result['checks']['process_continuity']='PASS'
                    stage='frozen-continuation';controller.phase(stage)
            with (out/'segments.tsv').open() as f:trace=list(csv.DictReader(f,delimiter='\t'))
            assert [r['segment'] for r in trace]==cfg['path']
            assert [r['sandbox_id'] for r in trace]==([parent]*(4 if sphinx else 3)+[sid]*(1 if sphinx else 2) if checkpoint else [sid]*5)
            result['replay']=verify(cfg['path'])
            assert result['replay']['action_count']==35
            key_outputs=cfg['key_outputs'] if sphinx else [('trace_index_state',4,'__len__() should return >= 0'),
                                          ('core_verify',7,'tests added'),('backbone_finish',4,'fixed tests'),
                                          ('backbone_finish',5,'1 failed, 3 passed'),('backbone_finish',6,'1 failed, 129 passed')]
            for segment,step,fragment in key_outputs:
                output=out/'rollouts/backbone'/segment/'stdout'/f'{step:03}.log'
                assert fragment in output.read_text(), 'missing key output: '+segment+'/'+str(step)
            result['checks']['key_outputs']='PASS'
            result['checks']['frozen_actions_order_and_diagnostic_exit_policy' if (continuation or pipeline) else 'actions_hash_order_expected_failures']='PASS'
            result['frozen_replay_elapsed_ns']=time.monotonic_ns()-replay_started_ns
        if not getattr(args,'diagnostic_only',False):
            stage='final-frozen-state';controller.phase(stage)
            final_extra=',image_background='+profile.background_probe() if sphinx else ''
            final=json.loads(guest('final-files',"cd /testbed && python3 - <<'EOF'\nimport json,hashlib,subprocess\nfrom pathlib import Path\nprint(json.dumps(dict(files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in "+repr(list(files))+"},changed=subprocess.check_output(['git','diff','--name-only'],text=True).splitlines()"+final_extra+")))\nEOF"))
            (out/'final-files.json').write_text(json.dumps(final,indent=2)+'\n')
            if sphinx:result['image_background']=profile.verify_final_state(source,final,cfg)
            else:assert final['files']==cfg['final_file_sha256'] and sorted(final['changed'])==sorted(files)
            patch=guest('final-patch','git -C /testbed diff -- '+ ' '.join(files))+'\n'
            assert normalize_patch(patch)==normalize_patch((args.inputs/'reference.patch').read_text())
            (out/'final.patch').write_text(patch)
            invariant=json.loads(guest('small-invariant','cd /testbed && '+python+' '+oracle_path))
            assert invariant['status']=='PASS';result['invariant']=invariant
            result['checks']['final_patch_and_source_hashes']='PASS'
            # Testing has its own phase; it must not alter or replace the archived replay state.
            stage='benchmark-tests';controller.phase(stage)
            if runtime:runtime.mark(sid,stage)
            test_input=out/'evaluation-input';test_input.mkdir()
            for name in ('swebench-task.json','required-tests.json','test.patch'):shutil.copy2(args.inputs/name,test_input/name)
            cli('upload-evaluation','upload',sid,test_input,'/workspace/evaluation-input')
            evaluation=json.loads(guest('benchmark-pytest','cd /testbed && '+python+' '+oracle_path+' --official /workspace/evaluation-input',timeout=165))
            assert evaluation['source_sha256']==(cfg['final_file_sha256'] if sphinx else cfg['final_file_sha256']['xarray/core/dataset.py'])
            cli('download-evaluation','download',sid,'/workspace/evaluation-input',out/'evaluation')
            xml=out/'evaluation/evaluation-input/official.xml'
            if not xml.exists():xml=out/'evaluation/official.xml'
            assert xml.exists()
            task=json.loads((args.inputs/'swebench-task.json').read_text());f2p=json.loads(task['FAIL_TO_PASS']);p2p=json.loads(task['PASS_TO_PASS'])
            (out/'evaluation-receipt.json').write_text(json.dumps(evaluation,indent=2)+'\n')
            result['official']=profile.grade_xml(xml.read_text(),f2p+p2p,f2p,p2p,evaluation['exit_code'],evaluation.get('test_id_aliases'))
            result['official_task_status']=result['official']['status']
            result['checks']['complete_benchmark_results_and_source_unchanged']='PASS'
            if physical:
                assert [p['after_segment'] for p in physical.points]==[s for s in cfg['path'] if s in physical_segments]
                result['physical_page_cache']='MEASURED_AT_BOUNDARIES'
                result['checks']['physical_cache_probe_and_task_state']='PASS'
                result['checks']['physical_cache_complete']='PASS' if all(p['status']=='PASS' for p in physical.points) else 'PARTIAL'
            if checkpoint:
                assert result['official']==expected,'restored task outcome differs from archived frozen patch evaluation'
                (out/'expected-official-result.json').write_text(json.dumps(expected,indent=2)+'\n')
                result['expected_evaluation_raw']=str(args.expected_evaluation_raw.resolve())
                result['checks']['official_outcome_matches_full_replay']='PASS'
                result['checks']['continuation_from_step_30_and_final_state' if sphinx else 'continuation_from_step_23_and_final_state']='PASS'
                result['checkpoint_restore']='PASS'
            success=True
    except BaseException as error:
        import traceback
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
        if sphinx and sid and stage=='remaining-frozen-segments':
            try:
                action=profile.failed_find_action(out,cfg)
                if action:
                    command=out/'failed-find-command.txt';command.write_text(action['command'])
                    cli('failure-find-upload','upload',sid,ROOT/'scripts/guest_find_diagnostic.py','/workspace/asb-failed-find.py',timeout=20)
                    cli('failure-find-command','upload',sid,command,'/workspace/asb-failed-find-command.txt',timeout=20)
                    diagnosis=json.loads(guest('failure-find-capture',frozen_shell('python3 /workspace/asb-failed-find.py /workspace/asb-failed-find-command.txt'),timeout=40))
                    assert diagnosis['status']=='CAPTURED' and diagnosis['command_sha256']==action['sha256']
                    result['failure_find_diagnostic']=diagnosis
                    (out/'failure-find-diagnostic.json').write_text(json.dumps(diagnosis,indent=2)+'\n')
            except Exception as diagnosis_error:result['failure_find_diagnostic_error']=repr(diagnosis_error)
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        cleanup={}
        if physical:
            physical.close();cleanup['physical_tracer_stopped']=physical.tracer.poll() is not None
        if sid:
            try:
                if runtime:runtime.stop(sid)
            except Exception as error:cleanup['runtime_monitor_error']=repr(error);success=False
            try:
                cli('delete-owned','delete',sid,timeout=60)
                assert json.loads(cli('list-after','list','--output','json',timeout=30))==[]
                cleanup.update(sandbox_removed=True,service_sandbox_list_empty=True)
            except Exception as error:cleanup['error']=repr(error);success=False
        if snapshot and success:
            try:
                cli('delete-owned-snapshot','template','delete',snapshot,timeout=60)
                cleanup['snapshot_removed']=True
            except Exception as error:cleanup['snapshot_error']=repr(error);success=False
        elif snapshot:cleanup['snapshot_retained_for_failure_diagnosis']=True
        if monitor:
            if monitor.poll() is not None:cleanup['monitor_unexpected_exit']=monitor.returncode;success=False
            else:
                os.killpg(monitor.pid,signal.SIGTERM)
                try:monitor.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(monitor.pid,signal.SIGKILL);monitor.wait(timeout=5)
                cleanup['monitor_stopped']=True
        if not monitoring:cleanup['monitoring_disabled']=True
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup,controller_elapsed_ns=time.monotonic_ns()-started)
        if success and runtime:result['VMM_PSS']='MEASURED_PER_OWNED_VMM'
        if continuation or pipeline:result['diagnostic_status']='PASS' if success else 'FAIL'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--task-profile',choices=['xarray6992','sphinx7590'],default='xarray6992')
    parser.add_argument('--diagnostic-only',action='store_true',help='12 find probes only; never counts as replay PASS')
    parser.add_argument('--diagnostic-probe',choices=['find','pipeline'],default='find')
    parser.add_argument('--persistent-sampling',action='store_true',help='Explicit Exp3 sampler variant; original commands and expected exits retained')
    parser.add_argument('--physical-cache-after',default='',help='Completed original segments for paused physical file-cache measurement')
    parser.add_argument('--monitor-mode',choices=['continuous','off'],default='continuous',help='Off control disables guest boundary/periodic and host memory sampling; Sphinx complete replay only, tool timing retained')
    parser.add_argument('--sphinx-pipeline-policy-version',type=int,choices=(1,2),default=1,help='1: only step 1; 2: approved steps 1 and 14; all other exits strict')
    parser.add_argument('--sphinx-pipeline-diagnostic',action='store_true',help='Approved read_repo/001 only: exact output/patch and 141 or 0; strict FAIL retained')
    parser.add_argument('--diagnostic-continuation',action='store_true',help='User-approved pinned SIGPIPE exception; strict replay remains FAIL')
    parser.add_argument('--evaluation-patch',type=Path,help='Captured actual final patch from verified 35-step run; independent evaluation only')
    parser.add_argument('--checkpoint',action='store_true',help='Full VM snapshot after Xarray step 22 / Sphinx step 29; restore and continue original actions')
    parser.add_argument('--expected-evaluation-raw',type=Path,help='Verified independent evaluation raw from archived complete diagnostic patch')
    parser.add_argument('--container');parser.add_argument('--config-home',type=Path)
    parser.add_argument('--service-url');parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not args.check_only and not all((args.container,args.config_home,args.service_url,args.output)):parser.error('live requires owned service and output')
    raise SystemExit(run(args))
