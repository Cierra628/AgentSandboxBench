"""Offline summary of the saved November 5 collection runs; no live queries."""
import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = 1_000_000_000


def read(path):
    return json.loads((ROOT / path).read_text())


def merged(spans):
    result = []
    for start, end in sorted(spans):
        assert end >= start
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def length(spans):
    return sum(end - start for start, end in merged(spans))


def event_span(event):
    if 'command_start_monotonic_ns' in event:
        return [event['command_start_monotonic_ns'], event['command_end_monotonic_ns']]
    # Legacy events include some local logging; do not rename them pure RPC time.
    return [event['start_monotonic_ns'], event['start_monotonic_ns'] + event['duration_ns']]


def summarize(row):
    raw = Path(row['raw'])
    result = read(raw / 'result.json')
    events = read(raw / 'events.json')
    assert result['status'] == 'PASS'
    prettier = row['task'] == 'prettier6604'
    start_key = 'rollout_start_monotonic_ns' if prettier else 'schedule_start_monotonic_ns'
    end_key = 'original_paths_end_monotonic_ns' if prettier else 'schedule_end_monotonic_ns'
    task = [result[start_key], result[end_key]]
    endpoint = result['schedule_smoke_metrics' if prettier else 'endpoint_metrics']
    assert endpoint['status'] == 'OBSERVED'
    categories = dict(task=[task], endpoint=[[endpoint['start_monotonic_ns'], endpoint['end_monotonic_ns']]],
                      validation=[], deletion=[], sampling_management_outside_task=[])
    selected = []
    validation = ('syntax-validation', 'extension-validation', 'functional-validation',
                  'final-source-patch', 'final-files', 'final-patch', 'small-invariant',
                  'upload-evaluation', 'benchmark-pytest', 'download-evaluation')
    for event in events:
        stage = re.sub(r'^\d{4}-', '', event['stage'])
        span = event_span(event)
        if stage.startswith('monitor-collect'):
            # The blocking collection command overlaps task execution, not active CPU work.
            continue
        if span[0] < task[1]:
            continue
        category = None
        if stage in ('delete-owned', 'delete-own-guest'):
            category = 'deletion'
        elif stage in validation or stage.startswith(('source-diff-', 'oracle-', 'upload-official-', 'official-')):
            category = 'validation'
        elif stage.startswith('monitor-'):
            category = 'sampling_management_outside_task'
        if category:
            categories[category].append(span)
            selected.append(dict(stage=event['stage'], category=category, span_ns=span,
                                 timing='command' if 'command_duration_ns' in event else 'legacy_event'))
    # Each category uses interval union, not the sum of concurrent guest calls.
    all_spans = [span for spans in categories.values() for span in spans]
    covered = sum(length(spans) for spans in categories.values())
    union = length(all_spans)
    overlap = covered - union
    # Different guests can validate and change monitor phase concurrently.
    # Keep both measured spans and report the overlap instead of double counting.
    assert length(categories['task'] + categories['endpoint']) == length(categories['task']) + length(categories['endpoint'])
    elapsed = result['controller_elapsed_ns']
    assert union <= elapsed
    stages = {key: length(spans) / NS for key, spans in categories.items()}
    stages['cross_category_overlap'] = overlap / NS
    stages['other_unclassified'] = (elapsed - union) / NS
    stages['controller'] = elapsed / NS
    stages['controller_minus_task'] = (elapsed - length(categories['task'])) / NS
    expected = row['timing_seconds']
    assert abs(stages['task'] - expected['task']) < 1e-8
    assert abs(stages['controller'] - expected['controller']) < 1e-8
    if prettier:
        assert result['actual_action_count'] == row['actions']
        tools_ns = sum(path['guest_action_sum_ns'] for path in result['paths'].values())
    else:
        with (ROOT / row['tools_file']).open() as stream:
            tools = list(csv.DictReader(stream, delimiter='\t'))
        assert len(tools) == row['actions']
        tools_ns = sum(int(tool['duration_ns']) for tool in tools)
    assert abs(tools_ns / NS - expected['tools']) < 1e-8
    return dict(**row, stages_seconds=stages, selected_events=selected,
                endpoint_wall_seconds=stages['endpoint'], endpoint_cpu_seconds=endpoint['cpu_ns'] / NS,
                pss_query_wall_seconds=(endpoint['pss']['end_monotonic_ns'] - endpoint['pss']['start_monotonic_ns']) / NS,
                p1_query_wall_seconds=(endpoint['p1']['end_monotonic_ns'] - endpoint['p1']['start_monotonic_ns']) / NS,
                event_timing_scope='command intervals where saved; legacy event intervals otherwise; interval unions within each category; other is unclassified, not sampling overhead')


def build():
    upgrade = 'results/task-metrics-upgrade-20261105/summary.json'
    two = 'results/external-matplotlib-text-20261105/summary.json'
    pair = 'results/prettier-sampling-pair-20261105/summary.json'
    rows = []
    for row in read(upgrade)['runs']:
        rows.append(dict(task=row['task'], actions=row['actions'], guests=1, raw=row['raw'],
                         source=upgrade, report=row['report'] + '/REPORT.md',
                         tools_file='results/task-metrics-upgrade-20261105/' + row['task'] + '/tools.tsv',
                         timing_seconds=row['timing_seconds'], pss_per_guest_mib=row['pss_mib'],
                         lower_files=row['lower_files'], p1_mib=row['p1_mib'], outcome=row['outcome']))
    for row in read(two)['tasks']:
        rows.append(dict(task=row['task_id'], actions=row['actions'], guests=1, raw=row['raw'],
                         source=two, report=row['report'] + '/REPORT.md',
                         tools_file=str(Path(row['raw']) / 'tools.tsv'),
                         timing_seconds=dict(task=row['task_seconds'], actions_download=row['action_download_seconds'],
                                             tools=row['tool_seconds'], controller=row['controller_seconds']),
                         pss_per_guest_mib=row['pss_per_guest_mib'], lower_files=row['lower_files'],
                         p1_mib=row['mincore_p1_mib'], outcome=row['original_result']))
    row = next(row for row in read(pair)['runs'] if row['mode'] == 'on')
    endpoint = row['endpoint']
    rows.append(dict(task='prettier6604', actions=row['actual_actions'], guests=7, raw=row['raw'],
                     source=pair, report='results/prettier-sampling-pair-20261105/REPORT.md',
                     timing_seconds=dict(task=row['task_seconds'], actions_download=row['actions_download_seconds'],
                                         tools=row['tool_seconds'], controller=row['controller_seconds']),
                     pss_per_guest_mib=endpoint['pss']['per_sandbox_bytes'] / 1048576,
                     lower_files=endpoint['p1']['unique_files'], p1_mib=endpoint['p1']['resident_bytes'] / 1048576,
                     outcome='每实例0通过2失败；原检查通过；七角色完整监控'))
    runs = [summarize(row) for row in rows]
    assert len(runs) == 8 and sum(row['actions'] for row in runs) == 551
    return dict(status='PASS', scope='Offline only; saved runs are not one execution or one monitoring configuration',
                runs=runs, source_files=[upgrade, two, pair],
                checks=['8 saved successful controllers; endpoint observations present',
                        'tool counts and cumulative durations match saved TSV or per-path records',
                        'task and controller durations match saved summary values',
                        'timing-category unions and explicit overlap reconcile to controller elapsed',
                        '551 actions across selected records, not a new experiment'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path, help='New output JSON; will not overwrite')
    args = parser.parse_args()
    value = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print('status=PASS runs=8 output=' + str(args.output))
