"""Compare one cache-sampling off/on real Prettier pair from saved metadata."""
import argparse
import csv
import json
from pathlib import Path


def difference(off_ns,on_ns):
    return dict(off_ns=off_ns,on_ns=on_ns,delta_ns=on_ns-off_ns,
                delta_percent=100*(on_ns-off_ns)/off_ns if off_ns else None)


def metrics(source,mode):
    inventory={}
    def raw(relative):
        p=source/relative;s=p.stat();inventory[str(relative)]=[s.st_ino,s.st_size,s.st_mtime_ns]
        return p.read_text()
    task=json.loads(raw('result.json'));cfg=json.loads(raw('effective-config.json'))
    events=json.loads(raw('events.json'))
    assert task['status']=='PASS' and task['cache_sampling']==mode
    assert task['cache_pair'] and task['provider_cache'] and task['batch_lookup']
    assert task['action_count']==(20 if task.get('reused_action_count') else 31) and task['logical_action_count']==42
    if task.get('reused_action_count'):assert task['verified_action_count']==31
    assert all(v=='PASS' for v in task['checks'].values())
    trace=list(csv.DictReader(raw('segments.tsv').splitlines(),delimiter='\t'))
    totals={};actions={};manifests={}
    for role,count in [('backbone',11),('early_1',10),('early_2',10)]:
        duration=0;identity=[];manifest=[]
        for segment in [s for s in trace if s['rollout']==role]:
            rows=list(csv.DictReader(raw(f"rollouts/{role}/{segment['segment']}/steps.tsv").splitlines(),delimiter='\t'))
            assert all(r['branch']==role and r['round']==segment['segment'] for r in rows)
            identity.extend((r['round'],r['step'],int(r['exit_code'])) for r in rows)
            manifest.append((segment['segment'],segment['manifest_sha256']))
            duration+=sum(int(r['duration_ns']) for r in rows)
        assert len(identity)==task['paths'][role]['action_count']==count
        assert duration==task['paths'][role]['guest_action_sum_ns']
        totals[role]=duration;actions[role]=identity;manifests[role]=manifest
    for role in ['early_1','early_2']:
        p=task['paths'][role]
        assert p['source_patch_check']==p['independent_check']=='PASS'
        assert (p['official_task_status'],p['official_passed'],p['official_failed'])==('FAIL',0,2)
        restored=json.loads(raw('restored-'+role+'.json'))
        assert restored['status']==restored['process']['status']==restored['boundary']['status']=='PASS'
    assert all(e['exit_code']==0 for e in events)
    def event(label):
        selected=[e for e in events if e['stage'].split('-',1)[1]==label]
        assert len(selected)==1,label
        return selected[0]['duration_ns']
    phase=task['phases'][0];assert phase['name']=='real-task-branches'
    result=dict(task_tool_sum_ns=sum(totals[r] for r in totals if not task.get('reused_action_count') or r!='backbone'),
        prefix_tool_sum_ns=0 if task.get('reused_action_count') else totals['backbone'],
        suffix_tool_sum_ns=totals['early_1']+totals['early_2'],
        early_1_tool_sum_ns=totals['early_1'],early_2_tool_sum_ns=totals['early_2'],
        branch_replay_elapsed_ns=task['branch_replay_elapsed_ns'],
        branch_phase_with_checks_ns=phase['end_ns']-phase['start_ns'],
        restore_and_cache_ready_ns=task['restore_and_cache_ready_ns'],
        controller_elapsed_ns=task['controller_elapsed_ns'],
        cold_start_cli_ns=0 if task.get('reused_action_count') else event('cold-backbone'),
        checkpoint_cli_ns=0 if task.get('reused_action_count') else event('snapshot-call'),
        restore_cli_sum_ns=sum(event('restore-'+r) for r in ['early_1','early_2']),
        official_early_1_cli_ns=event('official-early_1'),official_early_2_cli_ns=event('official-early_2'))
    if task.get('reused_action_count'):
        assert task['actual_action_count']==20 and task['reused_action_count']==11
        assert not any(e['stage'].split('-',1)[1] in ('cold-backbone','snapshot-call') for e in events)
    assert all(v>0 for k,v in result.items() if not task.get('reused_action_count') or k not in ('cold_start_cli_ns','checkpoint_cli_ns','prefix_tool_sum_ns'))
    if mode=='off':
        assert task['physical_page_cache']=='NOT_MEASURED_SAMPLING_OFF'
        assert not (source/'live-cache').exists()
    else:
        assert task['cleanup']['samplers_stopped']
        result['cache_sampler_stop_ns']=task['cache_sampler_stop_ns']
    for name,before in inventory.items():
        s=(source/name).stat();assert [s.st_ino,s.st_size,s.st_mtime_ns]==before
    return dict(source=str(source),mode=mode,metrics=result,task_checks=task['checks'],
        tool_times_per_role_ns=totals,action_identity=actions,manifests=manifests,
        final_patches={r:p['final_patch_sha256'] for r,p in task['paths'].items()},
        configuration={k:cfg[k] for k in ['guest_cpu','guest_memory_mib','sample_interval_seconds','executed_paths','final_roles']},
        input_stat=inventory)


def report(off,on,output):
    assert not output.exists()
    assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in [off,on])
    groups={mode:metrics(path,mode) for mode,path in [('off',off),('on',on)]}
    for key in ['action_identity','manifests','final_patches','configuration']:
        assert groups['off'][key]==groups['on'][key],key
    for r in groups.values():r.pop('action_identity')
    compared={k:difference(groups['off']['metrics'][k],groups['on']['metrics'][k])
              for k in groups['off']['metrics']}
    result=dict(status='PASS',unit='nanoseconds',order=['off','on'],runs=groups,comparison=compared,
        definitions=dict(task_tool_sum_ns='Sum of 31 actual guest action durations; concurrent work, not elapsed wall time.',
            suffix_tool_sum_ns='Sum of the two original 10-action suffixes; prefix occurs before sampling in both runs.',
            branch_replay_elapsed_ns='Host monotonic elapsed for both original suffixes, after restore/readiness; excludes final independent/official checks.',
            branch_phase_with_checks_ns='Recorded branch phase, including final checks, official evaluation and terminal allocation scan.',
            restore_and_cache_ready_ns='After parent deletion through two restores, boundary checks and optional sampler readiness.',
            controller_elapsed_ns='Controller lifecycle including startup, snapshot, preparation, task, official evaluation and cleanup; excludes RAM seed preparation and offline reports.',
            cache_sampler_stop_ns='On-run sampler teardown only; no corresponding off-run operation.',
            other_cli_ns='Individual CLI elapsed or explicit sum; overlaps/containment prevent summing them into lifecycle time.'),
        scope='One sequential off/on configuration comparison; guest kernel metadata, provider observation/GET_DATA and regular monitoring held enabled. Switch controls guest publication, host cache collection and BPF queries.',
        limitations=['One pair, fixed off-then-on order; no stable slowdown distribution or uncertainty interval.',
            'Kernel-table and provider-instrumentation independent costs are not separated.',
            'No cache clearing, workload prefetch, forced residency or extra sampler pause.'])
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with (output/'timing.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['metric','off_ns','on_ns','delta_ns','delta_percent'])
        writer.writeheader();writer.writerows(dict(metric=k,**v) for k,v in compared.items())
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--off',type=Path,required=True);p.add_argument('--on',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();r=report(args.off.resolve(),args.on.resolve(),args.output.resolve())
    print('status='+r['status']+' result_dir='+str(args.output))
