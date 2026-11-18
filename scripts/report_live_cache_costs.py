"""Summarize recorded host stage timings; saved metadata only, no live access."""
import argparse
import csv
import json
from pathlib import Path
import statistics

CATEGORIES = dict(
    guest_publication_read=('guest_before', 'guest_after'),
    vmm_pagemap=('vmm_first', 'vmm_middle', 'vmm_last'),
    maps_read=('maps_before', 'maps_after'),
    offset_translation=('backend_offsets_before', 'lower_offsets', 'backend_offsets_after'),
    backend_query=('backend_query_first', 'backend_query_last'),
    lower_query=('lower_query_first', 'lower_query_last'),
    provider_query=('provider_first', 'provider_last'),
    decode_rows=('decode_before', 'decode_after', 'host_rows', 'backend_rows', 'lower_rows'),
    accounting=('vmm_account', 'backend_account', 'lower_account', 'provider_account'))
BASE = {'guest_before', 'decode_before', 'vmm_first', 'vmm_middle', 'guest_after',
        'decode_after', 'vmm_last', 'host_rows', 'vmm_account'}
BACKEND = {'maps_before', 'backend_offsets_before', 'backend_query_first', 'backend_query_last',
           'maps_after', 'backend_offsets_after', 'backend_rows', 'backend_account'}
LOWER = {'lower_offsets', 'lower_query_first', 'lower_query_last', 'lower_rows', 'lower_account'}
PROVIDER = {'provider_first', 'provider_last', 'provider_account'}


def point_costs(point):
    stages = point['host_stage_costs']
    expected = set(BASE)
    if 'host_current_union_bytes' in point: expected |= BACKEND
    if 'host_three_current_union_bytes' in point: expected |= LOWER
    if 'host_four_current_union_bytes' in point: expected |= PROVIDER
    assert set(stages) == expected, 'unexpected/missing stage timings'
    cursor = point['host_sample_start_ns']
    for stage in stages.values():
        assert stage['cpu_ns'] >= 0 and stage['elapsed_ns'] >= 0
        assert stage['host_start_ns'] >= cursor, 'stages overlap or order differs'
        assert stage['elapsed_ns'] == stage['host_end_ns']-stage['host_start_ns']
        cursor = stage['host_end_ns']
    assert cursor <= point['host_sample_start_ns']+point['host_elapsed_ns'], 'stage outside point timer'
    if 'host_four_segment_current_union_bytes' in point:
        assert stages['provider_first']['host_end_ns'] <= stages['vmm_middle']['host_start_ns']
        assert stages['vmm_middle']['host_end_ns'] <= stages['provider_last']['host_start_ns']
    groups = {name: {key: sum(stages[s][key] for s in names if s in stages)
        for key in ('cpu_ns', 'elapsed_ns')} for name, names in CATEGORIES.items() if any(s in stages for s in names)}
    groups['unmeasured'] = dict(cpu_ns=point['host_thread_cpu_ns']-sum(v['cpu_ns'] for v in groups.values()),
        elapsed_ns=point['host_elapsed_ns']-sum(v['elapsed_ns'] for v in groups.values()))
    assert all(v >= 0 for v in groups['unmeasured'].values()), 'stage sums exceed point total'
    return dict(point=point['point'], categories=groups, stages=stages,
        host_thread_cpu_ns=point['host_thread_cpu_ns'], host_elapsed_ns=point['host_elapsed_ns'])


def summarize(samples, state):
    selected = [p for p in samples if 'host_stage_costs' in p]
    if not selected:
        return dict(status='NOT_RECORDED', samples=len(samples), points=[])
    assert len(selected) == len(samples), 'partial stage timing coverage'
    points = [point_costs(p) for p in selected]
    assert all(set(p['categories']) == set(points[0]['categories']) for p in points)
    means = {name: {key: statistics.mean(p['categories'][name][key] for p in points)
        for key in ('cpu_ns', 'elapsed_ns')} for name in points[0]['categories']}
    outside = state['host_thread_total_cpu_ns']-sum(p['host_thread_cpu_ns'] for p in points)
    assert outside >= 0
    return dict(status='PASS', samples=len(points), points=points, category_means=means,
        stage_means={name: {key: statistics.mean(p['stages'][name][key] for p in points)
            for key in ('cpu_ns', 'elapsed_ns')} for name in points[0]['stages']},
        point_cpu_mean_ns=statistics.mean(p['host_thread_cpu_ns'] for p in points),
        point_elapsed_mean_ns=statistics.mean(p['host_elapsed_ns'] for p in points),
        session_thread_cpu_ns=state['host_thread_total_cpu_ns'], outside_point_cpu_total_ns=outside)


def report(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    index = json.loads((source/'result.json').read_text())
    assert index['status'] == 'PASS'
    sessions = {}
    for name in index['sessions']:
        directory = source/name
        state = json.loads((directory/'result.json').read_text())
        samples = [json.loads(line) for line in (directory/'samples.jsonl').read_text().splitlines()]
        assert state['status'] == 'PASS' and state['samples'] == len(samples)
        sessions[name] = summarize(samples, state)
    value = dict(schema=1, sessions=sessions,
        scope='Host collection thread through accounting; encoding/output, helper processes and guest CPU separate.',
        unmeasured='Point total minus nonoverlapping recorded stages; includes timer/loop overhead, imports and unstaged checks.')
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    with (output/'costs.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['session', 'point', 'category', 'cpu_ns', 'elapsed_ns'])
        writer.writeheader()
        for name, session in sessions.items():
            for p in session['points']:
                for category, cost in p['categories'].items():
                    writer.writerow(dict(session=name, point=p['point'], category=category, **cost))
    print('status=WRITTEN result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report(args.source, args.output)
