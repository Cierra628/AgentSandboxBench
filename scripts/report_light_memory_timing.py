"""Read saved cgroup series; retain actual read windows, never interpolate PSS."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics


def stats(values):
    values=sorted(values)
    if not values:return dict(n=0,median=None,p95=None,maximum=None)
    return dict(n=len(values),median=statistics.median(values),p95=values[math.ceil(.95*len(values))-1],maximum=max(values))


def extract(row,parent,origin):
    if row['experiment_memory']['parent']!=parent:raise ValueError('parent scope changed')
    values=row['groups'][parent]
    reads=row.get('cgroup_diagnostics',{}).get('counter_reads',[])
    timing={}
    for key in ('stat','current'):
        found=[r for r in reads if r['path']==parent+'/memory.'+key and r['status']=='READ']
        if len(found)!=1:raise ValueError('missing or duplicate parent read interval: '+key)
        r=found[0];left=r['start_monotonic_ns'];right=r['end_monotonic_ns']
        if not row['monotonic_ns']<=left<=right<=row['monotonic_ns']+row['duration_ns']:
            raise ValueError('parent read outside sample window')
        timing[key]=(left,right)
    for key in ('current','file','anon','kernel','shmem'):
        if type(values.get(key)) is not int or values[key]<0:raise ValueError('invalid counter '+key)
    left=min(x[0] for x in timing.values());right=max(x[1] for x in timing.values())
    return dict(t_seconds=(left-origin)/1e9,parent_read_start_ns=left,parent_read_end_ns=right,
        stat_start_ns=timing['stat'][0],stat_end_ns=timing['stat'][1],
        current_start_ns=timing['current'][0],current_end_ns=timing['current'][1],
        parent_window_ms=(right-left)/1e6,all_groups_window_ms=row['duration_ns']/1e6,
        **{key+'_mib':values[key]/2**20 for key in ('current','file','anon','kernel','shmem')},
        file_minus_shmem_mib=(values['file']-values['shmem'])/2**20,
        closure_diagnostic=row['experiment_memory']['file_closure']['status'])


def report(source,out):
    saved=json.loads((source/'result.json').read_text());parent=saved['parent']
    raw=Path(saved['storage'])/'guest-cache'
    series=source/'full-memory/memory.jsonl'
    records=[json.loads(line) for line in series.read_text().splitlines()]
    origin=records[0]['monotonic_ns'];rows=[extract(r,parent,origin) for r in records]
    intervals=[(b['monotonic_ns']-a['monotonic_ns'])/1e6 for a,b in zip(records,records[1:])]
    if any(x<=0 for x in intervals):raise ValueError('non-increasing sample clock')
    out.mkdir(parents=True,exist_ok=False)
    def save_csv(name,values):
        with (out/name).open('x') as f:
            w=csv.DictWriter(f,fieldnames=list(values[0]));w.writeheader();w.writerows(values)
    save_csv('parent-curve.csv',rows)
    events=json.loads((raw/'events.json').read_text())
    markers=[]
    for event in events:
        t=event['start_ns'];before=[r for r in rows if r['parent_read_end_ns']<=t];after=[r for r in rows if r['parent_read_start_ns']>=t]
        markers.append(dict(stage=event['stage'],start_seconds=(t-origin)/1e9,duration_seconds=event['duration_ns']/1e9,
            preceding_sample_end_ns=before[-1]['parent_read_end_ns'] if before else None,
            preceding_distance_ms=(t-before[-1]['parent_read_end_ns'])/1e6 if before else None,
            following_sample_start_ns=after[0]['parent_read_start_ns'] if after else None,
            following_distance_ms=(after[0]['parent_read_start_ns']-t)/1e6 if after else None))
    save_csv('events.csv',markers)
    phases=json.loads((raw/'result.json').read_text())['phases']
    pss=[dict(phase=p['name'],pss_mib=p['vmm']['pss_kib']/1024,
        scan_ms=p['vmm']['pss_scan_duration_ns']/1e6,exact_read_start_ns=None,exact_read_end_ns=None,
        alignment='UNAVAILABLE: phase start/end precede PSS read; do not use as PSS timestamps') for p in phases]
    save_csv('pss-existing.csv',pss)
    summary=dict(source=str(source),parent=parent,points=len(rows),span_seconds=(records[-1]['monotonic_ns']-origin)/1e9,
        parent_window_ms=stats([r['parent_window_ms'] for r in rows]),
        all_groups_window_ms=stats([r['all_groups_window_ms'] for r in rows]),interval_ms=stats(intervals),
        total_sampler_cpu_seconds=sum(r['cpu_ns'] for r in records)/1e9,
        intervals_over_150ms=sum(x>150 for x in intervals),
        pss_scan_ms=stats([r['scan_ms'] for r in pss]),pss_aligned=False,
        file_minus_shmem_negative_points=sum(r['file_minus_shmem_mib']<0 for r in rows),
        new_measurement=False,includes_preparation_and_observer=True,
        meaning='Actual slice charged memory, not all physical pages used by sandboxes; no interpolation or shared-page allocation')
    with (out/'summary.json').open('x') as f:json.dump(summary,f,indent=2);f.write('\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();print(json.dumps(report(args.source,args.output),indent=2))
