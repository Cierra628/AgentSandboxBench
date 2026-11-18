"""Summarize the three agreed GRPO sampling pairs from saved results only."""
import argparse
from pathlib import Path
import statistics

from run_grpo_light_pair import ROOT,read,write


def summarize(pair_dirs, *, expected_pairs=3):
    assert expected_pairs>0 and len(pair_dirs)==len(set(pair_dirs))==expected_pairs
    expected_orders=[['off','on'] if i%2==0 else ['on','off'] for i in range(expected_pairs)]
    pairs=[];reference=None;source=None;snapshots=None
    for number,(directory,order) in enumerate(zip(pair_dirs,expected_orders),1):
        state=read(directory/'result.json');plan=read(directory/'plan.json');runs=read(directory/'runs.json')
        assert state['status']=='PASS' and state['source_returned_to_stopped'] and state['completed']==2
        assert plan['order']==order and [r['mode'] for r in runs]==order
        assert plan.get('grpo_setup_schedule','per-guest-setup')=='per-guest-setup', 'original repeat report requires the original setup schedule'
        assert not plan.get('light_skip_boundary_vmstat',False), 'original repeat report requires original boundary sampling'
        assert all(r.get('grpo_setup_schedule','per-guest-setup')=='per-guest-setup' for r in runs)
        assert not plan['host_sampling'] and not plan['pss'] and plan['planned_actions']==378
        saved_code={name:(directory/'code'/name).read_bytes() for name in (
            'run_prettier6604_grpo.py','sandbox_runtime_monitor.py','report_prettier6604_grpo.py','report_task_overview.py')}
        if snapshots is None:snapshots=saved_code
        assert saved_code==snapshots
        public=[]
        for run in runs:
            assert run['status']=='PASS' and run['actual_actions']==189
            identity={k:run[k] for k in ('paths','patches','official')}
            if reference is None:reference=identity
            assert reference==identity
            raw=Path(run['raw']);task=read(raw/'result.json');events=read(raw/'events.json')
            assert task.get('grpo_setup_schedule','per-guest-setup')=='per-guest-setup'
            assert not run.get('light_skip_boundary_vmstat',False) and not task.get('light_skip_boundary_vmstat',False)
            code=(raw/'code/run_prettier6604_grpo.py').read_bytes()
            if source is None:source=code
            assert code==source
            calls=[e for e in events if e['stage'].endswith('-controller-call')]
            end=max(e['start_monotonic_ns']+e['duration_ns'] for e in calls)
            phases=dict(create_and_prepare=task['first_tool_start_monotonic_ns']-task['rollout_start_monotonic_ns'],
                execution_and_download=end-task['first_tool_start_monotonic_ns'],
                post_path_checks=task['rollout_end_monotonic_ns']-end,cleanup=run['timings_ns']['cleanup'],
                other=task['controller_elapsed_ns']-run['timings_ns']['rollout']-run['timings_ns']['cleanup'])
            assert sum(phases.values())==run['timings_ns']['controller']==task['controller_elapsed_ns']
            row={k:run[k] for k in ('mode','actual_actions','official','sessions','timings_ns','minimum_free_disk_gib')}
            row.update(controller_phases_ns=phases)
            for key in ('workflow','storage','raw','report'):row[key]=str(Path(run[key]).relative_to(ROOT))
            if run['mode']=='on':
                monitor=read(raw/'runtime-monitor.json')
                row['collector_one_core_cpu_percent']=[
                    (s['receipt']['cpu_user_seconds']+s['receipt']['cpu_system_seconds'])/(s['receipt']['elapsed_ns']/1e9)*100
                    for s in monitor['sessions']]
            public.append(row)
        off=next(r for r in runs if r['mode']=='off');on=next(r for r in runs if r['mode']=='on')
        comparison={k:dict(off_ns=off['timings_ns'][k],on_ns=on['timings_ns'][k],
            difference_ns=on['timings_ns'][k]-off['timings_ns'][k],
            change_percent=(on['timings_ns'][k]/off['timings_ns'][k]-1)*100 if off['timings_ns'][k] else None)
            for k in off['timings_ns']}
        phases={k:next(r for r in public if r['mode']=='on')['controller_phases_ns'][k]-
            next(r for r in public if r['mode']=='off')['controller_phases_ns'][k] for k in public[0]['controller_phases_ns']}
        assert sum(phases.values())==comparison['controller']['difference_ns']
        pairs.append(dict(pair=number,directory=str(directory.relative_to(ROOT)),order=order,runs=public,
            comparison=comparison,controller_phase_difference_ns=phases))
    return aggregate(pairs)


def aggregate(pairs):
    stats={}
    for key in pairs[0]['comparison']:
        values=[p['comparison'][key] for p in pairs];changes=[v['change_percent'] for v in values]
        stats[key]=dict(off_median_ns=statistics.median(v['off_ns'] for v in values),
            on_median_ns=statistics.median(v['on_ns'] for v in values),
            paired_difference_median_ns=statistics.median(v['difference_ns'] for v in values),
            paired_change_median_percent=statistics.median(changes) if all(c is not None for c in changes) else None,
            paired_change_range_percent=[min(changes),max(changes)] if all(c is not None for c in changes) else None)
    count=len(pairs)
    return dict(status='PASS_GRPO_LIGHT_REPEATS',pair_count=count,run_count=2*count,actual_actions=378*count,
        sample_count=sum(s['samples'] for p in pairs for r in p['runs'] for s in r['sessions']),
        definition='Median and range of '+('three' if count==3 else str(count))+' signed (on/off-1)*100 paired changes; not a ratio of unpaired medians',
        scope='Guest-only seven-guest GRPO, .1 s sampling versus off, default reclaim, no host/PSS/physical queries',
        pairs=pairs,statistics=stats,new_live_actions=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs',type=Path,nargs=3,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=summarize([p.resolve() for p in args.pairs])
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'summary.json',result)
    print('status='+result['status']+' result_dir='+str(args.output.resolve()))


if __name__=='__main__':main()
