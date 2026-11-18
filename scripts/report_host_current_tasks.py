"""Compare saved host-current coverage and configuration; no live queries."""
import argparse
import csv
import json
from pathlib import Path

from report_host_file import current_statistics

ROOT = Path(__file__).resolve().parents[1]
# Fixed accepted runs, not a search for whichever run produces a smaller peak.
CASES = [
    ('prettier6604', '.artifacts/platform-tasks/20261020T030036Z-3306828',
     '20261020T030201Z-3307600', 'prettier6604', None),
    ('sphinx7590', '.artifacts/platform-tasks/20261020T164427Z-3886208',
     '20261020T164558Z-3887137', 'python-backbone',
     '.artifacts/host-current-primary-20261028/final/sphinx/summary.json'),
    ('xarray6992', '.artifacts/platform-tasks/20261020T164737Z-3889772',
     '20261020T164916Z-3890897', 'python-backbone', None),
    ('prettier14400', '.artifacts/host-exp1-light/20261028T063439Z-2055110',
     '20261028T063556Z-2055755', 'exp1/raw/20261028T063603Z-2056467',
     '.artifacts/host-exp1-light/20261028T063439Z-2055110/host-report/summary.json'),
    ('grpo7_reference', '.artifacts/host-full-rootfs/20261025T165257Z-shared-1101459',
     '20261025T165417Z-1102226', 'prettier6604',
     '.artifacts/host-current-primary-20261028/final/grpo-no-pss/summary.json'),
]


def read(path):
    return json.loads(path.read_text())


def collect(case):
    name, workflow, storage_id, task_dir, saved_summary = case
    storage = ROOT / '.artifacts/storage-isolated-service' / storage_id
    source = ROOT / workflow / 'host-file'
    outer = read(storage / 'result.json')
    receipt = read(source / 'result.json')
    creation = outer['host_file_creation']
    assert receipt['status'] == outer['status'] == 'PASS'
    assert receipt['cgroup'] == creation['cgroup']
    assert creation['preexisting_firecrackers'] == 0
    assert not any(creation[k] for k in ('firecracker_pool', 'startup_prewarm', 'block_pool'))
    with (source / 'memory.tsv').open() as stream:
        rows = [{k: int(v) if v else None for k, v in row.items()}
                for row in csv.DictReader(stream, delimiter='\t')]
    assert len(rows) == receipt['samples'] >= 2
    assert all(a['monotonic_ns'] < b['monotonic_ns'] for a, b in zip(rows, rows[1:]))
    marker = creation['verified_before_guest_creation_ns']
    assert rows[0]['monotonic_ns'] <= marker <= rows[-1]['monotonic_ns']
    current = current_statistics(rows, creation)
    if saved_summary:
        assert read(ROOT / saved_summary)['current'] == current
    for key in ('observed_peak_sample', 'after_service_ready_observed_peak_sample'):
        current.pop(key)
    raw = storage / task_dir
    task = read(raw / 'result.json')
    monitor = read(raw / 'runtime-monitor.json')
    assert task['status'] == monitor['status'] == 'PASS' and monitor['profile'] == 'light'
    assert all(s['status'] == 'PASS' and s['receipt']['interval_seconds'] == .1
               for s in monitor['sessions'])
    grpo = name == 'grpo7_reference'
    baseline = creation.get('before_guest_counters')
    seed = outer['immutable_cache_seed']
    if name in ('sphinx7590', 'xarray6992'):
        # The executed cold-start helper fixes two CPUs and defaults to 4096 MiB.
        cold = (storage / 'code/agentenv_cold_start.py').read_text()
        assert 'cpuCount=2' in cold and 'memory_mib=4096' in cold
        official = {k: task['official'][k] for k in ('passed', 'failed', 'skipped')}
        actions = task['replay']['action_count']
    elif name == 'prettier14400':
        actions = 26
        audit = storage / 'exp1/audit' / raw.name
        parsed = read(audit / 'official-task-result.json')['parser_results']
        official = dict(passed=parsed['num_tests_passed'], failed=parsed['num_tests_failed'])
    else:
        cfg = read(raw / 'effective-config.json')
        assert cfg['guest_cpu'] == 2 and cfg['guest_memory_mib'] == 4096
        parsed = read(raw / ('official-result-backbone.json' if grpo else 'official-task-result.json'))['parser_results']
        official = dict(passed=parsed['num_tests_passed'], failed=parsed['num_tests_failed'])
        actions = task['actual_action_count'] if grpo else task['action_count']
    pss = receipt.get('pss_interval_seconds') is not None
    broad = outer['host_file_residency']['status'] == 'OBSERVED'
    fixed_queries = read(raw / 'host-full-rootfs.json') if grpo else None
    if grpo:
        assert all(fixed_queries[p]['status'] == 'OBSERVED' for p in ('P0', 'P1'))
    source_dir = storage / 'code'
    value = dict(name=name, task_id=task['task_id'], workflow=workflow,
        storage=str(storage.relative_to(ROOT)), raw=str(raw.relative_to(ROOT)),
        executed_code=str(source_dir.relative_to(ROOT)),
        original_workflow_status=read(ROOT / workflow / 'result.json')['status'],
        task_capture_status=task['status'], official=official,
        strict_replay_status=task.get('strict_replay_status'),
        actions=actions, guests=len(monitor['sessions']), cpu_per_guest=2, memory_mib_per_guest=4096,
        guest_samples=sum(s['receipt']['samples'] for s in monitor['sessions']),
        guest_interval_seconds=.1, host_interval_seconds=receipt['interval_seconds'],
        host_samples=len(rows), maximum_host_gap_ns=max(b['monotonic_ns']-a['monotonic_ns'] for a,b in zip(rows,rows[1:])),
        pss_interval_seconds=receipt.get('pss_interval_seconds'),
        guest_control_batching=monitor.get('control_batching', False),
        guest_collector_cpu_seconds=sum(s['receipt']['cpu_user_seconds']+s['receipt']['cpu_system_seconds'] for s in monitor['sessions']),
        host_thread_cpu_ns=receipt['thread_cpu_ns'], host_counter_read_total_ns=receipt['counter_read_total_ns'],
        mincore=dict(final_inventory_requested=broad,
            final_files=len(outer['host_file_residency'].get('files', [])) if broad else None,
            fixed_phase_queries=['P0', 'P1'] if grpo else [],
            elapsed_ns=sum(fixed_queries[p]['elapsed_ns'] for p in ('P0', 'P1')) if grpo else
                outer['host_file_residency']['end_monotonic_ns']-outer['host_file_residency']['start_monotonic_ns'] if broad else None),
        preparation=dict(source_backup=str(Path(seed['source_backup']).relative_to(ROOT)),
            seed_task_id=seed['task_id'], ram_allocated_bytes=seed['ram_allocated_bytes'],
            volume_bytes=outer['capacity_bytes'], readonly_commits_bytes=outer.get('readonly_commits_bytes'),
            explicit_prebaseline_sync=outer.get('host_shared_preparation_sync', {}).get('status') == 'PASS',
            before_guest_file_dirty_bytes=baseline.get('file_dirty') if baseline else None,
            actual_io_state_recorded='io_controller_state' in creation),
        reclaim_policy='AgentENV defaults retained; no separate reclaim-counter observation',
        current=current,
        display='absolute current only; exact baseline absent' if baseline is None else 'absolute current and exact-baseline increase',
        performance_comparison='not a sampling-toggle pair',
        window='Input preparation to host collector stop; after-marker window also includes control, checks and cleanup',
        extra_queries_in_window=pss or broad or grpo)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    values = [collect(case) for case in CASES]
    matches = {}; missing = {}
    for filename in ('host_file_monitor.py', 'sandbox_runtime_monitor.py', 'agentenv_cold_start.py'):
        groups = []
        for value in values:
            path = ROOT / value['executed_code'] / filename
            if not path.exists():
                missing.setdefault(filename, []).append(value['name'])
                continue
            data = path.read_bytes()
            for group in groups:
                if data == group[0]:
                    group[1].append(value['name'])
                    break
            else:
                groups.append((data, [value['name']]))
        matches[filename] = [g[1] for g in groups]
    summary = dict(status='PASS_SAVED_CURRENT_TASK_INVENTORY', live_runs=0,
        cases=values, identical_executed_sources=matches, missing_executed_sources=missing,
        scope='Four full single code tasks; seven-guest GRPO is a separate reference, not N=1/N=7 control')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    fields = ['name', 'guests', 'actions', 'baseline_mib', 'peak_mib', 'peak_increase_mib',
              'host_samples', 'pss_interval_seconds', 'mincore', 'raw']
    with (args.output / 'tasks.tsv').open('w') as stream:
        writer = csv.DictWriter(stream, fields, delimiter='\t'); writer.writeheader()
        for value in values:
            c = value['current']
            mib = lambda n: 'NOT_MEASURED' if n is None else f'{n/2**20:.3f}'
            writer.writerow(dict(name=value['name'], guests=value['guests'], actions=value['actions'],
                baseline_mib=mib(c['before_guest_bytes']), peak_mib=mib(c['observed_peak_bytes']),
                peak_increase_mib=mib(c['after_service_ready_peak_increase_bytes']),
                host_samples=value['host_samples'], pss_interval_seconds=value['pss_interval_seconds'],
                mincore='P0/P1' if value['mincore']['fixed_phase_queries'] else 'final' if value['mincore']['final_inventory_requested'] else 'off',
                raw=value['raw']))
    print('status='+summary['status']+' result_dir='+str(args.output))


if __name__ == '__main__':
    main()
