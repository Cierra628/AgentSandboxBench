"""Offline alignment of guest Cached and read-only DAMON counters."""
import argparse
import csv
import json
from pathlib import Path
import shutil

from sandbox_runtime_monitor import DAMON_FIELDS, PREFIX


def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def analyze(memory,damon):
    assert len(memory)==len(damon)>=2,'memory/DAMON rows differ or are missing'
    merged=[];updates=[];drops=[];resets=[]
    origin=int(memory[0]['monotonic_ns'])
    for left,right in zip(memory,damon):
        assert all(left[k]==right[k] for k in PREFIX),'memory/DAMON sample identities differ'
        tick=int(left['monotonic_ns']);begin=int(right['read_start_monotonic_ns']);end=int(right['read_end_monotonic_ns'])
        assert tick<=begin<=end
        values={k:int(right[k]) for k in DAMON_FIELDS[1:]}
        assert all(values[k]>=0 for k in DAMON_FIELDS[2:])
        assert right['enabled'] in ('Y','N')
        current=dict(elapsed_seconds=(tick-origin)/1e9,monotonic_ns=tick,
            cached_bytes=int(left['Cached_kib'])*1024,enabled=right['enabled'],**values,
            phase=left['phase'],round=left['round'],step=left['step'],read_duration_ns=end-begin)
        if merged:
            previous=merged[-1];assert current['monotonic_ns']>previous['monotonic_ns']
            interval=dict(start_seconds=previous['elapsed_seconds'],end_seconds=current['elapsed_seconds'])
            for k in DAMON_FIELDS[2:]:
                if current[k]<previous[k]:resets.append(dict(interval,counter=k,before=previous[k],after=current[k]))
            applied=current['bytes_reclaimed_regions']-previous['bytes_reclaimed_regions']
            if applied>0:updates.append(dict(interval,reclaimed_bytes=applied))
            delta=current['cached_bytes']-previous['cached_bytes']
            if delta<=-2**20:drops.append(dict(interval,cached_before_bytes=previous['cached_bytes'],
                cached_after_bytes=current['cached_bytes'],drop_bytes=-delta,phase=current['phase'],round=current['round'],step=current['step']))
        merged.append(current)
    summary=dict(status='PASS',sample_count=len(merged),elapsed_seconds=merged[-1]['elapsed_seconds'],
        enabled_states=sorted({r['enabled'] for r in merged}),kdamond_pids=sorted({r['kdamond_pid'] for r in merged}),
        counter_resets=resets,total_reclaimed_bytes=None if resets else merged[-1]['bytes_reclaimed_regions']-merged[0]['bytes_reclaimed_regions'],
        total_tried_bytes=None if resets else merged[-1]['bytes_reclaim_tried_regions']-merged[0]['bytes_reclaim_tried_regions'],
        counter_increases=updates,cached_drops_at_least_1mib=drops,
        max_memory_sample_gap_seconds=max((b['monotonic_ns']-a['monotonic_ns'])/1e9 for a,b in zip(merged,merged[1:])),
        max_damon_read_duration_ns=max(r['read_duration_ns'] for r in merged),
        notes=['Counter publication can lag reclaim; fields are sequential reads, not an atomic event stream.',
               'Cumulative successful reclaim is not current cache size; concurrent reads can refill cache.',
               'PASS means complete aligned data; it does not require a cache drop or reclaim.'])
    return summary,merged


def report(raw,output):
    task=json.loads((raw/'result.json').read_text())
    policy=task.get('damon_policy','default')
    assert task['status']=='PASS' and policy in ('default','disabled-during-task')
    manifest=json.loads((raw/'runtime-monitor.json').read_text())
    assert manifest['status']=='PASS' and manifest['damon_observed'] and manifest['profile']=='light'
    assert len(manifest['sessions'])==1
    session=manifest['sessions'][0];assert session['collector_reaped'] and session['receipt']['damon_observed']
    directory=raw/session['raw_directory'];assert directory.resolve().is_relative_to(raw.resolve())
    receipt=json.loads((directory/'collector-status.json').read_text());assert receipt==session['receipt']
    memory=rows(directory/'guest-memory.tsv');damon=rows(directory/'guest-damon.tsv')
    assert len(memory)==receipt['samples']
    summary,merged=analyze(memory,damon)
    summary.update(raw=str(raw),interval_seconds=receipt['interval_seconds'],
        settings=json.loads((directory/'damon-settings.json').read_text()),damon_policy=policy)
    if policy=='disabled-during-task':
        disabled=json.loads((raw/'damon-disabled.json').read_text())
        restored=json.loads((raw/'damon-restored.json').read_text())
        assert task['cleanup']['damon_original_setting_restored']
        assert summary['enabled_states']==['N'] and summary['kdamond_pids']==[-1]
        assert not summary['counter_resets'] and summary['total_reclaimed_bytes']==summary['total_tried_bytes']==0
        assert disabled['after']['enabled']==restored['before']['enabled']=='N'
        assert restored['original_enabled']==disabled['before']['enabled']==restored['after']['enabled']
        assert disabled['after']['bytes_reclaimed_regions']==restored['before']['bytes_reclaimed_regions']
        assert disabled['after']['monotonic_ns']<=merged[0]['monotonic_ns']
        assert int(damon[-1]['read_end_monotonic_ns'])<=restored['before']['monotonic_ns']
        summary.update(disabled=disabled,restored=restored)
    output.mkdir(parents=True,exist_ok=False)
    with (output/'damon-memory.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(merged[0]),delimiter='\t');writer.writeheader();writer.writerows(merged)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True,constrained_layout=True)
    times=[r['elapsed_seconds'] for r in merged]
    axes[0].plot(times,[r['cached_bytes']/2**20 for r in merged],marker='.',markersize=3)
    axes[0].set(title='Guest Cached',ylabel='MiB')
    axes[1].step(times,[r['bytes_reclaimed_regions']/2**20 for r in merged],where='post',color='C1')
    axes[1].set(title='DAMON kernel-reported cumulative successful reclaim',ylabel='MiB',xlabel='Monotonic seconds since first sample')
    for ax in axes:ax.grid(alpha=.2)
    fig.suptitle('Prettier: DAMON disabled during task' if policy=='disabled-during-task' else 'Prettier: default reclaim observed without changing policy')
    fig.savefig(output/'damon-cache.png',dpi=120);fig.savefig(output/'damon-cache.svg',metadata={'Date':None});plt.close(fig)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    shutil.copy2(Path(__file__),output/Path(__file__).name)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.raw.resolve()):parser.error('report must be outside raw')
    value=report(args.raw.resolve(),args.output.resolve())
    print('status='+value['status']+' samples='+str(value['sample_count'])+' result_dir='+str(args.output.resolve()))
