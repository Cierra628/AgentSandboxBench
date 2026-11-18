"""Read saved light-rollout identities and inventory missing page-level evidence."""
import argparse
import csv
import json
from pathlib import Path


def audit(source):
    events = json.loads((source / 'identity-events.json').read_text())
    rounds = [json.loads(line) for line in (source / 'light-memory.jsonl').read_text().splitlines()]
    roles = rounds[0]['expected_roles']
    records = []
    for role in roles:
        path = source / 'live-cache' / role / 'vmm-child-binding.json'
        receipt = json.loads(path.read_text())
        binding = receipt['vmm_binding']
        active = [e for e in events if e['role'] == role and e['state'] == 'ACTIVE']
        assert receipt['status'] == 'PASS' and len(active) == 1
        assert active[0]['binding'] == binding
        assert receipt['cgroup_birth']['birth_cgroup'] == '0::' + binding['actual_cgroup']
        seen = []
        for row in rounds:
            vm = row['vmms'].get(role)
            if vm is None:
                continue
            assert (vm['pid'], vm['sandbox_id'], str(vm['counters']['start_ticks'])) == (
                binding['pid'], binding['sandbox_id'], str(binding['start_ticks']))
            assert vm['start_monotonic_ns'] >= active[0]['monotonic_ns']
            seen.append(row['round'])
        for event in events:
            if event['role'] == role:
                assert event.get('binding', event.get('previous_binding')) == binding
        records.append(dict(role=role, sandbox_id=binding['sandbox_id'], host_pid=binding['pid'],
            start_ticks=binding['start_ticks'], host_boot_id=binding['boot_id'],
            cgroup=binding['actual_cgroup'], active_ns=active[0]['monotonic_ns'],
            pss_samples=len(seen), first_round=seen[0], last_round=seen[-1],
            binding_source=str(path), identity_status='PASS'))
    assert len(records) == 7 and len({r['sandbox_id'] for r in records}) == 7
    patterns = ['**/kvm-slots.tsv', '**/ram-attestation.json', '**/marker.json',
                '**/*-before.bin', '**/*-after.bin', '**/*-host.bin']
    inventory = {pattern: sorted(str(p.relative_to(source)) for p in source.glob(pattern)) for pattern in patterns}
    return dict(identity_status='PASS', page_evidence_status='UNAVAILABLE' if not any(inventory.values()) else 'REQUIRES_REVIEW',
                source=str(source), roles=records, page_evidence_inventory=inventory,
                business_runs=0, historical_data_modified=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    with (args.output / 'identities.csv').open('x') as f:
        writer = csv.DictWriter(f, fieldnames=list(result['roles'][0]))
        writer.writeheader()
        writer.writerows(result['roles'])
    print(json.dumps({k: result[k] for k in ('identity_status', 'page_evidence_status', 'business_runs')}))
