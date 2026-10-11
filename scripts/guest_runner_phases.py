"""Five Bash builtin timestamps outside frozen actions; validate against an outer dual clock."""
from decimal import Decimal
import json

TAGS = ('entry', 'baseline', 'actions', 'finalize', 'exit')
BASELINE_TAGS = ('add', 'write_tree', 'reset', 'rev_parse', 'done')
BASELINE_BLOCK = '''git -C /testbed add -A
git -C /testbed write-tree > "$out/baseline-tree.txt"
git -C /testbed reset --mixed >/dev/null
git -C /testbed rev-parse HEAD > "$out/original-head.txt"'''


def instrument_baseline(source, add_trace=False):
    assert source.count(BASELINE_BLOCK) == 1
    lines = ['''baseline_timed_git() {
  local timing_file=$1 TIMEFORMAT=$'%3R\\t%3U\\t%3S'
  shift
  { time "$@" 2>&3; } 3>&2 2> "$timing_file"
}''']
    if add_trace:
        lines[0]=lines[0].replace('  { time', '''  if [[ $timing_file == "$out/baseline-add-time.tsv" ]]; then
    local -x GIT_TRACE2_EVENT="$out/baseline-add-events.jsonl"
    local -x GIT_TRACE_PERFORMANCE="$out/baseline-add-performance.txt"
    printf '%s\\n' "$(< /proc/diskstats)" > "$out/baseline-add-before.diskstats"
  fi
  { time''').replace('\n}', '''
  if [[ $timing_file == "$out/baseline-add-time.tsv" ]]; then
    printf '%s\\n' "$(< /proc/diskstats)" > "$out/baseline-add-after.diskstats"
  fi
}''')
    for number, (tag, command) in enumerate(zip(BASELINE_TAGS, BASELINE_BLOCK.splitlines())):
        lines.append(f'printf \'{tag}\\t%s\\n\' "$EPOCHREALTIME" {">" if number == 0 else ">>"} "$out/baseline-commands.tsv"')
        lines.append(f'baseline_timed_git "$out/baseline-{tag}-time.tsv" {command}')
    lines += ['printf \'done\\t%s\\n\' "$EPOCHREALTIME" >> "$out/baseline-commands.tsv"', 'unset -f baseline_timed_git']
    return source.replace(BASELINE_BLOCK, '\n'.join(lines))


def instrument(source, baseline_commands=False, baseline_add_trace=False):
    assert not baseline_add_trace or baseline_commands
    anchor = 'set -euo pipefail\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + 'runner_entry_time=$EPOCHREALTIME\n', 1)
    anchor = 'mkdir -p "$out/stdout" "$out/stderr"\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor + '''printf 'entry\\t%s\\n' "$runner_entry_time" > "$out/runner-phases.tsv"
unset runner_entry_time
''')
    anchor = 'git -C /testbed add -A\ngit -C /testbed write-tree'
    assert source.count(anchor) == 1
    source = source.replace(anchor, 'printf \'baseline\\t%s\\n\' "$EPOCHREALTIME" >> "$out/runner-phases.tsv"\n' + anchor)
    anchor = 'for action in "$actions"/*.sh; do'
    assert source.count(anchor) == 1
    source = source.replace(anchor, 'printf \'actions\\t%s\\n\' "$EPOCHREALTIME" >> "$out/runner-phases.tsv"\n' + anchor)
    anchor = 'if [[ $end_step == 026 ]]; then'
    assert source.count(anchor) == 1
    source = source.replace(anchor, 'printf \'finalize\\t%s\\n\' "$EPOCHREALTIME" >> "$out/runner-phases.tsv"\n' + anchor)
    source += '\nprintf \'exit\\t%s\\n\' "$EPOCHREALTIME" >> "$out/runner-phases.tsv"\n'
    return instrument_baseline(source,add_trace=baseline_add_trace) if baseline_commands else source


def instrument_host(source, helper='/workspace/command-clock'):
    anchor = "bash /workspace/guest-runner.sh\""
    assert source.count(anchor) == 1
    return source.replace(anchor, helper + ' /workspace/artifacts/replay/runner-envelope.json ' + anchor)


def collect(directory, action_sum_ns=None, baseline_commands=False, baseline_add_trace=False, baseline_repository='/testbed'):
    assert not baseline_add_trace or baseline_commands
    rows = [line.split('\t') for line in (directory / 'runner-phases.tsv').read_text().splitlines()]
    assert [row[0] for row in rows] == list(TAGS) and all(len(row) == 2 for row in rows)
    times = []
    for _, value in rows:
        assert value.count('.') == 1 and len(value.split('.')[1]) == 6 and value.replace('.', '').isdigit()
        times.append(int(Decimal(value) * 10**9))
    assert all(a < b for a, b in zip(times, times[1:]))
    clock = json.loads((directory / 'runner-envelope.json').read_text())
    assert clock['exit_code'] == 0 and clock['monotonic_raw_ns'] > 0
    assert abs(clock['realtime_ns'] - clock['monotonic_raw_ns']) <= 2_000_000
    durations = {name: b - a for name, a, b in zip(TAGS[:-1], times, times[1:])}
    span = times[-1] - times[0]
    assert span <= clock['realtime_ns'] + 2_000  # two microsecond timestamp rounding
    result = dict(clock=clock, phase_ns=durations, marked_span_ns=span,
                  envelope_minus_marked_ns=clock['realtime_ns'] - span)
    if action_sum_ns is not None:
        assert durations['actions'] >= action_sum_ns - 2_000_000
        result['loop_outside_action_timers_ns'] = durations['actions'] - action_sum_ns
    files = {p.name for p in directory.glob('baseline-*') if p.name != 'baseline-tree.txt'}
    expected = {'baseline-commands.tsv'} | {f'baseline-{tag}-time.tsv' for tag in BASELINE_TAGS[:-1]}
    if baseline_add_trace:
        expected |= {'baseline-add-events.jsonl','baseline-add-performance.txt','baseline-add-before.diskstats','baseline-add-after.diskstats'}
    assert files == (expected if baseline_commands else set())
    if baseline_commands:
        from math import isfinite
        commands = [row.split('\t') for row in (directory / 'baseline-commands.tsv').read_text().splitlines()]
        assert [row[0] for row in commands] == list(BASELINE_TAGS) and all(len(row) == 2 for row in commands)
        command_times = [int(Decimal(row[1]) * 10**9) for row in commands]
        assert times[1] <= command_times[0] < command_times[-1] <= times[2]
        assert all(a < b for a, b in zip(command_times, command_times[1:]))
        result['baseline_commands'] = {}
        for tag, start, end in zip(BASELINE_TAGS[:-1], command_times, command_times[1:]):
            values = [float(v) for v in (directory / f'baseline-{tag}-time.tsv').read_text().split()]
            assert len(values) == 3 and all(isfinite(v) and v >= 0 for v in values)
            assert values[0] * 1e9 <= end - start + 2_000_000
            result['baseline_commands'][tag] = dict(boundary_ns=end-start, real_seconds=values[0], user_seconds=values[1], system_seconds=values[2])
        result['baseline_outside_command_boundaries_ns'] = durations['baseline'] - (command_times[-1] - command_times[0])
        if baseline_add_trace:
            from git_step_diagnostic import add_performance_records,add_trace2_records
            from guest_diskstats import parse,delta
            argv=['git','-C',str(baseline_repository),'add','-A']
            performance=add_performance_records((directory/'baseline-add-performance.txt').read_text(),argv)
            trace=add_trace2_records((directory/'baseline-add-events.jsonl').read_text(),performance,argv)
            assert abs(performance['command_seconds']-trace['command_seconds'])<.003
            assert trace['command_seconds']<=result['baseline_commands']['add']['real_seconds']+.003
            disks=delta(*(parse((directory/f'baseline-add-{phase}.diskstats').read_text()) for phase in ('before','after')))
            result['baseline_add_trace']=dict(performance=performance,trace2=trace,diskstats=disks)
    return result
