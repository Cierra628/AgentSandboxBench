"""Read saved density results and file metadata; never invoke a workload or query pages."""
import argparse
import csv
import json
from pathlib import Path
import re
from statistics import median
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[1]
MIB=2**20


def read(path):return json.loads(path.read_text())
def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))
def dump(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def table(path,values):
    with path.open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(values[0]),delimiter='\t');writer.writeheader();writer.writerows(values)


def replay_record(raw,role):
    raw=Path(raw).resolve()
    events=read(raw/'events.json');replay=next(e for e in events if e['stage']=='replay')
    finish=next(e for e in events if e['stage']=='monitor-finish')
    managed=next(e for e in events if e['stage'].startswith('monitor-collect-'))
    result=read(raw/'result.json');session=read(raw/'runtime-monitor.json')['sessions'][0]
    offset=session['start_host_timestamp_ns']-session['start_host_monotonic_ns']
    stdout=next(raw.glob('*-replay.stdout'));opened_ns=stdout.stat().st_mtime_ns-offset
    tools=rows(raw/'tools.tsv');span=int(tools[-1]['end_monotonic_ns'])-int(tools[0]['start_monotonic_ns'])
    end=replay['start_monotonic_ns']+replay['duration_ns']
    memory=rows(raw/session['raw_directory']/'guest-memory.tsv')
    # The sampler is ready before the tool call. All these guest times share its own clock.
    guest_prefix=int(tools[0]['start_monotonic_ns'])-int(memory[0]['monotonic_ns'])
    guest_tail=int(memory[-1]['monotonic_ns'])-int(tools[-1]['end_monotonic_ns'])
    tail_points=[r for r in memory if int(r['monotonic_ns'])>=int(tools[-1]['end_monotonic_ns'])]
    return dict(guest=role,source=str(raw.relative_to(ROOT)),host_call_start_ns=replay['start_monotonic_ns'],
        host_call_recorded_end_ns=end,replay_state_end_ns=result['replay_end_monotonic_ns'],stdout_mtime_host_ns=stdout.stat().st_mtime_ns,
        stdout_mtime_estimated_monotonic_ns=opened_ns,
        recorded_call_seconds=replay['duration_ns']/1e9,guest_tool_span_seconds=span/1e9,
        call_start_to_stdout_mtime_seconds=(opened_ns-replay['start_monotonic_ns'])/1e9,
        stdout_mtime_to_recorded_end_seconds=(end-opened_ns)/1e9,
        recorded_end_to_state_end_seconds=(result['replay_end_monotonic_ns']-end)/1e9,
        state_end_to_finish_call_seconds=(finish['start_monotonic_ns']-result['replay_end_monotonic_ns'])/1e9,
        managed_collector_call_seconds=managed['duration_ns']/1e9,
        guest_sampler_prefix_seconds=guest_prefix/1e9,guest_sampler_tail_seconds=guest_tail/1e9,
        guest_sampler_tail_points=len(tail_points),guest_sampler_tail_maximum_gap_seconds=max(((int(b['monotonic_ns'])-int(a['monotonic_ns']))/1e9 for a,b in zip(tail_points,tail_points[1:])),default=None),
        guest_sampler_cpu_seconds=session['receipt']['cpu_user_seconds']+session['receipt']['cpu_system_seconds'],
        first_tool_guest_ns=int(tools[0]['start_monotonic_ns']),last_tool_guest_ns=int(tools[-1]['end_monotonic_ns']))


def window_metrics(host,start,end):
    selected=[r for r in host if start<=int(r['monotonic_ns'])<=end]
    if not selected:raise ValueError('empty saved window')
    return dict(start_monotonic_ns=start,end_monotonic_ns=end,span_seconds=(end-start)/1e9,samples=len(selected),
        current_peak_mib=max(int(r['current']) for r in selected)/MIB,
        current_median_mib=median(int(r['current']) for r in selected)/MIB)


def kernel_evidence(out):
    release=Path('/proc/sys/kernel/osrelease').read_text().strip()
    source=(Path('/lib/modules')/release/'build').resolve()
    data=Path('/sys/kernel/notes').read_bytes();i=0;running=None
    while i+12<=len(data):
        namesz,descsz,kind=struct.unpack_from('<III',data,i);i+=12
        name=data[i:i+namesz].rstrip(b'\0');i+=(namesz+3)&~3
        desc=data[i:i+descsz];i+=(descsz+3)&~3
        if name==b'GNU' and kind==3:running=desc.hex()
    notes=subprocess.check_output(['readelf','-n',str(source/'vmlinux')],text=True,timeout=30)
    match=re.search(r'Build ID: ([0-9a-f]+)',notes)
    if not match or match[1]!=running:raise ValueError('running kernel and local vmlinux build IDs differ')
    snippets={
        'mm/memcontrol.c':[(533,650),(650,670),(1330,1345),(1461,1495),(1518,1532),(4006,4047),(4089,4155),(4229,4237)],
        'kernel/cgroup/rstat.c':[(386,434)],
    }
    paths=[]
    for relative,ranges in snippets.items():
        path=source/relative;lines=path.read_text().splitlines();name=relative.replace('/','-')+'.txt'
        with (out/name).open('x') as stream:
            stream.write(lines[0]+'\nSource: '+str(path)+'\nLocal build ID: '+running+'\n\n')
            for start,end in ranges:
                for number in range(start,end+1):stream.write(f'{number}: {lines[number-1]}\n')
                stream.write('\n')
        paths.append(dict(source=str(path),saved=name,source_mtime_ns=path.stat().st_mtime_ns))
    return dict(release=release,running_build_id=running,local_vmlinux_build_id=match[1],source_root=str(source),
        version=Path('/proc/version').read_text().strip(),snippets=paths,
        identity_scope='Running kernel build ID matches this build directory vmlinux; source files precede its build. No event-time trace of a specific rstat flush was collected.')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    batch=read(ROOT/'.artifacts/host-density-20261102/result.json');assert batch['status']=='PASS' and len(batch['runs'])==12
    p0_rows=[];c4={}
    for run in batch['runs']:
        member=Path(run['result_dir']);storage=Path(run['storage']);task=read(storage/'density/result.json')
        published=ROOT/'results/host-density-20261102'/f"{run['task']}-N{run['count']}-{run['mode']}"
        summary=read(published/'summary.json');host=rows(published/'host-memory.tsv');p0=task['P0']
        coherent=next((r for r in host if int(r['monotonic_ns'])>=p0['end_monotonic_ns'] and int(r['file'])<=int(r['current'])),None)
        near=[dict(seconds_from_P0_end=(int(r['monotonic_ns'])-p0['end_monotonic_ns'])/1e9,current_mib=int(r['current'])/MIB,file_mib=int(r['file'])/MIB) for r in host if p0['end_monotonic_ns']-300000000<=int(r['monotonic_ns'])<=p0['end_monotonic_ns']+2000000000]
        name=f"{run['task']}-N{run['count']}-{run['mode']}";table(out/(name+'-P0.tsv'),near)
        peak=summary['components_at_execution_current_peak_bytes'];residual=(sum(peak[k] for k in ('file','anon','kernel'))-summary['execution_current_peak_bytes'])/MIB
        p0_rows.append(dict(task=run['task'],N=run['count'],mode=run['mode'],current_mib=p0['counters_after']['current']/MIB,
            file_mib=p0['counters_after']['file']/MIB,file_exceeds_current=p0['counters_after']['file']>p0['counters_after']['current'],
            first_file_at_most_current_after_P0_seconds=(int(coherent['monotonic_ns'])-p0['end_monotonic_ns'])/1e9 if coherent else None,
            execution_peak_components_minus_current_mib=residual,source=str(member.relative_to(ROOT))))
        if run['task']!='c4':continue
        records=[replay_record(storage/'density'/child['raw'],child['role']) for child in task['results']]
        table(out/('c4-'+run['mode']+'-calls.tsv'),records)
        start,end=summary['execution_window_monotonic_ns'];cut=max(r['stdout_mtime_estimated_monotonic_ns'] for r in records)
        observed=window_metrics(host,start,end);sensitivity=window_metrics(host,start,cut);tail=window_metrics(host,cut,end)
        assert abs(observed['current_median_mib']-summary['execution_current_median_bytes']/MIB)<1e-6
        disk=[json.loads(line) for line in (storage/'disk-samples.jsonl').read_text().splitlines()]
        offsets=[r['timestamp_ns']-r['monotonic_ns'] for r in disk]
        durations={}
        for r in records:
            # Broad saved-host context over the local logging delay; no attribution to all host I/O.
            a=r['stdout_mtime_estimated_monotonic_ns'];b=r['replay_state_end_ns']
            context=[point for point in host if a<=int(point['monotonic_ns'])<=b]
            if context:
                durations[r['guest']]=dict(start_file_dirty_mib=int(context[0]['file_dirty'])/MIB,end_file_dirty_mib=int(context[-1]['file_dirty'])/MIB,
                    maximum_file_writeback_mib=max(int(v['file_writeback']) for v in context)/MIB)
        c4[run['mode']]=dict(records=records,original_window=observed,stdout_completion_bound_window=sensitivity,
            post_stdout_tail=tail,dirty_context=durations,
            host_wall_minus_monotonic_offset_range_ns=[min(offsets),max(offsets)],
            host_maximum_sampling_gap_seconds=max(int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in zip(host,host[1:]))/1e9,
            disk_maximum_sampling_gap_seconds=max(b['monotonic_ns']-a['monotonic_ns'] for a,b in zip(disk,disk[1:]))/1e9,
            source=str(storage.relative_to(ROOT)),
            sensitivity_definition='Separate sensitivity calculation ending at the latest saved stdout mtime, after each subprocess.run returned; host wall/monotonic offset from its session start. This is an approximate bound from filesystem timestamps, not a replacement of the original action+download window.')
    table(out/'P0-summary.tsv',p0_rows)
    kernel=kernel_evidence(out)
    shared=ROOT/c4['shared']['source']
    snippets=[('controller-timing-source.txt',shared/'code/run_external_workload.py',[(38,57),(103,112)]),
        ('host-counter-source.txt',ROOT/'scripts/host_file_monitor.py',[(40,47),(205,219)]),
        ('monitor-stop-source.txt',shared/'density/density-guest-02/runtime-code/sandbox_runtime_monitor.py',[(314,348)]),
        ('preservation-source.txt',shared/'code/validate_isolated_agentenv.py',[(1770,1788)])]
    for name,path,ranges in snippets:
        lines=path.read_text().splitlines()
        with (out/name).open('x') as stream:
            stream.write('Source: '+str(path.relative_to(ROOT))+'\n\n')
            for start,end in ranges:
                for number in range(start,end+1):stream.write(f'{number}: {lines[number-1]}\n')
                stream.write('\n')
    evidence=dict(status='PASS_OFFLINE_ANALYSIS',scope='Saved task logs, sample tables, source and stdout mtimes only; no new service, guest, page query, workload read or settings change',
        c4=c4,P0=p0_rows,kernel=kernel,source_excerpts=[dict(saved=n,source=str(p.relative_to(ROOT))) for n,p,_ in snippets],
        timing_definition='Saved external call duration runs until after stdout file write and lock acquisition; events.json write occurs after its end timestamp. It is not a pure RPC duration.')
    dump(out/'evidence.json',evidence)
    print('status=PASS_OFFLINE_ANALYSIS result_dir='+str(out))


if __name__=='__main__':main()
