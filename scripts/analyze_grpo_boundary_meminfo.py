"""Decompose saved separate/merged boundary-meminfo controls; no live operations."""
import argparse
from pathlib import Path

from analyze_grpo_path_costs import ROOT,read,write,rows,analyze_run
from analyze_grpo_local_no_proxy import controller_phases,clipped_union,label,table
from analyze_grpo_boundary_vmstat import COMPONENTS,PHASES
from report_grpo_create_setup_repeats import control_breakdown
from run_grpo_light_pair import interval_union
from run_grpo_local_no_proxy_controls import comparisons


def critical_difference(separate,merged):
    a=next(p for p in separate['paths'] if p['role']==separate['critical_role'])
    b=next(p for p in merged['paths'] if p['role']==merged['critical_role'])
    assert a['exec_assignment']==b['exec_assignment']=='UNIQUE'
    delta={k:b[k]-a[k] for k in COMPONENTS}
    assert sum(delta.values())==merged['window_ns']-separate['window_ns']
    return dict(separate_role=a['role'],merged_role=b['role'],components_ns=delta,
        window_difference_ns=merged['window_ns']-separate['window_ns'])


def analyze(source):
    assert source['status']=='PASS' and source['stage']=='complete'
    assert source['comparison']=='boundary-meminfo' and source['source_restored_stopped']
    assert not source['remaining_firecrackers'] and not source['remaining_running_containers']
    assert comparisons(source['runs'],comparison='boundary-meminfo')==source['pairs']
    assert len({g for r in source['runs'] for g in r['guest_ids']})==28
    runs=[]
    for saved in source['runs']:
        raw=ROOT/saved['raw'];task=read(raw/'result.json');cfg=read(raw/'effective-config.json');events=read(raw/'events.json')
        variant=saved['boundary_meminfo_mode'];merged=variant=='merged'
        assert task.get('light_merge_boundary_meminfo',False)==cfg.get('light_merge_boundary_meminfo',False)==merged
        value=analyze_run(raw,'on',setup_schedule='per-guest-setup',skip_boundary_vmstat=True)
        assert value['window_ns']==saved['timings_ns']['frozen_tools_and_downloads_span']
        assert value['command_sum_ns']==saved['timings_ns']['tool_sum']
        assert value['exec_calls']==value['unique_exec_assignments']==35
        assert value['boundary_memory_rows']==448 and value['boundary_vmstat_rows']==0
        phases=controller_phases(task,events);bounds=phases['boundaries']
        assert phases['controller_ns']==saved['timings_ns']['controller']
        for k,t in (('actions_and_downloads','frozen_tools_and_downloads_span'),('cold_span','cold_request_span'),('cleanup','cleanup')):
            assert phases['groups_ns'][k]==saved['timings_ns'][t]
        controls=[e for e in events if label(e).startswith('monitor-') and not label(e).startswith('monitor-collect-')]
        assert len(controls)==35
        costs=control_breakdown(events)
        assert sum(c['sum_ns'] for c in costs.values())==saved['timings_ns']['monitor_control_call_sum']
        assert interval_union(controls)==saved['timings_ns']['monitor_control_union']
        windows=dict(before_actions=(bounds['observed_start'],bounds['action_start']),actions=(bounds['action_start'],bounds['action_end']),
            checks=(bounds['action_end'],task['rollout_end_monotonic_ns']),after_checks=(task['rollout_end_monotonic_ns'],bounds['observed_end']))
        overlap={k:clipped_union(controls,*v) for k,v in windows.items()}
        assert sum(overlap.values())==interval_union(controls)
        value.update(index=saved['index'],pair=saved['pair'],boundary_meminfo_mode=variant,controller=phases,
            management=costs,management_union_by_phase_ns=overlap)
        runs.append(value)
    pairs=[];path_pairs=[];segments=[];commands=[]
    for number in (1,2):
        a=next(r for r in runs if r['pair']==number and r['boundary_meminfo_mode']=='separate')
        b=next(r for r in runs if r['pair']==number and r['boundary_meminfo_mode']=='merged')
        critical=critical_difference(a,b)
        delta={k:b['controller']['groups_ns'][k]-a['controller']['groups_ns'][k] for k in PHASES}
        assert sum(delta.values())==b['controller']['controller_ns']-a['controller']['controller_ns']
        pairs.append(dict(pair=number,order=[r['boundary_meminfo_mode'] for r in runs if r['pair']==number],
            critical_comparison=critical,controller_phase_difference_ns=delta))
        for x in a['paths']:
            y=next(p for p in b['paths'] if p['role']==x['role'])
            assert x['steps']==y['steps'] and x['exec_assignment']==y['exec_assignment']=='UNIQUE'
            path_pairs.append(dict(pair=number,role=x['role'],steps=x['steps'],
                separate_path_ns=x['path_window_ns'],merged_path_ns=y['path_window_ns'],
                path_difference_ns=y['path_window_ns']-x['path_window_ns'],
                **{k.replace('_ns','_difference_ns'):y[k]-x[k] for k in COMPONENTS}))
        assert critical['separate_role']==critical['merged_role'], 'per-command comparison needs the same critical role'
        role=critical['separate_role'];x=next(p for p in a['paths'] if p['role']==role);y=next(p for p in b['paths'] if p['role']==role)
        for u,v in zip(x['segments'],y['segments'],strict=True):
            assert u['segment']==v['segment'];segment=u['segment']
            segments.append(dict(pair=number,role=role,segment=segment,command_difference_ns=v['command_ns']-u['command_ns'],
                exec_residual_difference_ns=(v['exec_min_ns']-v['command_ns'])-(u['exec_min_ns']-u['command_ns']),
                download_difference_ns=v['download_ns']-u['download_ns']))
            left=ROOT/a['raw']/'rollouts'/role/segment;right=ROOT/b['raw']/'rollouts'/role/segment
            actions=rows(left/'actions.tsv');assert actions==rows(right/'actions.tsv')
            texts={c['step']:c['command'] for c in actions}
            xs=rows(left/'steps.tsv');ys=rows(right/'steps.tsv');assert len(texts)==len(actions)==len(xs)
            for c,d in zip(xs,ys,strict=True):
                assert {k:z for k,z in c.items() if k!='duration_ns'}=={k:z for k,z in d.items() if k!='duration_ns'}
                commands.append(dict(pair=number,role=role,segment=segment,step=c['step'],command=texts[c['step']],exit_code=c['exit_code'],
                    separate_ns=int(c['duration_ns']),merged_ns=int(d['duration_ns']),difference_ns=int(d['duration_ns'])-int(c['duration_ns'])))
        assert sum(c['difference_ns'] for c in commands if c['pair']==number)==critical['components_ns']['command_ns']
        for k,c in (('command_difference_ns','command_ns'),('exec_residual_difference_ns','exec_minus_command_min_ns'),('download_difference_ns','download_ns')):
            assert sum(s[k] for s in segments if s['pair']==number)==critical['components_ns'][c]
    return dict(status='PASS_BOUNDARY_MEMINFO_OFFLINE_BREAKDOWN',comparison='boundary-meminfo',runs=runs,pairs=pairs,
        path_pairs=path_pairs,critical_segments=segments,critical_commands=commands,
        exec_calls=sum(r['exec_calls'] for r in runs),unique_exec_assignments=sum(r['unique_exec_assignments'] for r in runs),
        downloads=sum(r['exec_calls'] for r in runs),new_live_guests=0,new_live_actions=0,
        residual_scope='Whole exec minus guest command durations: boundary reads, runner work, API/client and scheduling; not individually timed',
        clock_scope='Host monotonic event windows and guest duration sums; no cross-clock absolute subtraction')


def publish(source,out):
    assert not out.exists(), 'output must be new'
    value=analyze(read(source));out.mkdir(parents=True,exist_ok=False)
    value['source']=str(source);write(out/'summary.json',value)
    table(out/'controller-phases.tsv',[dict(index=r['index'],pair=r['pair'],boundary_meminfo_mode=r['boundary_meminfo_mode'],
        **{k+'_ns':v for k,v in r['controller']['groups_ns'].items()},controller_ns=r['controller']['controller_ns']) for r in value['runs']])
    table(out/'phase-pairs.tsv',[dict(pair=p['pair'],**{k+'_ns':v for k,v in p['controller_phase_difference_ns'].items()}) for p in value['pairs']])
    table(out/'paths.tsv',[dict(index=r['index'],pair=r['pair'],boundary_meminfo_mode=r['boundary_meminfo_mode'],
        **{k:v for k,v in p.items() if k!='segments'}) for r in value['runs'] for p in r['paths']])
    for name,key in (('path-pairs','path_pairs'),('critical-segments','critical_segments'),('critical-commands','critical_commands')):
        table(out/(name+'.tsv'),value[key])
    table(out/'management.tsv',[dict(index=r['index'],boundary_meminfo_mode=r['boundary_meminfo_mode'],kind=k,**c,
        **{phase+'_union_ns':n for phase,n in r['management_union_by_phase_ns'].items()}) for r in value['runs'] for k,c in r['management'].items()])
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=publish(args.source,args.output)
    print('status='+value['status']+' result_dir='+str(args.output))


if __name__=='__main__':main()
