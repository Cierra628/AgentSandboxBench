"""Prepared-host native checkpoint archive and restore through separate owned services."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
from platform_tasks import ROOT,preflight,commands,read,write
from native_checkpoint_bundle import load


def report(directory,output):
    from report_sandbox_runtime import write_report
    from tool_trajectory import validate as validate_trace
    receipt=read(directory/'result.json');assert receipt['status']=='PASS' and receipt['cache_released']
    bundle=Path(receipt['bundle']);saved=load(bundle);original=saved['capture'];restore=None
    if output.exists() or not output.is_relative_to(ROOT/'.artifacts') or output.is_relative_to(directory):raise ValueError('new separate project report directory required')
    for child in receipt['phase_runs'].values():
        assert not output.is_relative_to(Path(child))
        outer=read(Path(child)/'result.json');assert outer['status']=='PASS'
        assert all(outer['cleanup'].get(k) for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_firecracker_identities_unchanged','original_service_unchanged'))
    for phase,child in receipt['phase_runs'].items():
        raw=Path(child)/'python-backbone';result=read(raw/'result.json');assert result['status']=='PASS' and all(result['cleanup'].values())
        if phase=='capture':assert result==original
        else:
            continuation=validate_trace(raw/'captured-continuation');assert continuation['action_count']==2 and not continuation['expected_failures']
            assert continuation['final_state']==result['files_after_continuation']
            assert set(read(Path(child)/'copied-input-hashes.json'))=={'deps'}
            assert 'immutable_cache_seed' not in read(Path(child)/'result.json')
            restore=result;assert result['next_tool_step']==3 and result['executed_global_steps']==[3,4]
            assert all(result['process_after'][k]==original['process_before'][k] for k in ('pid','start_ticks','boot_id','anonymous_sha256','open_fd_sha256','pathname_sha256'))
            assert result['process_after']['count']>original['process_before']['count']
            assert result['files_at_boundary']==original['files_at_boundary']
    assert restore is not None
    output.mkdir(parents=True);metrics={}
    for phase,child in receipt['phase_runs'].items():
        destination=output/phase;destination.mkdir();metrics[phase]=write_report(Path(child)/'python-backbone',destination)
    proof=dict(status='PASS',snapshot_alias=original['snapshot_alias'],snapshot_id=original['snapshot_id'],original_sandbox_id=original['original_sandbox_id'],restored_sandbox_id=restore['restored_sandbox_id'],
        process_before=original['process_before'],process_after=restore['process_after'],files_at_boundary=original['files_at_boundary'],files_after_continuation=restore['files_after_continuation'],
        executed_global_steps=restore['executed_global_steps'],archive_sha256=saved['archive_sha256'],archive_bytes=saved['archive_bytes'],repository_files=len(saved['repository_files']),
        scope='same prepared host and fixed server/deps; fresh private service from archived POSIX snapshot repository; original VM process/memory/open FD preserved, new host VMM; not cross-host compatibility or benchmark acceptance')
    write(output/'restore-proof.json',proof);write(output/'summary.json',dict(status='PASS',proof=proof,runtime_metrics=metrics))
    write(output/'result.json',dict(status='PASS',workflow='native-checkpoint-durable',source_workflow=str(directory),scope=proof['scope']))
    return output


def run(cfg,bundle=None):
    ready=preflight(cfg,durable_phase='restore' if bundle else 'capture',bundle=bundle)
    out=Path(cfg['output_root'])/('durable-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    os.umask(0o077);out.mkdir(parents=True,exist_ok=False)
    write(out/'effective-config.json',cfg);write(out/'preflight.json',ready)
    code=out/'workflow-code';code.mkdir()
    for name in ('run_native_checkpoint_workflow.py','native_checkpoint_bundle.py','run_native_checkpoint.py','guest_checkpoint_probe.py','platform_tasks.py','use_platform.py'):shutil.copy2(ROOT/'scripts'/name,code/name)
    write(out/'workflow-version.json',dict(git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in code.iterdir()}))
    cache=ROOT/'.artifacts/sphinx-backbone'/('platform-cache-'+out.name)
    result=dict(status='FAIL',workflow='native-checkpoint-durable',phase_runs={},task_id='native-checkpoint-demo');stage='cache-prepare'
    if bundle and bundle.name=='checkpoint-bundle' and bundle.parent.parent==ROOT/'.artifacts/storage-isolated-service':result['storage_result_dir']=str(bundle.parent)
    print('status=RUNNING result_dir='+str(out),flush=True)
    def call(argv,label,timeout):
        with (out/(label+'.log')).open('x') as log:subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
    try:
        if not bundle:call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--source-backup-directory',cfg['cache_backup_directory'],'--task-profile','sphinx7590'],stage,300)
        else:result.update(cache_released=True,ram_cache_mode='NOT_CREATED; restored solely from the native repository and fixed deps')
        for phase in (['restore'] if bundle else ['capture','restore']):
            stage=phase;preflight(cfg,durable_phase=phase,bundle=bundle)
            _,live=commands(cfg,cache,durable_phase=phase,bundle=bundle);call(live,phase,1000)
            matches=re.findall(r'status=PASS result_dir=(\S+)',(out/(phase+'.log')).read_text())
            children={Path(p) for p in matches if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
            assert len(children)==1;child=children.pop();result['phase_runs'][phase]=str(child)
            outer=read(child/'result.json');assert outer['status']=='PASS' and outer['cleanup']['successful_fixture_removed']
            assert not (child/'volume.ext4').exists()
            if phase=='capture':
                bundle=child/'checkpoint-bundle';load(bundle);result['storage_result_dir']=str(child)
                call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release-before-restore',90)
        result.update(status='PASS',bundle=str(bundle))
    except Exception as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if (cache/'resources.json').exists():
            try:
                call([sys.executable,'-B',ROOT/'scripts/prepare_prettier6604_cache.py',cache,'--release'],'cache-release',90)
                result['cache_released']=read(cache/'resources.json')['released']
            except Exception as error:result.update(status='FAIL',cache_release_error=repr(error))
    write(out/'result.json',result)
    if result['status']=='PASS':
        try:
            output=report(out,out.parent/('report-'+out.name));result['report_dir']=str(output)
            from platform_management import retain
            (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            retain(out,'native checkpoint archive and successful fresh-service restore reference')
        except Exception as error:
            result.update(status='FAIL',failed_stage='report',error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True);return 0 if result['status']=='PASS' else 1
