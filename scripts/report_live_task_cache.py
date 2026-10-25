"""Recount paired cache raw data during a frozen task; no live guest access."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import numpy as np

from kernel_cache_metadata import join
from live_task_cache import HOST_ROW

ROOT = Path(__file__).resolve().parents[1]
TASKS = {'prettier__prettier-6604': ('Prettier #6604',29),
         'sphinx-doc__sphinx-7590': ('Sphinx #7590',35),
         'pydata__xarray-6992': ('Xarray #6992',35)}


def recount(directory):
    samples = []
    for saved in map(json.loads, (directory/'samples.jsonl').read_text().splitlines()):
        prefix = directory/f"pair-{saved['point']:04}"
        rows = [dict(guest_pfn=p, first=a, middle=b, last=c)
                for p, a, b, c in HOST_ROW.iter_unpack(Path(str(prefix)+'-host.bin').read_bytes())]
        measured = join(Path(str(prefix)+'-before.bin').read_bytes(), Path(str(prefix)+'-after.bin').read_bytes(), rows)
        assert all(saved[k] == v for k, v in measured.items()), 'saved sample differs from raw recount'
        assert saved['unconfirmed_guest_page_capacity_bytes'] == measured['unconfirmed_after_pages']*4096
        backend_path=Path(str(prefix)+'-backend.bin')
        if backend_path.exists():
            from fast_cache_metadata import decode_array
            from snapshot_cache_residency import SnapshotCache, account
            before=decode_array(Path(str(prefix)+'-before.bin').read_bytes())
            after=decode_array(Path(str(prefix)+'-after.bin').read_bytes())
            host=np.fromfile(Path(str(prefix)+'-host.bin'),dtype='<u8').reshape(-1,4)
            backing=np.fromfile(backend_path,dtype='<u8').reshape(-1,4)
            layout=json.loads((directory/'ram-attestation.json').read_text())
            mapper=SnapshotCache.__new__(SnapshotCache)
            mapper.slots=layout['slots']
            mapper.regions=json.loads(Path(str(prefix)+'-maps.json').read_text())['before']
            assert np.array_equal(mapper.offsets(backing[:,0]),backing[:,1]), 'backend offsets differ from attested layout'
            counted=account(before,after,host,backing)
            assert all(saved[k]==v for k,v in counted.items()), 'backend raw recount differs'
        if Path(str(prefix)+'-lower.bin').exists():
            from audit_lower_cache import check_point
            check_point(directory,saved)
        if Path(str(prefix)+'-provider.json').exists():
            from provider_slot_cache import recount_point
            recount_point(directory,saved)
        samples.append(saved)
    assert [p['point'] for p in samples] == list(range(len(samples))) and len(samples) >= 2
    return samples



def residency_gaps(sample):
    """A missing host PTE does not prove the backing file has no resident page."""
    return dict(mapping_absent_guest_capacity_bytes=sample['host_nonresident_pages']*4096,
                swapped_mapping_guest_capacity_bytes=sample['host_swapped_pages']*4096,
                host_residency_unconfirmed_guest_capacity_bytes=sample.get('host_four_segment_unconfirmed_guest_capacity_bytes',sample.get('host_four_unconfirmed_guest_capacity_bytes',sample.get('host_three_unconfirmed_guest_capacity_bytes',sample.get('host_current_unconfirmed_guest_capacity_bytes',
                    (sample['unconfirmed_after_pages']+sample['host_nonresident_pages']+sample['host_swapped_pages'])*4096)))))


def physical_bytes(sample):
    return sample.get('host_four_segment_current_union_bytes',sample.get('host_four_current_union_bytes',sample.get('host_three_current_union_bytes',sample.get('host_current_union_bytes',sample['host_confirmed_cache_bytes']))))


def raw_inventory(source, samples, reuse):
    if reuse is not None:
        previous=json.loads(reuse.read_text())
        assert previous['source']==str(source.relative_to(ROOT)), 'inventory belongs to another source'
        assert previous['measurements']==samples, 'previous measurements differ from current recount'
        names={str(p.relative_to(source)) for p in (source/'live-cache').rglob('*') if p.is_file()}
        assert names==set(previous['raw_sha256']), 'recorded inventory file names differ'
        return previous['raw_sha256']
    return {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source/'live-cache').rglob('*')) if p.is_file()}


def report(source, output, *, reuse_raw_inventory=None):
    source, output = source.resolve(), output.resolve()
    assert source.is_relative_to(ROOT/'.artifacts') and output.is_relative_to(ROOT/'.artifacts')
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    result = json.loads((source/'result.json').read_text())
    metadata = json.loads((source/'live-cache/result.json').read_text())
    assert result['status'] == metadata['status'] == 'PASS'
    calibration=result['task_id']=='snapshot-cache-calibration'
    title,action_count=('16 MiB restore calibration',0) if calibration else TASKS[result['task_id']]
    replay=result.get('replay',result)
    assert replay.get('action_count',0) == action_count and result['cleanup']['live_cache_stopped']
    sessions = metadata.get('sessions', ['.'])
    groups = [recount(source/'live-cache'/name) for name in sessions]
    groups = [[dict(sample, **residency_gaps(sample)) for sample in group] for group in groups]
    samples = [sample for group in groups for sample in group]
    segmented=any('host_four_segment_current_union_bytes' in p for p in samples)
    assert len(samples) == metadata['samples']
    guest_results = [json.loads((source/'live-cache'/name/'guest-result.json').read_text()) for name in sessions]
    guest_result = guest_results[0] if len(sessions)==1 else dict(zip(sessions, guest_results))
    if len(sessions)>1:
        from live_task_cache import session_index
        states=[json.loads((source/'live-cache'/name/'result.json').read_text()) for name in sessions]
        assert session_index(states,metadata['gap'])==metadata
        if any('host_three_current_union_bytes' in p for p in samples):
            provider=any('host_four_current_union_bytes' in p for p in samples)
            metadata=dict(metadata,collector_version='snapshot-provider-segment-pfn-union-v6' if segmented else 'snapshot-provider-slot-pfn-union-v5' if provider else 'snapshot-raw-layer-pfn-union-v4',
                session_collector_versions={name:state['collector_version'] for name,state in zip(sessions,states)},
                scope='whole guest ordinary/shmem cache; original provider records plus stable per-sector attribution; current PFN union and old versions separate' if segmented else 'whole guest ordinary/shmem cache; current VMM, direct File-backend, raw memory-layer and attributed provider PFNs; unknown capacity and old versions separate' if provider else 'whole guest ordinary/shmem cache; current VMM, direct File-backend and raw memory-layer PFNs; unknown capacity and old versions separate')
    events = json.loads((source/'events.json').read_text())
    summary = dict(status='PASS', source=str(source.relative_to(ROOT)), vm_pause=False,
        task_id=result['task_id'], action_count=action_count, task_checks=result['checks'],
        strict_replay_status=result.get('strict_replay_status','PASS'),
        official_task_status=result.get('official_task_status','NOT_APPLICABLE'), final_patch_sha256=replay.get('final_patch_sha256'),
        timing=dict(guest_action_sum_ns=replay.get('guest_action_sum_ns'),
                    frozen_replay_elapsed_ns=result.get('frozen_replay_elapsed_ns'),
                    controller_elapsed_ns=result.get('controller_elapsed_ns'),
                    stages_ns={e['stage']:e['duration_ns'] for e in events
                               if e['stage'].startswith('segment-') or e['stage'] in
                               ('cold-start','checkpoint','restore','delete-original','restore-ready','official-task-tests','benchmark-pytest','live-cache-upload','live-cache-start',
                                'live-cache-ready','live-cache-bootstrap','live-cache-done','live-cache-result')}),
        sampling=metadata, guest_exporter=guest_result, sample_count=len(samples),
        observation_seconds=(samples[-1]['timestamp_ns']-samples[0]['timestamp_ns'])/1e9,
        observed_guest_cached_peak_bytes=max(p['guest_cached_bytes'] for p in samples),
        observed_host_confirmed_peak_bytes=max(physical_bytes(p) for p in samples),
        unconfirmed_capacity_max_bytes=max(p['host_residency_unconfirmed_guest_capacity_bytes'] for p in samples),
        changed_or_new_capacity_max_bytes=max(p['unconfirmed_guest_page_capacity_bytes'] for p in samples),
        mapping_absent_capacity_max_bytes=max(p['mapping_absent_guest_capacity_bytes'] for p in samples),
        host_residency_scope='VMM, direct backend, raw layer and union of original/segment provider attribution; legacy metrics retained, old versions and unknown buffer pages separate.' if segmented else 'VMM, direct backend, raw layer and valid attributed provider slots; old versions and unattributed allocation pages separate.' if any('host_four_current_union_bytes' in p for p in samples) else 'VMM, direct File-backend and raw memory-layer current PFN union; old versions separate.' if any('host_three_current_union_bytes' in p for p in samples) else 'VMM PFNs union corresponding current snapshot-cache PFNs; COW old versions separate.' if any('host_current_union_bytes' in p for p in samples) else 'Confirmed resident PFNs in VMM RAM mappings only; absent host PTEs do not establish zero backing-file cache.',
        session_metrics={name:dict(samples=len(group), first_sample_ns=group[0]['timestamp_ns'],
            last_sample_ns=group[-1]['timestamp_ns'],
            guest_cached_range_bytes=[min(p['guest_cached_bytes'] for p in group),max(p['guest_cached_bytes'] for p in group)],
            host_confirmed_range_bytes=[min(physical_bytes(p) for p in group),max(physical_bytes(p) for p in group)],
            unconfirmed_guest_capacity_max_bytes=max(p['host_residency_unconfirmed_guest_capacity_bytes'] for p in group),
            mapping_absent_capacity_max_bytes=max(p['mapping_absent_guest_capacity_bytes'] for p in group))
            for name,group in zip(sessions,groups)},
        tagged_minus_cached_range_bytes=[min(p['guest_cache_after_bytes']-p['guest_cached_bytes'] for p in samples),
                                        max(p['guest_cache_after_bytes']-p['guest_cached_bytes'] for p in samples)],
        host_pair_elapsed_mean_ns=statistics.mean(p['host_elapsed_ns'] for p in samples),
        host_pair_elapsed_max_ns=max(p['host_elapsed_ns'] for p in samples),
        max_sample_gap_ns=max(b['timestamp_ns']-a['timestamp_ns'] for a,b in zip(samples,samples[1:])),
        metadata_counters={k:max(p[side][k] for p in samples for side in ['metadata_before','metadata_after'])
                           for k in ['duplicate_adds','missing_removes','out_of_range','moved_pages','stale_records','bitmap_lock_ns']},
        measurements=samples,
        limitations=['Diagnostic guest kernel/config differs from the original baseline.',
                     'Confirmed residency is sampled over an interval, not an atomic instantaneous total.',
                     'Unconfirmed guest-page capacity includes changing, absent and swapped host mappings; it is not measured host RAM.',
                     'Backend PFNs are included only when queried and stable; missing or changed results stay unconfirmed.',
                     'Collector anonymous memory is locked; task file pages are not locked or prefetched.',
                     'No monitoring-off comparison in this run; local collector costs are not task slowdown.'],
        raw_sha256=raw_inventory(source,samples,reuse_raw_inventory))
    if reuse_raw_inventory is not None:
        summary['raw_inventory_reused_from']=str(reuse_raw_inventory)
        summary['raw_inventory_check']='Recorded digests reused, not recomputed; file names and recounted measurements checked.'
    if segmented:
        from provider_residency_report import build_breakdown
        summary['provider_buffer_breakdown']=build_breakdown(
            [source/'live-cache'/name for name in sessions],groups,sessions)
        summary['provider_buffer_breakdown']['files']=dict(table='provider-buffer.csv',
            curve='provider-buffer.svg',image='provider-buffer.png')
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='asb-live-task-cache-v1'
    import matplotlib.pyplot as plt
    provider=any('host_four_current_union_bytes' in p for p in samples)
    fig,axes=plt.subplots(3 if provider else 2,1,figsize=(10,9 if segmented else 8 if provider else 6),sharex=True)
    for index, group in enumerate(groups):
        times=[(p['timestamp_ns']-samples[0]['timestamp_ns'])/1e9 for p in group]
        axes[0].plot(times,[p['guest_cached_bytes']/2**20 for p in group],color='C0',label='Guest Cached' if index==0 else None)
        axes[0].plot(times,[physical_bytes(p)/2**20 for p in group],color='C1',label='Confirmed host physical residency' if index==0 else None)
        if any('host_current_union_bytes' in p for p in group):
            axes[0].plot(times,[p['host_vmm_confirmed_bytes']/2**20 for p in group],color='C2',ls='--',label='VMM mapped PFNs')
            axes[0].plot(times,[p['host_snapshot_current_bytes']/2**20 for p in group],color='C3',ls=':',label='Current snapshot-cache PFNs')
            if any('host_three_current_union_bytes' in p for p in group):
                axes[0].plot(times,[p['host_current_union_bytes']/2**20 for p in group],color='C5',ls='-.',label='VMM + direct backend union')
                axes[0].plot(times,[p['host_lower_current_bytes']/2**20 for p in group],color='C6',ls=':',label='Current raw-layer PFNs')
                axes[1].plot(times,[p['host_lower_old_snapshot_bytes']/2**20 for p in group],color='C7',ls=':',label='Old raw-layer copies (separate)')
            if any('host_provider_current_bytes' in p for p in group):
                axes[2].plot(times,[p['host_provider_current_bytes']/1024 for p in group],color='C8',marker='.',label='Current copies: single record' if segmented else 'Attributed current buffer copies')
                axes[2].plot(times,[p['host_provider_old_snapshot_bytes']/1024 for p in group],color='C4',ls='--',marker='.',label='Old copies: single record' if segmented else 'Old snapshot buffer copies (separate)')
                axes[1].plot(times,[p['provider_unattributed_resident_bytes']/2**20 for p in group],color='C9',ls=':',label='Unattributed: single record' if segmented else 'Whole buffer pages: content unattributed')
            if any('host_four_segment_current_union_bytes' in p for p in group):
                axes[0].plot(times,[p['host_four_current_union_bytes']/2**20 for p in group],color='C8',ls='--',label='Host union: single record')
                axes[2].plot(times,[p['host_provider_combined_current_bytes']/1024 for p in group],color='C2',marker='.',label='Current copies: incl. segments')
                axes[2].plot(times,[p['host_provider_combined_old_snapshot_bytes']/1024 for p in group],color='C6',ls='--',label='Old copies: incl. segments')
                axes[1].plot(times,[p['provider_segment_unattributed_resident_bytes']/2**20 for p in group],color='C8',ls='-.',label='Unattributed: incl. segments')
            axes[1].plot(times,[p['snapshot_old_version_bytes']/2**20 for p in group],color='C4',ls='--',label='Old snapshot versions (host RAM; separate)')
        axes[1].plot(times,[p['host_residency_unconfirmed_guest_capacity_bytes']/2**20 for p in group],color='C0',label='Unconfirmed guest-page capacity (not host RAM)' if index==0 else None)
    if len(groups)>1:
        left=(groups[0][-1]['timestamp_ns']-samples[0]['timestamp_ns'])/1e9
        right=(groups[1][0]['timestamp_ns']-samples[0]['timestamp_ns'])/1e9
        for ax in axes:ax.axvspan(left,right,color='grey',alpha=.18,label='Checkpoint/restore: no observations')
    sampling_label='no added sampling pause' if any('host_four_current_union_bytes' in p for p in samples) else 'no VM pause'
    axes[0].set(title=title+': whole guest page cache, '+sampling_label,ylabel='Cache (MiB)')
    axes[1].set(ylabel='Separate quantities (MiB)' if any('host_current_union_bytes' in p for p in samples) else 'Unconfirmed capacity (MiB)')
    if provider:axes[2].set(ylabel='Attributed buffer copies (KiB)')
    axes[-1].set(xlabel='Host elapsed time (s)')
    for ax in axes:ax.grid(alpha=.25);ax.legend()
    if segmented:axes[2].legend(ncol=2,fontsize=9)
    fig.tight_layout();fig.savefig(output/'cache.svg',metadata={'Date':None});fig.savefig(output/'cache.png');plt.close(fig)
    if segmented:
        from provider_residency_report import write_breakdown
        write_breakdown(summary['provider_buffer_breakdown'],output)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--reuse-raw-inventory',type=Path,
        help='Reuse a previous summary inventory after matching source, file names and recounted measurements; no digest recomputation.')
    args=parser.parse_args();summary=report(args.source,args.output,reuse_raw_inventory=args.reuse_raw_inventory)
    print(json.dumps(dict(status=summary['status'],samples=summary['sample_count'],output=str(args.output))))
