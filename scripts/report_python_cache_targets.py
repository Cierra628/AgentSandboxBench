"""Compare four completed Python target runs with the saved no-reclaim baselines."""
import argparse
import json
from pathlib import Path

from guest_diskstats import parse,delta
from report_python_cache_baseline import ROOT,distribution,task_samples

ORDER=['sphinx7590-65','sphinx7590-98','xarray6992-167','xarray6992-251']


def difference(baseline,value):
    assert baseline>0
    return dict(baseline=baseline,value=value,delta=value-baseline,delta_percent=100*(value-baseline)/baseline)


def control_stats(rows,target):
    values=[r['after']['cache_bytes'] for r in rows]
    stats=distribution(values)
    stats.update(above_target_samples=sum(v>target for v in values),total_samples=len(values),
        above_target_sample_fraction=sum(v>target for v in values)/len(values),max_excess_bytes=max(0,max(values)-target))
    return stats


def report(runs_file,baseline_file,baseline_runs_file,output):
    read=lambda p:json.loads(p.read_text())
    runs=read(runs_file);baseline=read(baseline_file);baseline_bindings=read(baseline_runs_file)
    assert list(runs)==ORDER and baseline['status']=='PASS' and not output.exists()
    groups={}
    for key,binding in runs.items():
        task,target_text=key.rsplit('-',1);target_mib=int(target_text);target=target_mib*2**20
        storage=Path(binding['storage']);source=storage/'python-backbone';platform=Path(binding['platform'])
        assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in [storage,platform,baseline_file.parent])
        receipt=read(storage/'result.json');result=read(source/'result.json');state=read(source/'capacity-control/result.json')
        assert read(platform/'result.json')['status']==receipt['status']==result['status']==state['status']=='PASS'
        assert all(receipt['cleanup'].values()) and not (storage/'volume.ext4').exists()
        assert result['checks']['cache_target_and_damon_restored']=='PASS' and result['cache_target_mib']==target_mib
        assert state['target_bytes']==target and not state.get('baseline_no_reclaim',False)
        assert state['damon_disabled']==dict(enabled='N',kdamond_pid=-1)
        original=state['capabilities_before']['damon']['enabled'];assert state['damon_restored']['enabled']==original
        assert (state['damon_restored']['kdamond_pid']>0 if original=='Y' else state['damon_restored']['kdamond_pid']==-1)
        old=baseline['runs'][task];old_source=ROOT/old['source'];old_result=read(old_source/'result.json')
        assert result['replay']['action_count']==35 and result['replay']['final_patch_sha256']==old_result['replay']['final_patch_sha256']
        assert result['official']==old_result['official'] and state['capabilities_before']['kernel']==old['kernel']
        # Configs differ only in target/baseline selection and output location.
        configs=[read(p/'effective-config.json') for p in [platform,Path(baseline_bindings[task]['platform'])]]
        compared=[{k:v for k,v in c.items() if k not in ['guest_cache_baseline','guest_cache_target_mib','output_root']} for c in configs]
        assert compared[0]==compared[1]
        before=read(source/'capacity-task-before.json');after=read(source/'capacity-task-after.json')
        rows=[json.loads(line) for line in (source/'capacity-control/samples.jsonl').read_text().splitlines()]
        selected=task_samples(rows,before,after,target_bytes=target)
        assert all(r['after']['cache_bytes']==r['after']['metadata']['records']*4096 for r in selected)
        assert all(r['after']['metadata'][k]==0 for r in selected for k in ['duplicate_adds','missing_removes','out_of_range'])
        physical=read(Path(binding['report'])/'live-cache/summary.json')
        assert physical['status']=='PASS' and physical['sampling']['diagnostic_kernel']==old['kernel']
        assert physical['sampling']['interval_seconds']==1.0 and state['period_seconds']==.05
        points=[p for p in physical['measurements'] if before['end_ns']<=p['guest_publication_before']['guest_start_ns'] and p['guest_end_ns']<=after['start_ns']]
        assert points
        disks=delta(parse(state['diskstats_before']),parse(state['diskstats_after']))
        device_read=sum(v['sectors_read']*512 for k,v in disks.items() if k.startswith('vd') and k not in state['disk_partitions'])
        cpu=state['cpu_user_seconds']+state['cpu_system_seconds']
        events=read(source/'events.json')
        groups[key]=dict(status='PASS',task=task,target_mib=target_mib,source=str(source.relative_to(ROOT)),actual_actions=35,
            task_window_guest_ns=[before['end_ns'],after['start_ns']],task_window_ns=after['start_ns']-before['end_ns'],
            task_start_cache_bytes=before['cache_bytes'],task_end_cache_bytes=after['cache_bytes'],all_control_samples=len(rows),task_samples=len(selected),
            before_cache=distribution([r['before']['cache_bytes'] for r in selected]),after_cache=control_stats(selected,target),
            identity_unknown_max_bytes=max(r[s]['metadata']['stale_records']*4096 for r in selected for s in ['before','after']),
            shmem_range_bytes=[min(r['after']['shmem_metadata_bytes'] for r in selected),max(r['after']['shmem_metadata_bytes'] for r in selected)],
            dirty_peak_bytes=max(r['after']['memory']['Dirty'] for r in selected),swap_used_peak_bytes=max(r['after']['memory']['SwapTotal']-r['after']['memory']['SwapFree'] for r in selected),
            reclaim_calls_all=sum(r['action']['requested_pages']>0 for r in rows),reclaim_calls_task=sum(r['action']['requested_pages']>0 for r in selected),
            reclaim_write_total_ns=sum(r['action']['write_ns'] for r in rows),controller_process_cpu_seconds=cpu,
            setup_cli_sum_ns=sum(e['duration_ns'] for e in events if e['stage'] in ['capacity-upload','capacity-start','capacity-ready','capacity-disabled-state']),
            stop_cli_sum_ns=sum(e['duration_ns'] for e in events if e['stage'] in ['capacity-stop','capacity-collect-result.json','capacity-collect-samples.jsonl']),
            observation_window_ns=state['io_end_ns']-state['io_start_ns'],guest_device_read_bytes=device_read,
            task_tool_sum_ns=result['replay']['guest_action_sum_ns'],replay_with_checks_ns=result['frozen_replay_elapsed_ns'],
            original_damon_enabled=original,damon_restored=True,
            physical=dict(all_samples=physical['sample_count'],task_samples=len(points),scope='VMM RAM PFNs only; no snapshot/backend/provider attribution',
                confirmed_range_bytes=[min(p['host_confirmed_cache_bytes'] for p in points),max(p['host_confirmed_cache_bytes'] for p in points)],
                unknown_guest_max_bytes=max(p['host_residency_unconfirmed_guest_capacity_bytes'] for p in points)),
            official={k:result['official'][k] for k in ['status','passed','failed','skipped']},strict_replay_status=result.get('strict_replay_status','PASS'),
            comparison=dict(tool_time=difference(old['task_tool_sum_ns'],result['replay']['guest_action_sum_ns']),
                replay_with_checks=difference(old['replay_with_checks_ns'],result['frozen_replay_elapsed_ns']),
                guest_read=difference(old['guest_device_read_bytes'],device_read),control_cpu=difference(old['baseline_process_cpu_seconds'],cpu)),
            curve=[dict(elapsed_ns=r['before']['start_ns']-before['end_ns'],before_bytes=r['before']['cache_bytes'],after_bytes=r['after']['cache_bytes']) for r in selected],
            storage_budget=dict(start_available_bytes=receipt['budget']['actual_host_available_bytes'],minimum_available_bytes=receipt['observed_host_available_min_bytes'],capacity_bytes=receipt['capacity_bytes']))
    summary=dict(status='PASS',actual_actions=140,order=ORDER,baseline_reused=str(baseline_file.relative_to(ROOT)),runs=groups,
        limitations=['One run per target and one reused baseline; sequential descriptive comparisons, not stable slowdown estimates.',
            'Targets act from after guest creation through replay, including preparation; no forced initial fill or matched cache contents.',
            'Periodic clean unmapped file traversal, not LRU or a hard admission limit; after-sample excess is not continuous timing.',
            'Reclaim write duration, process CPU and task timers overlap and must not be summed.',
            'Device read deltas span setup/control/monitoring, not precise tool-only or host physical-disk reads.',
            'Physical points select guest metadata intervals inside the task window; host queries may extend beyond them; unknown capacity remains separate.'])
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def plot(summary,baseline,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4.4))
    for ax,task in zip(axes,['sphinx7590','xarray6992']):
        old=baseline['runs'][task]['curve']
        ax.plot([v['elapsed_ns']/1e9 for v in old],[v['cache_bytes']/2**20 for v in old],color='#626b73',label='No active reclaim')
        for color,g in zip(['#d6742b','#23619b'],[v for v in summary['runs'].values() if v['task']==task]):
            ax.plot([v['elapsed_ns']/1e9 for v in g['curve']],[v['after_bytes']/2**20 for v in g['curve']],color=color,label=str(g['target_mib'])+' MiB target')
            ax.axhline(g['target_mib'],linestyle='--',color=color,linewidth=.7)
        ax.set_title(task+' / after periodic reclaim');ax.set_xlabel('Task-window elapsed (s)');ax.set_ylabel('Guest logical cache (MiB)')
        ax.spines[['top','right']].set_visible(False)
        ax.legend(loc='upper center',bbox_to_anchor=(.5,-.22),ncol=3,frameon=False,fontsize=8)
    fig.tight_layout();fig.savefig(output/'targets.png',dpi=160);fig.savefig(output/'targets.svg');plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--baseline-runs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summary=report(a.runs.resolve(),a.baseline.resolve(),a.baseline_runs.resolve(),a.output.resolve());plot(summary,json.loads(a.baseline.read_text()),a.output.resolve())
    print('status=PASS result_dir='+str(a.output))
