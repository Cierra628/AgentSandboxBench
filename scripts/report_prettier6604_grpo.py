"""Audit corrected GRPO/BPO/TVCache raw data and write a new offline report."""
import argparse
import csv
import json
import re
from pathlib import Path
import shutil

from prettier6604_inputs import validate, sha
from prepare_prettier6604_rollouts import audit as audit_inputs, sampled_segment
from run_prettier6604_grpo import verify_path, execution_paths, verify_process, prefix_tree_plan
from prettier6604_boundary import verify as verify_boundary
from audit_prettier6604_official import inspect as inspect_official, reference, signature


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def audit(raw, task_dir):
    cfg, _, source_patch = validate()
    result=json.loads((raw/'result.json').read_text())
    schedule=result.get('schedule','grpo')
    assert result['status']=='PASS' and result['task_id']==cfg['task_id']
    assert schedule in ('grpo','bpo','tvcache')
    check='sampled_actions_order_exit_codes_and_distinct_instances' if schedule!='grpo' else 'sampled_actions_order_exit_codes_and_distinct_roots'
    assert result['checks'][check]=='PASS'
    assert result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
    assert result['cleanup']['service_sandbox_list_empty'] and result['cleanup']['monitor_stopped']
    config=json.loads((raw/'effective-config.json').read_text())
    roles=result['roles'];assert roles==config['selected_roles'] and len(set(roles))==len(roles)
    final_roles=result.get('final_roles',roles)
    workload=raw/'frozen-input';provenance=json.loads((raw/'input-provenance.json').read_text())
    assert sha(workload/'agent-sampling.json')==cfg['sampling_sha256']
    variant_file=raw/'sampling-variant.json'
    for name,digest in provenance['exported_sha256'].items():
        if name=='guest-runner.sh' and variant_file.exists():
            from sandbox_runtime_monitor import boundary_runner
            variant=json.loads(variant_file.read_text())
            original=workload/'guest-runner.original.sh'
            assert sha(original)==cfg['runner_sha256']==digest==variant['original_runner_sha256']
            assert sha(workload/name)==variant['executed_runner_sha256']
            assert (workload/name).read_text()==boundary_runner(original.read_text(),guest_phases=variant.get('phase_updates')=='guest runner')
        else:assert sha(workload/name)==digest
    samples=json.loads((workload/'agent-sampling.json').read_text())['segments']
    paths=json.loads((workload/'rollouts.json').read_text())
    executed=config.get('executed_paths',{r:paths[r] for r in roles})
    smoke_role=final_roles[0] if schedule=='bpo' and len(final_roles)==1 else roles[0] if schedule=='grpo' and len(roles)==1 else None
    if schedule=='tvcache' and len(roles)==2:smoke_role='early_1'
    assert executed==execution_paths(paths,schedule,smoke_role)
    names=provenance['original_segment_names']
    assert audit_inputs(workload,samples,paths,names)['status']=='PASS'
    trace=rows(raw/'segments.tsv');ids=rows(raw/'sandbox-ids.tsv')
    assert len(ids)==len(roles) and {r['role'] for r in ids}==set(roles)
    by_role={r['role']:r['sandbox_id'] for r in ids};assert len(set(by_role.values()))==len(roles)
    assert set(result['cleanup']['sandboxes_removed'])==set(by_role.values())
    if schedule=='tvcache':
        planned=prefix_tree_plan({r:paths[r] for r in roles})
        assert config['prefix_tree_plan']==planned
        assert result['checks']['deterministic_prefix_tree_order']=='PASS'
        assert [[r['rollout'],r['segment']] for r in trace]==planned['segments']
        forks=[r for r in rows(raw/'events.tsv') if r['event']=='fork']
        assert [r['point'] for r in forks]==list(planned['forks'])
        for event in forks:
            f=planned['forks'][event['point']]
            assert event['parent']==by_role[f['parent_role']]
            assert event['children'].split(',')==[by_role[r] for r in f['child_roles']]
            assert int(event['latency_ns'])>0
            for child in f['child_roles']:
                assert next(r['created_from'] for r in ids if r['role']==child)==event['checkpoint_id']
    checkpoints=[]
    if schedule=='grpo':assert all(not r['parent_id'] and r['created_from']=='root' for r in ids)
    else:
        assert result['checks']['restored_files_progress_and_process']=='PASS'
        steps=[11] if smoke_role else [11,24]
        checkpoints=[json.loads((raw/('checkpoint-'+str(step)+'.json')).read_text()) for step in steps]
        assert set(result['cleanup']['snapshots_removed'])=={c['template'] for c in checkpoints}
        parent=by_role['backbone']
        root_row=next(r for r in ids if r['role']=='backbone')
        assert not root_row['parent_id'] and root_row['created_from']=='root'
        for saved in checkpoints:
            assert saved['parent']==parent and saved['status']=='PASS'
            verify_boundary(saved['boundary'],saved['boundary'],saved['step'])
            for segment in ('read_repo','read_typescript') if saved['step']==11 else ('read_repo','read_typescript','core_patch','core_verify'):
                for name in ('steps.tsv','actions.tsv'):
                    key='/workspace/artifacts/exp5/backbone/'+segment+'/'+name
                    assert saved['boundary']['files_sha256'][key]==sha(raw/'rollouts/backbone'/segment/name)
        for row in ids:
            role=row['role']
            if role=='backbone':continue
            restored=json.loads((raw/('restored-'+role+'.json')).read_text())
            saved=next(c for c in checkpoints if c['step']==(11 if role.startswith('early') else 24))
            assert row['parent_id']==parent==restored['parent'] and row['created_from']==saved['template']==restored['template']
            assert row['sandbox_id']==restored['sandbox_id'] and restored['status']=='PASS'
            assert restored['before']==saved['boundary'] and restored['process']['before']==saved['process']
            verify_boundary(restored['before'],restored['after'],saved['step'])
            verify_process(restored['process']['before'],restored['process']['after'])
            assert restored['branch_next_segment']==executed[role][0]
    expected=reference(raw/'expected-official-audit.json')
    guests={};per_role={}
    for role in roles:
        assert [r['segment'] for r in trace if r['rollout']==role]==executed[role]
        assert all(r['sandbox_id']==by_role[role] for r in trace if r['rollout']==role)
        info=verify_path(raw,role,executed[role],workload,samples,names)
        for key,value in info.items():assert result['paths'][role][key]==value
        guest=[r for segment in executed[role] for r in rows(raw/'rollouts'/role/segment/'guest-memory.tsv')]
        peak=max(guest,key=lambda r:int(r['MemTotal_kib'])-int(r['MemFree_kib']))
        used=(int(peak['MemTotal_kib'])-int(peak['MemFree_kib']))*1024
        parts={k:int(peak[k+'_kib'])*1024 for k in ('AnonPages','Cached','Buffers','Shmem')}
        guests[role]=guest
        per_role[role]=dict(**info,sample_count=len(guest),components_at_peak_bytes=parts,
                components_at_peak_percent={k:100*v/used for k,v in parts.items()})
        if role not in final_roles:
            assert schedule=='bpo' and smoke_role and role=='backbone'
            assert result['paths'][role]['official_task_status']=='NOT_RUN_PREFIX_ONLY'
            per_role[role]['official_task_status']='NOT_RUN_PREFIX_ONLY'
            continue
        diff_files=list(raw.glob('*-source-diff-'+role+'.stdout'));assert len(diff_files)==1
        assert diff_files[0].read_text().strip()==('' if role=='early_2' else source_patch.strip())
        assert json.loads((raw/('independent-'+role+'.json')).read_text())['status']=='PASS'
        logs=[p for p in raw.glob('*.stdout') if re.fullmatch(r'\d{4}-official-'+role+r'\.stdout',p.name)];assert len(logs)==1
        official=inspect_official(task_dir,logs[0].read_text())
        assert official==json.loads((raw/('official-result-'+role+'.json')).read_text())
        if role!='early_2':assert signature(official)==signature(expected)
        per_role[role].update(official_task_status=official['status'],official_passed=official['parser_results']['num_tests_passed'],
                official_failed=official['parser_results']['num_tests_failed'])
    assert result['action_count']==sum(v['action_count'] for v in per_role.values())
    logical_count=sum(len(sampled_segment(samples,r,names[s])['actions']) for r in final_roles for s in paths[r])
    assert result.get('logical_action_count',result['action_count'])==logical_count
    assert result['segment_count']==len(trace)
    host=rows(raw/'host/memory-samples.tsv');assert host
    peak=max(host,key=lambda r:int(r['cgroup_current_bytes']))
    events=json.loads((raw/'events.json').read_text());assert all(r['exit_code']==0 for r in events)
    cold={role:next(r for r in events if r['stage'].endswith('-cold-'+role)) for role in roles
          if any(r['stage'].endswith('-cold-'+role) for r in events)}
    cold_overlap=(max(e['start_monotonic_ns'] for e in cold.values()) < min(e['start_monotonic_ns']+e['duration_ns'] for e in cold.values())) if len(cold)>1 else None
    if schedule=='grpo' and len(roles)==7:assert cold_overlap
    tool_calls=[e for e in events if e['stage'].endswith('-controller-call')];assert len(tool_calls)==2*len(trace)
    span=lambda values:max(e['start_monotonic_ns']+e['duration_ns'] for e in values)-min(e['start_monotonic_ns'] for e in values)
    checkpoint_calls=[e for e in events if e['stage'].endswith('-snapshot-call')]
    if schedule!='grpo':assert len(checkpoint_calls)==len(checkpoints)
    restore_calls={role:next(e for e in events if e['stage'].endswith('-restore-'+role)) for role in roles if role!='backbone'} if schedule!='grpo' else {}
    return dict(status='PASS',scope=result['scope'],task_id=result['task_id'],roles=roles,schedule=schedule,
            logical_action_count=logical_count,
            actions=result['action_count'],segments=result['segment_count'],per_role=per_role,
            host=dict(sample_count=len(host),observed_peak_bytes=int(peak['cgroup_current_bytes']),
                components_at_peak_bytes={k:int(peak['cgroup_'+k+'_bytes']) for k in ('anon','file','kernel')}),
            timing=dict(controller_elapsed_ns=result['controller_elapsed_ns'],cold_request_span_ns=span(list(cold.values())),
                cold_request_per_role_ns={r:e['duration_ns'] for r,e in cold.items()},cold_requests_overlap=cold_overlap,
                checkpoint_call_ns=[e['duration_ns'] for e in checkpoint_calls],
                restore_request_per_role_ns={r:e['duration_ns'] for r,e in restore_calls.items()},
                frozen_tools_and_downloads_span_ns=span(tool_calls),
                frozen_tools_and_downloads_call_sum_ns=sum(e['duration_ns'] for e in tool_calls),stages=events),
            cleanup=result['cleanup'],physical_page_cache='NOT_MEASURED',VMM_PSS='NOT_MEASURED',
            monitoring_impact='NOT_VALIDATED',checkpoint_restore='PASS' if schedule!='grpo' else 'NOT_RUN',
            checkpoint_count=len(checkpoints),
            measurement_notes=['Guest: /proc/meminfo KiB converted to bytes; MemTotal-MemFree; runner windows only.',
                'Per-guest peaks are not summed into a concurrent peak. Guest and host counters are not added.',
                'Cached is a logical guest counter, not deduplicated physical page cache. Shmem overlaps Cached; Slab not collected.',
                'Host: memory charged to one temporary service cgroup; includes its VMMs, excludes controller and RAM cache preparation. This is not total project memory. Zero VMM columns are unmeasured placeholders.',
                'Times are ns. Cold creation includes missing layers. Controller tools include execution and downloads.',
                'Parallel call sums are not elapsed time. Host phase names may overlap between branches.',
                'BPO/TVCache child curves contain executed suffixes only; inherited prefix actions and memory samples are not counted again. Logical action count is separate.',
                'First frozen execution to last download is a wall-clock span; checkpoint/restore and verification gaps may be inside it. It is not exclusive tool time.',
                'Guest/host curves use recorded wall clocks; no independent clock calibration. Sampling maxima are observed peaks.']),guests,host


def report(raw,output,task_dir):
    raw=raw.resolve();output=output.resolve()
    assert not output.is_relative_to(raw), 'report must be outside raw'
    before={str(p.relative_to(raw)):sha(p) for p in sorted(raw.rglob('*')) if p.is_file()}
    summary,guests,host=audit(raw,task_dir)
    output.mkdir(parents=True,exist_ok=False)
    from report_sandbox_runtime import write_report
    summary['runtime_metrics']=write_report(raw,output,host)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    after={str(p.relative_to(raw)):sha(p) for p in sorted(raw.rglob('*')) if p.is_file()}
    assert before==after, 'raw files changed during report'
    (output/'input-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    shutil.copy2(Path(__file__),output/'report-source.py')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--official-task',type=Path,required=True)
    args=parser.parse_args();summary=report(args.raw,args.output,args.official_task)
    print('status='+summary['status']+' result_dir='+str(args.output))
