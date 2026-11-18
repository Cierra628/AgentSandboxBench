"""Classify saved host residency evidence offline; never open backing files or devices."""
import argparse
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def loop_summary(trial):
    assert trial['status'] == 'PASS'
    rows = {}
    for name in ('baseline', 'immediate', 'settled'):
        point = trial['snapshots'][name]
        files = point['files']
        assert all(row['stable'] for row in files)
        assert len({(row['device'], row['inode']) for row in files}) == len(files)
        assert point['resident_bytes'] == sum(row['resident_bytes'] for row in files)
        file_bytes = max(point['inner_before']['file'], point['inner_after']['file'])
        rows[name] = dict(resident_bytes=point['resident_bytes'], file_bytes=file_bytes,
            signed_gap_bytes=point['resident_bytes']-file_bytes,
            outer_minus_inner_after_bytes=point['outer_after']['file']-point['inner_after']['file'],
            file_change_during_query_bytes=point['inner_after']['file']-point['inner_before']['file'])
    before, after = rows['baseline'], rows['settled']
    return dict(io_accounting=trial['io_accounting_requested'], phases=rows,
        resident_growth_bytes=after['resident_bytes']-before['resident_bytes'],
        file_growth_bytes=after['file_bytes']-before['file_bytes'],
        signed_gap_change_bytes=after['signed_gap_bytes']-before['signed_gap_bytes'],
        initialization_classification='PRESENT_BEFORE_PAYLOAD_OWNERSHIP_AND_TIMING_UNRESOLVED',
        payload_accounting_classification=('POSITIVE_INCREMENTAL_SHORTFALL'
            if after['signed_gap_bytes'] > before['signed_gap_bytes']
            else 'NO_POSITIVE_INCREMENTAL_SHORTFALL'))


def inventory_summary(inventory, writable, manifest):
    assert inventory['status'] == 'OBSERVED' and not inventory['errors']
    roots = [Path(path) for path in inventory['roots']]
    labels = ('input-commits', 'private-image-cache', 'private-loop-image',
              'input-ram-loop-image', 'snapshot-store', 'snapshot-runtime',
              'input-deps', 'private-deps', 'private-logs', 'sandbox-records', 'template-builds')
    # This restore audit accepts its original six roots and the expanded eleven-root layout.
    assert len(roots) in (6, len(labels))
    labels = labels[:len(roots)]
    files = inventory['files']; page = inventory['page_size']
    assert len({(row['device'], row['inode']) for row in files}) == len(files)
    counts, sizes = Counter(), Counter()
    for row in files:
        assert row['stable'] and row['resident_bytes'] == row['resident_pages']*page
        path = Path(row['path'])
        matches = [label for root, label in zip(roots, labels)
                   if path == root or root in path.parents]
        assert len(matches) == 1, row['path']
        counts[matches[0]] += 1; sizes[matches[0]] += row['resident_bytes']
    resident = sum(sizes.values())
    assert resident == inventory['resident_bytes']
    file_before, file_after = (inventory[key]['file'] for key in ('cgroup_before', 'cgroup_after'))
    assert inventory['minimum_unaccounted_or_timing_bytes'] == max(0, resident-max(file_before, file_after))
    # The saved fd list has paths and sizes, but no inode or residency information.
    private_env = roots[1].parent
    inventoried_env = {str(Path(row['path']).relative_to(private_env)) for row in files
                       if private_env in Path(row['path']).parents}
    omitted = {}
    for row in writable:
        path = row['path']
        assert path.startswith('/workspace/env/')
        relative = path.removeprefix('/workspace/env/')
        if relative not in inventoried_env:
            omitted[relative] = dict(path=relative, observed_size_bytes=row['size'])
    deps = [row for row in manifest['deps'].values() if row['type'] == 'file']
    return dict(file_count=len(files), page_size=page, resident_bytes=resident,
        cgroup_file_before_bytes=file_before, cgroup_file_after_bytes=file_after,
        file_minus_inventory_bytes=max(file_before, file_after)-resident,
        cgroup_shmem_before_bytes=inventory['cgroup_before']['shmem'],
        cgroup_shmem_after_bytes=inventory['cgroup_after']['shmem'],
        input_ram_image_resident_bytes=sizes['input-ram-loop-image'],
        categories={label:dict(file_count=counts[label], resident_bytes=sizes[label]) for label in labels},
        omitted_writable_paths=list(omitted.values()),
        omitted_paths_observed_size_bytes=sum(row['observed_size_bytes'] for row in omitted.values()),
        omitted_paths_page_rounded_size_bytes=sum((row['observed_size_bytes']+page-1)//page*page for row in omitted.values()),
        omitted_deps_manifest=dict(file_count_per_tree=len(deps),
            logical_size_per_tree_bytes=sum(row['size'] for row in deps),
            page_rounded_size_per_tree_bytes=sum((row['size']+page-1)//page*page for row in deps),
            omitted_trees=['input-readonly/env/deps', 'private-volume/env/deps'] if len(roots)==6 else [],
            two_trees_logical_size_bytes=2*sum(row['size'] for row in deps)),
        omitted_size_meaning='Saved logical lengths only, not measured residency or a final-time upper bound; paths are not inode identities.',
        accounting_validation='AGGREGATES_DO_NOT_RESOLVE_OWNERSHIP')


def audit(loop_dir, storage):
    batch = read(loop_dir/'result.json'); assert batch['status'] == 'PASS'
    loops = [loop_summary(trial) for trial in batch['trials']]
    assert [row['io_accounting'] for row in loops] == ['no', 'yes']
    inventory = read(storage/'host-file-residency.json')
    coverage = inventory_summary(inventory, read(storage/'writable-files.json'),
                                 read(storage/'copied-input-hashes.json'))
    final_keys = {(row['device'], row['inode']) for row in inventory['files']}
    restore = read(storage/'prettier6604/host-restore.json')
    assert restore['status'] == 'PASS'
    live = []
    for group in restore['groups']:
        query = group['residency']; assert query['status'] == 'OBSERVED'
        assert group['end_monotonic_ns'] <= query['start_monotonic_ns']
        assert query['end_monotonic_ns'] <= group['device_released_ns'] < inventory['start_monotonic_ns']
        counts, sizes = Counter(), Counter(); raw_count = 0
        for row in query['files']:
            assert row['stable']
            counts[row['kind']] += 1; sizes[row['kind']] += row['resident_bytes']
            if row['kind'] == 'raw-memory-layer':
                assert (row['device'], row['inode']) in final_keys
                raw_count += 1
        live.append(dict(sandboxes=group['sandboxes'],
            categories={kind:dict(file_count=counts[kind], resident_bytes=sizes[kind]) for kind in sizes},
            raw_layers_also_in_final_inventory=raw_count,
            comparison='Different time windows; do not add live and final residency.'))
    return dict(status='PASS', scope='Offline saved JSON and metadata only; no guest, backing read or residency query.',
        sources=dict(loop=str(loop_dir.relative_to(ROOT)), restore=str(storage.relative_to(ROOT))),
        loops=loops, final_inventory=coverage, live_inventory=live,
        unresolved=dict(initialization_charge_owner=True, initialization_stats_timing=True,
                        final_extra_file_composition=True, masked_external_charge=True),
        savings_ratio=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--loop-dir', type=Path, required=True)
    parser.add_argument('--storage-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = audit(args.loop_dir.resolve(), args.storage_dir.resolve())
    with args.output.open('x') as stream:
        stream.write(json.dumps(value, indent=2)+'\n')
    print('status=PASS output='+str(args.output))


if __name__ == '__main__':
    main()
