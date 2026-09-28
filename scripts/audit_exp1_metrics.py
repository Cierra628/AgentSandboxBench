"""Audit existing Exp1 correctness and measured metrics without running sandboxes."""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MISSING = 'NOT_MEASURED'
EXPECTED_STEPS = [f'{i:03d}' for i in range(1, 27)]
EXPECTED_FAILURES = {'008': 127, '016': 127}


def sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def tsv(path):
    if not path.exists():
        return []
    with path.open(newline='') as source:
        return list(csv.DictReader(source, delimiter='\t'))


def observed_samples(raw, backend):
    guest = tsv(raw / 'replay/guest-memory.tsv')
    host = tsv(raw / 'host/memory-samples.tsv')
    guest_metrics = dict(sample_count=len(guest), observed_peak_used_estimate_kib=MISSING,
                         observed_peak_cached_kib=MISSING, observed_peak_anon_pages_kib=MISSING,
                         observed_peak_slab_kib=MISSING)
    if guest:
        guest_metrics.update(observed_peak_used_estimate_kib=max(
            int(row['MemTotal_kib']) - int(row['MemAvailable_kib']) for row in guest),
            observed_peak_cached_kib=max(int(row['Cached_kib']) for row in guest),
            observed_peak_anon_pages_kib=max(int(row['AnonPages_kib']) for row in guest),
            observed_peak_slab_kib=max(int(row['Slab_kib']) for row in guest))
    host_scope = ('selected AgentENV service cgroup; may include pool/shared overhead'
                  if backend == 'agentenv' else 'run-owned TrEnv-X sandbox parent cgroup; excludes orchestrator')
    host_metrics = dict(sample_count=len(host), scope=host_scope,
                        observed_peak_current_bytes=MISSING,
                        observed_peak_file_bytes=MISSING,
                        observed_peak_anon_bytes=MISSING,
                        observed_peak_vmm_pss_kib=MISSING)
    if host:
        for key, column in [('observed_peak_current_bytes', 'cgroup_current_bytes'),
                            ('observed_peak_file_bytes', 'cgroup_file_bytes'),
                            ('observed_peak_anon_bytes', 'cgroup_anon_bytes')]:
            host_metrics[key] = max(int(row[column]) for row in host)
        if any(int(row.get('firecracker_pss_kib', 0)) for row in host):
            host_metrics['observed_peak_vmm_pss_kib'] = max(
                int(row.get('firecracker_pss_kib', 0)) for row in host)
            host_metrics['vmm_scope'] = 'selected Firecracker in service cgroup; may include pool'
    return guest_metrics, host_metrics


def audit_run(item):
    backend, mode = item['backend'], item['mode']
    assert backend in ('agentenv', 'trenvx')
    assert mode in ('full', 'full-light', 'checkpoint-013', 'checkpoint-022',
                    'full-diagnostic', 'checkpoint-022-diagnostic')
    raw = ROOT / item['raw']
    audit = ROOT / item['audit'] if backend == 'agentenv' else raw
    result = json.loads((audit / 'result.json').read_text())
    assert result['status'] == 'PASS'
    if backend == 'trenvx':
        assert result['full_trajectory_verified'] is True
    replay = raw / 'replay'
    steps = tsv(replay / 'steps.tsv')
    assert [row['step'] for row in steps] == EXPECTED_STEPS
    failures = {row['step']: int(row['exit_code']) for row in steps if int(row['exit_code'])}
    assert failures == EXPECTED_FAILURES
    guest, host = observed_samples(raw, backend)
    timings = dict(guest_runner_action_sum_ns=sum(int(row['duration_ns']) for row in steps),
                   controller_runner_call_ns=MISSING, checkpoint_call_ns=MISSING,
                   restore_call_ns=MISSING, create_to_tool_ready_ns=MISSING,
                   controller_end_to_end_ns=result.get('controller_elapsed_ns', MISSING))
    if backend == 'agentenv':
        events_path = audit / 'events.json'
        if events_path.exists():
            events = {row['stage']: int(row['duration_ns'])
                      for row in json.loads(events_path.read_text()) if row['status'] == 'PASS'}
            if 'checkpoint' in events:
                timings['checkpoint_call_ns'] = events['checkpoint']
            if 'restore' in events:
                timings['restore_call_ns'] = events['restore']
            if 'cold-start' in events and 'initial-ready' in events:
                timings['create_to_tool_ready_ns'] = events['cold-start'] + events['initial-ready']
            segments = [key for key in events if key.startswith('actions-')]
            if segments:
                timings['controller_runner_call_ns'] = sum(events[key] for key in segments)
        effective = json.loads((audit / 'effective-config.json').read_text())
        cache_policy = effective['cache_policy']
        snapshot_semantics = ('complete VM memory and filesystem snapshot' if mode != 'full'
                              else 'not applicable')
        image_provenance = effective.get('server_image_id', effective.get('image', MISSING))
    else:
        event_rows = tsv(raw / 'controller/events.tsv')
        for row in event_rows:
            if row['event'] == 'checkpoint':
                timings['checkpoint_call_ns'] = int(row['latency_ns'])
            if row['event'] == 'start':
                timings['restore_call_ns'] = int(row['latency_ns'])
        labels = (['replay-all'] if mode.startswith('full')
                  else ['replay-prefix', 'replay-continuation'])
        calls = [json.loads((raw / f'{label}.json').read_text()) for label in labels]
        assert all(call['exit_code'] == 0 for call in calls)
        timings['controller_runner_call_ns'] = sum(call['controller_elapsed_ns'] for call in calls)
        effective = json.loads((raw / 'replay-config.json').read_text())
        cache_policy = effective['cache_policy']
        snapshot_semantics = ('file-state-only' if mode.startswith('checkpoint-')
                              else 'not applicable')
        image_provenance = 'existing local seed sha256:' + json.loads(
            (raw / 'config.json').read_text())['seed_sha256']
        if mode == 'full-light' or mode.endswith('-diagnostic'):
            assert result['checks']['host_light_sampling'] == 'PASS'
            assert json.loads((raw / 'host-sampling.json').read_text())['sample_count'] == len(
                tsv(raw / 'host/memory-samples.tsv'))
        if mode.endswith('-diagnostic'):
            assert json.loads((raw/'config.json').read_text())['diagnostic_metrics'] is True
            events=[json.loads(line) for line in (raw/'diagnostic-events.jsonl').read_text().splitlines()]
            assert all(event['status']=='PASS' for event in events)
            by_phase={event['phase']:event['elapsed_ns'] for event in events}
            timings['sdk_create_ns']=by_phase['sdk-create']
            timings['create_to_first_tool_success_ns']=by_phase['create-to-first-tool-success']
            if mode.startswith('checkpoint'):
                timings['restore_to_first_tool_success_ns']=by_phase['restore-to-first-tool-success']
                timings['parent_delete_ns']=by_phase['parent-delete']
            observations=[]
            for path in sorted(raw.glob('vmm-*.json')):
                row=json.loads(path.read_text())
                assert row['status']=='PASS' and row['cgroup_membership_verified'] is True
                assert 'Pss' in row['memory_bytes']
                observations.append(dict(label=path.stem,pss_bytes=row['memory_bytes']['Pss'],
                    scan_elapsed_ns=row['scan_elapsed_ns'],sandbox_id=row['sandbox_id'],scope=row['scope']))
            assert len(observations)==(4 if mode.startswith('checkpoint') else 2)
            host['vmm_boundary_observations']=observations
    return dict(run_id=raw.name, backend=backend, mode=mode, status='PASS',
        raw_dir=str(raw), action_manifest_sha256=sha(replay / 'actions.tsv'),
        original_head_sha256=sha(replay / 'original-head.txt'),
        final_patch_sha256=sha(replay / 'patch.diff'),
        action_count=len(steps), expected_failures=failures,
        cwd='/testbed', session_semantics='one bash per frozen action',
        cache_policy=cache_policy, image_provenance=image_provenance,
        restore_semantics=snapshot_semantics, timing_ns=timings,
        guest_memory=guest, host_memory=host,
        file_content_physical_bytes=MISSING,
        performance_comparison='NOT_VALIDATED')


def main():
    config_path = ROOT / (sys.argv[1] if len(sys.argv) > 1
                          else 'configs/exp1-metrics-audit.json')
    config = json.loads(config_path.read_text())
    assert len(config['runs']) >= 2
    runs = [audit_run(item) for item in config['runs']]
    for key in ('action_manifest_sha256', 'original_head_sha256', 'final_patch_sha256'):
        assert len({run[key] for run in runs}) == 1, key
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}'
    out = ROOT / config['output_root'] / run_id
    os.umask(0o077)
    out.mkdir(parents=True, exist_ok=False)
    result = dict(status='PASS', run_id=run_id, audit_scope='existing raw data only',
        input_config_sha256=sha(config_path), script_sha256=sha(Path(__file__)),
        correctness_cross_backend='PASS: manifest, source HEAD, final patch, order, failures',
        cross_backend_memory_performance='NOT_VALIDATED',
        limitations=['guest runner action sums include shell/timeout wrapper and differ with sampling mode',
            'host cgroup samples are service-wide; VMM PSS is a separate metric',
            'observed sample maxima are not instantaneous peaks',
            'older TrEnv-X correctness runs disabled guest sampling and have no host samples',
            'backend checkpoint semantics and image provenance differ; timings are not causal A/B results'],
        runs=runs)
    lines = ['# Exp1 existing-data metrics audit', '',
             f'Correctness hashes and action order match across {len(runs)} successful runs. '
             'Timing and memory values below retain their original scope; this is not a performance comparison.', '',
             '| Backend / mode | Guest action sum (ms) | Controller runner call (ms) | Guest samples | Host samples |',
             '| --- | ---: | ---: | ---: | ---: |']
    for row in runs:
        t = row['timing_ns']
        call = t['controller_runner_call_ns']
        call_display = f'{call/1e6:.1f}' if isinstance(call, int) else call
        lines.append(f"| {row['backend']} / {row['mode']} | "
            f"{t['guest_runner_action_sum_ns']/1e6:.1f} | "
            f"{call_display} | {row['guest_memory']['sample_count']} | "
            f"{row['host_memory']['sample_count']} |")
    lines += ['', 'Guest used estimate, Cached, AnonPages and Slab are guest-only; host cgroup '
              'current/file/anon retain each run’s recorded service scope; VMM PSS is separate. File-content physical '
              'residency is unmeasured in these task runs.', '',
              'Older TrEnv-X guest-memory.tsv files contain only a header and host samples are absent. '
              'New diagnostic runs additionally record first successful tool return and separate boundary VMM scans; '
              'these do not measure exact earliest readiness or complete end-to-end overhead.',
              'AgentENV and TrEnv-X checkpoint semantics and image provenance differ. '
              'The observed action sums and call times cannot be used as speedup estimates.', '']
    (out / 'report.md').write_text('\n'.join(lines))
    (out / 'effective-config.json').write_text(json.dumps(config, indent=2) + '\n')
    (out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'status=PASS result_dir={out}')


if __name__ == '__main__':
    main()
