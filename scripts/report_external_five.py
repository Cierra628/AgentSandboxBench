"""Summarize saved single-guest evidence; quantities retain separate scopes."""
import csv
import json
from pathlib import Path
import shutil
import statistics

MIB=2**20


def table(path):
    with Path(path).open() as f:return list(csv.DictReader(f,delimiter='\t'))


def sample_stats(rows,keys,start=None,end=None):
    selected=[r for r in rows if (start is None or int(r['monotonic_ns'])>=start) and (end is None or int(r['monotonic_ns'])<=end)]
    if not selected:raise ValueError('no samples in requested host window')
    return dict(samples=len(selected),start_monotonic_ns=int(selected[0]['monotonic_ns']),end_monotonic_ns=int(selected[-1]['monotonic_ns']),
        quantities={k:dict(observed_peak_bytes=max(int(r[k]) for r in selected),sample_median_bytes=statistics.median(int(r[k]) for r in selected)) for k in keys},
        median_method='Unweighted median of valid periodic samples; observed peaks may miss transients')


def report(out,raw,storage,dest):
    out,raw,storage,dest=map(Path,(out,raw,storage,dest))
    task=json.loads((raw/'result.json').read_text());native=json.loads((storage/'result.json').read_text())
    if task['status']!='PASS' or task['measurement_status']!='COMPLETE':raise ValueError('complete measurement required')
    endpoint=task['endpoint'];session=json.loads((raw/'runtime-monitor.json').read_text())['sessions'][0]
    host=table(out/'host-file/memory.tsv');tools=table(raw/'tools.tsv');vm=table(raw/'vmm/host-vmm.tsv')
    if not tools or len(tools)!=task['actions'] or not vm or endpoint['pss']['denominator']!=1:raise ValueError('missing measured evidence')
    vpss=[r for r in vm if r['pss_kib']!='']
    if not vpss:raise ValueError('periodic VMM PSS missing')
    receipts=dict(host=json.loads((out/'host-file/result.json').read_text()),guest=session['receipt'],vmm=task['vmm_collector'])
    if any(r['status']!='PASS' for r in receipts.values()):raise ValueError('collector failed')
    management=[e for e in json.loads((raw/'events.json').read_text()) if e['stage'].startswith('monitor-')]
    windows=dict(task=dict(start_monotonic_ns=task['task_start_monotonic_ns'],end_monotonic_ns=task['task_end_monotonic_ns'],duration_ns=task['task_duration_ns']),
        replay=dict(start_monotonic_ns=task['replay_start_monotonic_ns'],end_monotonic_ns=task['replay_end_monotonic_ns']),
        download=dict(start_monotonic_ns=task['download_start_monotonic_ns'],end_monotonic_ns=task['task_end_monotonic_ns']))
    costs=dict(endpoint_setup=json.loads((raw/'endpoint-setup.json').read_text()),collectors=receipts,endpoint_pss={k:endpoint['pss'][k] for k in ('cpu_ns','start_monotonic_ns','end_monotonic_ns')},
        endpoint_mincore={k:endpoint['p1'][k] for k in ('cpu_ns','start_monotonic_ns','end_monotonic_ns')},sampling_management_calls=management,
        interpretation='Collector CPU and query wall/CPU are observed costs, not task slowdown; no extra control run')
    value=dict(status='PASS',task_id=task['task_id'],sandbox_count=1,windows=windows,
        controller_elapsed_ns=task['controller_elapsed_ns'],outer_elapsed_ns=json.loads((out/'result.json').read_text()).get('outer_elapsed_ns'),
        external_invocation_receipt_path=json.loads((out/'result.json').read_text()).get('external_invocation_receipt_path'),
        tool_duration_sum_ns=sum(int(r['duration_ns']) for r in tools),actions=len(tools),
        host=dict(full=sample_stats(host,('current','file','anon','kernel','shmem','file_dirty','file_writeback')),
            task=sample_stats(host,('current','file'),task['task_start_monotonic_ns'],task['task_end_monotonic_ns']),
            replay=sample_stats(host,('current','file'),task['replay_start_monotonic_ns'],task['replay_end_monotonic_ns']),
            before_guest=native['host_file_creation']['before_guest_counters'],initial_host_sample=host[0],cgroup=endpoint['cgroup']),
        endpoint=endpoint,periodic_pss_observed_peak_bytes=max(int(r['pss_kib'])*1024 for r in vpss),costs=costs,
        original_validation=task['original_validation'],functional_evidence=task['functional_evidence'],
        snapshot_save=task['snapshot_save'],snapshot_restore=task['snapshot_restore'],cleanup=dict(guest=task['cleanup'],service=native['cleanup']),
        raw_paths=dict(controller=str(raw),native=str(storage),host=str(out/'host-file')),
        interpretation='PSS/N, group memory.current/file, global specified-lower mincore and guest Cached are separate overlapping quantities. Never sum. Upper and other backing residency are unknown; mincore minus file is not confirmed outside-group capacity or savings.')
    shutil.copy2(raw/'tools.tsv',dest/'tools.tsv')
    (dest/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    zero=int(vpss[0]['monotonic_ns']);fig,ax=plt.subplots(figsize=(10,4),layout='constrained')
    ax.plot([(int(r['monotonic_ns'])-zero)/1e9 for r in vpss],[int(r['pss_kib'])/1024 for r in vpss])
    ax.set(xlabel='Host monotonic elapsed seconds',ylabel='VMM PSS/N (MiB/guest; N=1)',title=task['task_id']+' — periodic PSS');ax.grid(alpha=.2)
    fig.savefig(dest/'pss.png',dpi=140);fig.savefig(dest/'pss.svg');plt.close(fig)
    pss=endpoint['pss']['per_sandbox_bytes']/MIB;p1=endpoint['p1']['resident_bytes']/MIB
    text=f'''# {task['task_id']} single-guest measurement

Task window: {task['task_duration_ns']/1e9:.6f} s (controller entry before creation through replay and download; checks/cleanup excluded). Tool cumulative: {value['tool_duration_sum_ns']/1e9:.6f} s across {len(tools)} original actions. Background dependency waits are retained separately for CompCert. No save/restore occurred.

Endpoint VMM PSS/N: {pss:.3f} MiB/guest, N=1. Specified initial lower residency: {p1:.3f} MiB across {endpoint['p1']['unique_files']} unique files. These quantities are not added. Coverage insufficiency flag: {endpoint['coverage_insufficient']}; upper and other backing objects remain unknown.

Original checker: {task['original_validation']['actual_status']}, exit {task['original_validation']['actual_exit_code']}; expected exit {task['original_validation']['exit_code']}. Scope: {task['original_validation']['scope']}. Log4j2 exit 141 remains FAIL even when replay and artifact checks pass.

See summary.json for before-guest baseline, full/task/replay observed peaks and sample medians, exact timing windows, separate collector CPU/sample/gap receipts, endpoint query costs, original results and cleanup receipts. tools.tsv retains each call and exit. guest.png, host/overview.png and pss.png use separate physical quantities and clocks. No sampling-on/off comparison or slowdown estimate is made.

Raw controller evidence: {raw}
Native lifecycle evidence: {storage}
Host samples: {out/'host-file'}
'''
    (dest/'REPORT.md').write_text(text)
    return value
