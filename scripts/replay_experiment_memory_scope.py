"""Offline replay of saved parent/direct-child counters; no live cgroup access."""
import argparse
from collections import Counter
import json
from pathlib import Path

from experiment_memory import CGROOT, normalize_groups, observation_status


def replay(storage, output):
    setup = json.loads((storage/'vmm-cgroup-setup.json').read_text())
    metrics = json.loads((storage/'prettier6604/schedule-smoke-metrics.json').read_text())
    root = Path(setup['parent']); service = setup['control']
    rows = [json.loads(line) for line in (storage/'vmm-cgroup-samples/memory.jsonl').read_text().splitlines()]
    output.mkdir(parents=True, exist_ok=False)
    parsed = []; counts = Counter()
    with (output/'memory-replay.jsonl').open('x') as stream:
        for index, row in enumerate(rows):
            value = normalize_groups(root, row['groups'])
            counts[observation_status(value)] += 1
            item = dict(source_row=index, monotonic_ns=row['monotonic_ns'], source_duration_ns=row['duration_ns'], experiment_memory=value)
            stream.write(json.dumps(item)+'\n'); parsed.append(item)
    chosen = min(parsed, key=lambda row:abs(row['monotonic_ns']-metrics['p1']['end_monotonic_ns']))
    value = chosen['experiment_memory']
    controllers = [p for p in value['direct_children'] if Path(p).suffix=='.service']
    if len(controllers)!=1:raise ValueError('expected one controller service in saved endpoint sample')
    observed = dict(parent_file_bytes=value['parent_counters']['file'],
                    direct_children_file_bytes=value['direct_children_sum']['file'],
                    docker_file_bytes=value['direct_children'][service]['file'],
                    controller_file_bytes=value['direct_children'][controllers[0]]['file'])
    # Exact integers behind the four rounded MiB values requested for this run.
    expected = dict(parent_file_bytes=12903604224, direct_children_file_bytes=12903604224,
                    docker_file_bytes=73261056, controller_file_bytes=12817141760)
    births = []
    for p in sorted((storage/'service-logs/vmm-cgroups').glob('vmm-*.json')):
        birth = json.loads(p.read_text())
        group = CGROOT/birth['birth_cgroup'].removeprefix('0::/').lstrip('/')
        valid = (group.parent==root and str(group) in value['direct_children'] and p.stem==group.name
                 and birth['creation']=='clone3_CLONE_INTO_CGROUP')
        births.append(dict(source=str(p), cgroup=str(group), valid=valid, birth=birth))
    birth_valid = len(births)==7 and len({r['cgroup'] for r in births})==7 and all(r['valid'] for r in births)
    valid = observed==expected and birth_valid and value['file_closure']['parent_minus_children_bytes']==0
    deltas = [r['experiment_memory']['file_closure']['parent_minus_children_bytes'] for r in parsed]
    finite = [d for d in deltas if d is not None]
    summary = dict(status=('PASS_WITH_CLOSURE_ANOMALIES' if counts['MISMATCH'] or counts['INCOMPLETE'] else 'PASS') if valid else 'FAIL',
                   source_storage=str(storage), source_metrics_status=metrics['status'],
                   source_status_preserved=True, samples=len(rows), observation_status_counts=dict(counts),
                   signed_file_difference_min_bytes=min(finite), signed_file_difference_max_bytes=max(finite),
                   representative=chosen, offset_from_p1_end_ns=chosen['monotonic_ns']-metrics['p1']['end_monotonic_ns'],
                   observed_bytes=observed, expected_bytes=expected, exact_expected_match=observed==expected,
                   observed_mib={k:v/2**20 for k,v in observed.items()}, birth_records=births, birth_paths_verified=birth_valid,
                   replay_limit='Saved sampler enumerated all direct children. No second inventory was saved; replay cannot detect a disappeared unrecorded child or backfill live PID checks.')
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    return summary


def audit_inventory(index):
    records = json.loads(index.read_text())['runs_detail']; result = []
    for row in records:
        storage = Path(row['storage']); raw = Path(row['raw'])
        metrics = json.loads((raw/'schedule-smoke-metrics.json').read_text())
        outer = json.loads((storage/'result.json').read_text())
        creation = outer.get('host_file_creation')
        result.append(dict(task=row['task'], round=row['round'], schedule=row['schedule'], raw=str(raw),
                           legacy_cgroup=metrics['cgroup'],
                           dedicated_parent_recorded=creation.get('cgroup') if creation else None,
                           parent_before_guest_counters=creation.get('before_guest_counters') if creation else None,
                           controller_membership_recorded=metrics.get('controller_cgroup') or outer.get('controller_cgroup')))
    return dict(index=str(index), runs=len(result),
                dedicated_parent_records=sum(r['dedicated_parent_recorded'] is not None for r in result),
                controller_membership_records=sum(r['controller_membership_recorded'] is not None for r in result), records=result)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--inventory', type=Path)
    args = parser.parse_args()
    value = replay(args.storage, args.output)
    if args.inventory:
        (args.output/'ordinary-scope-audit.json').write_text(json.dumps(audit_inventory(args.inventory), ensure_ascii=False, indent=2)+'\n')
    print('status='+value['status']+' result_dir='+str(args.output))
    print(json.dumps(value['observed_mib']))
    raise SystemExit(1 if value['status']=='FAIL' else 0)
