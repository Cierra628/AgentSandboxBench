"""Audit existing Exp1 correctness and measured metrics without running sandboxes."""
import csv
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MISSING = 'NOT_MEASURED'
EXPECTED_STEPS = [f'{i:03d}' for i in range(1, 27)]
EXPECTED_FAILURES = {'008': 127, '016': 127}
KEY_OUTPUT = '    document.addEventListener("DOMContentLoaded", () => {'


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
                         observed_peak_slab_kib=MISSING,
                         sampling_mode='periodic' if guest else MISSING,
                         boundary_observations=[])
    if guest:
        guest_metrics.update(observed_peak_used_estimate_kib=max(
            int(row['MemTotal_kib']) - int(row['MemAvailable_kib']) for row in guest),
            observed_peak_cached_kib=max(int(row['Cached_kib']) for row in guest),
            observed_peak_anon_pages_kib=max(int(row['AnonPages_kib']) for row in guest),
            observed_peak_slab_kib=max(int(row['Slab_kib']) for row in guest))
    boundaries=[]
    for path in sorted(raw.glob('guest-memory-*.json')):
        row=json.loads(path.read_text())
        if 'meminfo_bytes' not in row:  # Older files held tool output, not parsed values.
            continue
        assert row['status']=='PASS' and row['page_size']==4096
        boundaries.append(dict(label=path.stem.removeprefix('guest-memory-'),
                               timestamp_ns=row['timestamp_ns'],scope=row['scope'],
                               meminfo_bytes=row['meminfo_bytes']))
    guest_metrics['boundary_observations']=boundaries
    if boundaries and not guest:
        values=[row['meminfo_bytes'] for row in boundaries]
        guest_metrics.update(sample_count=len(values),sampling_mode='controller_boundary',
            observed_peak_used_estimate_kib=max((v['MemTotal']-v['MemAvailable'])//1024 for v in values),
            observed_peak_cached_kib=max(v['Cached']//1024 for v in values),
            observed_peak_anon_pages_kib=max(v['AnonPages']//1024 for v in values),
            observed_peak_slab_kib=max(v['Slab']//1024 for v in values))
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


def measured(value, source, boundary):
    return dict(status='MEASURED' if value is not None else MISSING,
                duration_ns=value, source=source if value is not None else None,
                boundary=boundary)


def check_final_result(raw, audit, result, backend, mode):
    """Recheck the final artifacts and distinguish independent tests from replay checks."""
    checks = json.loads((audit/'checks.json').read_text()) if backend == 'agentenv' else result['checks']
    expected_check = 'replay_correctness' if backend == 'agentenv' else 'exp1_replay_correctness'
    assert checks.get(expected_check) == 'PASS'
    assert KEY_OUTPUT in (raw/'replay/stdout/024.log').read_text()
    correctness_path = audit/'correctness.json'
    correctness = json.loads(correctness_path.read_text()) if correctness_path.exists() else {}
    patch_digest = sha(raw/'replay/patch.diff')
    if correctness.get('patch_sha256'):
        assert correctness['patch_sha256'] == patch_digest
    if correctness.get('expected_failures'):
        assert correctness['expected_failures'] == EXPECTED_FAILURES
    if correctness.get('action_order'):
        assert correctness['action_order'] == 'PASS'
    if backend == 'agentenv':
        assert checks.get('input_hashes') == 'PASS'
        independent = checks.get('independent_svg_properties',
                                 correctness.get('independent_task_tests', 'NOT_RUN'))
        if independent == 'PASS':
            checked = json.loads((raw/correctness['independent_result_file']).read_text()) if correctness.get('independent_result_file') else json.loads((audit/'independent-svg.stdout').read_text())
            assert set(checked) == {'svg', 'nested_svg', 'html_control'}
            assert all(checked[name]['javascript_formatted'] and checked[name]['idempotent']
                       for name in checked)
            if not correctness.get('independent_result_file'):
                assert any(e['stage']=='independent-svg' and e['status']=='PASS'
                           for e in json.loads((audit/'events.json').read_text()))
        if mode != 'full':
            assert checks.get('checkpoint_interface') == 'PASS'
            assert checks.get('process_continuity') == 'PASS'
            assert checks.get('patch_matches_full_replay') == 'PASS'
            assert (audit/'process-continuity.json').is_file()
            if (audit/'restored-boundary.json').exists():
                from exp1_inputs import verify_boundary
                boundary = json.loads((audit/'restored-boundary.json').read_text())
                assert checks.get('restored_progress_and_files') == 'PASS'
                assert verify_boundary(boundary['before'], boundary['after'], mode.split('-')[1]) == boundary
        recovery = dict(interface='PASS', file_state='PASS', process_state='PASS') if mode != 'full' else dict(
            interface='NOT_APPLICABLE', file_state='NOT_APPLICABLE', process_state='NOT_APPLICABLE')
    else:
        assert checks.get('frozen_input_hashes') == 'PASS'
        assert correctness.get('input_hashes') == 'PASS'
        assert correctness.get('action_024_oracle') == 'PASS'
        assert correctness.get('patch_sha256') == patch_digest
        independent = correctness.get('independent_svg_properties', 'NOT_RUN')
        if independent == 'PASS':
            before=json.loads((raw/'independent-before.json').read_text())
            after=json.loads((raw/'independent-after.json').read_text())
            assert before['exit_code']==1 and after['exit_code']==0
            baseline=json.loads(before['stdout']); checked=json.loads(after['stdout'])
            assert baseline['svg']['javascript_formatted'] is False
            assert set(checked)=={'svg','nested_svg','html_control'}
            assert all(checked[name]['javascript_formatted'] and checked[name]['idempotent']
                       for name in checked)
        if mode.startswith('checkpoint-'):
            step = mode.split('-')[1]
            assert checks.get('checkpoint_after_'+step) == 'PASS'
            assert checks.get('file_restore_interface') == 'PASS'
            assert checks.get('prefix_state_preserved') == 'PASS'
            if step == '022':
                assert checks.get('modified_source_preserved') == 'PASS'
        recovery = dict(interface='PASS', file_state='PASS', process_state='NOT_APPLICABLE') if mode.startswith(
            'checkpoint-') else dict(interface='NOT_APPLICABLE', file_state='NOT_APPLICABLE',
                                      process_state='NOT_APPLICABLE')
    assert independent in ('PASS', 'NOT_RUN')
    official_task = 'NOT_RUN'
    official_path = audit/'official-task-result.json'
    if checks.get('official_task_tests') == 'PASS':
        official = json.loads(official_path.read_text())
        assert official['status'] == 'PASS' and official['phase'] == 'replayed_patch' and official['exit_code'] == 0
        assert official['F2P'] and set(official['F2P']+official['P2P']) <= set(official['parser_results']['passed_tests'])
        assert not official['parser_results']['failed_tests']
        official_task = 'PASS'
    return dict(status='PASS', action_order='PASS', expected_failures='PASS',
                key_output_024='PASS', final_patch='PASS',
                independent_svg=independent,
                official_task_tests=official_task,
                recovery=recovery,
                official_full_tests=correctness.get('official_full_tests', 'NOT_RUN'),
                patch_sha256=patch_digest,
                scope='frozen replay and task-specific checks; patch equality does not prove full upstream tests')


def check_cleanup(raw, audit, result, backend, mode):
    if backend == 'agentenv':
        checks = json.loads((audit/'checks.json').read_text())
        assert checks.get('cleanup') == 'PASS'
        log = (raw/'cleanup.log').read_text()
        assert 'deleted=' in log or 'Deleted sandbox ' in log
        if mode != 'full':
            assert 'deleted_snapshot=' in log
            assert json.loads((audit/'list-.stdout').read_text()) == []
            assert json.loads((audit/'list-template.stdout').read_text()) == []
            events = json.loads((audit/'events.json').read_text())
            for stage in ('delete-original', 'delete-restored', 'delete-snapshot'):
                assert any(e['stage'] == stage and e['status'] == 'PASS' for e in events)
        return dict(status='PASS', sandbox='PASS', service='RETAINED_SHARED_SERVICE',
                    cgroup_and_loop=MISSING, archived_images='NOT_APPLICABLE',
                    evidence=('checks.json and CLI deletion receipts; cleanup.log reconstructed from audit'
                              if 'reconstructed_after_run=1' in log else
                              'checks.json and live CLI deletion receipts'))
    checks = result['checks']; host = result['host_cleanup']
    assert checks.get('sdk_cleanup') == checks.get('service_shutdown') == 'PASS'
    assert host['cgroup_removed'] is True and host['loop_query_exit_code'] == 0
    assert host['owned_loop_backings'] == []
    archive = raw/'data.tar.gz'
    removal = raw/'archive-removal.json'
    manifest = raw/'archive-manifest.json'
    image_cleanup = raw/'image-cleanup.json'
    if image_cleanup.is_file():
        saved = json.loads(image_cleanup.read_text())
        cfg = json.loads((raw/'config.json').read_text())
        assert saved['run_id'] == raw.name and saved['source_removed']
        if saved['mode']=='verified-checkpoint-control-images-discarded':
            assert cfg['discard_verified_checkpoint_images'] and cfg['checkpoint_after']=='022'
            assert cfg['monitoring_control'] in ('off','on')
            assert all(result['checks'][key]=='PASS' for key in ('checkpoint_after_022','file_restore_interface',
                'modified_source_preserved','prefix_state_preserved','official_task_tests'))
            assert json.loads((raw/'checkpoint.json').read_text())['next_step']=='023'
        else:
            assert saved['mode']=='successful-exp1-images-discarded'
            assert cfg['discard_success_images'] and cfg['scenario'] == 'exp1' and cfg['checkpoint_after'] is None
        assert result['checks']['successful_image_cleanup'] == 'PASS'
        assert saved['entries'] and all(len(row['sha256']) == 64 for row in saved['entries'] if row['kind'] == 'file')
        assert not archive.exists() and not manifest.exists()
        image_state = 'SUCCESS_IMAGES_REMOVED; RAW_AND_HASHES_RETAINED'
    elif archive.is_file():
        saved = json.loads(manifest.read_text())
        if result.get('data_retention'):
            assert saved['archive_sha256'] == result['data_retention']['archive_sha256']
        assert saved['gzip_test'] == saved['tar_compare'] == 'PASS' and saved['source_removed']
        assert archive.stat().st_size == saved['archive_bytes']
        image_state = 'RETAINED_WITH_VERIFIED_MANIFEST'
    elif removal.is_file():
        removed = json.loads(removal.read_text())
        assert removed['archive_sha256'] == json.loads(manifest.read_text())['archive_sha256']
        image_state = 'REMOVED_AFTER_VERIFICATION'
    else:
        raise AssertionError('TrEnv-X image retention has no verified record')
    assert not (raw/'data').exists()
    return dict(status='PASS', sandbox='PASS', service='PASS', cgroup_and_loop='PASS',
                archived_images=image_state,
                evidence='run checks, cgroup/loop receipt, archive manifest and current retention state')


def phase_breakdown(raw, audit, backend, mode, timings):
    phases = {}
    def add(name, value, source, boundary):
        phases[name] = measured(value, source, boundary)
    add('guest_action_sum', timings['guest_runner_action_sum_ns'], 'replay/steps.tsv',
        'sum of 26 guest runner action durations; excludes controller and setup')
    call = timings['controller_runner_call_ns']
    add('controller_runner_calls', call if isinstance(call, int) else None,
        ('audit/events.json' if (audit/'events.json').exists() else 'controller-exec-duration-ns.txt') if backend == 'agentenv' else 'replay-*.json',
        'sum of controller calls that launch frozen runner segments')
    if backend == 'agentenv':
        event_file = audit/'events.json'
        events = {e['stage']:int(e['duration_ns']) for e in json.loads(event_file.read_text())
                  if e['status']=='PASS'} if event_file.exists() else {}
        add('create_to_first_success', events['cold-start']+events['initial-ready']
            if {'cold-start','initial-ready'} <= events.keys() else None,
            'audit/events.json', 'cold start call through first successful true command')
        add('checkpoint', events.get('checkpoint'), 'audit/events.json', 'snapshot create CLI call')
        add('restore_interface', events.get('restore'), 'audit/events.json', 'snapshot start CLI call')
        add('restore_to_first_success', events['restore']+events['restored-ready']
            if {'restore','restored-ready'} <= events.keys() else None,
            'audit/events.json', 'snapshot start call through first successful true command')
        add('template_build', None, None, 'not separately recorded')
        add('parent_delete', events.get('delete-original'), 'audit/events.json', 'delete original sandbox CLI call')
    else:
        path=raw/'diagnostic-events.jsonl'
        events={e['phase']:int(e['elapsed_ns']) for line in path.read_text().splitlines()
                if (e:=json.loads(line))['status']=='PASS'} if path.exists() else {}
        controller=tsv(raw/'controller/events.tsv')
        checkpoint=next((int(e['latency_ns']) for e in controller if e['event']=='checkpoint'),None)
        restore=next((int(e['latency_ns']) for e in controller if e['event']=='start'),None)
        add('create_to_first_success',events.get('create-to-first-tool-success'),
            'diagnostic-events.jsonl','SDK create through first successful mkdir command')
        add('checkpoint',events.get('checkpoint',checkpoint),
            'diagnostic-events.jsonl' if 'checkpoint' in events else 'controller/events.tsv',
            'controller file capture, sealing and template construction')
        add('restore_interface',events.get('restore-interface',restore),
            'diagnostic-events.jsonl' if 'restore-interface' in events else 'controller/events.tsv',
            'controller start of child VM from sealed file layer')
        add('restore_to_first_success',events.get('restore-to-first-tool-success'),
            'diagnostic-events.jsonl','start restored VM through first successful environment command')
        add('template_build',events.get('template-build'),'diagnostic-events.jsonl',
            'prepare private template before trajectory')
        add('parent_delete',events.get('parent-delete'),'diagnostic-events.jsonl',
            'delete parent VM after checkpoint')
    full = timings['controller_end_to_end_ns']
    add('full_run_including_cleanup',full if isinstance(full,int) else None,
        'audit/result.json' if backend=='agentenv' else 'result.json',
        ('controller start through sandbox cleanup and on-run report' if backend=='agentenv' else
         'outer start through sandbox cleanup and verified image archiving when enabled'))
    return phases


def audit_run(item):
    backend, mode = item['backend'], item['mode']
    assert backend in ('agentenv', 'trenvx')
    assert mode in ('full', 'full-light', 'checkpoint-013', 'checkpoint-022',
                    'full-diagnostic', 'checkpoint-022-diagnostic') or (
                        backend == 'agentenv' and re.fullmatch(r'checkpoint-0(0[1-9]|1[0-9]|2[0-5])', mode))
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
        if timings['controller_runner_call_ns'] == MISSING and (raw/'controller-exec-duration-ns.txt').exists():
            timings['controller_runner_call_ns'] = int((raw/'controller-exec-duration-ns.txt').read_text())
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
            interval = json.loads((raw/'config.json').read_text())['host_sample_interval_seconds']
            if mode == 'full-light':
                assert interval > 0
            if interval > 0:
                assert result['checks']['host_light_sampling'] == 'PASS'
                assert json.loads((raw / 'host-sampling.json').read_text())['sample_count'] == len(
                    tsv(raw / 'host/memory-samples.tsv'))
            else:
                assert result['checks'].get('host_light_sampling') is None
                assert not (raw/'host-sampling.json').exists() and not (raw/'host/memory-samples.tsv').exists()
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
    final_checks = check_final_result(raw, audit, result, backend, mode)
    cleanup = check_cleanup(raw, audit, result, backend, mode)
    phases = phase_breakdown(raw, audit, backend, mode, timings)
    named_file = dict(status=MISSING, bytes=None, scope='no named-file mapping measurement')
    measured_file = raw/'file-physical/after-continuation'
    if measured_file.is_dir():
        from measure_file_physical import audit_run as audit_file
        file_audit = audit_file(measured_file)
        assert file_audit['measurement_status'] == 'PASS'
        counted = file_audit['content_accounting']
        named_file = dict(status='MEASURED', bytes=counted['attributed_physical_bytes'],
            file_pages=counted['file_cache_unique_pages'],
            verified_anonymous_pages=counted['verified_anonymous_unique_pages'],
            unknown_pages=counted['unknown_unique_pages'],
            scope='one declared sealed source file, resident mapped pages only; not whole task')
        config = json.loads((raw/'config.json').read_text())
        if config.get('file_physical_manifest'):
            path = raw/config['file_physical_manifest']
            assert sha(path) == config['file_physical_manifest_sha256']
            declared = json.loads(path.read_text())['files']
            manifest = json.loads((measured_file/'manifest.json').read_text())
            owners = [row['owner'] for vm in manifest['vms'] for row in vm['files']]
            assert owners == [row['path'] for row in declared]
            named_file.update(files=declared,
                scope='explicit named files in sealed checkpoint and base; resident mapped pages only; not whole task')
    return dict(run_id=raw.name, backend=backend, mode=mode, status='PASS',
        raw_dir=str(raw), action_manifest_sha256=sha(replay / 'actions.tsv'),
        original_head_sha256=sha(replay / 'original-head.txt'),
        final_patch_sha256=sha(replay / 'patch.diff'),
        action_count=len(steps), expected_failures=failures,
        cwd='/testbed', session_semantics='one bash per frozen action',
        cache_policy=cache_policy, image_provenance=image_provenance,
        restore_semantics=snapshot_semantics, timing_ns=timings,
        guest_memory=guest, host_memory=host,
        final_checks=final_checks, cleanup=cleanup, phase_breakdown=phases,
        named_file_mapping=named_file,
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
    result = dict(status='PASS', schema_version=3, run_id=run_id, audit_scope='existing raw data only',
        input_config_sha256=sha(config_path), script_sha256=sha(Path(__file__)),
        correctness_cross_backend='PASS: manifest, source HEAD, final patch, order, failures, key output',
        cross_backend_memory_performance='NOT_VALIDATED',
        limitations=['guest runner action sums include shell/timeout wrapper and differ with sampling mode',
            'host cgroup samples are service-wide; VMM PSS is a separate metric',
            'observed sample maxima are not instantaneous peaks',
            'older TrEnv-X correctness runs disabled guest sampling and have no host samples',
            'AgentENV historical full run lacks complete phase timing; missing is not zero',
            'AgentENV first-tool probe is true; TrEnv-X first-tool probe is mkdir/environment',
            'TrEnv-X named-file observation covers one source file, not total task file content',
            'backend checkpoint semantics and image provenance differ; timings are not causal A/B results'],
        runs=runs)
    lines = ['# Exp1 两后端基础指标审计', '',
             f'从已有原始数据复核 {len(runs)} 次成功运行：动作顺序、预期失败、关键输出、最终补丁和清理。'
             '每个阶段的测量边界见 `report.json`；不同后端的耗时不能直接作性能对照。', '',
             '| 后端 / 模式 | 最终重放 | 独立 SVG 检查 | 清理 | 来宾动作和 (ms) | 控制器调用和 (ms) | 保存 (ms) | 恢复至首个工具成功 (ms) | 来宾 / 宿主机样本 |',
             '| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    def milliseconds(metric):
        return f"{metric['duration_ns']/1e6:.1f}" if metric['status']=='MEASURED' else MISSING
    for row in runs:
        phases = row['phase_breakdown']
        lines.append(f"| {row['backend']} / {row['mode']} | "
            f"{row['final_checks']['status']} | {row['final_checks']['independent_svg']} | "
            f"{row['cleanup']['status']} | {milliseconds(phases['guest_action_sum'])} | "
            f"{milliseconds(phases['controller_runner_calls'])} | {milliseconds(phases['checkpoint'])} | "
            f"{milliseconds(phases['restore_to_first_success'])} | "
            f"{row['guest_memory']['sample_count']} / {row['host_memory']['sample_count']} |")
    latest = [max((r for r in runs if r['backend']==backend and r['mode'].startswith('checkpoint-')),
                  key=lambda r:r['run_id']) for backend in ('agentenv','trenvx')]
    lines += ['', '## 两种后端最近一次恢复运行', '',
              '| 后端 / 运行编号 | 创建至首个工具成功 | 保存 | 恢复接口 | 恢复至首个工具成功 | 包含清理的本次总耗时 | 运行资源清理 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for row in latest:
        p=row['phase_breakdown']
        lines.append(f"| {row['backend']} / {row['run_id']} | {milliseconds(p['create_to_first_success'])} | "
            f"{milliseconds(p['checkpoint'])} | {milliseconds(p['restore_interface'])} | "
            f"{milliseconds(p['restore_to_first_success'])} | "
            f"{milliseconds(p['full_run_including_cleanup'])} | {row['cleanup']['status']} |")
    lines += ['', '表中时间单位均为毫秒。AgentENV 首个工具检查执行 `true`；TrEnv-X 创建后执行 `mkdir`、恢复后读取环境。'
              'AgentENV 总耗时包含运行时报告，TrEnv-X 总耗时包含镜像归档与校验，不能直接相减作速度结论。', '',
              '来宾内存、宿主机资源组内存、VMM PSS 和文件内容物理页分别报告，不能相加。'
              'AgentENV 的宿主机样本来自服务资源组，TrEnv-X 的样本来自本次沙箱的父资源组。'
              '较早的 TrEnv-X 运行没有来宾样本；新诊断使用阶段边界采样，AgentENV 使用周期采样，样本峰值不能直接对照。'
              '文件页结果只覆盖一个指定源码，不代表全任务文件内容。', '',
              '`NOT_MEASURED` 表示未测量，不能当作零；`NOT_RUN` 表示未执行独立或官方完整测试。'
              '归档已删除的运行仅保留轨迹、配置、样本与清理记录。'
              '两后端镜像来源、恢复语义和缓存条件不同，本报告不估计性能收益。', '']
    (out / 'report.md').write_text('\n'.join(lines))
    (out / 'effective-config.json').write_text(json.dumps(config, indent=2) + '\n')
    (out / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'status=PASS result_dir={out}')


if __name__ == '__main__':
    main()
