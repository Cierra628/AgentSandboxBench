"""Publish completed density members and curves, without final batch acceptance or fits."""
import argparse
import csv
import json
from pathlib import Path
import shutil
from statistics import median

ROOT=Path(__file__).resolve().parents[1]
MIB=2**20


def read(path):return json.loads(path.read_text())
def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))
def write(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
def table(path,value):
    with path.open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(value[0]),delimiter='\t');writer.writeheader();writer.writerows(value)


def memory_metrics(data,start,end):
    selected=[r for r in data if start<=int(r['monotonic_ns'])<=end]
    if not selected:raise ValueError('no current samples in the execution window')
    peak=max(selected,key=lambda r:int(r['current']))
    return dict(current_peak_bytes=max(int(r['current']) for r in data),execution_current_peak_bytes=int(peak['current']),
        execution_current_median_bytes=median(int(r['current']) for r in selected),execution_samples=len(selected),
        components_at_execution_current_peak_bytes={k:int(peak[k]) for k in ('file','anon','kernel','shmem')})


def collect_member(member,dest):
    state=read(member/'result.json');assert state['status']=='PASS'
    storage=Path(state['storage']);native=read(storage/'result.json');task=read(storage/'density/result.json');req=state['request']
    assert native['status']==task['status']=='PASS' and task['P0']['resident_bytes']==0
    assert task['identity']['status']=='PASS' and task['copy_independence']['status']=='PASS'
    c4=req['task']=='c4';actions=36 if c4 else 29
    host=rows(member/'host-file/memory.tsv');calls=[];tools=[];guest_memory=[]
    for i,child in enumerate(task['results']):
        raw=storage/'density'/child['raw'];events=read(raw/'events.json')
        selected=[e for e in events if e['stage'] in ('replay','download-tools')] if c4 else [e for e in events if e['stage'].startswith('controller-call-')]
        assert len(selected)==(2 if c4 else 10) and all(e['exit_code']==0 for e in selected);calls+=selected
        segments={'original-tools':raw/'tools.tsv'} if c4 else {segment:raw/'rollouts/backbone'/segment/'steps.tsv' for segment in ('read_repo','read_typescript','core_patch','core_verify','backbone_finish')}
        for segment,path in segments.items():
            for row in rows(path):
                tools.append(dict(guest_index=i,segment=segment,step=row['step'],duration_ns=int(row['duration_ns']),exit_code=int(row['exit_code'])))
        monitor=read(raw/'runtime-monitor.json');session=monitor['sessions'][0]
        memory=rows(raw/session['raw_directory']/'guest-memory.tsv')
        assert len(memory)==session['receipt']['samples'] and session['receipt']['interval_seconds']==.1
        guest_memory.append((i,memory))
    assert len(tools)==actions*req['count']
    start=min(e['start_monotonic_ns'] for e in calls);end=max(e['start_monotonic_ns']+e['duration_ns'] for e in calls)
    metrics=memory_metrics(host,start,end);disk=[json.loads(x) for x in (storage/'disk-samples.jsonl').read_text().splitlines()]
    creations=list(task['guests'].values())
    result=dict(status='PASS_MEMBER_ONLY',task=req['task'],count=req['count'],mode=req['mode'],actions=len(tools),
        source_member=str(member.relative_to(ROOT)),source_storage=str(storage.relative_to(ROOT)),**metrics,
        execution_window_monotonic_ns=[start,end],cold_baseline=task['P0']['counters_after'],
        p0_counter_flags={position:dict(file_exceeds_current=task['P0'][position]['file']>task['P0'][position]['current']) for position in ('counters_before','counters_after')},
        p0_resident_bytes=task['P0']['resident_bytes'],p1_resident_bytes=task['P1']['resident_bytes'],
        residency_queries={p:{k:task[p][k] for k in ('start_monotonic_ns','end_monotonic_ns','elapsed_ns','cpu_ns','resident_bytes','counters_before','counters_after')} for p in ('P0','P1')},
        selected_file_count=len(task['P1']['objects']),selected_file_shortfall_lower_bound_bytes=max(0,task['P1']['resident_bytes']-max(task['P1']['counters_before']['file'],task['P1']['counters_after']['file'])),
        accounting_scope='Group input import, copy preparation, control, private service, loop backing caches and guests; not guest-exclusive RAM',
        timings_ns=dict(tool_sum=sum(t['duration_ns'] for t in tools),actions_and_downloads=end-start,
            creation_request_span=max(r['end_monotonic_ns'] for r in creations)-min(r['start_monotonic_ns'] for r in creations),controller=task['controller_elapsed_ns']),
        volume_capacity_bytes=native['capacity_bytes'],volume_minimum_available_bytes=min(r['volume_available_bytes'] for r in disk),host_minimum_available_bytes=min(r['host_available_bytes'] for r in disk),
        identity='PASS',copy_independence='PASS',official='Every guest retained 36 expected exits, 9898-file roundtrip and syntax check' if c4 else 'Every guest retained 0 passed / 2 failed',collector=state['collector'])
    if c4 and any('call_timing' in child['result'] for child in task['results']):
        result['external_call_timing']=[dict(guest=child['role'],timing=child['result'].get('call_timing')) for child in task['results']]
    if 'pressure_context' in req:
        result.update(condition=req['condition'],pressure_enabled=req['pressure_enabled'],
            before_pressure=task['pressure_before_guests']['before'],after_pressure=task['pressure_before_guests']['after'])
    dest.mkdir(parents=True,exist_ok=False)
    shutil.copy2(member/'host-file/memory.tsv',dest/'host-memory.tsv');table(dest/'tools.tsv',tools)
    normalized=[]
    for i,points in guest_memory:
        for row in points:
            normalized.append(dict(guest_index=i,monotonic_ns=int(row['monotonic_ns']),elapsed_ns=int(row['elapsed_ns']),
                guest_used_bytes=(int(row['MemTotal_kib'])-int(row['MemAvailable_kib']))*1024,cached_bytes=int(row['Cached_kib'])*1024,shmem_bytes=int(row['Shmem_kib'])*1024))
    table(dest/'guest-memory.tsv',normalized)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    t0=int(host[0]['monotonic_ns']);xs=[(int(r['monotonic_ns'])-t0)/1e9 for r in host]
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,constrained_layout=True)
    axes[0].plot(xs,[int(r['current'])/MIB for r in host],label='memory.current')
    axes[0].axhline(result['cold_baseline']['current']/MIB,linestyle='--',color='gray',label='After P0, before guests')
    axes[0].set_ylabel('Group charge memory.current (MiB)')
    for key in ('file','anon','kernel','shmem'):axes[1].plot(xs,[int(r[key])/MIB for r in host],label='memory.stat '+key)
    axes[1].set_ylabel('memory.stat components (MiB)');axes[1].set_xlabel('Host monotonic elapsed seconds')
    for ax in axes:
        ax.axvspan((start-t0)/1e9,(end-t0)/1e9,alpha=.1,color='green',label='Original actions + downloads');ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle(f"{'C4' if c4 else 'Prettier'} identical backbone: N={req['count']}, {req['mode']}; group includes preparation")
    fig.savefig(dest/'host.png',dpi=130);plt.close(fig)
    fig,axes=plt.subplots(req['count'],2,figsize=(12,max(3,2.5*req['count'])),squeeze=False,sharey='col',constrained_layout=True)
    for i,points in guest_memory:
        x=[int(r['elapsed_ns'])/1e9 for r in points]
        axes[i,0].plot(x,[(int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 for r in points]);axes[i,0].set_ylabel('MemTotal -\nMemAvailable (MiB)',fontsize=9)
        axes[i,1].plot(x,[int(r['Cached_kib'])/1024 for r in points]);axes[i,1].set_ylabel('Guest Cached (MiB)')
        for ax in axes[i]:ax.set_title(f'Guest {i}: same {actions}-action backbone');ax.grid(alpha=.2)
    for ax in axes[-1]:ax.set_xlabel('Guest session monotonic elapsed seconds')
    fig.savefig(dest/'guest.png',dpi=130);plt.close(fig)
    fig,ax=plt.subplots(figsize=(11,4),constrained_layout=True)
    for i in range(req['count']):
        selected=[t for t in tools if t['guest_index']==i];ax.plot(range(1,actions+1),[t['duration_ns']/1e9 for t in selected],marker='.',label=f'Guest {i}')
    ax.set(xlabel='Fixed backbone action index',ylabel='Tool duration (seconds)',title=f'{actions} identical actions per guest; cumulative durations are not wall time');ax.legend(ncol=5,fontsize=8);ax.grid(alpha=.2)
    fig.savefig(dest/'tools.png',dpi=130);plt.close(fig)
    write(dest/'summary.json',result)
    for p in dest.iterdir():p.chmod(0o644)
    dest.chmod(0o755)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    batch=read(a.source/'result.json');a.output.mkdir(parents=True,exist_ok=True);a.output.chmod(0o755)
    for run in batch['runs']:
        prefix=run['condition']+'-' if 'condition' in run else ''
        dest=a.output/(prefix+f"{run['task']}-N{run['count']}-{run['mode']}")
        if dest.exists():
            assert read(dest/'summary.json')['source_member']==str(Path(run['result_dir']).relative_to(ROOT))
            assert all((dest/name).is_file() for name in ('host-memory.tsv','guest-memory.tsv','tools.tsv','host.png','guest.png','tools.png'))
            continue
        result=collect_member(Path(run['result_dir']),dest)
        print(f"status={result['status']} result_dir={dest}")
    print(f"status=PARTIAL_COLLECTION completed={len(batch['runs'])}/{len(batch.get('plan',range(12)))}; no final fit or batch acceptance")


if __name__=='__main__':main()
