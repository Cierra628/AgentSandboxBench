"""Offline whole-guest logical cache and confirmed host residency report; no live access."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from kernel_cache_metadata import join

ROOT=Path(__file__).resolve().parents[1]


def meminfo(text):
    return {p[0].rstrip(':'):int(p[1])*1024 for line in text.splitlines() if len(p:=line.split())==3 and p[2]=='kB'}


def report(source,output):
    source,output=source.resolve(),output.resolve()
    assert source.is_relative_to(ROOT/'.artifacts') and output.is_relative_to(ROOT/'.artifacts')
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    result=json.loads((source/'result.json').read_text());assert result['status']=='PASS'
    points=[]
    for original in result['pairs']:
        n=original['point'];prefix=f'pair-{n:02}'
        left=(source/(prefix+'-before.bin')).read_bytes();right=(source/(prefix+'-after.bin')).read_bytes()
        host=json.loads((source/(prefix+'-host.json')).read_text())
        recomputed=join(left,right,host)
        assert all(original[k]==v for k,v in recomputed.items()), 'saved pair differs from raw metadata/PFN recount'
        before=json.loads((source/(prefix+'-before.json')).read_text())
        after=json.loads((source/(prefix+'-after.json')).read_text())
        m=meminfo(after['meminfo_after'])
        points.append(dict(original,guest_cached_bytes=m['Cached'],guest_shmem_bytes=m['Shmem'],
            guest_buffers_bytes=m['Buffers'],tagged_minus_cached_bytes=original['guest_cache_after_bytes']-m['Cached'],
            unconfirmed_guest_page_capacity_bytes=original['unconfirmed_after_pages']*4096,
            sampling_meminfo_cached_before_bytes=meminfo(before['meminfo_before'])['Cached']))
    assert len(points)==6 and result['guest_heartbeats'][-1]>result['guest_heartbeats'][0]
    summary=dict(status='PASS',source=str(source.relative_to(ROOT)),kernel=result['kernel'],scope=result['scope'],
        vm_pause=False,pairs=points,known_types=result['known_types'],guest_heartbeats=result['guest_heartbeats'],
        metadata_static_bytes=result['metadata_static_bytes'],limitations=result['limitations'],
        bitmap_lock_max_ns=max(p['metadata_after']['bitmap_lock_ns'] for p in points),
        proc_read_mean_ns=statistics.mean(v for p in points for v in [p['guest_proc_read_before_ns'],p['guest_proc_read_after_ns']]),
        proc_read_max_ns=max(v for p in points for v in [p['guest_proc_read_before_ns'],p['guest_proc_read_after_ns']]),
        metrics=dict(guest_cached='whole guest /proc/meminfo Cached, bytes; includes shmem, excludes Buffers/SwapCached',
            guest_tagged='all tracked ordinary/shmem cache PFNs at metadata snapshot; bytes',
            host_confirmed='distinct resident host PFNs for unchanged cache lifetime tags across the read interval; bytes',
            unconfirmed='remaining guest cache page capacity; not actual measured host RAM',
            counter_difference='tagged count minus Cached at nearby times; includes interval changes and per-CPU counter batching'),
        raw_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir() if p.is_file() and p.name!='verification.json'})
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='asb-whole-cache-v1'
    import matplotlib.pyplot as plt
    t=[(p['timestamp_ns']-points[0]['timestamp_ns'])/1e9 for p in points]
    fig,axes=plt.subplots(2,1,figsize=(9,6),sharex=True)
    axes[0].plot(t,[p['guest_cached_bytes']/2**20 for p in points],'o-',label='Guest Cached')
    axes[0].plot(t,[p['host_confirmed_cache_bytes']/2**20 for p in points],'s--',label='Confirmed host physical residency')
    axes[0].set(title='Whole guest page cache: live membership tags, no VM pause',ylabel='Cache (MiB)')
    axes[0].legend()
    axes[1].bar(t,[p['unconfirmed_guest_page_capacity_bytes']/2**20 for p in points],width=.25,label='Unconfirmed guest-page capacity (not host RAM)')
    axes[1].set(xlabel='Host elapsed time (s)',ylabel='Unconfirmed capacity (MiB)');axes[1].legend()
    for ax in axes:ax.grid(alpha=.25)
    fig.tight_layout();fig.savefig(output/'cache.svg',metadata={'Date':None});fig.savefig(output/'cache.png');plt.close(fig)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();s=report(args.source,args.output)
    print(json.dumps(dict(status=s['status'],pairs=len(s['pairs']),output=str(args.output))))
