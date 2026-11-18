"""Locate the saved 64/128 MiB initial-cache divergence, without live interfaces."""
import argparse
import csv
import json
from pathlib import Path

from kernel_cache_metadata import decode

PAGE=4096


def read(path):
    return json.loads(path.read_text())


def membership_change(before,after):
    left,a=decode(before);right,b=decode(after)
    assert left['stale_records']==right['stale_records']==0
    stable=sum(b.get(p)==tag for p,tag in a.items())
    retired=len(a)-stable;added=len(b)-stable
    assert len(b)-len(a)==added-retired
    return dict(before_bytes=len(a)*PAGE,after_bytes=len(b)*PAGE,
        stable_pages=stable,retired_or_reused_pages=retired,new_lifetime_pages=added,
        net_change_bytes=(len(b)-len(a))*PAGE,
        shmem_before_pages=left['shmem_pages'],shmem_after_pages=right['shmem_pages'],
        header_checks='PASS')


def memory_trace(path,cutoff):
    with path.open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
    times=[int(r['monotonic_ns']) for r in rows]
    assert all(a<b for a,b in zip(times,times[1:]))
    curve=[dict(guest_monotonic_ns=int(r['monotonic_ns']),cached_bytes=int(r['Cached_kib'])*1024,
        buffers_bytes=int(r['Buffers_kib'])*1024,available_bytes=int(r['MemAvailable_kib'])*1024,
        phase=r['phase']) for r in rows if int(r['monotonic_ns'])<cutoff]
    assert len(curve)>1
    a,b=min(zip(curve,curve[1:]),key=lambda pair:pair[1]['cached_bytes']-pair[0]['cached_bytes'])
    return dict(samples=len(curve),first=curve[0],last=curve[-1],
        largest_adjacent_drop=dict(before=a,after=b,delta_bytes=b['cached_bytes']-a['cached_bytes']),curve=curve)


def role_summary(source,role,side,events,sessions):
    event=lambda label:next(e for e in events if e['stage'].split('-',1)[1]==label)
    state=read(source/'capacity-control'/(role+'-result.json'))
    assert state['status']=='PASS' and state['damon_disabled']==dict(enabled='N',kdamond_pid=-1)
    ctrl=[json.loads(line) for line in (source/'capacity-control'/(role+'-samples.jsonl')).read_text().splitlines()]
    assert ctrl[0]['action']['requested_pages']==0
    assert ctrl[0]['before']['start_ns']>=state['io_start_ns']
    session=next(v for v in sessions if v['role']==role)
    assert session['status']=='PASS'
    memory=memory_trace(source/'runtime'/session['sandbox_id']/session['session_id']/'guest-memory.tsv',state['io_start_ns'])
    live=source/'live-cache'/side
    first=json.loads((live/'samples.jsonl').read_text().splitlines()[0]);assert first['point']==0
    change=membership_change((live/'pair-0000-before.bin').read_bytes(),(live/'pair-0000-after.bin').read_bytes())
    assert (change['before_bytes'],change['after_bytes'])==(first['guest_cache_before_bytes'],first['guest_cache_after_bytes'])
    before=first['guest_publication_before'];after={k:first[k] for k in ('guest_start_ns','guest_end_ns')}
    cap=event('capacity-start-'+role)
    task=min(e['start_monotonic_ns'] for e in events
        if e['stage'].split('-',1)[1]=='controller-call' and e['start_monotonic_ns']>cap['start_monotonic_ns'])
    stages=first['host_stage_costs']
    assert stages['guest_after']['host_end_ns']<task,'first pair extends into original suffix execution'
    requests=[r['before']['start_ns'] for r in ctrl if r['action']['requested_pages']]
    assert not requests or after['guest_end_ns']<requests[0],'first pair overlaps explicit reclaim'
    damon=state['capabilities_before']['damon']
    assert damon['enabled']=='Y' and int(damon['kdamond_pid'])>0
    return dict(memory_before_control=memory,first_pair=dict(**change,
        guest_before_start_ns=before['guest_start_ns'],guest_before_end_ns=before['guest_end_ns'],
        guest_after_start_ns=after['guest_start_ns'],guest_after_end_ns=after['guest_end_ns'],
        guest_interval_ns=after['guest_end_ns']-before['guest_start_ns'],
        host_metadata_relative_to_capacity_cli_ns={name:[stages[name][k]-cap['start_monotonic_ns']
            for k in ('host_start_ns','host_end_ns')] for name in ('guest_before','guest_after')},
        host_metadata_end_before_suffix_ns=task-stages['guest_after']['host_end_ns']),
        control_io_start_guest_ns=state['io_start_ns'],first_control_guest_ns=ctrl[0]['before']['start_ns'],
        first_control_cache_bytes=ctrl[0]['before']['cache_bytes'],first_control_requested_pages=0,
        first_nonzero_request_guest_ns=requests[0] if requests else None,
        restore_to_capacity_cli_ns=cap['start_monotonic_ns']-event('restore-'+role)['start_monotonic_ns'],
        damon_before={k:damon[k] for k in ('enabled','kdamond_pid','nr_reclaimed_regions','bytes_reclaimed_regions','nr_reclaim_tried_regions')})


def gap_terms(low,high):
    left,right=low['first_pair'],high['first_pair']
    before=right['before_bytes']-left['before_bytes']
    during=right['net_change_bytes']-left['net_change_bytes']
    tail=(high['first_control_cache_bytes']-right['after_bytes'])-(low['first_control_cache_bytes']-left['after_bytes'])
    final=high['first_control_cache_bytes']-low['first_control_cache_bytes']
    assert before+during+tail==final
    return dict(high_minus_low_before_first_pair_bytes=before,
        different_net_change_during_pair_bytes=during,after_pair_to_first_control_bytes=tail,
        high_minus_low_at_first_control_bytes=final)


def report(low,high,output):
    assert not output.exists()
    assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in (low,high))
    assert read(low/'checkpoint-11.json')==read(high/'checkpoint-11.json')
    groups={}
    for target,source in ((64,low),(128,high)):
        task=read(source/'result.json');assert task['status']=='PASS' and task['cache_target_mib']==target
        events=read(source/'events.json');sessions=read(source/'runtime-monitor.json')['sessions']
        groups[str(target)]={role:role_summary(source,role,side,events,sessions)
                            for role,side in [('early_1','child-a'),('early_2','child-b')]}
    terms=gap_terms(groups['64']['early_2'],groups['128']['early_2'])
    result=dict(status='PASS',mode='OFFLINE_SAVED_DATA_ONLY',live_runs=0,same_checkpoint=True,
        sources={'64':str(low),'128':str(high)},runs=groups,branch_b_gap_terms=terms,
        observation='B diverged in the first paired metadata window before original suffixes and before nonzero explicit reclaim.',
        decision='Recommend stopping owned guest DAMON and waiting for worker exit once in the common parent before checkpoint; preserve original enable state for restoration.',
        cause_evidence='DAMON remained enabled through restore checks and sampler setup; stop is asynchronous. Saved records do not identify the reclaiming thread for each retired page.',
        scope='Member-table capacity and guest Cached timeline; no new host physical measurement or task timing. Existing physical accounting and runtime outputs unchanged.')
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--low',type=Path,required=True);p.add_argument('--high',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=report(a.low.resolve(),a.high.resolve(),a.output.resolve())
    print('status='+result['status']+' result_dir='+str(a.output))
