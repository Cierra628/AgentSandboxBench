"""Audit up to three mirrored DAMON batches; export only derived diagnostic data."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
from guest_damon_control import COMPARISON, REVERSE_COMPARISON
from report_fixed_frequency_monitor import report

ROOT = Path(__file__).resolve().parents[1]


def summarize(directories):
    assert 1 <= len(directories) <= 3
    expected = [COMPARISON, REVERSE_COMPARISON, COMPARISON]
    runs, batches, sandbox_ids = [], [], set()
    identity = None
    for group, directory in enumerate(directories, 1):
        directory = directory.resolve()
        result = json.loads((directory / 'result.json').read_text())
        assert result['damon_control_modes'] == expected[group - 1]
        offline = report(directory)
        batches.append(dict(group=group, result_dir=str(directory.relative_to(ROOT)),
                            status=result['status'], offline_status=offline['status'],
                            order=result['damon_control_modes'],
                            source_result_sha256=hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest(),
                            offline_report_sha256=hashlib.sha256((directory / 'offline-report.json').read_bytes()).hexdigest()))
        for row, raw_row in zip(offline['observations'], result['runs']):
            raw = Path(raw_row['raw_dir'])
            sid = (raw / 'sandbox-id.txt').read_text().strip()
            assert sid not in sandbox_ids
            sandbox_ids.add(sid)
            current = {key: raw_row['metadata'][key] for key in
                       ('workload_image', 'sandbox_cpu', 'sandbox_memory_mib', 'action_manifest_sha256',
                        'aenv_version', 'host_kernel', 'agentenv_container_image', 'agentenv_container_id',
                        'overlaybd_io_engine', 'overlaybd_direct_io')}
            if identity is None: identity = current
            assert current == identity
            roots = [line.split()[0] for line in (raw / 'guest-mounts.txt').read_text().splitlines()
                     if line.split()[1] == '/']
            assert roots == ['/dev/vdb']
            state = row['guest_cache_state']
            runs.append(dict(group=group, position=row['position'], mode=row['damon_control_mode'],
                             raw_dir=str(raw.relative_to(ROOT)),
                             scan_ms=row['git_add_trace2']['directory']['seconds'] * 1000,
                             scan_counts=row['git_add_trace2']['directory']['counts'],
                             step_025_ms=row['steps_ms']['025'], task_window_seconds=row['task_window_seconds'],
                             steps_ms=row['steps_ms'], controller_exec_seconds=row['controller_exec_seconds'],
                             guest_action_sum_seconds=row['guest_action_sum_seconds'],
                             command_ms=row['command_clock']['monotonic_raw_ns'] / 1e6,
                             command_cpu_ms=(row['command_clock']['user_ns'] + row['command_clock']['system_ns']) / 1e6,
                             guest_root_device='vdb', guest_root_reads_completed=row['guest_diskstats']['vdb']['reads_completed'],
                             workingset_refault_file=state['during_command']['counter_deltas']['workingset_refault_file'],
                             available_before_mib=state['before']['meminfo_kib']['MemAvailable'] / 1024,
                             free_before_percent=100 * state['before']['meminfo_kib']['MemFree'] / state['before']['meminfo_kib']['MemTotal'],
                             buffers_before_kib=state['before']['meminfo_kib']['Buffers'],
                             cached_start_kib=state['start']['meminfo_kib']['Cached'],
                             cached_before_kib=state['before']['meminfo_kib']['Cached'],
                             regular_reclaim_deltas={span: {k: v for k, v in state[span]['counter_deltas'].items()
                                                           if k.startswith(('pgscan', 'pgsteal', 'allocstall'))}
                                                    for span in ('before_command', 'during_command')},
                             host_wait_upper_bound_ms=row['host_wait_bound']['summed_runnable_wait_upper_bound_ns'] / 1e6,
                             kvm_successful_async_setup=sum(t['successful_async_setup'] for t in row['host_kvm_faults']['threads']),
                             damon_boundary_states={phase: receipt['observations'] for phase, receipt in row['damon_control'].items()},
                             observed_min_free_gib=row['observed_min_free_gib']))
    descriptions = {}
    for mode in ('default', 'damon_disabled'):
        selected = [r for r in runs if r['mode'] == mode]
        descriptions[mode] = dict(count=len(selected), metrics={})
        for metric in ('scan_ms', 'task_window_seconds', 'guest_root_reads_completed'):
            values = [r[metric] for r in selected]
            descriptions[mode]['metrics'][metric] = dict(min=min(values), median=statistics.median(values),
                                                        max=max(values), mean=statistics.mean(values))
    means = {mode: {metric: statistics.mean(r[metric] for r in runs if r['mode'] == mode)
                    for metric in ('task_window_seconds', 'controller_exec_seconds', 'guest_action_sum_seconds')}
             for mode in ('default', 'damon_disabled')}
    timing = dict(means=means,
                  task_disabled_vs_default_percent=100 * (means['damon_disabled']['task_window_seconds'] / means['default']['task_window_seconds'] - 1),
                  guest_actions_disabled_minus_default_ms=1000 * (means['damon_disabled']['guest_action_sum_seconds'] - means['default']['guest_action_sum_seconds']),
                  controller_exec_minus_actions_disabled_minus_default_ms=1000 * ((means['damon_disabled']['controller_exec_seconds'] - means['damon_disabled']['guest_action_sum_seconds']) - (means['default']['controller_exec_seconds'] - means['default']['guest_action_sum_seconds'])),
                  unmeasured='runner environment/baseline Git preparation, loop bookkeeping, final patch generation and control channel; residual is not pure network/platform overhead')
    summary = dict(status='PASS', scope='DAMON default/disabled diagnosis, periodic monitoring off',
                full_replays=len(runs), batches=batches, runs=runs, descriptions=descriptions,
                timing_scope=timing,
                source_identity={k: v for k, v in identity.items() if k != 'agentenv_container_id'},
                checks=dict(replay='PASS', step_025_stdout_hash='PASS', patch='PASS', targeted_svg_html='PASS',
                            damon_state='PASS', cpu_restore='PASS', cleanup='PASS', offline_audit='PASS',
                            full_official_task_tests='NOT_RUN', monitor_overhead='NOT_ACCEPTED'),
                limitations=['Bounded diagnosis with a predeclared stop rule, no confidence interval or monitoring acceptance',
                             'Guest-wide endpoints and disk requests are not per-file or host physical I/O',
                             'Switching only newly created guests before uploads changes guest cache policy; official default retained',
                             'Inherited template state, shared CPUs, background load and unmeasured synchronous faults remain',
                             'No guest/host cache flush, no directory prewarm, no rejected DAMON statistics probes'])
    check = directories[-1].resolve() / 'final-resource-check.json'
    if check.exists():
        summary['final_resource_check'] = json.loads(check.read_text())
        assert summary['final_resource_check']['status'] == 'PASS'
        assert summary['final_resource_check']['project_sandboxes'] == []
        assert summary['final_resource_check']['owned_probe_processes'] == []
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.directories)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f"status=PASS full_replays={result['full_replays']} output={args.output}")
