"""Offline host file report; optional single-sandbox linear extrapolation."""
import argparse
import csv
import json
from pathlib import Path
import shutil

from host_file_monitor import linear_reference


def report(source, residency_path, output, *, allow_incomplete_inventory=False):
    source, output = Path(source), Path(output)
    receipt=json.loads((source/'result.json').read_text())
    if receipt['status']!='PASS': raise ValueError('host sampling failed or incomplete')
    with (source/'memory.tsv').open() as stream:
        rows=[{k:int(v) if v else None for k,v in row.items()} for row in csv.DictReader(stream,delimiter='\t')]
    pss_requested=receipt.get('pss_interval_seconds',1) is not None
    pss=[json.loads(line) for line in (source/'pss.jsonl').read_text().splitlines()] if pss_requested else []
    if len(rows)!=receipt['samples'] or len(rows)<2: raise ValueError('missing host samples')
    if not all(b['monotonic_ns']>a['monotonic_ns'] for a,b in zip(rows,rows[1:])): raise ValueError('host clock order')
    residency=json.loads(Path(residency_path).read_text())
    if residency['status']!='OBSERVED' and not (allow_incomplete_inventory and residency['status']=='INCOMPLETE'):
        raise ValueError('backing residency inventory incomplete')
    outer=json.loads((Path(residency_path).parent/'result.json').read_text())
    creation=outer['host_file_creation']
    if creation['preexisting_firecrackers']!=0 or any(creation[k] for k in ('firecracker_pool','startup_prewarm','block_pool')):
        raise ValueError('fresh guest creation proof failed')
    if creation['cgroup']!=receipt['cgroup']: raise ValueError('creation/sampling cgroups differ')
    complete_pss=[row for row in pss if row['status']=='OBSERVED']
    marker=creation['verified_before_guest_creation_ns']
    earlier=[row for row in rows if row['monotonic_ns']<=marker]
    later=[row for row in rows if row['monotonic_ns']>=marker]
    if not earlier or not later:raise ValueError('host samples do not cover guest creation marker')
    baseline=dict(synchronous_counters=creation.get('before_guest_counters'),
        preceding_sample=earlier[-1],following_sample=later[0],
        preceding_gap_ns=marker-earlier[-1]['monotonic_ns'],following_gap_ns=later[0]['monotonic_ns']-marker,
        interpretation='Exact creation-time counter exists only in synchronous_counters. Bracketing samples retain their own times; not an interpolated baseline.')
    summary=dict(status='PASS' if residency['status']=='OBSERVED' else 'OBSERVED_COUNTERS_INCOMPLETE_INVENTORY',scope='Host cgroup file accounting, including shmem and workflow preparation/controller/service; not guest Cached or exact guest-cache physical residency.',
        primary_metric='memory.stat:file',context_metric='memory.current',
        samples=len(rows),pss_samples=len(pss),partial_pss_samples=len(pss)-len(complete_pss),
        file_initial_bytes=rows[0]['file'],file_final_bytes=rows[-1]['file'],
        file_peak_bytes=max(row['file'] for row in rows),current_peak_bytes=max(row['current'] for row in rows),
        creation_baseline=baseline,
        file_before_guest_bytes=creation['before_guest_counters']['file'] if 'before_guest_counters' in creation else None,
        shmem_peak_bytes=max(row['shmem'] for row in rows),
        file_excluding_shmem_peak_bytes=max(row['file']-row['shmem'] for row in rows),
        file_peak_after_service_ready_bytes=max(row['file'] for row in rows if row['monotonic_ns']>=creation['verified_before_guest_creation_ns']),
        pss_observed_peak_bytes=max((row['pss'] for row in complete_pss),default=None),
        maximum_sampling_gap_ns=max(b['monotonic_ns']-a['monotonic_ns'] for a,b in zip(rows,rows[1:])),
        creation=creation,collector=receipt,
        mincore={k:residency[k] for k in ('status','resident_bytes','cgroup_before','cgroup_after',
            'minimum_unaccounted_or_timing_bytes','file_residency_lower_bound_bytes','accounting','scope','cpu_ns','start_monotonic_ns','end_monotonic_ns')},
        accounting_completeness='NOT_PROVEN_BY_AGGREGATES',
        pss_interpretation='Independent mapped-memory check. Total PSS is compared with current as context; file PSS is mapped file memory, not all cached file memory. Differences may be legitimate, including unmapped cache, kernel memory, outside sharers and process exits.',
        curve_caption='Guest Cached and host file are different layers. Guest cache in private anonymous guest RAM contributes to host anon; file-backed guest RAM may contribute to host file regardless of the guest page type. No equality expected.',
        comparison='No measured no-sharing baseline. N*A is a separately labelled single-sandbox extrapolation; use the same workload phase, initial cache and concurrency conditions and report common preparation overhead.')
    if not pss_requested:summary.update(pss_status='NOT_REQUESTED',pss_interpretation='Not requested in the explicit shared-file profile.')
    output.mkdir(parents=True,exist_ok=False)
    for name in ('memory.tsv','result.json',*(['pss.jsonl'] if pss_requested else [])):shutil.copy2(source/name,output/('collector-'+name if name=='result.json' else name))
    shutil.copy2(residency_path,output/'backing-residency.json')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3 if pss_requested else 2,1,figsize=(10,8 if pss_requested else 6),sharex=True)
    start=rows[0]['monotonic_ns'];times=[(row['monotonic_ns']-start)/1e9 for row in rows]
    axes[0].plot(times,[row['file']/2**20 for row in rows],color='#2563eb',label='Host file (includes shmem)',linewidth=2)
    axes[0].plot(times,[(row['file']-row['shmem'])/2**20 for row in rows],color='#0f766e',linestyle='--',label='Host file minus shmem (context)')
    for ax in axes:
        ax.axvline((creation['verified_before_guest_creation_ns']-start)/1e9,color='#94a3b8',linestyle=':',label='Service ready, before first guest')
    axes[0].set_title('Primary: host file-cache accounting')
    axes[1].plot(times,[row['current']/2**20 for row in rows],label='Host memory.current',color='#64748b')
    axes[1].plot(times,[row['anon']/2**20 for row in rows],label='Host anon',color='#d97706')
    ptimes=[(row['start_monotonic_ns']-start)/1e9 for row in pss]
    # Partial process scans are gaps, never zero or silently treated as complete.
    if pss_requested:axes[1].plot(ptimes,[row['pss']/2**20 if row['status']=='OBSERVED' else float('nan') for row in pss],label='All scoped processes: PSS',color='#7c3aed',linestyle='--')
    axes[1].set_title('Context: total accounting and mapped residency (different coverage)')
    if pss_requested:
        for key,label in [('pss_file','PSS file'),('pss_anon','PSS anon'),('pss_shmem','PSS shmem')]:
            axes[2].plot(ptimes,[row[key]/2**20 if row['status']=='OBSERVED' and row[key] is not None else float('nan') for row in pss],label=label)
        axes[2].set_title('Independent PSS breakdown: mapped pages only')
    else:axes[1].set_title('Context: total accounting; PSS not requested')
    for ax in axes:ax.set_ylabel('MiB');ax.grid(alpha=.2);ax.legend();ax.set_ylim(bottom=0)
    axes[-1].set_xlabel('Host monotonic elapsed seconds; preparation and task verification included')
    fig.text(.5,.012,'Host file is not guest Cached. Guest RAM may be host anon or file-backed; these curves do not isolate guest page cache.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.025,1,1));fig.savefig(output/'overview.png',dpi=150);fig.savefig(output/'overview.svg');plt.close(fig)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('single_summary',type=Path);parser.add_argument('multiple_summary',type=Path,nargs='?')
    parser.add_argument('--residency',type=Path,help='Offline report mode: first path is the saved host-file sampling directory')
    parser.add_argument('--sandboxes',type=int);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.residency:
        if args.multiple_summary is not None or args.sandboxes is not None:parser.error('residency report and N-sandbox extrapolation are separate modes')
        value=report(args.single_summary,args.residency,args.output)
        print('status='+value['status']+' output='+str(args.output));raise SystemExit(0)
    if args.multiple_summary is None or args.sandboxes is None:parser.error('comparison requires two summaries and --sandboxes')
    a=json.loads(args.single_summary.read_text());b=json.loads(args.multiple_summary.read_text())
    if any(value['status']!='PASS' or value['mincore']['accounting']=='SHORTFALL_REQUIRES_RESOLUTION' for value in (a,b)):
        parser.error('resolve failed measurement or mincore accounting shortfall before extrapolating')
    value=linear_reference(a['file_peak_bytes'],b['file_peak_bytes'],args.sandboxes)
    value.update(metric='host file observed peak; full workflow',single_source=str(args.single_summary),multiple_source=str(args.multiple_summary))
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS output='+str(args.output))
