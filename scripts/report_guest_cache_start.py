"""Report the four initial guest-cache interventions from saved outputs only."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from guest_diskstats import delta
from report_provider_pair_timing import metrics
from report_live_task_cache import physical_bytes, residency_gaps

ORDER = ('preserve', 'drop-clean', 'drop-clean', 'preserve')
ROLES = ('early_1', 'early_2')


def endpoints(intervention, start, end):
    policy = intervention['policy']
    assert intervention['status'] == start['status'] == end['status'] == 'PASS'
    assert start['policy'] == end['policy'] == 'observe'
    before, synced, after = [intervention[k] for k in ('before_sync', 'after_sync', 'after_control')]
    left, right = start['observation'], end['observation']
    assert len({v['boot_id'] for v in [before, synced, after, left, right]}) == 1
    assert all(a['end_monotonic_ns'] <= b['start_monotonic_ns']
               for a, b in zip([before, synced, after, left], [synced, after, left, right]))
    for value in [before, synced, after, left, right]:
        assert value['guest_cache_bytes'] == value['metadata']['records'] * 4096
        assert all(value['metadata'][k] == 0 for k in ('duplicate_adds', 'missing_removes', 'out_of_range'))
    if policy == 'drop-clean':
        assert after['guest_cache_bytes'] < synced['guest_cache_bytes'], 'drop did not reduce cache'
    devices = delta(left['disks'], right['disks'])
    virtio = [name for name in devices if name.startswith('vd') and not left['disk_is_partition'][name]]
    assert virtio, 'no nonpartition virtio device counters'
    faults = {k: right['vmstat'][k] - left['vmstat'][k] for k in left['vmstat']}
    assert all(v >= 0 for v in faults.values())
    return dict(policy=policy,
        before_sync_guest_cache_bytes=before['guest_cache_bytes'],
        after_sync_guest_cache_bytes=synced['guest_cache_bytes'],
        after_control_guest_cache_bytes=after['guest_cache_bytes'],
        task_start_guest_cache_bytes=left['guest_cache_bytes'], task_end_guest_cache_bytes=right['guest_cache_bytes'],
        task_start_cached_kib=left['meminfo_kib']['Cached'], task_end_cached_kib=right['meminfo_kib']['Cached'],
        task_start_shmem_kib=left['meminfo_kib']['Shmem'],
        task_start_dirty_kib=left['meminfo_kib']['Dirty'], task_start_writeback_kib=left['meminfo_kib']['Writeback'],
        post_control_rebound_bytes=left['guest_cache_bytes']-after['guest_cache_bytes'],
        sync_ns=intervention['sync_ns'], control_ns=intervention['control_ns'],
        after_control_to_start_ns=left['start_monotonic_ns']-after['end_monotonic_ns'],
        endpoint_window_ns=right['end_monotonic_ns']-left['start_monotonic_ns'],
        guest_vmstat_delta=faults, guest_device_deltas=devices,
        virtio_devices=virtio, virtio_read_bytes=sum(devices[n]['sectors_read'] * 512 for n in virtio))


def check_run(storage, wrapper, policy):
    read = lambda p: json.loads(p.read_text())
    receipt = read(storage / 'result.json'); source = storage / 'prettier6604'
    task = read(source / 'result.json'); wrapped = read(wrapper / 'result.json')
    assert receipt['status'] == wrapped['status'] == 'PASS' and all(receipt['cleanup'].values())
    assert receipt['budget']['actual_host_available_bytes'] > 50 * 2**30
    assert receipt['observed_host_available_min_bytes'] > 26 * 2**30
    assert not (storage / 'volume.ext4').exists()
    assert task['guest_cache_policy'] == policy
    common = metrics(source, 'on')
    physical = read(wrapper / 'report/summary.json'); assert physical['status'] == 'PASS'
    results = {}
    for role, side in zip(ROLES, ('child-a', 'child-b')):
        control = [read(source / 'cache-control' / f'{role}-{phase}.json')
                   for phase in ('intervention', 'task-start', 'task-end')]
        restored = read(source / f'restored-{role}.json')
        assert all(c['sandbox_id'] == restored['sandbox_id'] and c['role'] == role for c in control)
        observed = endpoints(*control)
        directory = source / 'live-cache' / side
        state = read(directory / 'result.json')
        assert state['status'] == 'PASS' and not state['vm_pause']
        assert state['collector_version'] == 'snapshot-provider-segment-pfn-union-v6'
        samples = [json.loads(line) for line in (directory / 'samples.jsonl').read_text().splitlines()]
        queries = 0
        for d in (directory, directory / 'lower-query'):
            assert read(d / 'snapshot-backend.json')['batch_lookup']['mincore_max_gap_pages'] == 2
            q = read(d / 'snapshot-query-result.json')
            assert q['returncode'] == 0 and len(q['queries']) == 2 * len(samples)
            assert all(v['missing_kernel_results'] == 0 for v in q['queries'])
            queries += len(q['queries'])
        left = control[1]['observation']; right = control[2]['observation']
        inside = [s for s in samples if s['guest_publication_before']['guest_start_ns'] >= left['end_monotonic_ns']
                  and s['guest_end_ns'] <= right['start_monotonic_ns']]
        assert inside, 'no physical sample with its guest metadata interval inside endpoint window'
        observed.update(tool_sum_ns=common['tool_times_per_role_ns'][role],
            physical_samples_total=len(samples), physical_samples_inside_endpoints=len(inside), queries=queries,
            physical_curve=[dict(point=s['point'], guest_start_ns=s['guest_publication_before']['guest_start_ns'],
                guest_end_ns=s['guest_end_ns'], guest_cache_bytes=s['guest_cache_after_bytes'],
                host_confirmed_current_bytes=physical_bytes(s),
                unknown_guest_capacity_bytes=residency_gaps(s)['host_residency_unconfirmed_guest_capacity_bytes']) for s in inside])
        results[role] = observed
    return dict(status='PASS', policy=policy, storage=str(storage), wrapper=str(wrapper),
                common=common, roles=results, overlap_pairs=len(physical['pairs']),
                guest_cache_preparation_ns=task['guest_cache_preparation_ns'],
                disk_min_available_bytes=receipt['observed_host_available_min_bytes'])


def compare(preserve, dropped):
    for key in ('action_identity', 'manifests', 'final_patches', 'configuration'):
        assert preserve['common'][key] == dropped['common'][key], key
    compared = {}
    for role in ROLES:
        a, b = preserve['roles'][role], dropped['roles'][role]
        assert b['task_start_guest_cache_bytes'] < a['task_start_guest_cache_bytes'], 'initial cache contrast missing'
        compared[role] = {}
        for key in ('task_start_guest_cache_bytes', 'tool_sum_ns', 'virtio_read_bytes'):
            old, new = a[key], b[key]
            compared[role][key] = dict(preserve=old, drop_clean=new, delta=new-old,
                                       delta_percent=100*(new-old)/old if old else None)
    a = preserve['common']['metrics']['branch_replay_elapsed_ns']
    b = dropped['common']['metrics']['branch_replay_elapsed_ns']
    return dict(roles=compared, parallel_suffix_ns=dict(preserve=a, drop_clean=b, delta=b-a,
                                                       delta_percent=100*(b-a)/a))


def report(runs_file, output):
    runs = json.loads(runs_file.read_text())
    assert [r['policy'] for r in runs] == list(ORDER) and not output.exists()
    groups = [check_run(Path(r['storage']), Path(r['wrapper']), r['policy']) for r in runs]
    pairs = [compare(groups[0], groups[1]), compare(groups[3], groups[2])]
    all_sources = [Path(r[k]) for r in runs for k in ('storage', 'wrapper')]
    assert all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in all_sources)
    output.mkdir(parents=True)
    rows = []
    for number, group in enumerate(groups, 1):
        for role, v in group['roles'].items():
            rows.append(dict(run=number, policy=group['policy'], role=role,
                start_cache_bytes=v['task_start_guest_cache_bytes'], end_cache_bytes=v['task_end_guest_cache_bytes'],
                tool_sum_ns=v['tool_sum_ns'], virtio_read_bytes=v['virtio_read_bytes'],
                pgmajfault=v['guest_vmstat_delta']['pgmajfault'], sync_ns=v['sync_ns'], control_ns=v['control_ns']))
    for key in ('action_identity', 'manifests', 'final_patches', 'configuration'):
        assert all(g['common'][key] == groups[0]['common'][key] for g in groups), key
    result = dict(status='PASS', order=list(ORDER), live_runs=4, runs=groups, comparisons=pairs,
        aggregate={policy:{role:{key:statistics.mean(g['roles'][role][key] for g in groups if g['policy']==policy)
            for key in ('task_start_guest_cache_bytes', 'tool_sum_ns', 'virtio_read_bytes')} for role in ROLES}
                   for policy in ('preserve', 'drop-clean')},
        scope='Same 11-action prefix and two original 10-action suffixes; identical named branches compared across runs. Both conditions sync; only drop-clean writes guest drop_caches=1.',
        limitations=['Two runs per condition, reversed order; descriptive paired effects, not a stable population estimate.',
            'Pre-intervention cache states already differ; background reclamation and refill are not independently isolated.',
            'Guest device reads may be served by host caches; not host physical-disk reads.',
            'Endpoint counters cover both-suffix wall window and include guest monitors and boundary probes.',
            'Physical curves select guest metadata intervals inside the endpoint window; host reads can extend beyond that interval. Unknown capacity remains separate.',
            'Full cache sampling is enabled equally but its workload-dependent cost is included.',
            'One-shot initial state intervention; no continuous cache-capacity limit.'])
    (output / 'summary.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    with (output / 'runs.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args(); result=report(args.runs.resolve(), args.output.resolve())
    print('status='+result['status']+' result_dir='+str(args.output))
