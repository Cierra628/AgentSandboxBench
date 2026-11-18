"""Read completed no-reclaim Python baselines and select task-window samples."""
import argparse
import json
import math
from pathlib import Path
import statistics

from guest_diskstats import parse,delta

ROOT=Path(__file__).resolve().parents[1]
REFERENCES={'sphinx7590':'20261015T053138Z-1860659','xarray6992':'20261015T065740Z-1883035'}


def task_samples(rows,before,after,*,target_bytes=None):
    left=before['end_ns'];right=after['start_ns'];assert left<right
    assert rows
    if target_bytes is None:
        assert all(r['target_bytes'] is None and r['action']['requested_pages']==0 and r['action']['write_ns']==0 for r in rows)
    else:
        assert all(r['target_bytes']==target_bytes and r['action']['requested_pages']==max(0,(r['before']['cache_bytes']-target_bytes+4095)//4096) for r in rows)
    selected=[r for r in rows if left<=r['before']['start_ns'] and r['after' if target_bytes is not None else 'before']['end_ns']<=right]
    assert len(selected)>=2,'not enough task-window samples'
    assert all(r['before']['cache_bytes']==r['before']['metadata']['records']*4096 for r in selected)
    assert all(r['before']['metadata'][k]==0 for r in selected for k in ['duplicate_adds','missing_removes','out_of_range'])
    return selected


def distribution(values):
    ordered=sorted(values);assert ordered
    return dict(min_bytes=ordered[0],peak_bytes=ordered[-1],mean_bytes=statistics.mean(ordered),
                median_bytes=statistics.median(ordered),p95_bytes=ordered[math.ceil(.95*len(ordered))-1])


def report(runs_file,output):
    read=lambda p:json.loads(p.read_text())
    runs=read(runs_file);assert list(runs)==['sphinx7590','xarray6992']
    assert not output.exists()
    groups={}
    for task,binding in runs.items():
        storage=Path(binding['storage']);source=storage/'python-backbone';platform=Path(binding['platform'])
        assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in [storage,platform])
        receipt=read(storage/'result.json');result=read(source/'result.json');state=read(source/'capacity-control/result.json')
        assert read(platform/'result.json')['status']==receipt['status']==result['status']==state['status']=='PASS'
        assert all(receipt['cleanup'].values()) and not (storage/'volume.ext4').exists()
        assert result['checks']['cache_baseline_no_reclaim_and_damon_restored']=='PASS'
        assert state['baseline_no_reclaim'] and state['target_bytes'] is None
        assert state['capabilities_before']['kernel']=='6.1.175-asb-cache-v3-reclaim'
        assert state['damon_disabled']==dict(enabled='N',kdamond_pid=-1)
        original=state['capabilities_before']['damon']['enabled']
        assert state['damon_restored']['enabled']==original
        assert (state['damon_restored']['kdamond_pid']>0 if original=='Y' else state['damon_restored']['kdamond_pid']==-1)
        reference=read(ROOT/'.artifacts/storage-isolated-service'/REFERENCES[task]/'python-backbone/result.json')
        assert result['replay']['action_count']==35
        assert result['replay']['final_patch_sha256']==reference['replay']['final_patch_sha256']
        assert result['official']==reference['official']
        before=read(source/'capacity-task-before.json');after=read(source/'capacity-task-after.json')
        rows=[json.loads(line) for line in (source/'capacity-control/samples.jsonl').read_text().splitlines()]
        selected=task_samples(rows,before,after)
        capacity=distribution([r['before']['cache_bytes'] for r in selected])
        # The unified live report already independently recounted these same raw inputs.
        physical=read(Path(binding['report'])/'live-cache/summary.json')
        assert physical['status']=='PASS' and physical['sampling']['diagnostic_kernel']==state['capabilities_before']['kernel']
        points=[v for v in physical['measurements'] if before['end_ns']<=v['guest_publication_before']['guest_start_ns'] and v['guest_end_ns']<=after['start_ns']]
        assert points
        disks=delta(parse(state['diskstats_before']),parse(state['diskstats_after']))
        device_read=sum(v['sectors_read']*512 for k,v in disks.items() if k.startswith('vd') and k not in state['disk_partitions'])
        groups[task]=dict(source=str(source.relative_to(ROOT)),status='PASS',action_count=35,
            kernel=state['capabilities_before']['kernel'],task_window_guest_ns=[before['end_ns'],after['start_ns']],
            task_window_ns=after['start_ns']-before['end_ns'],task_samples=len(selected),all_observation_samples=len(rows),
            cache=capacity,shmem_range_bytes=[min(r['before']['shmem_metadata_bytes'] for r in selected),max(r['before']['shmem_metadata_bytes'] for r in selected)],
            dirty_peak_bytes=max(r['before']['memory']['Dirty'] for r in selected),
            identity_unknown_max_bytes=max(r['before']['metadata']['stale_records']*4096 for r in selected),
            identity_unknown_samples=sum(r['before']['metadata']['stale_records']>0 for r in selected),
            swap_used_peak_bytes=max(r['before']['memory']['SwapTotal']-r['before']['memory']['SwapFree'] for r in selected),
            suggested_targets_mib={str(f):math.floor(capacity['peak_bytes']/2**20*f) for f in [.5,.75]},
            task_tool_sum_ns=result['replay']['guest_action_sum_ns'],replay_with_checks_ns=result['frozen_replay_elapsed_ns'],
            baseline_process_cpu_seconds=state['cpu_user_seconds']+state['cpu_system_seconds'],
            baseline_observation_window_ns=state['io_end_ns']-state['io_start_ns'],guest_device_read_bytes=device_read,
            original_damon_enabled=original,damon_restored=True,reclaim_requests=0,
            physical=dict(all_samples=physical['sample_count'],task_samples=len(points),scope='VMM RAM PFNs only; no snapshot/backend/provider attribution in this run',
                task_confirmed_range_bytes=[min(v['host_confirmed_cache_bytes'] for v in points),max(v['host_confirmed_cache_bytes'] for v in points)],
                task_unknown_guest_max_bytes=max(v['host_residency_unconfirmed_guest_capacity_bytes'] for v in points)),
            official={k:result['official'][k] for k in ['status','passed','failed','skipped']},strict_replay_status=result.get('strict_replay_status','PASS'),
            curve=[dict(elapsed_ns=r['before']['start_ns']-before['end_ns'],cache_bytes=r['before']['cache_bytes']) for r in selected],
            physical_curve=[dict(elapsed_ns=v['guest_end_ns']-before['end_ns'],confirmed_bytes=v['host_confirmed_cache_bytes']) for v in points],
            storage_budget=dict(start_available_bytes=receipt['budget']['actual_host_available_bytes'],minimum_available_bytes=receipt['observed_host_available_min_bytes'],capacity_bytes=receipt['capacity_bytes']))
    summary=dict(status='PASS',scope='One baseline per task; no capacity-target experiments',actual_actions=70,runs=groups,
        limitations=['Observed task-window peaks at 50 ms, not continuous maxima or minimum working sets.',
            'Task window includes segment checks; preparation and official evaluation excluded.',
            'Guest device reads cover baseline process lifetime, including setup and monitoring; not host disk reads.',
            'Baseline and kernel/physical observation costs are included; no stable timing comparison from one run.',
            '50% and 75% targets are recommendations only; no limit group has run.'])
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def plot(summary,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,3.8))
    for ax,(task,g) in zip(axes,summary['runs'].items()):
        ax.plot([r['elapsed_ns']/1e9 for r in g['curve']],[r['cache_bytes']/2**20 for r in g['curve']],label='Guest logical cache',color='#23619b')
        ax.plot([r['elapsed_ns']/1e9 for r in g['physical_curve']],[r['confirmed_bytes']/2**20 for r in g['physical_curve']],'.',label='Host confirmed: VMM only',color='#d6742b')
        ax.set_title(task+' / no active reclaim');ax.set_xlabel('Task-window elapsed (s)');ax.set_ylabel('MiB')
        ax.spines[['top','right']].set_visible(False);ax.legend(frameon=False,fontsize=9)
    fig.tight_layout();fig.savefig(output/'baseline.png',dpi=160);fig.savefig(output/'baseline.svg');plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summary=report(a.runs.resolve(),a.output.resolve());plot(summary,a.output.resolve())
    print('status=PASS result_dir='+str(a.output))
