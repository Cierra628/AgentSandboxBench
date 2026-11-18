"""Saved GRPO paths: causal exec/download matching, command totals and bounded gaps."""
import argparse
import csv
from pathlib import Path
import re

from run_grpo_light_pair import ROOT,read,write,rows


def matching(graph,locked=None):
    owners={};assignment={}
    if locked:
        row,col=locked
        if col not in graph[row]:return None
        owners[col]=row;assignment[row]=col
    def add(row,seen):
        for col in graph[row]:
            if col in seen or locked and col==locked[1]:continue
            seen.add(col)
            if col not in owners or add(owners[col],seen):
                owners[col]=row;assignment[row]=col;return True
        return False
    for row in sorted(graph,key=lambda key:len(graph[key])):
        if locked and row==locked[0]:continue
        if not add(row,set()):return None
    return assignment


def possible_matches(graph):
    assert matching(graph) is not None, 'no complete causal assignment'
    return {key:[i for i in choices if matching(graph,(key,i)) is not None] for key,choices in graph.items()}


def analyze_run(raw,mode,*,setup_schedule='create-all-before-setup',skip_boundary_vmstat=False):
    task=read(raw/'result.json');cfg=read(raw/'effective-config.json');events=read(raw/'events.json')
    assert setup_schedule in ('create-all-before-setup','per-guest-setup')
    assert task['status']=='PASS' and task['grpo_setup_schedule']==setup_schedule
    assert type(skip_boundary_vmstat) is bool
    if skip_boundary_vmstat:
        assert mode=='on' and task.get('light_skip_boundary_vmstat') is True
        assert cfg.get('light_skip_boundary_vmstat') is True
    calls=[e for e in events if e['stage'].endswith('-controller-call')]
    downloads={};execs={};sid_by_role={r['role']:r['sandbox_id'] for r in rows(raw/'sandbox-ids.tsv')}
    for event in calls:
        assert event['exit_code']==0
        content=(raw/(event['stage']+'.stdout')).read_text().strip()
        if not content:execs[event['index']]=event;continue
        m=re.fullmatch(r'Downloaded directory ([0-9a-f-]+):/workspace/artifacts/exp5/([^/]+)/([^/]+) to (.+)/rollouts/\2/\3',content)
        assert m, 'unrecognized controller output: '+event['stage']
        sid,role,segment=m.group(1,2,3)
        assert sid==sid_by_role[role] and (role,segment) not in downloads
        downloads[role,segment]=event
    paths=cfg['executed_paths'];count=sum(map(len,paths.values()))
    assert len(downloads)==len(execs)==count and len(calls)==2*count
    graph={}
    for role,path in paths.items():
        lower=task['role_first_segment_monotonic_ns'][role]
        for segment in path:
            d=downloads[role,segment]
            graph[role,segment]=[i for i,e in execs.items() if lower<=e['start_monotonic_ns'] and
                e['start_monotonic_ns']+e['duration_ns']<=d['start_monotonic_ns']]
            lower=d['start_monotonic_ns']+d['duration_ns']
    options=possible_matches(graph)
    origin=min(e['start_monotonic_ns'] for e in calls)
    end=max(d['start_monotonic_ns']+d['duration_ns'] for d in downloads.values())
    output=[]
    for role,path in paths.items():
        command=0;boundary_memory=0;boundary_vmstat=0;steps_count=0;exec_lo=0;exec_hi=0;download=0;segments=[]
        for segment in path:
            directory=raw/'rollouts'/role/segment;steps=rows(directory/'steps.tsv')
            assert all(r['branch']==role and r['round']==segment for r in steps)
            guest=sum(int(r['duration_ns']) for r in steps)
            mem=rows(directory/'guest-memory.tsv');vmstat=rows(directory/'guest-vmstat.tsv')
            expected=2+2*len(steps) if mode=='on' else 0
            assert len(mem)==expected and len(vmstat)==(0 if skip_boundary_vmstat else expected)
            durations=[execs[i]['duration_ns'] for i in options[role,segment]]
            lo,hi=min(durations),max(durations);d=downloads[role,segment]
            segments.append(dict(segment=segment,command_ns=guest,exec_min_ns=lo,exec_max_ns=hi,
                download_ns=d['duration_ns'],possible_exec_event_indices=options[role,segment],
                download_event_index=d['index']))
            command+=guest;steps_count+=len(steps);exec_lo+=lo;exec_hi+=hi;download+=d['duration_ns']
            boundary_memory+=len(mem);boundary_vmstat+=len(vmstat)
        assert command==task['paths'][role]['guest_action_sum_ns']
        last=downloads[role,path[-1]];start=task['role_first_segment_monotonic_ns'][role]
        finish=last['start_monotonic_ns']+last['duration_ns'];wall=finish-start
        gap_lo=wall-exec_hi-download;gap_hi=wall-exec_lo-download
        assert gap_lo>=0 and exec_lo>=command
        output.append(dict(role=role,steps=steps_count,segment_count=len(path),path_window_ns=wall,
            start_offset_from_original_window_ns=start-origin,finish_offset_ns=finish-origin,
            last_to_batch_end_ns=end-finish,command_ns=command,exec_min_ns=exec_lo,exec_max_ns=exec_hi,
            exec_minus_command_min_ns=exec_lo-command,exec_minus_command_max_ns=exec_hi-command,
            download_ns=download,gap_min_ns=gap_lo,gap_max_ns=gap_hi,
            exec_assignment='UNIQUE' if all(len(options[role,s])==1 for s in path) else 'BOUNDED',
            boundary_memory_rows=boundary_memory,boundary_vmstat_rows=boundary_vmstat,segments=segments))
    critical=max(output,key=lambda r:r['finish_offset_ns'])
    assert critical['path_window_ns']+critical['start_offset_from_original_window_ns']==end-origin
    unique=sum(len(ids)==1 for ids in options.values())
    return dict(mode=mode,raw=str(raw.relative_to(ROOT)),window_ns=end-origin,critical_role=critical['role'],
        critical_path_assignment=critical['exec_assignment'],exec_calls=count,unique_exec_assignments=unique,
        bounded_exec_assignments=count-unique,command_sum_ns=sum(r['command_ns'] for r in output),
        exec_sum_ns=sum(e['duration_ns'] for e in execs.values()),download_sum_ns=sum(d['duration_ns'] for d in downloads.values()),
        boundary_memory_rows=sum(r['boundary_memory_rows'] for r in output),
        boundary_vmstat_rows=sum(r['boundary_vmstat_rows'] for r in output),paths=output)


def summarize(summary,*,setup_schedule='create-all-before-setup'):
    pairs=[]
    for pair in summary['pairs']:
        runs=[analyze_run(ROOT/r['raw'],r['mode'],setup_schedule=setup_schedule) for r in pair['runs']]
        for r in runs:
            expected=next(v for v in pair['runs'] if v['mode']==r['mode'])['timings_ns']
            assert r['window_ns']==expected['frozen_tools_and_downloads_span'] and r['command_sum_ns']==expected['tool_sum']
        off=next(r for r in runs if r['mode']=='off');on=next(r for r in runs if r['mode']=='on')
        a=next(r for r in off['paths'] if r['role']==off['critical_role'])
        b=next(r for r in on['paths'] if r['role']==on['critical_role'])
        assert a['exec_assignment']==b['exec_assignment']=='UNIQUE', 'critical path needs a unique assignment'
        keys=('command_ns','exec_minus_command_min_ns','download_ns','gap_min_ns','start_offset_from_original_window_ns')
        delta={k:b[k]-a[k] for k in keys}
        assert sum(delta.values())==on['window_ns']-off['window_ns']
        pairs.append(dict(pair=pair['pair'],order=pair['order'],runs=runs,
            critical_comparison=dict(off_role=a['role'],on_role=b['role'],window_difference_ns=on['window_ns']-off['window_ns'],components_ns=delta)))
    return dict(status='PASS_GRPO_PATH_COST_OFFLINE',pairs=pairs,
        exec_calls=sum(r['exec_calls'] for p in pairs for r in p['runs']),
        unique_exec_assignments=sum(r['unique_exec_assignments'] for p in pairs for r in p['runs']),
        bounded_exec_assignments=sum(r['bounded_exec_assignments'] for p in pairs for r in p['runs']),
        definitions=dict(exec='Whole host aenv exec command; includes guest runner and commands, not pure API overhead',
            exec_minus_command='Exec duration minus saved guest command durations; includes boundary sampling, runner setup, API/client and scheduling',
            download='Whole host aenv download command, including packaging, transport, extraction and CLI work',
            gap='Path marker to final download minus serial exec/download; includes controller work and dispatch/recording delays',
            matching='One-to-one causal assignment: prior download end <= exec start; exec end <= own download start; no nearest-time rule',
            bounds='Per-path min/max sums enclose every feasible matching; dependent path bounds must not be summed',
            clock='Host event monotonic clock for windows; guest duration sums only, no host/guest absolute-clock subtraction'),
        new_live_guests=0,new_live_actions=0)


def paired_paths(result):
    output=[]
    for pair in result['pairs']:
        off=next(r for r in pair['runs'] if r['mode']=='off');on=next(r for r in pair['runs'] if r['mode']=='on')
        for a in off['paths']:
            b=next(r for r in on['paths'] if r['role']==a['role'])
            assert a['steps']==b['steps']
            output.append(dict(pair=pair['pair'],role=a['role'],steps=a['steps'],
                off_path_ns=a['path_window_ns'],on_path_ns=b['path_window_ns'],path_difference_ns=b['path_window_ns']-a['path_window_ns'],
                command_difference_ns=b['command_ns']-a['command_ns'],
                exec_residual_difference_min_ns=b['exec_minus_command_min_ns']-a['exec_minus_command_max_ns'],
                exec_residual_difference_max_ns=b['exec_minus_command_max_ns']-a['exec_minus_command_min_ns'],
                download_difference_ns=b['download_ns']-a['download_ns'],
                critical_off=a['role']==off['critical_role'],critical_on=b['role']==on['critical_role']))
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=summarize(read(args.summary))
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'summary.json',result)
    with (args.output/'paths.tsv').open('x') as stream:
        fields=['pair','mode','role','steps','path_window_ns','command_ns','exec_min_ns','exec_max_ns',
            'exec_minus_command_min_ns','exec_minus_command_max_ns','download_ns','gap_min_ns','gap_max_ns',
            'last_to_batch_end_ns','exec_assignment','boundary_memory_rows','boundary_vmstat_rows']
        writer=csv.DictWriter(stream,fieldnames=fields,delimiter='\t');writer.writeheader()
        for pair in result['pairs']:
            for run in pair['runs']:
                for path in run['paths']:
                    writer.writerow(dict(pair=pair['pair'],mode=run['mode'],**{key:path[key] for key in fields[2:]}))
    paired=paired_paths(result)
    with (args.output/'path-pairs.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(paired[0]),delimiter='\t');writer.writeheader();writer.writerows(paired)
    print('status='+result['status']+' result_dir='+str(args.output.resolve()))


if __name__=='__main__':main()
