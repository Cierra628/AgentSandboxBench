"""Recount one saved two-target guest cache-control validation offline."""
import argparse
import json
from pathlib import Path
import statistics

from report_live_task_cache import recount, physical_bytes, residency_gaps


def summarize(rows,phase,settle_seconds=5):
    selected=[v for v in rows if v['phase']==phase['name'] and v['before']['start_ns']>=phase['start_ns']+settle_seconds*1e9]
    assert len(selected)>=2
    target=phase['target_bytes'];before=[v['before']['cache_bytes'] for v in selected];after=[v['after']['cache_bytes'] for v in selected]
    weights=[max(0,b['before']['start_ns']-a['before']['start_ns']) for a,b in zip(selected,selected[1:])]
    weights.append(max(0,phase['end_ns']-selected[-1]['before']['start_ns']))
    result=dict(samples=len(selected),target_bytes=target,settle_seconds=settle_seconds,
        before_range_bytes=[min(before),max(before)],before_mean_bytes=statistics.mean(before),
        after_range_bytes=[min(after),max(after)],after_mean_bytes=statistics.mean(after),
        observed_window_ns=sum(weights),
        sample_hold_above_target_estimate_ns=sum(w for w,v in zip(weights,before) if target is not None and v>target),
        before_above_target_samples=sum(v>target for v in before) if target is not None else None,
        max_before_excess_bytes=max(max(0,v-target) for v in before) if target is not None else None,
        max_after_excess_bytes=max(max(0,v-target) for v in after) if target is not None else None)
    return result


def report(source,output):
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    read=lambda p:json.loads(p.read_text())
    runtime=read(source/'result.json');guest=read(source/'guest/result.json')
    assert runtime['status']==guest['status']=='PASS' and runtime['owned_guest_deleted']
    assert guest['content_checks']=='PASS' and guest['anonymous_excluded_from_cache']
    assert guest['anonymous_guard_resident_pages_before']==guest['anonymous_guard_resident_pages_after']==2048
    assert guest['damon_disabled']['kdamond_pid']==-1
    assert guest['damon_restored']['enabled']==guest['capabilities_after']['damon']['enabled']
    rows=[json.loads(line) for line in (source/'guest/samples.jsonl').read_text().splitlines()]
    assert [p['name'] for p in guest['phases']]==['observe','high','low','high-return']
    for row in rows:
        for value in [row['before'],row['after']]:
            assert value['cache_bytes']==value['metadata']['records']*4096
            assert all(value['metadata'][key]==0 for key in ['duplicate_adds','missing_removes','out_of_range'])
        if row['action']:
            assert row['action']['requested_pages']==max(0,(row['before']['cache_bytes']-row['target_bytes']+4095)//4096)
    phases=[dict(phase=p['name'],**summarize(rows,p)) for p in guest['phases'][1:]]
    high,low,returned=phases
    separated=low['before_range_bytes'][1]<min(high['before_range_bytes'][0],returned['before_range_bytes'][0])
    half_spacing=abs(high['target_bytes']-low['target_bytes'])/2
    target_nearest=all(abs(p['before_mean_bytes']-p['target_bytes'])<half_spacing for p in phases)
    samples=recount(source/'live-cache');state=read(source/'live-cache/result.json')
    assert state['samples']==len(samples) and state['status']=='PASS' and not state['vm_pause']
    actions=[v['action'] for v in rows if v['action'] and v['action']['requested_pages']]
    result=dict(status='PASS',control_result='PASS' if separated and target_nearest else 'TARGETS_NOT_SEPARATED',
        hard_limit=False,source=str(source),kernel=runtime['kernel'],period_seconds=guest['period_seconds'],
        demand=dict(file_bytes=256*2**20,block_bytes=512*1024,sleep_after_read_seconds=.02,reader=guest['reader']),
        phases=phases,phase_metadata=guest['phases'],cycle_samples=len(rows),reclaim_calls=len(actions),
        invalidated_entries=sum(v['invalidated_entries'] for v in actions),
        reclaim_write_total_ns=sum(v['write_ns'] for v in actions),
        reclaim_write_max_ns=max(v['write_ns'] for v in actions),
        kernel_reclaim_total_ns=sum(v['elapsed_ns'] for v in actions),
        controller_cpu_ns=sum(v['controller_cpu_ns'] for v in rows),
        controller_cycle_max_ns=max(v['cycle_elapsed_ns'] for v in rows),
        anonymous_guard_bytes=guest['anonymous_guard_bytes'],shmem_guard_bytes=guest['shmem_guard_bytes'],
        anonymous_guard_pages_before=guest['anonymous_guard_resident_pages_before'],
        anonymous_guard_pages_after=guest['anonymous_guard_resident_pages_after'],
        anon_pages_range_bytes=[min(v['after']['memory']['AnonPages'] for v in rows),max(v['after']['memory']['AnonPages'] for v in rows)],
        swap_used_range_bytes=[min(v['after']['memory']['SwapTotal']-v['after']['memory']['SwapFree'] for v in rows),
                              max(v['after']['memory']['SwapTotal']-v['after']['memory']['SwapFree'] for v in rows)],
        guest_cpu_seconds=guest['cpu_user_seconds']+guest['cpu_system_seconds'],
        host_sampling=dict(samples=len(samples),host_confirmed_range_bytes=[min(physical_bytes(v) for v in samples),max(physical_bytes(v) for v in samples)],
            unknown_guest_capacity_max_bytes=max(residency_gaps(v)['host_residency_unconfirmed_guest_capacity_bytes'] for v in samples),
            collector_thread_cpu_ns=state['host_thread_total_cpu_ns'],scope='VMM RAM PFNs only; no snapshot/provider source validation in this run'),
        capabilities=read(source/'guest/capabilities-before.json'),damon_disabled=guest['damon_disabled'],damon_restored=guest['damon_restored'],
        checks=dict(content_and_guards='PASS',anonymous_not_cache='PASS',metadata_counters='PASS',independent_physical_recount='PASS',
                    two_target_ranges_separated=separated,target_means_nearer_requested_level=target_nearest),
        limitations=['Periodic target for total guest logical file/shmem cache; reclaim attempts only clean unmapped regular-file pages.',
            'Victims follow superblock/inode/page-index traversal, not the normal LRU replacement policy.',
            'Dirty/mapped/shmem and other unhandled pages can prevent reaching the target. No admission-time hard cap.',
            'Invalidated-entry count may include concurrent shadow entries; current capacity comes from independent membership snapshots.',
            'Above-target duration is a sample-and-hold estimate using before-reclaim observations, not continuous event timing.',
            'One synthetic-demand guest, no real task replay, restore, dual-guest deduplication or sampling-off cost control.',
            'Whole-guest AnonPages includes control buffers; guard residency and source exclusions do not imply an unchanged total anonymous RSS.'])
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def plot(source,summary,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows=[json.loads(line) for line in (source/'guest/samples.jsonl').read_text().splitlines()]
    begin=summary['phase_metadata'][0]['start_ns']
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=True)
    xs=[(v['before']['start_ns']-begin)/1e9 for v in rows]
    axes[0].plot(xs,[v['before']['cache_bytes']/2**20 for v in rows],label='Before reclaim',color='#23619b',linewidth=1)
    axes[0].plot(xs,[v['after']['cache_bytes']/2**20 for v in rows],label='After reclaim',color='#d6742b',linewidth=1)
    axes[0].plot(xs,[v['target_bytes']/2**20 if v['target_bytes'] is not None else float('nan') for v in rows],
                 label='Periodic target',color='#333333',linestyle='--')
    axes[0].set_ylabel('Guest file cache (MiB)');axes[0].legend(frameon=False,ncol=3)
    axes[1].plot(xs,[v['cycle_elapsed_ns']/1e6 for v in rows],color='#23619b',label='Control cycle')
    axes[1].axhline(summary['period_seconds']*1000,color='#333333',linestyle='--',label='Requested interval')
    axes[1].set_ylabel('Cycle elapsed (ms)');axes[1].set_xlabel('Guest elapsed time (s)');axes[1].legend(frameon=False)
    for p in summary['phase_metadata'][1:]:
        x=(p['start_ns']-begin)/1e9
        for ax in axes:ax.axvline(x,color='#aaaaaa',linewidth=.7)
    for ax in axes:ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Continuous clean-file cache reclaim: 128 / 64 / 128 MiB periodic targets')
    fig.tight_layout();fig.savefig(output/'capacity.png',dpi=160);fig.savefig(output/'capacity.svg');plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();s=report(args.source.resolve(),args.output.resolve());plot(args.source.resolve(),s,args.output.resolve())
    print('status='+s['status']+' control='+s['control_result']+' result_dir='+str(args.output))
