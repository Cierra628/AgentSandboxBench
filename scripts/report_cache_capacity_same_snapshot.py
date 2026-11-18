"""Offline 64/128 MiB comparison from one actual retained checkpoint."""
import argparse
import json
from pathlib import Path

from report_guest_cache_capacity_task import check_run,plot


def report(storage,wrapper,output,recovery=None):
    assert not output.exists()
    inputs=[storage,wrapper,*([recovery] if recovery else [])]
    assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in inputs)
    read=lambda p:json.loads(p.read_text())
    first=storage/'prettier6604';second=(recovery/'prettier6604') if recovery else storage/'prettier6604-128'
    states=[read(p/'result.json') for p in (first,second)]
    early=bool(states[0].get('damon_before_checkpoint'))
    assert early==bool(states[1].get('damon_before_checkpoint'))
    if early:assert read(first/'damon-before-checkpoint.json')==read(second/'damon-before-checkpoint.json')
    checkpoints=[read(p/'checkpoint-11.json') for p in (first,second)]
    assert checkpoints[0]==checkpoints[1]
    template=checkpoints[0]['template'];parent=checkpoints[0]['parent']
    assert states[0]['cleanup']['snapshots_retained_for_reuse']==[template]
    assert states[1]['cleanup']['snapshots_removed']==[template]
    assert [(s['actual_action_count'],s['reused_action_count']) for s in states]==[(31,0),(20,11)]
    children=[]
    for source in (first,second):
        for role in ('early_1','early_2'):
            restored=read(source/('restored-'+role+'.json'))
            assert restored['template']==template and restored['parent']==parent
            assert restored['status']==restored['process']['status']==restored['boundary']['status']=='PASS'
            children.append(restored['sandbox_id'])
    assert len(set(children))==4 and parent not in children
    labels=[[e['stage'].split('-',1)[1] for e in read(p/'events.json')] for p in (first,second)]
    assert [v.count('snapshot-call') for v in labels]==[1,0]
    assert [v.count('cold-backbone') for v in labels]==[1,0]
    groups={'64':check_run(storage,wrapper,64,incomplete_sequence=True) if recovery else check_run(storage,wrapper,64),
            '128':check_run(recovery or storage,wrapper,128,'prettier6604' if recovery else 'prettier6604-128','report-128')}
    for target,source in [('64',first),('128',second)]:
        for role,side in [('early_1','child-a'),('early_2','child-b')]:
            state=read(source/'capacity-control'/(role+'-result.json'))
            rows=[json.loads(line) for line in (source/'live-cache'/side/'samples.jsonl').read_text().splitlines()]
            selected=[v for v in rows if state['io_start_ns']<=v['guest_publication_before']['guest_start_ns']
                      and v['guest_end_ns']<=state['io_end_ns']]
            assert selected,'no guest metadata interval wholly inside control window'
            groups[target]['roles'][role]['control_physical']=dict(samples=len(selected),
                confirmed_range_bytes=[min(v['host_four_segment_current_union_bytes'] for v in selected),
                                       max(v['host_four_segment_current_union_bytes'] for v in selected)],
                unknown_guest_max_bytes=max(v['host_four_segment_unconfirmed_guest_capacity_bytes'] for v in selected),
                unknown_provider_range_bytes=[min(v['provider_segment_unattributed_resident_bytes'] for v in selected),
                                              max(v['provider_segment_unattributed_resident_bytes'] for v in selected)])
    for key in ('action_identity','manifests','final_patches','configuration'):
        assert groups['64']['common'][key]==groups['128']['common'][key],key
    def diff(high,low):return dict(target128=high,target64=low,delta=low-high,delta_percent=100*(low-high)/high if high else None)
    comparison={k:diff(groups['128']['common']['metrics'][k],groups['64']['common']['metrics'][k])
                for k in ('early_1_tool_sum_ns','early_2_tool_sum_ns','suffix_tool_sum_ns','branch_replay_elapsed_ns')}
    for role in ('early_1','early_2'):
        comparison[role+'_guest_read_bytes']=diff(groups['128']['roles'][role]['virtio_read_bytes'],groups['64']['roles'][role]['virtio_read_bytes'])
        comparison[role+'_initial_cache_bytes']=diff(groups['128']['roles'][role]['initial_cache_bytes'],groups['64']['roles'][role]['initial_cache_bytes'])
        if early:
            comparison[role+'_earliest_restored_cache_bytes']=diff(groups['128']['roles'][role]['damon_before_checkpoint']['earliest_restored_cache_bytes'],groups['64']['roles'][role]['damon_before_checkpoint']['earliest_restored_cache_bytes'])
    result=dict(status='PASS',order=[64,128],checkpoint_count=1,restored_children=4,actual_actions=51,
        private_service_recovery=bool(recovery),failed_restore_attempts=1 if recovery else 0,
        damon_before_checkpoint=early,
        prefix_actions=11,suffix_actions=40,reused_prefix_evidence_actions=11,runs=groups,comparison=comparison,
        limitations=['One reverse-order pair, not a stable slowdown estimate.',
            'Identical saved guest state does not ensure identical live cache after restore checks or equal host cache state.',
            '128 MiB is a ceiling target, not a forced fill. Periodic clean-file traversal is not LRU or a hard limit.',
            'Device reads cover the controller window, including preparation/stop/monitoring, not host disk reads.',
            'Control physical points have guest metadata intervals inside control; host queries may extend beyond them. Unknown guest and provider quantities remain separate.',
            'First lifecycle includes prefix creation/save; second skips them. Compare suffix timing, not whole lifecycle.',
            ('Both inherit disabled DAMON from one parent checkpoint and restore the saved parent original state before official checks; no controller-off baseline.' if early else
             'Both stop owned guest DAMON only while controlling, then restore it for official checks; no controller-off baseline.')])
    if recovery:result['limitations'].append('Private service restarted from a verified full-volume backup before 128 MiB after a slot-metadata filename collision; same checkpoint and binaries, host warm state not matched.')
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage',type=Path,required=True);p.add_argument('--wrapper',type=Path,required=True)
    p.add_argument('--recovery',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=report(a.storage.resolve(),a.wrapper.resolve(),a.output.resolve(),a.recovery.resolve() if a.recovery else None);plot(result,a.output.resolve())
    print('status='+result['status']+' result_dir='+str(a.output))
