"""Summarize the fixed six within-round cache comparisons; no task execution."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics

from run_python_cache_repeats import ROOT, TASKS, TARGETS, schedule


def describe(values):
    assert len(values)>=2
    return dict(count=len(values),values=values, mean=statistics.mean(values), median=statistics.median(values),
                minimum=min(values), maximum=max(values), sample_stdev=statistics.stdev(values),
                positive=sum(v>0 for v in values), negative=sum(v<0 for v in values))


def spread(values):
    assert len(values)==6
    return describe(values)


def source_session(item, receipt, binding, pause, resume):
    assert receipt['source_service_name']==binding['service']
    after=pause is not None and item['index']>pause['completed_runs']
    expected=resume['identity'] if after else binding['identity']
    assert receipt['original_service']==expected, 'unrecorded source process change'
    return 'after_pause' if after else 'before_pause'


def paired_summary(group):
    continuous=[v for v in group if not v['cross_pause_pair']]
    return dict(all_six={metric:spread([v['comparison'][metric]['delta_percent'] for v in group])
                         for metric in ['tool_time','replay_with_checks','guest_read']},
                continuous={metric:describe([v['comparison'][metric]['delta_percent'] for v in continuous])
                            for metric in ['tool_time','replay_with_checks','guest_read']},
                continuous_rounds=[v['round'] for v in continuous],
                cross_pause=[dict(round=v['round'],comparison=v['comparison']) for v in group if v['cross_pause_pair']])


def low_high_summary(rows,task):
    comparisons=[]
    for number in range(1,7):
        low=next(v for v in rows if v['task']==task and v['round']==number and v['condition']=='L')
        high=next(v for v in rows if v['task']==task and v['round']==number and v['condition']=='H')
        assert low['source_session']==high['source_session']
        comparisons.append({k:100*(low[k]-high[k])/high[k]
                            for k in ['task_tool_sum_ns','replay_with_checks_ns','guest_device_read_bytes']})
    return {k:spread([v[k] for v in comparisons]) for k in comparisons[0]}


def report(directory, output):
    read=lambda p:json.loads(p.read_text())
    assert read(directory/'result.json')['status']=='PASS'
    assert read(directory/'plan.json')==schedule() and not output.exists()
    bindings=read(directory/'runs.json'); assert len(bindings)==36
    source=read(directory/'source-binding.json')
    pause=read(directory/'pause-receipt.json') if (directory/'pause-receipt.json').exists() else None
    resume=read(directory/'resume-source.json') if pause else None
    if pause:
        assert pause['status']=='PAUSED' and resume['status']=='PASS'
        assert pause['source_service']==resume['service']==source['service']
        assert resume['retained_container']
    groups={}; rows=[]; identities=[]
    for number in range(1,7):
        folder=directory/f'round-{number:02d}'
        baselines=read(folder/'baseline/summary.json'); targets=read(folder/'targets/summary.json')
        assert baselines['status']==targets['status']=='PASS'
        for task in TASKS:
            baseline=baselines['runs'][task]
            for condition in 'BLH':
                target='baseline' if condition=='B' else TARGETS[task][condition]
                key=f'r{number:02d}-{task}-{target}'
                item=next(v for v in schedule()['runs'] if v['key']==key)
                value=baseline if condition=='B' else targets['runs'][task+'-'+target]
                binding=bindings[key]; receipt=read(Path(binding['storage'])/'result.json')
                assert receipt['status']=='PASS' and all(receipt['cleanup'].values())
                assert receipt['source_service_kind']=='source-rebuilt'
                session=source_session(item,receipt,source,pause,resume)
                identity=dict(session=session,service=receipt['source_service_name'],**receipt['original_service'])
                if identity not in identities: identities.append(identity)
                row=dict(round=number,task=task,condition=condition,target_mib=None if condition=='B' else int(target),
                    position=item['position'],key=key,source=value['source'],source_session=session,task_tool_sum_ns=value['task_tool_sum_ns'],
                    replay_with_checks_ns=value['replay_with_checks_ns'],guest_device_read_bytes=value['guest_device_read_bytes'],
                    process_cpu_seconds=value['baseline_process_cpu_seconds'] if condition=='B' else value['controller_process_cpu_seconds'],
                    cache=value['cache'] if condition=='B' else value['after_cache'],
                    identity_unknown_max_bytes=value['identity_unknown_max_bytes'],physical=value['physical'],
                    storage_budget=value['storage_budget'],official=value['official'],strict_replay_status=value['strict_replay_status'])
                if condition!='B':
                    row.update(comparison=value['comparison'],reclaim_write_total_ns=value['reclaim_write_total_ns'],
                               reclaim_calls_task=value['reclaim_calls_task'])
                rows.append(row)
    for row in rows:
        baseline=next(v for v in rows if v['task']==row['task'] and v['round']==row['round'] and v['condition']=='B')
        row['cross_pause_pair']=row['source_session']!=baseline['source_session']
    for task in TASKS:
        selected=[v for v in rows if v['task']==task]
        assert len({json.dumps(v['official'],sort_keys=True) for v in selected})==1
        by_condition={}
        for condition in 'BLH':
            group=[v for v in selected if v['condition']==condition]
            assert len(group)==6
            value=dict(tool_seconds=spread([v['task_tool_sum_ns']/1e9 for v in group]),
                cache_mean_mib=spread([v['cache']['mean_bytes']/2**20 for v in group]),
                cache_peak_mib=spread([v['cache']['peak_bytes']/2**20 for v in group]),
                process_cpu_seconds=spread([v['process_cpu_seconds'] for v in group]),
                guest_read_mib=spread([v['guest_device_read_bytes']/2**20 for v in group]),
                tool_seconds_by_position={str(p):[v['task_tool_sum_ns']/1e9 for v in group if v['position']==p] for p in [1,2,3]})
            if condition!='B':
                paired=paired_summary(group)
                value['paired_delta_percent']=paired['all_six']
                value['continuous_paired_delta_percent']=paired['continuous']
                value['continuous_rounds']=paired['continuous_rounds']
                value['cross_pause_pairs']=paired['cross_pause']
                value['max_excess_mib']=spread([v['cache']['max_excess_bytes']/2**20 for v in group])
                value['above_target_sample_fraction']=spread([v['cache']['above_target_sample_fraction'] for v in group])
            by_condition[condition]=value
        groups[task]=by_condition
    pause_summary=None if not pause else dict(paused_utc=pause['paused_utc'],resumed_utc=resume['resumed_utc'],
        gap_seconds=(datetime.fromisoformat(resume['resumed_utc'])-datetime.fromisoformat(pause['paused_utc'])).total_seconds(),
        completed_before_pause=pause['completed_runs'],cross_pause_keys=[v['key'] for v in rows if v['cross_pause_pair']])
    summary=dict(status='PASS',completed_runs=36,actual_actions=1260,rounds=6,source_service_sessions=identities,pause=pause_summary,
        physical_samples=sum(v['physical']['all_samples'] for v in rows),
        task_physical_samples=sum(v['physical']['task_samples'] for v in rows),groups=groups,runs=rows,
        low_vs_high_delta_percent={task:low_high_summary(rows,task) for task in TASKS},
        minimum_host_available_bytes=min(v['storage_budget']['minimum_available_bytes'] for v in rows),
        limitations=['Each target is paired only with the baseline from the same task and round; old pilot runs excluded.',
            'Six repeated observations per condition, not a population-wide stable slowdown guarantee.',
            'Execution order balanced; Xarray round 6 crosses the recorded overnight pause and is separate from its five continuous paired comparisons.',
            'Host global cache and background activity not reset or made identical.',
            'Task tool time includes effects of the controller and instrumentation; their costs are not isolated.',
            'Guest device reads include setup and monitoring, not host physical disk reads or exact tool-only I/O.',
            'Periodic target, not hard limit; after-sample excess fraction is not continuous duration.',
            'Physical scope is VMM RAM current corresponding pages only; changing/unknown identities remain separate.'])
    output.mkdir(); (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def plot(summary, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(10.5,7),sharex=True)
    for column,task in enumerate(TASKS):
        for row,(metric,label) in enumerate([('tool_time','Task tool time change (%)'),('guest_read','Guest device read change (%)')]):
            ax=axes[row,column]; ax.axhline(0,color='#90979d',linewidth=.8)
            for condition,color in [('L','#d6742b'),('H','#23619b')]:
                values=summary['groups'][task][condition]['paired_delta_percent'][metric]['values']
                crossed={v['round'] for v in summary['groups'][task][condition]['cross_pause_pairs']}
                ax.plot(range(1,7),[v if n not in crossed else float('nan') for n,v in enumerate(values,1)],'o-',color=color,label=TARGETS[task][condition]+' MiB')
                for n in crossed:
                    ax.plot(n,values[n-1],marker='D',markerfacecolor='none',markeredgecolor=color,linestyle='none',label='Across overnight pause' if condition=='L' else None)
            ax.set_ylabel(label); ax.spines[['top','right']].set_visible(False)
            ax.set_xticks(range(1,7)); ax.grid(axis='y',alpha=.15)
            if row==0: ax.set_title(task+' / same-round baseline'); ax.legend(frameon=False)
            else: ax.set_xlabel('Round')
    fig.tight_layout(); fig.savefig(output/'repeats.png',dpi=160); fig.savefig(output/'repeats.svg'); plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    summary=report(args.directory.resolve(),args.output.resolve()); plot(summary,args.output.resolve())
    print('status=PASS result_dir='+str(args.output.resolve()))
