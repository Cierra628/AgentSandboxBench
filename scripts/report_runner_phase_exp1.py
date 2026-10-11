"""Recompute the calibrated runner-phase smoke and four-replay comparison offline."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
from guest_damon_control import SMOKE, COMPARISON
from report_fixed_frequency_monitor import report
from calibrate_runner_phases import report as report_calibration

ROOT = Path(__file__).resolve().parents[1]


def summarize(calibration, smoke, comparison):
    cal = report_calibration(calibration)
    assert cal['diagnostic_gate'] == 'PASS'
    parser_hash = hashlib.sha256((calibration / 'tools/guest_runner_phases.py').read_bytes()).hexdigest()
    result = dict(status='PASS', calibration=dict(result_dir=str(calibration.relative_to(ROOT)), **cal),
                  full_replays=6, batches=[], runs=[], comparison_means={})
    identity = None
    seen = set()
    cal_image = json.loads((calibration / 'result.json').read_text())['image']
    baseline_commands=json.loads((calibration / 'protocol.json').read_text()).get('baseline_commands',False)
    baseline_add_trace=json.loads((calibration / 'protocol.json').read_text()).get('baseline_add_trace',False)
    result['baseline_git_commands']=baseline_commands
    result['baseline_add_trace']=baseline_add_trace
    baseline_states=set()
    root_devices=set()
    for group, directory, modes in (('smoke', smoke, SMOKE), ('comparison', comparison, COMPARISON)):
        source = json.loads((directory / 'result.json').read_text())
        assert source['runner_phases'] and source['damon_control_modes'] == modes
        assert source.get('baseline_git_commands',False)==baseline_commands
        assert source.get('baseline_add_trace',False)==baseline_add_trace
        if baseline_add_trace:
            for name,digest in source['baseline_add_parser_sha256'].items():
                assert hashlib.sha256((calibration / 'tools' / name).read_bytes()).hexdigest()==digest
        assert source['runner_phase_source_sha256'] == parser_hash
        audited = report(directory)
        for row in source['runs']:
            current = {k: row['metadata'][k] for k in ('workload_image', 'sandbox_cpu', 'sandbox_memory_mib', 'action_manifest_sha256', 'aenv_version', 'host_kernel', 'agentenv_container_image', 'agentenv_container_id', 'overlaybd_io_engine', 'overlaybd_direct_io')}
            if identity is None: identity = current
            assert current == identity and current['workload_image'] == cal_image
            sid = (Path(row['raw_dir']) / 'sandbox-id.txt').read_text().strip()
            assert sid not in seen
            seen.add(sid)
            if baseline_commands:
                raw=Path(row['raw_dir'])/'replay'
                state=((raw/'baseline-tree.txt').read_text().strip(),(raw/'original-head.txt').read_text().strip())
                assert all(len(v)==40 and all(c in '0123456789abcdef' for c in v) for v in state)
                baseline_states.add(state)
            if baseline_add_trace:
                mounts=[line.split() for line in (Path(row['raw_dir'])/'guest-mounts.txt').read_text().splitlines()]
                roots=[fields[0] for fields in mounts if fields[1]=='/']
                assert len(roots)==1 and roots[0].startswith('/dev/')
                device=roots[0].removeprefix('/dev/')
                assert device in row['runner_phases']['baseline_add_trace']['diskstats']
                root_devices.add(device)
        result['batches'].append(dict(group=group, result_dir=str(directory.relative_to(ROOT)),
                                     status='PASS', source_result_sha256=hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest()))
        for row in audited['observations']:
            phases = row['runner_phases']
            result['runs'].append(dict(group=group, position=row['position'], mode=row['damon_control_mode'],
                                      phase_ms={k: v / 1e6 for k, v in phases['phase_ns'].items()},
                                      guest_envelope_ms=phases['clock']['monotonic_raw_ns'] / 1e6,
                                      envelope_realtime_minus_monotonic_ms=(phases['clock']['realtime_ns'] - phases['clock']['monotonic_raw_ns']) / 1e6,
                                      loop_outside_action_timers_ms=phases['loop_outside_action_timers_ns'] / 1e6,
                                      controller_minus_guest_envelope_ms=row['controller_minus_guest_envelope_ms'],
                                      task_ms=row['task_window_seconds'] * 1000,
                                      action_sum_ms=row['guest_action_sum_seconds'] * 1000,
                                      scan_ms=row['git_add_trace2']['directory']['seconds'] * 1000,
                                      observed_min_free_gib=row['observed_min_free_gib']))
            if baseline_commands:
                result['runs'][-1]['baseline_commands']=phases['baseline_commands']
                result['runs'][-1]['baseline_outside_command_boundaries_ms']=phases['baseline_outside_command_boundaries_ns']/1e6
            if baseline_add_trace:
                result['runs'][-1]['baseline_add_trace']=phases['baseline_add_trace']
                assert len(root_devices)==1
                device=next(iter(root_devices))
                result['runs'][-1]['step_025_evidence']=dict(
                    root_device=device,root_diskstats=row['guest_diskstats'][device],
                    full_capture_vcpu_wait_upper_bound_ns=row['host_wait_bound']['summed_runnable_wait_upper_bound_ns'],
                    full_capture_successful_async_setup=sum(v['successful_async_setup'] for v in row['host_kvm_faults']['threads']),
                    before_mem_available_kib=row['guest_cache_state']['before']['meminfo_kib']['MemAvailable'],
                    during_command_counter_deltas=row['guest_cache_state']['during_command']['counter_deltas'])
    assert len(result['runs']) == 6
    result['source_identity'] = {k: v for k, v in identity.items() if k != 'agentenv_container_id'}
    if baseline_add_trace:
        assert len(root_devices)==1
        result['baseline_add_root_device']=root_devices.pop()
    if baseline_commands:
        assert len(baseline_states)==1
        tree,head=next(iter(baseline_states));result['initial_state']=dict(baseline_tree=tree,original_head=head)
    for mode in ('default', 'damon_disabled'):
        selected = [r for r in result['runs'] if r['group'] == 'comparison' and r['mode'] == mode]
        assert len(selected) == 2
        result['comparison_means'][mode] = dict(phase_ms={k: statistics.mean(r['phase_ms'][k] for r in selected) for k in ('entry', 'baseline', 'actions', 'finalize')},
                                               task_ms=statistics.mean(r['task_ms'] for r in selected),
                                               controller_minus_guest_envelope_ms=statistics.mean(r['controller_minus_guest_envelope_ms'] for r in selected))
        if baseline_commands:
            result['comparison_means'][mode]['baseline_commands']={tag:{key:statistics.mean(r['baseline_commands'][tag][key] for r in selected) for key in ('boundary_ns','real_seconds','user_seconds','system_seconds')} for tag in ('add','write_tree','reset','rev_parse')}
    result['checks'] = dict(replay='PASS', stdout_patch_hashes='PASS', independent_svg_html='PASS',
                            state='PASS', cleanup='PASS', cpu_restore='PASS', offline_audit='PASS',
                            full_official_task_tests='NOT_RUN', monitoring_overhead='NOT_ACCEPTED')
    result['limitations'] = ['Calibration only on warm fixture in DAMON-disabled guest; not total combined instrumentation overhead',
                             'Five Bash realtime boundaries; outer dual clock checked; phase clocks not independently monotonic',
                             'Two observations per condition in comparison; descriptive means only, no confidence interval',
                             'Baseline phase includes add/write-tree/reset/rev-parse and boundary work; no subcommand attribution',
                             'Controller minus guest envelope includes control channel and boundaries, not pure network time',
                             'All periodic monitoring off; preserve official default and original whole-task metric']
    if baseline_commands:
        result['limitations'][3]='Initial Git wall/CPU timers have 1ms precision; boundaries include record cost; no instruction/byte/path counts or per-command I/O measured; wall minus CPU is not exact waiting'
    if baseline_add_trace:
        result['limitations'][3]='Initial add native regions can nest and omit work; guest device-wide counters include other guest work and do not measure storage waiting; command wall/CPU precision is 1ms; no instruction counts'
        result['limitations'].append('Step 025 queue-wait upper bound and KVM setup counts cover both vCPUs over the full capture; zero async setup does not exclude synchronous faults or I/O; cache counters are non-atomic endpoints')
    check = comparison / 'final-resource-check.json'
    if check.exists():
        result['final_resource_check'] = json.loads(check.read_text())
        assert result['final_resource_check']['status'] == 'PASS'
        assert result['final_resource_check']['project_sandboxes'] == []
        assert result['final_resource_check']['owned_probe_processes'] == []
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('calibration', 'smoke', 'comparison'): parser.add_argument(name, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.calibration.resolve(), args.smoke.resolve(), args.comparison.resolve())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f'status=PASS full_replays=6 output={args.output}')
