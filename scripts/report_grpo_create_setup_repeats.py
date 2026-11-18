"""Three saved create-all-before-setup GRPO pairs; no service or guest operations."""
import argparse
import csv
from pathlib import Path
import re

from report_grpo_light_repeats import aggregate
from run_grpo_light_pair import ROOT,read,write,rows,interval_union

SCHEDULE='create-all-before-setup'
ORDERS=[['off','on'],['on','off'],['off','on']]
CODE=('run_grpo_light_pair.py','run_prettier6604_grpo.py','sandbox_runtime_monitor.py',
      'report_prettier6604_grpo.py','report_task_overview.py')


def control_breakdown(events):
    groups={}
    for event in events:
        label=re.sub(r'^\d+-','',event['stage'])
        if label.startswith('monitor-') and not label.startswith('monitor-collect-'):
            groups.setdefault(label,[]).append(event)
    return {key:dict(count=len(values),sum_ns=sum(e['duration_ns'] for e in values),
        union_ns=interval_union(values),max_ns=max(e['duration_ns'] for e in values)) for key,values in groups.items()}


def phases(task,events):
    created=task['all_created_monotonic_ns'];prepared=task['all_prepared_monotonic_ns']
    first=task['first_tool_start_monotonic_ns'];end=task['controller_end_monotonic_ns']
    last=max(e['start_monotonic_ns']+e['duration_ns'] for e in events if e['stage'].endswith('-controller-call'))
    values=dict(before_rollout=task['controller_elapsed_ns']-(end-task['rollout_start_monotonic_ns']),
        creation=created-task['rollout_start_monotonic_ns'],preparation=prepared-created,
        prepared_to_first_action=first-prepared,actions_and_downloads=last-first,
        final_checks=task['rollout_end_monotonic_ns']-last,
        after_checks_and_cleanup=end-task['rollout_end_monotonic_ns'])
    assert all(v>=0 for v in values.values()) and sum(values.values())==task['controller_elapsed_ns']
    assert task['created_to_controller_end_ns']==end-created and task['create_to_setup_ns']==prepared-created
    return values


def summarize(pair_dirs):
    assert len(pair_dirs)==len(set(pair_dirs))==3
    pairs=[];snapshot=None;identity=None;collector_code=None;guest_ids=[]
    for number,(directory,order) in enumerate(zip(pair_dirs,ORDERS),1):
        plan=read(directory/'plan.json');state=read(directory/'result.json')
        runs=read(directory/'runs.json');saved=read(directory/'comparison.json')
        assert plan['grpo_setup_schedule']==saved['grpo_setup_schedule']==SCHEDULE
        assert not plan.get('light_skip_boundary_vmstat',False) and not saved.get('light_skip_boundary_vmstat',False), 'these repeats retain boundary vmstat'
        assert state['status']=='PASS' and state['completed']==2 and state['source_returned_to_stopped']
        assert plan['order']==order==[r['mode'] for r in runs] and len(runs)==2
        assert plan['planned_actions']==378 and not plan['host_sampling'] and not plan['pss']
        code={name:(directory/'code'/name).read_bytes() for name in CODE}
        if snapshot is None:snapshot=code
        assert code==snapshot, 'pair source code differs'
        public=[]
        for run in runs:
            assert run['status']=='PASS' and run['actual_actions']==189 and run['grpo_setup_schedule']==SCHEDULE
            assert not run.get('light_skip_boundary_vmstat',False), 'these repeats retain boundary vmstat'
            current={k:run[k] for k in ('paths','patches','official')}
            if identity is None:identity=current
            assert current==identity, 'task paths, patches or official outcomes differ'
            raw=Path(run['raw']);task=read(raw/'result.json');events=read(raw/'events.json')
            assert task['status']=='PASS' and task['grpo_setup_schedule']==SCHEDULE
            assert not task.get('light_skip_boundary_vmstat',False), 'these repeats retain boundary vmstat'
            assert (raw/'code/run_prettier6604_grpo.py').read_bytes()==snapshot['run_prettier6604_grpo.py']
            assert task['controller_elapsed_ns']==run['timings_ns']['controller']
            for key in ('created_to_controller_end_ns','create_to_setup_ns'):assert task[key]==run['timings_ns'][key]
            guest_ids.extend(r['sandbox_id'] for r in rows(raw/'sandbox-ids.tsv'))
            row={k:run[k] for k in ('mode','actual_actions','official','sessions','timings_ns','minimum_free_disk_gib')}
            row['controller_phases_ns']=phases(task,events)
            row['grpo_setup_schedule']=SCHEDULE
            for key in ('workflow','storage','raw','report'):row[key]=str(Path(run[key]).relative_to(ROOT))
            if run['mode']=='on':
                assert len(run['sessions'])==7
                row['monitor_control_breakdown']=control_breakdown(events)
                assert sum(g['sum_ns'] for g in row['monitor_control_breakdown'].values())==run['timings_ns']['monitor_control_call_sum']
                actual={name:(raw/'runtime-code'/name).read_bytes() for name in ('sandbox_runtime_monitor.py','guest_memory_collector.py')}
                assert actual['sandbox_runtime_monitor.py']==snapshot['sandbox_runtime_monitor.py']
                if collector_code is None:collector_code=actual
                assert actual==collector_code, 'actual collector code differs'
                manifest=read(raw/'runtime-monitor.json')
                row['collector_one_core_cpu_percent']=[
                    (s['receipt']['cpu_user_seconds']+s['receipt']['cpu_system_seconds'])/(s['receipt']['elapsed_ns']/1e9)*100
                    for s in manifest['sessions']]
            else:assert not run['sessions']
            public.append(row)
        off=next(r for r in runs if r['mode']=='off');on=next(r for r in runs if r['mode']=='on')
        assert off['timings_ns'].keys()==on['timings_ns'].keys()
        comparison={k:dict(off_ns=off['timings_ns'][k],on_ns=on['timings_ns'][k],
            difference_ns=on['timings_ns'][k]-off['timings_ns'][k],
            change_percent=(on['timings_ns'][k]/off['timings_ns'][k]-1)*100 if off['timings_ns'][k] else None)
            for k in off['timings_ns']}
        assert {k:{a:b for a,b in v.items() if a!='difference_ns'} for k,v in comparison.items()}==saved['comparison']
        difference={k:next(r for r in public if r['mode']=='on')['controller_phases_ns'][k]-
            next(r for r in public if r['mode']=='off')['controller_phases_ns'][k] for k in public[0]['controller_phases_ns']}
        assert sum(difference.values())==comparison['controller']['difference_ns']
        pairs.append(dict(pair=number,directory=str(directory.relative_to(ROOT)),order=order,runs=public,
            comparison=comparison,controller_phase_difference_ns=difference))
    assert len(guest_ids)==len(set(guest_ids))==42, 'expected 42 distinct created guests'
    result=aggregate(pairs)
    result.update(status='PASS_GRPO_CREATE_SETUP_REPEATS',grpo_setup_schedule=SCHEDULE,guest_count=42,
        additional_actions_after_first_pair=756,additional_guests_after_first_pair=28,
        code_consistency='PASS_PAIR_AND_ACTUAL_EXECUTION_SNAPSHOTS',
        scope='Three independent create-all-before-setup GRPO pairs, guest-only .1 s vs off, default reclaim; old schedules excluded')
    return result


def plot(result,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    out.mkdir(parents=True,exist_ok=False)
    fields=[('controller','Controller total (s)'),('created_to_controller_end_ns','After all creates through cleanup (s)'),
        ('cold_request_span','Create request batch (s)')]
    fig,axes=plt.subplots(3,1,figsize=(10,8),sharex=True,constrained_layout=True)
    x=[1,2,3]
    for ax,(key,label) in zip(axes,fields):
        for mode,color,marker in [('off','#2563a6','o'),('on','#d76b1c','s')]:
            values=[p['comparison'][key][mode+'_ns']/1e9 for p in result['pairs']]
            ax.plot(x,values,color=color,marker=marker,label='Sampling '+mode,linewidth=1.5)
        ax.set_ylabel(label);ax.set_ylim(bottom=0);ax.grid(alpha=.25)
    axes[0].legend();axes[0].set_title('Prettier GRPO: create all 7 guests before setup, 3 paired runs')
    axes[-1].set_xticks(x);axes[-1].set_xlabel('Pair (off/on, on/off, off/on)')
    for suffix in ('png','svg'):fig.savefig(out/('paired-times.'+suffix),dpi=150)
    plt.close(fig)
    with (out/'timings.tsv').open('x') as stream:
        writer=csv.writer(stream,delimiter='\t')
        keys=['controller','created_to_controller_end_ns','cold_request_span','create_to_setup_ns','tool_sum','frozen_tools_and_downloads_span']
        writer.writerow(['pair','order','mode',*[k+'_seconds' for k in keys]])
        for pair in result['pairs']:
            for run in pair['runs']:writer.writerow([pair['pair'],'-'.join(pair['order']),run['mode'],*[run['timings_ns'][k]/1e9 for k in keys]])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pairs',type=Path,nargs=3,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--plot-output',type=Path,help='Optional new directory for paired timing plot and TSV')
    args=parser.parse_args()
    result=summarize([p.resolve() for p in args.pairs])
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'summary.json',result)
    if args.plot_output:plot(result,args.plot_output)
    print('status='+result['status']+' result_dir='+str(args.output.resolve()))


if __name__=='__main__':main()
