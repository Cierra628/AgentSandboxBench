"""Offline host memory accounting report; legacy file-only extrapolation."""
import argparse
import csv
import json
from pathlib import Path
import shutil

from host_file_monitor import linear_reference


def current_statistics(rows, creation):
    """Summarize saved counters without interpolating a missing creation baseline."""
    marker=creation['verified_before_guest_creation_ns']
    baseline=creation.get('before_guest_counters')
    baseline_current=baseline.get('current') if baseline is not None else None
    after=[row for row in rows if row['monotonic_ns']>=marker]
    peak=max(rows,key=lambda row:row['current'])
    after_peak=max(after,key=lambda row:row['current'])
    duration=rows[-1]['monotonic_ns']-rows[0]['monotonic_ns']
    mean=sum(a['current']*(b['monotonic_ns']-a['monotonic_ns'])
             for a,b in zip(rows,rows[1:]))/duration
    return dict(metric='memory.current',unit='bytes',
        scope='Measurement cgroup and descendants: preparation, controller, private service and all sandboxes.',
        window_start_monotonic_ns=rows[0]['monotonic_ns'],window_end_monotonic_ns=rows[-1]['monotonic_ns'],
        initial_bytes=rows[0]['current'],final_bytes=rows[-1]['current'],
        observed_peak_bytes=peak['current'],observed_peak_sample=peak,
        time_weighted_mean_bytes=mean,
        mean_method='Previous sample held until next sample; first-to-last sample only, including recorded gaps. Estimate, not a continuous integral.',
        before_guest_bytes=baseline_current,
        baseline_status='OBSERVED_BEFORE_GUEST' if baseline_current is not None else 'NOT_MEASURED',
        after_service_ready_observed_peak_bytes=after_peak['current'],
        after_service_ready_observed_peak_sample=after_peak,
        after_service_ready_peak_increase_bytes=after_peak['current']-baseline_current if baseline_current is not None else None,
        final_increase_from_before_guest_bytes=rows[-1]['current']-baseline_current if baseline_current is not None else None,
        interpretation='Charged group memory, not complete sandbox physical memory. Before-guest baseline includes prepared inputs and service; increases include subsequent control and service work. Peaks are sampled, final sample may be after guest deletion.',
        coverage=dict(outside_group_charges='NOT_INCLUDED',unaccounted_kernel_allocations='NOT_INCLUDED',
                      swap_bytes='NOT_MEASURED',hugetlb_coverage='NOT_VERIFIED'),
        components='anon/file/kernel explain current; shmem is part of file. Counters are sequential and may lag: do not add them to current or label their residual unaccounted memory.')


def report(source, residency_path, output, *, allow_incomplete_inventory=False, creation_path=None, explicit_axes=False):
    source, output = Path(source), Path(output)
    receipt=json.loads((source/'result.json').read_text())
    if receipt['status']!='PASS': raise ValueError('host sampling failed or incomplete')
    with (source/'memory.tsv').open() as stream:
        rows=[{k:int(v) if v else None for k,v in row.items()} for row in csv.DictReader(stream,delimiter='\t')]
    pss_requested=receipt.get('pss_interval_seconds',1) is not None
    pss=[json.loads(line) for line in (source/'pss.jsonl').read_text().splitlines()] if pss_requested else []
    if len(rows)!=receipt['samples'] or len(rows)<2: raise ValueError('missing host samples')
    if not all(b['monotonic_ns']>a['monotonic_ns'] for a,b in zip(rows,rows[1:])): raise ValueError('host clock order')
    residency=json.loads(Path(residency_path).read_text()) if residency_path is not None else None
    if residency is not None and residency['status']!='OBSERVED' and not (allow_incomplete_inventory and residency['status']=='INCOMPLETE'):
        raise ValueError('backing residency inventory incomplete')
    outer=json.loads((Path(creation_path) if creation_path is not None else Path(residency_path).parent/'result.json').read_text())
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
    current=current_statistics(rows,creation)
    summary=dict(status='PASS' if residency is None or residency['status']=='OBSERVED' else 'OBSERVED_COUNTERS_INCOMPLETE_INVENTORY',scope=current['scope'],
        report_kind='CURRENT_ONLY' if residency is None else 'WITH_FILE_INVENTORY',
        report_schema='host-memory-current-v1',primary_metric='memory.current',context_metric='memory.stat:file',
        current=current,
        samples=len(rows),pss_samples=len(pss),partial_pss_samples=len(pss)-len(complete_pss),
        file_initial_bytes=rows[0]['file'],file_final_bytes=rows[-1]['file'],
        file_peak_bytes=max(row['file'] for row in rows),current_peak_bytes=max(row['current'] for row in rows),
        creation_baseline=baseline,
        file_before_guest_bytes=(creation.get('before_guest_counters') or {}).get('file'),
        shmem_peak_bytes=max(row['shmem'] for row in rows),
        file_excluding_shmem_peak_bytes=max(row['file']-row['shmem'] for row in rows),
        file_peak_after_service_ready_bytes=max(row['file'] for row in rows if row['monotonic_ns']>=creation['verified_before_guest_creation_ns']),
        pss_observed_peak_bytes=max((row['pss'] for row in complete_pss),default=None),
        maximum_sampling_gap_ns=max(b['monotonic_ns']-a['monotonic_ns'] for a,b in zip(rows,rows[1:])),
        creation=creation,collector=receipt,
        mincore={k:residency[k] for k in ('status','resident_bytes','cgroup_before','cgroup_after',
            'minimum_unaccounted_or_timing_bytes','file_residency_lower_bound_bytes','accounting','scope','cpu_ns','start_monotonic_ns','end_monotonic_ns')} if residency is not None else dict(status='NOT_REQUESTED',resident_bytes=None,accounting='NOT_MEASURED'),
        accounting_completeness='NOT_PROVEN_BY_AGGREGATES',
        pss_interpretation='Independent mapped-memory check. Total PSS is compared with current as context; file PSS is mapped file memory, not all cached file memory. Differences may be legitimate, including unmapped cache, kernel memory, outside sharers and process exits.',
        curve_caption='Current is group-charged memory, not complete physical coverage. Host file and guest Cached are different layers; mincore and PSS overlap with current, never add them.',
        comparison='No measured no-sharing baseline. N*A is a separately labelled single-sandbox extrapolation; use the same workload phase, initial cache and concurrency conditions and report common preparation overhead.')
    if not pss_requested:summary.update(pss_status='NOT_REQUESTED',pss_interpretation='Not requested by the saved collector.')
    output.mkdir(parents=True,exist_ok=False)
    for name in ('memory.tsv','result.json',*(['pss.jsonl'] if pss_requested else [])):shutil.copy2(source/name,output/('collector-'+name if name=='result.json' else name))
    if residency_path is not None:shutil.copy2(residency_path,output/'backing-residency.json')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3 if pss_requested else 2,1,figsize=(10,8 if pss_requested else 6),sharex=True)
    start=rows[0]['monotonic_ns'];times=[(row['monotonic_ns']-start)/1e9 for row in rows]
    axes[0].plot(times,[row['current']/2**20 for row in rows],color='#2563eb',label='Host memory.current',linewidth=2)
    if current['before_guest_bytes'] is not None:
        axes[0].axhline(current['before_guest_bytes']/2**20,color='#64748b',linestyle='--',label='Before-guest baseline (inputs + service included)')
    for ax in axes:
        ax.axvline((creation['verified_before_guest_creation_ns']-start)/1e9,color='#94a3b8',linestyle=':',label='Service ready, before first guest')
    axes[0].set_title('Primary: group-charged host memory (preparation + service + sandboxes)')
    for key,color,label in [('anon','#d97706','Host anon'),('file','#0f766e','Host file (includes shmem)'),('kernel','#64748b','Host kernel')]:
        axes[1].plot(times,[row[key]/2**20 if row.get(key) is not None else float('nan') for row in rows],label=label,color=color)
    ptimes=[(row['start_monotonic_ns']-start)/1e9 for row in pss]
    # Partial process scans are gaps, never zero or silently treated as complete.
    axes[1].set_title('Components: anon / file / kernel (not added to current)')
    if pss_requested:
        for key,label in [('pss','Total PSS'),('pss_file','PSS file'),('pss_anon','PSS anon'),('pss_shmem','PSS shmem')]:
            axes[2].plot(ptimes,[row[key]/2**20 if row['status']=='OBSERVED' and row[key] is not None else float('nan') for row in pss],label=label)
        axes[2].set_title('Independent PSS breakdown: mapped pages only')
    for ax in axes:ax.set_ylabel('MiB');ax.grid(alpha=.2);ax.legend();ax.set_ylim(bottom=0)
    if explicit_axes:
        for ax,label in zip(axes,('Group memory.current, MiB','Group memory.stat components, MiB','Mapped PSS, MiB')):ax.set_ylabel(label)
    axes[-1].set_xlabel('Host monotonic elapsed seconds; preparation and task verification included')
    fig.text(.5,.012,'Current excludes outside-group charges and unaccounted allocations. Host file is not guest Cached. Do not add mincore/PSS.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.025,1,1));fig.savefig(output/'overview.png',dpi=150);fig.savefig(output/'overview.svg');plt.close(fig)
    # Preparation can dominate the full-window scale; keep a separate baseline view.
    active=[row for row in rows if row['monotonic_ns']>=creation['verified_before_guest_creation_ns']]
    base=current['before_guest_bytes']
    fig,ax=plt.subplots(figsize=(10,4))
    ax.plot([(row['monotonic_ns']-creation['verified_before_guest_creation_ns'])/1e9 for row in active],
            [(row['current']-(base if base is not None else 0))/2**20 for row in active],color='#2563eb')
    ax.axhline(0,color='#94a3b8',linestyle=':');ax.grid(alpha=.2)
    ax.set_ylabel('MiB above before-guest baseline' if base is not None else 'MiB (absolute; baseline not measured)')
    if explicit_axes:ax.set_ylabel('Group memory.current above baseline, MiB' if base is not None else 'Group memory.current (absolute), MiB')
    ax.set_title('Host memory.current after service ready; control and cleanup included')
    ax.set_xlabel('Monotonic seconds since before-guest marker')
    fig.tight_layout();fig.savefig(output/'after-service-ready.png',dpi=150);fig.savefig(output/'after-service-ready.svg');plt.close(fig)
    def mib(value):return 'NOT_MEASURED' if value is None else f'{value/2**20:.3f} MiB'
    (output/'REPORT.md').write_text('\n'.join([
        '# Host总内存记账', '',
        '主指标为`memory.current`，覆盖统计组内的准备、控制程序、私有服务和全部沙箱。', '',
        '| 指标 | 数值 |', '| --- | --- |',
        f"| 首次／最后采样 | {mib(current['initial_bytes'])} / {mib(current['final_bytes'])} |",
        f"| 全流程观测峰值 | {mib(current['observed_peak_bytes'])} |",
        f"| 时间加权平均估计值 | {mib(current['time_weighted_mean_bytes'])} |",
        f"| 创建guest前基线 | {mib(current['before_guest_bytes'])} |",
        f"| 服务就绪后观测峰值 | {mib(current['after_service_ready_observed_peak_bytes'])} |",
        f"| 该峰值相对创建前基线的增量 | {mib(current['after_service_ready_peak_increase_bytes'])} |",
        f"| 最后采样相对创建前基线的增量 | {mib(current['final_increase_from_before_guest_bytes'])} |", '',
        '基线包含已准备的输入及服务。增量还包含控制和服务的后续变化，不是任务进程独占内存；最后采样可能在删除guest后。观测峰值可能漏掉采样间的短峰。', '',
        '组外记账及未记账内核分配不包含在内，swap未采，hugetlb大页覆盖未核实；这些读数表示组内总记账，不能直接推算可多部署多少沙箱。', '',
        'anon/file/kernel解释组成，shmem已在file内。顺序读取的分项不必精确相加相等。PSS、mincore与current重叠，不能作为额外加项；host file也不是guest Cached对应的物理页。', '',
        'summary.json保存采样读取用时、线程CPU用时和最大采样间隔；若原来采了PSS，线程CPU还包含其扫描。', '',
        '原双摘要比较命令仍只外推file峰值，不比较current或整体内存节省。', ''
    ]))
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('single_summary',type=Path);parser.add_argument('multiple_summary',type=Path,nargs='?')
    parser.add_argument('--residency',type=Path,help='Offline report mode: first path is the saved host-file sampling directory')
    parser.add_argument('--current-only',action='store_true',help='Offline total-accounting report; no file residency inventory required')
    parser.add_argument('--creation',type=Path,help='Saved isolated-service result.json with host_file_creation, required for current-only')
    parser.add_argument('--sandboxes',type=int);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.current_only:
        if args.creation is None or args.residency is not None or args.multiple_summary is not None or args.sandboxes is not None:
            parser.error('current-only requires --creation and no residency or extrapolation arguments')
        value=report(args.single_summary,None,args.output,creation_path=args.creation)
        print('status='+value['status']+' output='+str(args.output));raise SystemExit(0)
    if args.creation is not None:parser.error('--creation requires --current-only')
    if args.residency:
        if args.multiple_summary is not None or args.sandboxes is not None:parser.error('residency report and N-sandbox extrapolation are separate modes')
        value=report(args.single_summary,args.residency,args.output)
        print('status='+value['status']+' output='+str(args.output));raise SystemExit(0)
    if args.multiple_summary is None or args.sandboxes is None:parser.error('comparison requires two summaries and --sandboxes')
    a=json.loads(args.single_summary.read_text());b=json.loads(args.multiple_summary.read_text())
    if any(value.get('report_kind')=='CURRENT_ONLY' for value in (a,b)):
        parser.error('file extrapolation requires residency reports; current-only reports have no residency inventory')
    if any(value['status']!='PASS' or value['mincore']['accounting']=='SHORTFALL_REQUIRES_RESOLUTION' for value in (a,b)):
        parser.error('resolve failed measurement or mincore accounting shortfall before extrapolating')
    value=linear_reference(a['file_peak_bytes'],b['file_peak_bytes'],args.sandboxes)
    value.update(metric='host file observed peak; full workflow',single_source=str(args.single_summary),multiple_source=str(args.multiple_summary))
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS output='+str(args.output))
