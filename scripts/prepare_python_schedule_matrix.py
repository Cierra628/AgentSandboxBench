"""Prepare Sphinx/Xarray schedule metadata offline; never start a service or guest."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROLES = ('backbone', 'early_1', 'early_2', 'early_3', 'late_1', 'late_2', 'late_3')
TASKS = ('sphinx7590', 'xarray6992')
ORDERS = (('grpo', 'bpo', 'tvcache'), ('bpo', 'tvcache', 'grpo'), ('tvcache', 'grpo', 'bpo'))


def read(path):
    return json.loads(Path(path).read_text())


def task_plan(root, profile):
    entry = read(root / f'configs/platform-{profile}-endpoint-metrics.json')
    prepared = root / entry['inputs']
    cfg = read(prepared / 'config.json')
    workload = root / cfg['workload']
    paths = read(workload / 'rollouts.json')
    samples = read(workload / 'agent-sampling.json')['segments']
    assert set(paths) == set(ROLES) and paths['backbone'] == cfg['path']
    assert len(paths['backbone']) == 5
    assert cfg['guest_cpu'] == 2 and cfg['guest_memory_mib'] == 4096
    role_rows = {}
    unique = {}
    for role in ROLES:
        path = paths[role]
        if role.startswith('early_'):
            assert path[:2] == paths['backbone'][:2]
        if role.startswith('late_'):
            assert path[:4] == paths['backbone'][:4]
        segments = []
        for segment in path:
            sample = next((s for s in samples if s['role'] == role and s['segment'] == segment), None)
            if sample is None:
                sample = next(s for s in samples if s['role'] == 'backbone' and s['segment'] == segment)
            manifest = workload / 'segments' / segment / 'actions.tsv'
            with manifest.open() as stream:
                actions = list(csv.DictReader(stream, delimiter='\t'))
            assert [a['step'] for a in actions] == [f'{i:03}' for i in range(1, len(sample['actions']) + 1)]
            codes = [a['exit_code'] for a in sample['actions']]
            if role == 'backbone':
                assert codes == cfg['expected_exit_codes'][segment]
            value = dict(segment=segment, actions=len(actions), expected_exit_codes=codes,
                         manifest=str(manifest.relative_to(root)))
            if segment in unique:
                assert unique[segment] == value
            unique[segment] = value
            segments.append(value)
        role_rows[role] = dict(path=path, actions=sum(s['actions'] for s in segments), segments=segments)
    # The existing seven-role tree has singleton early/late continuations.
    suffixes = [s for role in ROLES for s in paths[role][0 if role == 'backbone' else 2 if role.startswith('early_') else 4:]]
    assert len(suffixes) == len(set(suffixes)), 'tree needs a different scheduling plan'
    logical = sum(r['actions'] for r in role_rows.values())
    restored = sum(unique[s]['actions'] for s in suffixes)
    prefixes = [dict(after_segment=paths['backbone'][i-1],
                     after_backbone_action=sum(unique[s]['actions'] for s in paths['backbone'][:i]),
                     child_roles=[r for r in ROLES if r.startswith(prefix)])
                for i, prefix in ((2, 'early_'), (4, 'late_'))]
    assert (logical, restored) == (230, 104)
    return dict(task_id=cfg['task_id'], profile=profile, input_config=str(prepared.relative_to(root) / 'config.json'),
                workload=str(workload.relative_to(root)), image=cfg['source_image'],
                cpu_per_guest=2, memory_mib_per_guest=4096, initial_lower_reference_count=13,
                replay_policy=entry['replay_policy'], roles=role_rows, checkpoints=prefixes,
                expected=dict(grpo=dict(actual_actions=logical, logical_actions=logical, saves=0, restores=0),
                              bpo=dict(actual_actions=restored, logical_actions=logical, saves=2, restores=6),
                              tvcache=dict(actual_actions=restored, logical_actions=logical, saves=2, restores=6)),
                runtime_ready=False)


def plan(root=ROOT):
    tasks = [task_plan(root, profile) for profile in TASKS]
    runs = [dict(task=t['profile'], round=number, schedule=schedule, status='NOT_RUN',
                 expected=t['expected'][schedule])
            for t in tasks for number, order in enumerate(ORDERS, 1) for schedule in order]
    assert len(runs) == 18
    assert all(v == 3 for v in Counter((r['task'], r['schedule']) for r in runs).values())
    return dict(status='OFFLINE_PLAN_VALIDATED', runtime_ready=False, tasks=tasks, runs=runs,
                guest_count=7, repeats_per_schedule=3, business_runs=0,
                ordinary_interval_seconds=.1, pss_interval_seconds=1,
                host_scope='private_service_cgroup', block_cache_method='cachestat',
                expanded_files=True, cache_layers=True, split_ram_cache=True,
                default_reclaim=True, p0=False, cache_reset=False, pressure=False,
                existing_runs_included=False,
                checks_scope='Saved tree, action counts and sampled exit metadata; not runtime or branch outcome validation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT / '.artifacts' or not output.is_relative_to(ROOT / '.artifacts'):
        parser.error('output must be a new directory inside .artifacts')
    if output.exists():
        parser.error('output already exists; do not overwrite saved work')
    value = plan()
    output.mkdir(parents=True)
    (output / 'plan.json').write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print('status=OFFLINE_PLAN_VALIDATED runtime_ready=false result_dir=' + str(output))


if __name__ == '__main__':
    main()
