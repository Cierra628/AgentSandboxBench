"""Normalize the saved three-schedule smoke results without live queries."""
import argparse
import csv
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def rows(path):
    with path.open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def normalize(source):
    saved = read(source)
    assert saved['status'] == 'PASS'
    normalized = []
    initial_reference = None
    for run in saved['runs']:
        raw = Path(run['raw'])
        task = read(raw / 'result.json')
        endpoint = read(raw / 'schedule-smoke-metrics.json')
        monitor = read(raw / 'runtime-monitor.json')
        pss = endpoint['pss']; p1 = endpoint['p1']
        assert task['status'] == monitor['status'] == 'PASS'
        assert endpoint['status'] == 'OBSERVED'
        assert monitor['profile'] == 'full'
        assert monitor['interval_seconds'] == .1 and monitor['host_pss_interval_seconds'] == 1
        assert len(task['paths']) == len(pss['processes']) == pss['denominator'] == 7
        assert len({p['identity']['pid'] for p in pss['processes']}) == 7
        assert all(p['uid'] == 0 for p in pss['processes'])
        assert {p['role'] for p in pss['processes']} == set(task['paths'])
        assert all((p['official_passed'], p['official_failed']) == (0, 2) for p in task['paths'].values())
        assert sum(p['pss_kib'] for p in pss['processes']) * 1024 == pss['total_bytes']
        assert pss['total_bytes'] / 7 == pss['per_sandbox_bytes']
        initial_keys = {(f['device'], f['inode']) for f in p1['files']}
        assert len(initial_keys) == len(p1['files']) == 19
        assert all(f['stable'] and set(f['users']) == set(task['paths']) for f in p1['files'])
        assert sum(f['resident_bytes'] for f in p1['files']) == p1['resident_bytes']
        roots = endpoint['rootfs_inventory']
        for guest in roots['guests'].values():
            reference = sorted((f['reference']['digest'], f['reference']['size'])
                               for f in guest['lowers'] if (f.get('device'), f.get('inode')) in initial_keys)
            assert len(reference) == 19
            if initial_reference is None:
                initial_reference = reference
            assert reference == initial_reference, 'initial image objects differ'
        extras = [f for f in roots['objects'] if (f['device'], f['inode']) not in initial_keys]
        steps = [step for segment in rows(raw / 'segments.tsv')
                 for step in rows(raw / 'rollouts' / segment['rollout'] / segment['segment'] / 'steps.tsv')]
        count = len(steps); tools_ns = sum(int(step['duration_ns']) for step in steps)
        assert count == run['actual_actions'] == task['actual_action_count']
        assert tools_ns == sum(p['guest_action_sum_ns'] for p in task['paths'].values())
        assert tools_ns / 1e9 == run['tool_seconds']
        start = task['rollout_start_monotonic_ns']
        first = task['first_tool_start_monotonic_ns']
        end = endpoint['original_paths_end_monotonic_ns']
        assert start <= first <= end <= pss['start_monotonic_ns'] <= pss['end_monotonic_ns'] <= p1['start_monotonic_ns'] <= p1['end_monotonic_ns']
        assert (end - first) / 1e9 == run['action_download_window_seconds']
        assert task['controller_elapsed_ns'] / 1e9 == run['controller_seconds']
        assert pss['total_bytes'] / 2**20 == run['pss_total_mib']
        assert pss['per_sandbox_bytes'] / 2**20 == run['pss_per_sandbox_mib']
        assert p1['resident_bytes'] / 2**20 == run['mincore_p1_mib']
        before = p1['counters_before']['file']; after = p1['counters_after']['file']
        gap = p1['resident_bytes'] - max(before, after)
        normalized.append(dict(schedule=run['schedule'], raw=str(raw), n=7,
            actual_actions=count, logical_actions=run['logical_actions'],
            vmm_pss_endpoint_total_mib=run['pss_total_mib'],
            vmm_pss_endpoint_per_guest_mib=run['pss_per_sandbox_mib'],
            initial_lower_mincore_p1_mib=run['mincore_p1_mib'],
            initial_lower_count=19, p0_resident_bytes=None,
            initial_lower_fully_resident=all(f['pages'] == f['resident_pages'] for f in p1['files']),
            additional_active_lower_count=len(extras),
            additional_active_lower_logical_size_mib=sum(f['size'] for f in extras)/2**20,
            additional_active_lower_resident_bytes=None,
            upper_resident_bytes=None, vm_memory_backing_resident_bytes=None,
            service_cgroup_file_before_mib=before/2**20,
            service_cgroup_file_after_mib=after/2**20,
            residency_minus_max_file_mib=gap/2**20,
            file_coverage='SHORTFALL_REQUIRES_RESOLUTION' if gap > 0 else 'NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETENESS',
            outside_cgroup_resident_bytes=None,
            schedule_run_seconds=(end-start)/1e9,
            initial_create_and_prepare_seconds=(first-start)/1e9,
            action_download_window_seconds=run['action_download_window_seconds'],
            controller_seconds=run['controller_seconds'], actual_tool_sum_seconds=tools_ns/1e9,
            outer_call_through_verification_seconds=run['private_service_round_seconds'],
            pss_start_after_paths_end_seconds=(pss['start_monotonic_ns']-end)/1e9,
            pss_query_seconds=(pss['end_monotonic_ns']-pss['start_monotonic_ns'])/1e9,
            p1_start_after_paths_end_seconds=(p1['start_monotonic_ns']-end)/1e9,
            p1_query_seconds=(p1['end_monotonic_ns']-p1['start_monotonic_ns'])/1e9,
            endpoint_observation_seconds=run['endpoint_wall_seconds']))
    assert {r['schedule'] for r in normalized} == {'grpo', 'bpo', 'tvcache'} and len(normalized) == 3
    return dict(status='PASS', scope='saved data only; no live proc, cgroup, mincore, service, or sandbox queries',
                source=str(source.resolve()), schema='schedule-metrics-offline-v1',
                units='MiB = bytes / 2**20; seconds = nanoseconds / 1e9', runs=normalized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    assert not args.output.exists(), 'output must be new'
    result = normalize(args.summary)
    args.output.mkdir(parents=True)
    (args.output / 'normalized-runs.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    with (args.output / 'normalized-runs.tsv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result['runs'][0]), delimiter='\t')
        writer.writeheader(); writer.writerows(result['runs'])
    print('status=PASS result_dir='+str(args.output))


if __name__ == '__main__':
    main()
