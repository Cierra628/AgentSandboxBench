"""Compare two saved real-task runs under 128/64 MiB continuous targets."""
import argparse
import json
from pathlib import Path
import statistics

from report_provider_pair_timing import metrics
from guest_diskstats import parse,delta


def check_run(storage,wrapper,target,source_name='prettier6604',report_name='report',incomplete_sequence=False):
    read=lambda p:json.loads(p.read_text())
    receipt=read(storage/'result.json');source=storage/source_name;task=read(source/'result.json')
    if incomplete_sequence:
        assert receipt['status']=='FAIL' and receipt['failed_stage']=='prettier6604-reused-capacity-128'
        assert task['status']=='PASS' and target==64 and task['actual_action_count']==31
    else:assert receipt['status']=='PASS'
    assert all(receipt['cleanup'].values())
    assert not (storage/'volume.ext4').exists()
    assert receipt['budget']['actual_host_available_bytes']>50*2**30
    assert receipt['observed_host_available_min_bytes']>26*2**30
    assert task['cache_target_mib']==target
    parent=None
    if task.get('damon_before_checkpoint'):
        parent=read(source/'damon-before-checkpoint.json')
        assert parent['status']=='PASS' and parent['disabled']==dict(enabled='N',kdamond_pid=-1)
    common=metrics(source,'on');physical=read(wrapper/report_name/'summary.json');assert physical['status']=='PASS'
    roles={};maps=[];queries=0
    for role,side in [('early_1','child-a'),('early_2','child-b')]:
        state=read(source/'capacity-control'/(role+'-result.json'))
        assert state['status']=='PASS' and state['target_bytes']==target*2**20
        assert state['capabilities_before']['kernel']=='6.1.175-asb-cache-v3-reclaim'
        assert state['damon_disabled']['kdamond_pid']==-1
        expected=parent['original_enabled'] if parent else state['capabilities_before']['damon']['enabled']
        assert state['damon_restored']['enabled']==expected
        inherited=None
        if parent:
            inherited=read(source/('restored-damon-'+role+'.json'))
            assert inherited['status']=='PASS' and inherited['original_enabled']==expected
            assert inherited['damon']['enabled']=='N' and int(inherited['damon']['kdamond_pid'])==-1
            assert state['capabilities_before']['damon']['enabled']=='N' and int(state['capabilities_before']['damon']['kdamond_pid'])==-1
            assert state['parent_original_damon_enabled']==expected
            assert (state['damon_restored']['kdamond_pid']>0) if expected=='Y' else state['damon_restored']['kdamond_pid']==-1
        disks=delta(parse(state['diskstats_before']),parse(state['diskstats_after']))
        virtio=[name for name in disks if name.startswith('vd') and name not in state['disk_partitions']]
        assert virtio
        left=dict(v.split() for v in state['vmstat_before'].splitlines());right=dict(v.split() for v in state['vmstat_after'].splitlines())
        rows=[json.loads(line) for line in (source/'capacity-control'/(role+'-samples.jsonl')).read_text().splitlines()]
        assert len(rows)>2
        for row in rows:
            assert row['target_bytes']==target*2**20
            assert row['action']['requested_pages']==max(0,(row['before']['cache_bytes']-target*2**20+4095)//4096)
            assert all(row[side]['metadata'][k]==0 for side in ['before','after'] for k in ['duplicate_adds','missing_removes','out_of_range'])
        live=source/'live-cache'/side;sample=read(live/'result.json')
        assert sample['diagnostic_kernel']=='6.1.175-asb-cache-v3-reclaim' and not sample['vm_pause']
        for d in [live,live/'lower-query']:
            config=read(d/'snapshot-backend.json');maps.append(config['batch_lookup']['map_info']['id'])
            assert config['batch_lookup']['mincore_max_gap_pages']==2
            q=read(d/'snapshot-query-result.json');assert q['returncode']==0 and len(q['queries'])==2*sample['samples']
            assert all(v['missing_kernel_results']==0 for v in q['queries']);queries+=len(q['queries'])
        roles[role]=dict(samples=len(rows),target_bytes=target*2**20,
            initial_cache_bytes=rows[0]['before']['cache_bytes'],
            first_after_cache_bytes=rows[0]['after']['cache_bytes'],
            io_window_ns=state['io_end_ns']-state['io_start_ns'],
            virtio_read_bytes=sum(disks[name]['sectors_read']*512 for name in virtio),guest_device_deltas=disks,
            guest_vmstat_delta={k:int(right[k])-int(left[k]) for k in ['pgfault','pgmajfault','pgpgin','pgpgout']},
            before_range_bytes=[min(v['before']['cache_bytes'] for v in rows),max(v['before']['cache_bytes'] for v in rows)],
            after_range_bytes=[min(v['after']['cache_bytes'] for v in rows),max(v['after']['cache_bytes'] for v in rows)],
            after_mean_bytes=statistics.mean(v['after']['cache_bytes'] for v in rows),
            reclaim_calls=sum(v['action']['requested_pages']>0 for v in rows),
            reclaim_write_total_ns=sum(v['action']['write_ns'] for v in rows),
            controller_cycle_cpu_ns=sum(v['controller_cpu_ns'] for v in rows),
            controller_process_cpu_seconds=state['cpu_user_seconds']+state['cpu_system_seconds'],
            swap_used_range_bytes=[min(v['after']['memory']['SwapTotal']-v['after']['memory']['SwapFree'] for v in rows),
                                  max(v['after']['memory']['SwapTotal']-v['after']['memory']['SwapFree'] for v in rows)],
            curve=[dict(elapsed_ns=v['before']['start_ns']-rows[0]['before']['start_ns'],before_bytes=v['before']['cache_bytes'],
                        after_bytes=v['after']['cache_bytes']) for v in rows])
        if inherited:
            roles[role]['damon_before_checkpoint']=dict(original_enabled=expected,
                earliest_restored_cache_bytes=inherited['observed']['cache_bytes'],
                earliest_restored_guest_ns=inherited['observed']['start_ns'],
                inherited_disabled=True,original_restored=True)
    assert len(set(maps))==4
    return dict(status='PASS',target_mib=target,storage=str(storage),wrapper=str(wrapper),common=common,roles=roles,
        queries=queries,overlap_pairs=len(physical['pairs']),physical_samples=physical['child_samples'],
        preparation_ns=task['guest_capacity_preparation_ns'],stop_ns=task['guest_capacity_stop_ns'])


def report(runs_path,output):
    runs=json.loads(runs_path.read_text());assert list(runs)==['128','64'] and not output.exists()
    assert all(not output.is_relative_to(Path(v[k])) and not Path(v[k]).is_relative_to(output)
               for v in runs.values() for k in ['storage','wrapper'])
    groups={k:check_run(Path(v['storage']),Path(v['wrapper']),int(k)) for k,v in runs.items()}
    for key in ['action_identity','manifests','final_patches','configuration']:
        assert groups['128']['common'][key]==groups['64']['common'][key],key
    def diff(a,b):return dict(target128=a,target64=b,delta=b-a,delta_percent=100*(b-a)/a if a else None)
    comparison={k:diff(groups['128']['common']['metrics'][k],groups['64']['common']['metrics'][k])
                for k in ['early_1_tool_sum_ns','early_2_tool_sum_ns','suffix_tool_sum_ns','branch_replay_elapsed_ns','controller_elapsed_ns']}
    result=dict(status='PASS',order=[128,64],live_runs=2,actual_actions=62,runs=groups,comparison=comparison,
        limitations=['One sequential pair; descriptive impact, not a stable slowdown estimate.',
            'Continuous target after restore, not a forced initial fill; cache can remain below target when demand is small.',
            'Control curves include preparation and stop; original suffix execution timers exclude those operations.',
            'Guest device/fault deltas cover the entire controller window, including preparation/stop and monitors; not host disk I/O.',
            'Both conditions disable only owned guest DAMON while the controller runs and restore it before official checks.',
            'Normal sampler, custom controller and kernel instrumentation costs are included; no no-controller baseline in this pair.',
            'Reclaim is clean unmapped regular-file only, traversal policy not LRU, periodic target not a hard admission cap.'])
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def plot(summary,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(13,3.8))
    for ax,role in zip(axes[:2],['early_1','early_2']):
        for target,color in [('128','#23619b'),('64','#d6742b')]:
            rows=summary['runs'][target]['roles'][role]['curve']
            ax.plot([v['elapsed_ns']/1e9 for v in rows],[v['after_bytes']/2**20 for v in rows],color=color,label=target+' MiB')
            ax.axhline(int(target),color=color,linestyle='--',linewidth=.8)
        ax.set_title(role+': after periodic reclaim');ax.set_xlabel('Controller elapsed (s)');ax.set_ylabel('Guest cache (MiB)')
    ax=axes[2]
    for offset,target,color in [(-.18,'128','#23619b'),(.18,'64','#d6742b')]:
        values=[summary['runs'][target]['common']['tool_times_per_role_ns'][role]/1e9 for role in ['early_1','early_2']]
        bars=ax.bar([i+offset for i in range(2)],values,width=.34,color=color);ax.bar_label(bars,fmt='%.3f',padding=3)
    ax.set_xticks([0,1],['early_1','early_2']);ax.set_title('Original suffix tool time');ax.set_ylabel('Seconds');ax.set_ylim(0,ax.get_ylim()[1]*1.15)
    for ax in axes:ax.spines[['top','right']].set_visible(False)
    axes[0].legend(frameon=False);fig.suptitle('Real Prettier task: 128 / 64 MiB periodic cache targets')
    fig.tight_layout();fig.savefig(output/'task-capacity.png',dpi=160);fig.savefig(output/'task-capacity.svg');plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();s=report(a.runs.resolve(),a.output.resolve());plot(s,a.output.resolve())
    print('status='+s['status']+' result_dir='+str(a.output))
