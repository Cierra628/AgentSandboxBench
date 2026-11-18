"""Recount two restored guests and deduplicate only overlapping sample windows."""
import argparse,csv,hashlib,json,statistics
from pathlib import Path
from report_live_task_cache import recount,physical_bytes,residency_gaps
from audit_lower_cache import check_point


def physical_set(directory,sample):
    """Use the independently reconstructed current set for the saved method."""
    _,physical=check_point(directory,sample)
    if 'host_four_segment_current_union_bytes' in sample:
        import struct
        from audit_provider_segments import independent_segments
        prefix=directory/f"pair-{sample['point']:04}"
        read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        rows=lambda suffix:list(struct.Struct('<4Q').iter_unpack(read(suffix)))
        provider=json.loads(read('-provider.json'))
        counted,physical=independent_segments(read('-before.bin'),read('-after.bin'),
            rows('-host.bin'),rows('-backend.bin'),rows('-lower.bin'),
            provider['first'],provider['last'],physical)
        assert all(sample[k]==v for k,v in counted.items()), 'independent provider recount differs'
    assert len(physical)*4096==physical_bytes(sample), 'physical set excludes a measured source'
    return physical


def report(source,output):
    source=source.resolve();output=output.resolve();assert not output.exists() and not output.is_relative_to(source)
    result=json.loads((source/'result.json').read_text());assert result['status']=='PASS' and all(result['cleanup'].values())
    assert result['task_id']=='snapshot-cache-pair' or result['task_id']=='prettier__prettier-6604' and result.get('cache_pair')
    sides={};sets={};costs={}
    for side in ['child-a','child-b']:
        directory=source/'live-cache'/side
        sides[side]=recount(directory);sets[side]={}
        for sample in sides[side]:
            sets[side][sample['point']]=physical_set(directory,sample)
        state=json.loads((directory/'result.json').read_text());assert state['status']=='PASS'
        guest=json.loads((directory/'guest-result.json').read_text())
        queries=[json.loads((d/'snapshot-query-result.json').read_text()) for d in [directory,directory/'lower-query']]
        assert all(q['returncode']==0 for q in queries)
        costs[side]=dict(samples=len(sides[side]),host_thread_cpu_ns=state['host_thread_total_cpu_ns'],
            guest_cpu_ns=guest['publication_cpu_ns'],tracer_cpu_ns=sum(q['tracer_cpu_ns'] for q in queries),
            missing_kernel_results=sum(x['missing_kernel_results'] for q in queries for x in q['queries']),
            host_pair_mean_ns=statistics.mean(s['host_elapsed_ns'] for s in sides[side]),
            host_pair_max_ns=max(s['host_elapsed_ns'] for s in sides[side]))
    paired=[];coverage={};used=set()
    for phase in result['phases']:
        count=0
        for a in sides['child-a']:
            start=a['timestamp_ns'];end=start+a['host_elapsed_ns']
            if not phase['start_ns']<=start<=end<=phase['end_ns']:continue
            choices=[]
            for b in sides['child-b']:
                bs=b['timestamp_ns'];be=bs+b['host_elapsed_ns']
                if b['point'] in used or not phase['start_ns']<=bs<=be<=phase['end_ns']:continue
                overlap=min(end,be)-max(start,bs)
                if overlap>0:choices.append((overlap,b))
            if not choices:continue
            overlap,b=max(choices,key=lambda p:p[0]);used.add(b['point'])
            left=sets['child-a'][a['point']];right=sets['child-b'][b['point']]
            paired.append(dict(phase=phase['name'],point_a=a['point'],point_b=b['point'],
                timestamp_ns=max(start,b['timestamp_ns']),overlap_ns=overlap,
                guest_a_cached_bytes=a['guest_cached_bytes'],guest_b_cached_bytes=b['guest_cached_bytes'],
                host_a_bytes=len(left)*4096,host_b_bytes=len(right)*4096,
                host_sum_bytes=(len(left)+len(right))*4096,host_union_bytes=len(left|right)*4096,
                shared_bytes=len(left&right)*4096,
                unknown_a_capacity_bytes=residency_gaps(a)['host_residency_unconfirmed_guest_capacity_bytes'],
                unknown_b_capacity_bytes=residency_gaps(b)['host_residency_unconfirmed_guest_capacity_bytes']))
            assert len(left|right)==len(left)+len(right)-len(left&right)
            count+=1
        coverage[phase['name']]=count
    assert paired
    status='PASS' if all(coverage.values()) and any(p['shared_bytes']>0 for p in paired) else 'PARTIAL'
    provider=all('host_four_segment_current_union_bytes' in p for group in sides.values() for p in group)
    assert provider or not any('host_four_segment_current_union_bytes' in p for group in sides.values() for p in group)
    assert not result.get('provider_cache') or provider, 'requested provider collection missing'
    def stats():
        return {str(p.relative_to(source)):[s.st_ino,s.st_size,s.st_mtime_ns]
            for p in sorted(source.rglob('*')) if p.is_file() for s in [p.stat()]}
    inventory=stats() if provider else {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.rglob('*')) if p.is_file()}
    summary=dict(status=status,task_checks=result['checks'],phase_pairs=coverage,pairs=paired,costs=costs,
        child_samples={k:len(v) for k,v in sides.items()},
        scope='Current VMM, direct memory device cache and raw memory-layer PFNs; old snapshot versions excluded; overlapping observation windows, not atomic snapshots.',
        limitations=['One common 11-step prefix and two original 10-step suffixes; not the full seven-path schedule.' if result.get('cache_pair') else 'Synthetic 16 MiB workload; not two complete task trajectories.',
                     'Provider temporary/anonymous buffers and compressed/multi-layer snapshots are not attributed.',
                     'Guest cache and host PFNs change during collection; unknown capacity is not physical RAM.'],
        **({'raw_stat':inventory} if provider else {'raw_sha256':inventory}))
    output.mkdir(parents=True)
    if provider:
        from provider_residency_report import build_breakdown,write_breakdown
        summary['scope']='Current VMM, direct device, single raw memory layer and attributed provider PFN union; old versions excluded; overlapping windows, not atomic snapshots.'
        summary['limitations'][1]='Provider unknown resident bytes and boundary pages remain separate; compressed/multiple raw layers not attributed.'
        summary['provider_buffers']=build_breakdown([source/'live-cache'/s for s in sides],list(sides.values()),list(sides))
        assert all(s['status']=='PASS' for s in summary['provider_buffers']['sessions'].values())
        write_breakdown(summary['provider_buffers'],output)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (output/'pairs.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(paired[0]));w.writeheader();w.writerows(paired)
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='snapshot-pair-20261015'
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(10,8),sharex=True)
    for side,color in [('child-a','C0'),('child-b','C1')]:
        samples=sides[side];times=[(p['timestamp_ns']-paired[0]['timestamp_ns'])/1e9 for p in samples]
        axes[0].plot(times,[p['guest_cached_bytes']/2**20 for p in samples],color=color,label=side+' guest Cached')
        axes[0].plot(times,[physical_bytes(p)/2**20 for p in samples],color=color,ls='--',label=side+' host physical union')
    t=[(p['timestamp_ns']-paired[0]['timestamp_ns'])/1e9 for p in paired]
    for field,label in [('host_sum_bytes','Sum before cross-guest deduplication'),('host_union_bytes','Cross-guest PFN union'),('shared_bytes','Shared physical bytes')]:
        axes[1].plot(t,[p[field]/2**20 for p in paired],marker='o',ls='none',label=label)
    for field,label in [('unknown_a_capacity_bytes','child-a unknown capacity'),('unknown_b_capacity_bytes','child-b unknown capacity')]:
        axes[2].plot(t,[p[field]/2**20 for p in paired],marker='o',ls='none',label=label)
    for phase in result['phases']:
        for ax in axes:
            x=(phase['start_ns']-paired[0]['timestamp_ns'])/1e9
            ax.axvline(x,color='grey',alpha=.4,ls=':')
        axes[0].text(x,.98,phase['name'],transform=axes[0].get_xaxis_transform(),va='top',fontsize=8)
    for ax in axes:ax.set(ylabel='MiB');ax.grid(alpha=.2);ax.legend(fontsize=8)
    axes[0].set_title('Two restored Prettier branches: original frozen suffixes, no sampler pause' if result.get('cache_pair') else 'Two restored guests: shared reads and divergent writes, no sampler pause')
    axes[2].set_xlabel('Host elapsed time (s)')
    fig.tight_layout();fig.savefig(output/'pair.svg',metadata={'Date':None});fig.savefig(output/'pair.png');plt.close(fig)
    if provider:assert stats()==inventory, 'input metadata changed during reporting'
    else:assert all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in inventory.items())
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=report(a.source,a.output)
    print('status='+r['status']+' pairs='+str(len(r['pairs']))+' result_dir='+str(a.output))
    raise SystemExit(0 if r['status']=='PASS' else 1)
