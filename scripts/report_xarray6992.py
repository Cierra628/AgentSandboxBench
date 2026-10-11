"""Recompute Xarray backbone correctness and metrics without starting any VM."""
import argparse
import csv
import json
from pathlib import Path
from xarray6992_inputs import ROOT, SHELL_PATH, sha, validate, normalize_patch, grade_xml,verify_diagnostic_replay
from run_prettier6604_grpo import verify_path


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def audit(raw,evaluation_raw=None):
    sphinx=json.loads((raw/'prepared-input/config.json').read_text())['task_id']=='sphinx-doc__sphinx-7590'
    if sphinx:
        import sphinx7590_inputs as profile
    else:
        import xarray6992_inputs as profile
    cfg,_,samples,names=profile.validate(raw/'prepared-input')
    result=json.loads((raw/'result.json').read_text());receipt=json.loads((raw.parent/'result.json').read_text())
    evaluation_result=result;evaluation_source=raw;evaluation_receipt=receipt
    if evaluation_raw:
        evaluation_source=evaluation_raw
        evaluation_result=json.loads((evaluation_raw/'result.json').read_text())
        evaluation_receipt=json.loads((evaluation_raw.parent/'result.json').read_text())
        assert result['status']==receipt['status']=='FAIL' and result['failed_stage']=='benchmark-tests'
        assert result['replay']['action_count']==35 and result['checks']['final_patch_and_source_hashes']=='PASS'
        assert evaluation_result['status']==evaluation_receipt['status']=='PASS' and evaluation_result['evaluation_only']
        assert Path(evaluation_result['archived_replay_raw']).resolve()==raw.resolve()
        assert sha(raw/'final.patch')==sha(evaluation_raw/'archived-input.patch')==evaluation_result['archived_patch_sha256']
        assert normalize_patch((raw/'final.patch').read_text())==normalize_patch((evaluation_raw/'final.patch').read_text())
        assert validate(evaluation_raw/'prepared-input')[0]==cfg
        assert json.loads((evaluation_raw/'final-files.json').read_text())['files']==cfg['final_file_sha256']
        for key in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
                    'original_firecracker_identities_unchanged','original_service_unchanged'):assert evaluation_receipt['cleanup'][key]
    else:assert result['status']==receipt['status']=='PASS'
    assert result['cleanup']['service_sandbox_list_empty'] and result['cleanup']['monitor_stopped']
    for key in ('owned_container_removed','mount_and_loop_removed',
                'original_firecracker_identities_unchanged','original_service_unchanged'):assert receipt['cleanup'][key]
    assert receipt['cleanup']['failed_fixture_retained' if evaluation_raw else 'successful_fixture_removed']
    workload=raw/'frozen-input'
    variant=result.get('sampling_variant')
    for name,digest in cfg['input_sha256'].items():
        if name=='guest-runner.sh' and variant:
            if variant['mode']=='continuous per sandbox; boundary samples per segment':
                from sandbox_runtime_monitor import boundary_runner
                instrument=lambda text:boundary_runner(text,guest_phases=variant.get('phase_updates')=='guest runner')
            else:
                from xarray6992_persistent_sampling import instrument
            original=workload/'guest-runner.original.sh'
            assert sha(original)==digest==variant['original_runner_sha256']
            assert sha(workload/name)==variant['executed_runner_sha256']
            assert (workload/name).read_text()==instrument(original.read_text())
        else:assert sha(workload/name)==digest
    if variant and variant['mode']!='continuous per sandbox; boundary samples per segment':
        for segment in cfg['path']:
            directory=raw/'rollouts/backbone'/segment
            collector=json.loads((directory/'collector-status.json').read_text())
            assert collector['status']=='PASS' and collector['samples']==len(rows(directory/'guest-memory.tsv'))
            assert collector['samples']==len(rows(directory/'guest-vmstat.tsv'))
    continuation=result.get('diagnostic_continuation',False)
    pipeline=result.get('sphinx_pipeline_diagnostic',False)
    if pipeline:assert sphinx and result['diagnostic_status']==receipt['diagnostic_status']=='PASS' and result['strict_replay_status']==receipt['strict_replay_status']=='FAIL'
    if continuation:
        assert result['diagnostic_status']==('FAIL' if evaluation_raw else 'PASS') and result['strict_replay_status']=='FAIL'
        if not evaluation_raw:assert receipt['strict_replay_status']=='FAIL'
    replay=profile.verify_pipeline_replay(raw,cfg,cfg['path'],samples,names,policy_version=result.get('sphinx_pipeline_policy_version',1)) if pipeline else verify_diagnostic_replay(raw,cfg,cfg['path']) if continuation else verify_path(raw,'backbone',cfg['path'],workload,samples,names)
    assert replay==result['replay']
    segments=rows(raw/'segments.tsv');ids=rows(raw/'sandbox-ids.tsv')
    checkpoint=result.get('checkpoint_restore')=='PASS'
    assert ids[0]['created_from']=='root'
    assert [r['segment'] for r in segments]==cfg['path']
    if checkpoint:
        if sphinx:
            from sphinx7590_boundary import verify as verify_boundary,configuration
            before_source=json.loads((raw/'source-before.json').read_text())
            boundary_cfg=configuration(cfg,raw/'prepared-input/base-files',raw/'frozen-input/agent-sampling.json',before_source['image_background']['files'])
            assert boundary_cfg==json.loads((raw/'boundary-config.json').read_text())
        else:
            from xarray6992_boundary import verify as verify_boundary
            boundary_cfg=cfg
        from run_prettier6604_grpo import verify_process
        assert not evaluation_raw and len(ids)==2 and ids[1]['created_from']=='restored'
        assert ids[1]['parent_id']==ids[0]['sandbox_id'] and ids[1]['sandbox_id']!=ids[0]['sandbox_id']
        assert [r['sandbox_id'] for r in segments]==[ids[0]['sandbox_id']]*(4 if sphinx else 3)+[ids[1]['sandbox_id']]*(1 if sphinx else 2)
        boundary=verify_boundary(json.loads((raw/'boundary-before.json').read_text()),json.loads((raw/'boundary-after.json').read_text()),boundary_cfg)
        assert boundary==json.loads((raw/'restored-boundary.json').read_text())
        process=json.loads((raw/'process-continuity.json').read_text())
        assert process==verify_process(process['before'],process['after'])
        assert result['cleanup']['snapshot_removed'] and receipt['checkpoint_restore']=='PASS'
        for key in ('checkpoint_interface','restored_progress_and_files','process_continuity',
                    'continuation_from_step_30_and_final_state' if sphinx else 'continuation_from_step_23_and_final_state','official_outcome_matches_full_replay'):
            assert result['checks'][key]=='PASS'
    else:
        assert len(ids)==1 and all(r['sandbox_id']==ids[0]['sandbox_id'] for r in segments)
    assert json.loads((raw/'source-before.json').read_text())['files']==cfg['base_file_sha256']
    environment=json.loads((raw/'shell-environment.json').read_text())
    assert environment['frozen']['PATH']==SHELL_PATH and environment['frozen']['python']=='/opt/miniconda3/bin/python'
    assert environment['frozen']['CONDA_PREFIX'] is None
    assert result['baseline']['imported_source'].startswith('/testbed/sphinx/' if sphinx else '/testbed/xarray/')
    assert json.loads((raw/'final-files.json').read_text())['files']==cfg['final_file_sha256']
    if sphinx:
        background=profile.verify_final_state(json.loads((raw/'source-before.json').read_text()),json.loads((raw/'final-files.json').read_text()),cfg)
        assert background==result['image_background']
    assert normalize_patch((raw/'final.patch').read_text())==normalize_patch((raw/'prepared-input/reference.patch').read_text())
    key_outputs=cfg['key_outputs'] if sphinx else [('trace_index_state',4,'__len__() should return >= 0'),
            ('core_verify',7,'tests added'),('backbone_finish',4,'fixed tests'),
            ('backbone_finish',5,'1 failed, 3 passed'),('backbone_finish',6,'1 failed, 129 passed')]
    for segment,step,fragment in key_outputs:
        assert fragment in (raw/'rollouts/backbone'/segment/'stdout'/f'{step:03}.log').read_text()
    task=json.loads((raw/'prepared-input/swebench-task.json').read_text());f2p=json.loads(task['FAIL_TO_PASS']);p2p=json.loads(task['PASS_TO_PASS'])
    evaluation=json.loads((evaluation_source/'evaluation-receipt.json').read_text())
    assert evaluation['source_sha256']==(cfg['final_file_sha256'] if sphinx else cfg['final_file_sha256']['xarray/core/dataset.py'])
    xmls=list((evaluation_source/'evaluation').rglob('official.xml'));assert len(xmls)==1
    aliases=evaluation.get('test_id_aliases',{})
    if aliases:
        from xarray6992_test_ids import resolve_test_ids
        resolved,computed=resolve_test_ids(f2p+p2p,json.loads((xmls[0].parent/'collected-test-ids.json').read_text()))
        assert computed==aliases==json.loads((xmls[0].parent/'test-id-aliases.json').read_text())
        assert resolved==json.loads((xmls[0].parent/'resolved-tests.json').read_text())
    official=profile.grade_xml(xmls[0].read_text(),f2p+p2p,f2p,p2p,evaluation['exit_code'],aliases);assert official==evaluation_result['official']
    guest=[row for segment in cfg['path'] for row in rows(raw/'rollouts/backbone'/segment/'guest-memory.tsv')]
    host=rows(raw/'host/memory-samples.tsv');assert guest and host
    peak=max(guest,key=lambda r:int(r['MemTotal_kib'])-int(r['MemFree_kib']))
    used=(int(peak['MemTotal_kib'])-int(peak['MemFree_kib']))*1024
    parts={k:int(peak[k+'_kib'])*1024 for k in ('AnonPages','Cached','Buffers','Shmem')}
    hp=max(host,key=lambda r:int(r['cgroup_current_bytes']))
    events=json.loads((raw/'events.json').read_text());assert all(e['exit_code']==0 for e in events)
    calls=[e for e in events if e['stage']=='controller-exec'];assert len(calls)==5
    downloads=[e for e in events if e['stage']=='controller-download'];assert len(downloads)==5
    all_calls=calls+downloads
    phase=lambda name:next(e['duration_ns'] for e in events if e['stage']==name)
    evaluation_events=json.loads((evaluation_source/'events.json').read_text())
    evaluation_phase=lambda name:next(e['duration_ns'] for e in evaluation_events if e['stage']==name)
    summary=dict(status='PASS',task_id=cfg['task_id'],schedule='single frozen backbone',actions=35,segments=5,
        report_validation_status='PASS',scope='offline report audit; diagnostic completion, strict replay and task evaluation have separate statuses',
        official_task_status=official['status'],official=official,
        guest=dict(sample_count=len(guest),observed_peak_bytes=used,components_at_peak_bytes=parts,
                   components_at_peak_percent={k:100*v/used for k,v in parts.items()}),
        host=dict(sample_count=len(host),observed_peak_bytes=int(hp['cgroup_current_bytes']),
                  components_at_peak_bytes={k:int(hp['cgroup_'+k+'_bytes']) for k in ('anon','file','kernel')}),
        timing=dict(controller_elapsed_ns=result['controller_elapsed_ns'],cold_request_ns=phase('cold-start'),
                    guest_action_sum_ns=replay['guest_action_sum_ns'],controller_exec_ns=[e['duration_ns'] for e in calls],
                    controller_download_ns=[e['duration_ns'] for e in downloads],
                    frozen_exec_and_download_span_ns=max(e['start_monotonic_ns']+e['duration_ns'] for e in all_calls)-min(e['start_monotonic_ns'] for e in all_calls),
                    benchmark_test_call_ns=evaluation_phase('benchmark-pytest'),cleanup_call_ns=phase('delete-owned'),stages=events),
        page_cache=dict(physical_bytes='NOT_MEASURED',control='no manual host or guest cache drop',
                        state='verified task-converted layers bound from read-only RAM cache; baseline oracle precedes replay; not a cold-download comparison'
                        if receipt.get('immutable_cache_seed') else 'reused immutable service cache; missing task layers fetched at creation; baseline oracle precedes frozen replay'),
        readonly_cache_preparation=receipt.get('immutable_cache_seed'),
        checkpoint_restore='NOT_RUN',monitoring_impact='NOT_VALIDATED',VMM_PSS='NOT_MEASURED',
        config=cfg,sampling_variant=variant,shell_environment=environment,cleanup=receipt['cleanup'],storage={k:receipt[k] for k in ('capacity_bytes','observed_volume_used_peak_bytes',
               'observed_host_available_min_bytes','host_available_after_cleanup_bytes')},
        notes=['Guest /proc/meminfo KiB converted to bytes; used = MemTotal - MemFree; only original runner windows.',
               'Cached is a logical counter, not deduplicated physical page cache; Shmem overlaps Cached; Slab not sampled.',
               'Host memory.current/stat: memory charged to the temporary service cgroup, including VMM and charged image caches; excludes controller and separate RAM preparation caches. Read-only lower-layer RAM file allocation and preparation time are listed separately.',
               'Guest and host memory are not added; sample maxima are observed peaks, not exact instantaneous peaks.',
               'Stage durations are ns; nested stages overlap and must not be summed into total elapsed time.',
               'Original final self-tests contain one failure; benchmark tests run separately after archiving frozen patch.',
               'Evaluation uses selected SWE-bench test patch and all 957 listed F2P/P2P nodes directly in the image; full official Docker harness not run.',
               'One functional run does not establish performance variance, cache causality, monitoring impact or shared-memory benefit.'])
    if variant:summary['notes'].append('One sampler process per segment replaces shell process churn; asynchronous advisory markers may miss short phase boundaries. Frozen actions and expected exits unchanged; overhead NOT_VALIDATED.')
    if sphinx:
        summary['strict_replay_status']='PASS'
        summary['image_background']=background
        summary['notes']=[n for n in summary['notes'] if not n.startswith(('Original final self-tests','Evaluation uses selected'))]
        summary['notes'].extend(['Original Sphinx final focused tests: 25 passed; exploratory nonzero exits retained.',
            'Evaluation applies the selected SWE-bench test patch and all 25 original nodes; direct image pytest, not full official Docker harness.'])
    if pipeline:
        summary.update(sphinx_pipeline_diagnostic=True,diagnostic_status='PASS',strict_replay_status='FAIL',exit_differences=replay['exit_differences'])
        summary['notes'].append('Explicit approved read_repo/001 diagnostic only; exact command/stdout/stderr/prefix patch required; historical strict FAIL retained.')
    if pipeline and result.get('sphinx_pipeline_policy_version')==2:
        assert receipt['sphinx_pipeline_policy_version']==2
        summary['sphinx_pipeline_policy_version']=2
        summary['notes'][-1]='Approved diagnostic policy v2 permits 141 or 0 only for exact read_repo/001 and core_patch/001 command/stdout/stderr/segment patch; historical strict FAIL retained.'
    if continuation:
        summary.update(diagnostic_status='PASS',strict_replay_status='FAIL',
            strict_replay_scope=result['strict_replay_scope'],exit_differences=replay['exit_differences'],
            observed_exit_codes_status=replay['observed_exit_codes_status'])
        summary['notes'].append('Complete diagnostic mode only: historical strict replay FAIL is retained. Only trace_index_state/002 SIGPIPE 141 with exact original stdout is permitted; expected code remains 0.')
    if checkpoint:
        reference=Path(result['expected_evaluation_raw'])
        reference_result=json.loads((reference/'result.json').read_text())
        ref_summary,_,_=audit(reference) if sphinx else audit((ROOT/Path(reference_result['archived_replay_raw'])).resolve(),reference)
        assert ref_summary['official']==official==json.loads((raw/'expected-official-result.json').read_text())
        assert ref_summary['config']==cfg
        summary.update(checkpoint_restore='PASS',checkpoint_boundary=boundary,process_continuity=process,
            recovery_semantics=result['recovery_semantics'],expected_evaluation_raw=str(reference))
        summary['timing'].update(checkpoint_call_ns=phase('checkpoint'),delete_original_call_ns=phase('delete-original'),
            restore_call_ns=phase('restore'),restore_ready_call_ns=phase('restore-ready'),
            boundary_before_call_ns=phase('boundary-before'),boundary_after_call_ns=phase('boundary-after'),
            snapshot_cleanup_call_ns=phase('delete-owned-snapshot'))
        summary['notes'].append(('Snapshot after step 29; parent deleted; distinct restored VM executes only steps 30-35.' if sphinx else 'Snapshot after step 22; parent deleted; distinct restored VM executes only steps 23-35.')+' Files and progress match, next segment was absent, and one probe process retains PID/start ticks/boot ID and advances. This does not audit every process, anonymous-memory byte or open descriptor. Local snapshot removed after successful validation; remote durability and later reload not verified.')
    summary['test_id_aliases']=aliases
    if evaluation_raw:
        summary['evaluation_only_raw']=str(evaluation_raw)
        summary['source_statuses']=dict(frozen_replay_run=result['status'],independent_evaluation_run=evaluation_result['status'])
        summary['evaluation_cleanup']=evaluation_receipt['cleanup']
        summary['evaluation_storage']={k:evaluation_receipt[k] for k in ('capacity_bytes','observed_volume_used_peak_bytes','observed_host_available_min_bytes')}
        summary['timing'].update(initial_test_collection_attempt_ns=phase('benchmark-pytest'),
            independent_evaluation_controller_ns=evaluation_result['controller_elapsed_ns'],
            independent_evaluation_cleanup_ns=evaluation_phase('delete-owned'))
        eval_host=rows(evaluation_raw/'host/memory-samples.tsv');ep=max(eval_host,key=lambda r:int(r['cgroup_current_bytes']))
        summary['independent_evaluation_host']=dict(sample_count=len(eval_host),observed_peak_bytes=int(ep['cgroup_current_bytes']))
        summary['notes'].append('Replay run remains FAIL because its first test collection used three whitespace-truncated IDs. Independent evaluation applied the actual archived patch in a fresh VM; it did not replay actions or restore VM state. Unique collected full IDs map to original benchmark labels; all 957 cases must exist. Guest test-phase memory is not sampled. Report completion does not overwrite either source run.')
    return summary,guest,host


def report(raw,output,evaluation_raw=None):
    raw=raw.resolve();output=output.resolve();assert not output.is_relative_to(raw)
    if evaluation_raw:evaluation_raw=evaluation_raw.resolve();assert not output.is_relative_to(evaluation_raw)
    sources={'replay':raw,**({'evaluation':evaluation_raw} if evaluation_raw else {})}
    hashes=lambda:{label+'/'+str(p.relative_to(base)):sha(p) for label,base in sources.items() for p in sorted(base.rglob('*')) if p.is_file()}
    before=hashes()
    summary,guest,host=audit(raw,evaluation_raw)
    output.mkdir(parents=True,exist_ok=False)
    from report_sandbox_runtime import write_report
    summary['runtime_metrics']=write_report(raw,output,host)
    after=hashes();assert before==after
    summary['raw_file_count']=len(before);summary['raw_hashes_unchanged']=True
    (output/'raw-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('status=PASS result_dir='+str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--evaluation-raw',type=Path,help='Independent task-evaluation raw using actual archived patch; source FAIL retained')
    args=parser.parse_args();report(args.raw,args.output,args.evaluation_raw)
