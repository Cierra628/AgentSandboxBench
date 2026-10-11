"""Default per-sandbox logical memory curves; historical segment data remain supported."""
import csv
import hashlib
import json
from pathlib import Path


def rows(path):
    with path.open() as stream: return list(csv.DictReader(stream, delimiter='\t'))


def samples(raw):
    raw = Path(raw); groups = {}; lifecycle = raw/'runtime-monitor.json'
    if lifecycle.exists():
        manifest = json.loads(lifecycle.read_text())
        assert manifest['status'] == 'PASS', 'continuous monitor incomplete'
        for session in manifest['sessions']:
            if manifest.get('collector_lifecycle_version')==2:
                assert session.get('collector_reaped') is True,'managed collector was not reaped before closing its session'
            directory = raw/session['raw_directory']
            assert directory.resolve().is_relative_to((raw/'runtime').resolve())
            receipt = json.loads((directory/'collector-status.json').read_text())
            data = rows(directory/'guest-memory.tsv')
            assert receipt == session['receipt'] and receipt['status'] == 'PASS'
            assert len(data) == receipt['samples'] == len(rows(directory/'guest-vmstat.tsv')) and len(data) >= 2
            sid = session['sandbox_id']; token = session['session_id']
            assert all(r['sandbox_id'] == sid and r['session_id'] == token and r['role'] == session['role'] for r in data)
            assert all(int(b['monotonic_ns']) > int(a['monotonic_ns']) for a,b in zip(data,data[1:]))
            assert not receipt.get('error')
            groups.setdefault(sid, dict(role=session['role'], chunks=[], sessions=[]))['chunks'].append(data)
            groups[sid]['sessions'].append(session)
        if (raw/'sandbox-ids.tsv').exists():
            assert set(groups) == {r['sandbox_id'] for r in rows(raw/'sandbox-ids.tsv')}, 'some sandbox monitoring is missing'
        return groups, 'continuous while running; explicit gaps around snapshots and before tool readiness'
    for segment in rows(raw/'segments.tsv'):
        sid = segment['sandbox_id']; role = segment['rollout']
        data = rows(raw/'rollouts'/role/segment['segment']/'guest-memory.tsv')
        groups.setdefault(sid, dict(role=role, chunks=[], sessions=[]))['chunks'].append(data)
    return groups, 'historical tool segments only; not continuous lifecycle monitoring'


def curve_chunks(data):
    # Guest wall clocks may jump. Never bridge recorded gaps or clock reversals.
    result = []; current = []
    for row in data:
        if current:
            delta = int(row['timestamp_ns'])-int(current[-1]['timestamp_ns'])
            mono = int(row.get('monotonic_ns',row['timestamp_ns']))-int(current[-1].get('monotonic_ns',current[-1]['timestamp_ns']))
            if delta <= 0 or delta > 500_000_000 or mono > 500_000_000:
                result.append(current); current = []
        current.append(row)
    if current: result.append(current)
    return result


def write_report(raw, output, host=None):
    raw = Path(raw); output = Path(output)
    assert not output.resolve().is_relative_to(raw.resolve()), 'report must be outside raw'
    def hashes():
        result={}
        for p in sorted(raw.rglob('*')):
            if p.is_file():
                with p.open('rb') as stream:result[str(p.relative_to(raw))]=hashlib.file_digest(stream,'sha256').hexdigest()
        return result
    before=hashes()
    source_result=json.loads((raw/'result.json').read_text()) if (raw/'result.json').exists() else {}
    groups, scope = samples(raw)
    assert groups and all(any(g['chunks']) for g in groups.values())
    summary = dict(status='PASS', scope=scope, units='raw meminfo KiB; charts MiB (2^20 bytes)', per_sandbox=[],
                   source_run_status=source_result.get('status','UNAVAILABLE'),
                   timing=dict(unit='ns',controller_elapsed_ns=source_result.get('controller_elapsed_ns'),
                       guest_action_sum_ns=source_result.get('guest_action_sum_ns',source_result.get('replay',source_result.get('prefix',{})).get('guest_action_sum_ns')),
                       guest_action_sum_per_role_ns={k:v['guest_action_sum_ns'] for k,v in source_result.get('paths',{}).items() if 'guest_action_sum_ns' in v},
                       stages=json.loads((raw/'events.json').read_text()) if (raw/'events.json').exists() else [],
                       scope='existing controller events; stage and concurrent call sums are not end-to-end elapsed time; missing values stay null'),
                   physical_page_cache='NOT_MEASURED',
                   notes=['Cached includes overlapping Shmem; guest values are not cross-VM physical deduplication.',
                          'Guest and host metrics are separate; per-VM peaks are not summed.',
                          'Recorded guest wall clocks are not independently calibrated across VMs.',
                          'Sampling maxima are observed peaks; missing intervals are not interpolated.',
                          'Collector CPU/maxrss describe collection cost only, not sandbox CPU or private memory.'])
    for sid,g in groups.items():
        data = [r for chunk in g['chunks'] for r in chunk]
        peak = max(data,key=lambda r:int(r['MemTotal_kib'])-int(r['MemFree_kib']))
        used = (int(peak['MemTotal_kib'])-int(peak['MemFree_kib']))*1024
        parts = {k:int(peak[k+'_kib'])*1024 for k in ('Cached','AnonPages','Buffers','Shmem','Slab') if k+'_kib' in peak}
        summary['per_sandbox'].append(dict(sandbox_id=sid,role=g['role'],sample_count=len(data),
            observed_nonfree_peak_bytes=used,components_at_peak_bytes=parts,
            components_at_peak_percent={k:100*v/used if used else None for k,v in parts.items()},
            cached_min_bytes=min(int(r['Cached_kib'])*1024 for r in data),
            cached_observed_peak_bytes=max(int(r['Cached_kib'])*1024 for r in data),
            slab_status='MEASURED' if 'Slab_kib' in peak else 'NOT_MEASURED',
            sessions=[dict(stop_reason=s['stop_reason'],**s['receipt']) for s in g['sessions']]))
    if host is None:
        path=raw/'host/memory-samples.tsv'; host=rows(path) if path.exists() else []
    import matplotlib
    matplotlib.use('Agg'); matplotlib.rcParams['svg.hashsalt']='sandbox-runtime-v1'
    import matplotlib.pyplot as plt
    origin=min(int(r['timestamp_ns']) for g in groups.values() for c in g['chunks'] for r in c)
    fig,axes=plt.subplots(3 if host else 2,1,figsize=(12,9 if host else 6),constrained_layout=True)
    for i,(sid,g) in enumerate(groups.items()):
        first=True
        for data in g['chunks']:
            for chunk in curve_chunks(data):
                label=g['role']+' / '+sid[-8:] if first else None;first=False
                t=[(int(r['timestamp_ns'])-origin)/1e9 for r in chunk]
                axes[0].plot(t,[int(r['Cached_kib'])/1024 for r in chunk],color='C'+str(i%10),label=label)
                axes[1].plot(t,[(int(r['MemTotal_kib'])-int(r['MemFree_kib']))/1024 for r in chunk],color='C'+str(i%10),label=label)
    axes[0].set_title('Per-sandbox page cache: guest Cached (logical counter)')
    axes[1].set_title('Per-sandbox nonfree memory: MemTotal - MemFree')
    if host:
        for key in ('current','anon','file','kernel'):
            axes[2].plot([(int(r['timestamp_ns'])-origin)/1e9 for r in host],[int(r['cgroup_'+key+'_bytes'])/2**20 for r in host],label=key)
        axes[2].set_title('Host service cgroup aggregate; not per-sandbox guest memory')
    for ax in axes:
        ax.set(xlabel='Recorded wall-clock seconds since first guest sample',ylabel='MiB');ax.grid(alpha=.25);ax.legend(fontsize=8,ncol=2)
    fig.suptitle(scope,fontsize=9);fig.savefig(output/'memory.svg',metadata={'Date':None});fig.savefig(output/'memory.png',dpi=120);plt.close(fig)
    for sid,g in groups.items():
        fig,ax=plt.subplots(figsize=(11,4),constrained_layout=True)
        for key in ('Cached','AnonPages','Buffers','Shmem','Slab','Dirty'):
            first=True
            for data in g['chunks']:
                if not data or key+'_kib' not in data[0]:continue
                for chunk in curve_chunks(data):
                    ax.plot([(int(r['timestamp_ns'])-origin)/1e9 for r in chunk],[int(r[key+'_kib'])/1024 for r in chunk],
                            label=key if first else None,color='C'+str(('Cached','AnonPages','Buffers','Shmem','Slab','Dirty').index(key)));first=False
        ax.set(title=g['role']+' / '+sid,xlabel='Recorded wall-clock seconds',ylabel='Guest logical memory (MiB)');ax.legend();ax.grid(alpha=.25)
        fig.savefig(output/('guest-'+sid+'.svg'),metadata={'Date':None});plt.close(fig)
    from report_sandbox_performance import write_report as performance_report
    summary['performance']=performance_report(raw,groups,output,origin)
    assert hashes()==before, 'raw inputs changed while generating curves'
    summary.update(raw_hashes_unchanged=True,raw_file_count=len(before))
    (output/'input-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    (output/'runtime-metrics.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    assert not args.output.resolve().is_relative_to(args.raw.resolve())
    args.output.mkdir(parents=True,exist_ok=False)
    value=write_report(args.raw,args.output)
    print('status='+value['status']+' source_run_status='+value['source_run_status']+' result_dir='+str(args.output.resolve()))
