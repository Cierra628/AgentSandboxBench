"""Three task metrics from saved logs: tool latency, guest memory and Cached."""
import argparse
import csv
import json
from pathlib import Path

from report_sandbox_runtime import samples, rows, curve_chunks


def tool_rows(raw, result):
    tools = []
    for segment in rows(raw/'segments.tsv'):
        for row in rows(raw/'rollouts'/segment['rollout']/segment['segment']/'steps.tsv'):
            duration = int(row['duration_ns'])
            assert duration >= 0
            tools.append(dict(tool_index=len(tools)+1, sandbox_id=segment['sandbox_id'],
                role=segment['rollout'], segment=segment['segment'], step=row['step'],
                exit_code=int(row['exit_code']), duration_ns=duration))
    assert tools, 'tool timings are missing'
    total = sum(t['duration_ns'] for t in tools)
    expected = result.get('guest_action_sum_ns', result.get('replay', {}).get('guest_action_sum_ns'))
    if expected is not None: assert total == expected, 'tool timing total differs from source result'
    return tools, total


def write_tool_report(raw, output):
    """Monitor-off control: actual timings only, never invent memory samples."""
    raw, output = Path(raw), Path(output)
    result=json.loads((raw/'result.json').read_text())
    assert result['status']=='PASS' and result['monitor_mode']=='off'
    assert result['cleanup']['monitoring_disabled']
    assert not any((raw/name).exists() for name in ('runtime-monitor.json','host-vmm','host/memory-samples.tsv'))
    tools,total=tool_rows(raw,result)
    for segment in rows(raw/'segments.tsv'):
        directory=raw/'rollouts'/segment['rollout']/segment['segment']
        for name in ('guest-memory.tsv','guest-vmstat.tsv'):
            assert not rows(directory/name), 'monitor-off has boundary samples'
    with (output/'tools.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(tools[0]),delimiter='\t')
        writer.writeheader();writer.writerows(tools)
    return dict(status='PASS',task_id=result['task_id'],tool_count=len(tools),
        tool_duration_sum_ns=total,source_monitor_profile='off',memory='NOT_MEASURED',
        official_task_status=result.get('official_task_status'),
        strict_replay_status=result.get('strict_replay_status'),diagnostic_status=result.get('diagnostic_status'),
        scope='tool timing retained; guest periodic/boundary and host sampling disabled; original task checks retained')


def write_report(raw, output, *, private_file_control=False):
    raw, output = Path(raw), Path(output)
    assert not output.resolve().is_relative_to(raw.resolve()), 'report must be outside raw'
    result = json.loads((raw/'result.json').read_text())
    groups, scope = samples(raw)
    assert groups, 'guest samples are missing'
    tools,total=tool_rows(raw,result)
    memory, chunks, per_sandbox = [], [], []
    origin = min(int(r['timestamp_ns']) for g in groups.values() for c in g['chunks'] for r in c)
    sessions = [s for g in groups.values() for s in g['sessions']]
    host_origin = min(s['start_host_monotonic_ns'] for s in sessions) if sessions else None
    for sid, group in groups.items():
        selected = []
        for index, data in enumerate(group['chunks']):
            if group['sessions']:
                session = group['sessions'][index]
                interval = session['receipt']['interval_seconds']
                clock, local_origin = 'monotonic_ns', int(data[0]['monotonic_ns'])
                offset = session['start_host_monotonic_ns']-host_origin
            else:
                interval, clock, local_origin, offset = .1, 'timestamp_ns', origin, 0
            for chunk in curve_chunks(data, max_gap_ns=int(max(.5, 1.5*interval)*1e9), clock=clock):
                converted = []
                for r in chunk:
                    used = (int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))*1024
                    assert used >= 0
                    converted.append(dict(sandbox_id=sid, role=group['role'],
                        timestamp_ns=int(r['timestamp_ns']), elapsed_seconds=(int(r[clock])-local_origin+offset)/1e9,
                        memory_used_bytes=used, cached_bytes=int(r['Cached_kib'])*1024,
                        shmem_bytes=int(r['Shmem_kib'])*1024))
                memory.extend(converted); selected.extend(converted)
                chunks.append((sid, group['role'], converted))
        assert selected, 'empty guest memory series'
        per_sandbox.append(dict(sandbox_id=sid, role=group['role'], sample_count=len(selected),
            memory_observed_peak_bytes=max(r['memory_used_bytes'] for r in selected),
            cached_observed_peak_bytes=max(r['cached_bytes'] for r in selected)))
    for name, data in [('tools.tsv', tools), ('memory.tsv', memory)]:
        with (output/name).open('x') as stream:
            writer=csv.DictWriter(stream, fieldnames=list(data[0]), delimiter='\t')
            writer.writeheader(); writer.writerows(data)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
    axes[0].bar([r['tool_index'] for r in tools], [r['duration_ns']/1e9 for r in tools], color='C0')
    axes[0].set(title='Tool latency', xlabel='Tool call index', ylabel='Seconds')
    labelled = set()
    colors = {sid:'C'+str(i%10) for i,sid in enumerate(groups)}
    for sid, role, data in chunks:
        label = role+' / '+sid[-8:] if sid not in labelled else None
        labelled.add(sid)
        for ax, field in zip(axes[1:], ('memory_used_bytes', 'cached_bytes')):
            ax.plot([r['elapsed_seconds'] for r in data], [r[field]/2**20 for r in data],
                marker='.', color=colors[sid], label=label)
    for ax, title in zip(axes[1:], ('Guest memory: MemTotal - MemAvailable', 'Guest page cache: Cached (includes Shmem)')):
        ax.set(title=title, xlabel='Monotonic seconds from first monitor session' if sessions else 'Recorded seconds since first memory sample', ylabel='MiB')
        ax.legend()
    for ax in axes: ax.grid(axis='y', alpha=.25)
    fig.suptitle(result.get('task_id', raw.name))
    fig.savefig(output/'overview.svg', metadata={'Date':None})
    fig.savefig(output/'overview.png', dpi=120); plt.close(fig)
    manifest=json.loads((raw/'runtime-monitor.json').read_text()) if (raw/'runtime-monitor.json').exists() else {}
    summary=dict(status='PASS', task_id=result.get('task_id'), source_run_status=result['status'],
        official_task_status=result.get('official_task_status'), strict_replay_status=result.get('strict_replay_status'),
        diagnostic_status=result.get('diagnostic_status'), scope=scope,
        source_monitor_profile=manifest.get('profile', 'historical'),
        damon_observed=manifest.get('damon_observed',False),
        tool_count=len(tools), tool_duration_sum_ns=total, per_sandbox=per_sandbox,
        definitions=dict(tool_latency='guest command elapsed time; excludes host API and model inference',
            memory='guest MemTotal - MemAvailable; kernel estimate, not nonfree or host physical memory',
            page_cache='guest Cached; includes Shmem; do not add Shmem again',
            memory_time='guest monotonic deltas within sessions; host session starts position sessions; setup delay between host session start and first guest sample is not calibrated' if sessions else 'historical segment-only wall time'),
        physical_page_cache='NOT_MEASURED',
        files=dict(curve='overview.svg', tools='tools.tsv', memory='memory.tsv'),
        notes=['Observed sample maxima, not instantaneous peaks; gaps are not interpolated.',
               'Report PASS means logs were read consistently; original task status and nonzero exits are retained.'])
    if result.get('host_scaling_mode'):
        from host_scaling import audit
        summary['host_scaling']=audit(raw)
        summary['official_task_status']='NOT_RUN_PREFIX_ONLY' if result['host_scaling_mode']=='cold' else 'FAIL'
    elif private_file_control or result.get('host_private_file_control'):
        from host_private_files import audit_prefix_task
        summary['private_file_control_prefix']=audit_prefix_task(raw)
        summary['official_task_status']='NOT_RUN_PREFIX_ONLY'
    elif result.get('host_prefix_count'):
        from host_prefix_compare import audit_prefix
        summary['prefix_comparison']=audit_prefix(raw)
        summary['official_task_status']='NOT_RUN_PREFIX_ONLY'
    elif 'rollout_start_monotonic_ns' in result:
        from report_prettier6604_grpo import audit
        schedule,_,_=audit(raw,Path(result['official_task_directory']))
        summary['schedule_audit']={key:schedule[key] for key in ('status','schedule','roles','actions','segments','logical_action_count','checkpoint_count','checkpoint_restore','cleanup')}
        start=result['rollout_start_monotonic_ns'];end=result['rollout_end_monotonic_ns']
        assert start<=result['first_tool_start_monotonic_ns']<=end
        summary['rollout_timing']=dict(start_monotonic_ns=start,end_monotonic_ns=end,
            elapsed_ns=end-start,scope='Before sandbox creation through all branch checks; excludes outer preparation, reporting and cleanup',
            checkpoint_request_ns=schedule['timing']['checkpoint_call_ns'],
            restore_request_per_role_ns=schedule['timing']['restore_request_per_role_ns'])
        events=schedule['timing']['stages']
        summary['rollout_timing']['restore_return_to_first_segment_ns']={role:
            result['role_first_segment_monotonic_ns'][role]-(event['start_monotonic_ns']+event['duration_ns'])
            for role in result['roles'] for event in events if event['stage'].endswith('-restore-'+role)}
        summary['rollout_timing']['restore_gap_scope']='Restore request returned to first segment dispatch; includes monitor setup and state checks, not a guest-command start timestamp'
        summary['official_task_status']='FAIL' if any(p['official_task_status']=='FAIL' for p in result['paths'].values()) else 'PASS'
    if result.get('host_full_rootfs_control'):
        control=result['host_full_rootfs_control']
        assert control['status']=='OBSERVED' and control['schedule'] in ('grpo','bpo')
        summary['host_full_rootfs_control']=dict(profile=control['profile'],mode=control['mode'],
            P0_resident_bytes=control['P0']['resident_bytes'],P1_resident_bytes=control['P1']['resident_bytes'],
            raw='host-full-rootfs.json',scope='Initial lower-file host residency only; separate from these guest logical-memory curves')
        if control['schedule']=='bpo':
            summary['host_full_rootfs_control']['generated_layers']=dict(resident_bytes=control['generated_layers']['P1']['resident_bytes'],scope='Separate generated layers within this run; not part of main savings')
    (output/'runtime-metrics.json').write_text(json.dumps(summary, indent=2)+'\n')
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw', type=Path); parser.add_argument('output', type=Path)
    parser.add_argument('--private-file-control',action='store_true',help='Explicit recovery of a private-control prefix report; cache evidence stays in its group output')
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.raw.resolve()):parser.error('output must be outside raw')
    args.output.mkdir(parents=True, exist_ok=False)
    summary=write_report(args.raw, args.output,private_file_control=args.private_file_control)
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print('status='+summary['status']+' result_dir='+str(args.output.resolve()))
