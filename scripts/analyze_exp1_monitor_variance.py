"""Offline decomposition of an Exp1 monitoring series; no sandbox access."""
import csv
import hashlib
import json
import pathlib
import re
import statistics
import sys

result_dir = pathlib.Path(sys.argv[1]).resolve()
source_bytes = (result_dir / 'result.json').read_bytes()
series = json.loads(source_bytes)
assert series['status'] == 'PASS' and len(series['pairs']) == 10
expected_patch = series['reference_patch_sha256']


def psi_total(context, name):
    match = re.search(rf'^{name} .*?total=(\d+)', context['memory_pressure'], re.M)
    return int(match.group(1)) if match else None


def pearson(x, y):
    xm, ym = statistics.mean(x), statistics.mean(y)
    numerator = sum((a - xm) * (b - ym) for a, b in zip(x, y))
    denominator = (sum((a - xm) ** 2 for a in x) * sum((b - ym) ** 2 for b in y)) ** 0.5
    return numerator / denominator if denominator else None


rows = []
all_arms = []
step_durations = {}
for pair in series['pairs']:
    assert len(pair['arms']) == 2
    arms = {arm['mode']: arm for arm in pair['arms']}
    assert set(arms) == {'off', 'performance'}
    measured = {}
    for mode, arm in arms.items():
        raw, audit = pathlib.Path(arm['raw_dir']), pathlib.Path(arm['audit_dir'])
        assert json.loads((audit / 'result.json').read_text())['status'] == 'PASS'
        checks = json.loads((audit / 'checks.json').read_text())
        assert all(checks[x] == 'PASS' for x in
                   ('input_hashes', 'runner', 'replay_correctness', 'cleanup', 'task_window'))
        assert hashlib.sha256((raw / 'replay/patch.diff').read_bytes()).hexdigest() == expected_patch
        with (raw / 'replay/steps.tsv').open() as f:
            steps = list(csv.DictReader(f, delimiter='\t'))
        assert [x['step'] for x in steps] == [f'{i:03}' for i in range(1, 27)]
        durations = {x['step']: int(x['duration_ns']) for x in steps}
        assert sum(durations.values()) == arm['guest_action_sum_ns']
        assert int((raw / 'task-window-duration-ns.txt').read_text()) == arm['task_window_duration_ns']
        assert int((raw / 'controller-exec-duration-ns.txt').read_text()) == arm['controller_exec_ns']
        for step, duration in durations.items():
            step_durations.setdefault(step, []).append(duration / 1e6)
        measured[mode] = durations
        all_arms.append(arm)
    off, perf = arms['off'], arms['performance']
    delta = lambda key: (perf[key] - off[key]) / 1e6
    node_delta = sum((measured['performance'][x] - measured['off'][x]) / 1e6
                     for x in ('023', '024'))
    off_psi = psi_total(off['host_context_after'], 'some') - psi_total(off['host_context_before'], 'some')
    perf_psi = psi_total(perf['host_context_after'], 'some') - psi_total(perf['host_context_before'], 'some')
    row = dict(pair=pair['number'], order='→'.join(x['mode'] for x in pair['arms']),
               task_delta_ms=delta('task_window_duration_ns'),
               task_delta_percent=100 * (perf['task_window_duration_ns'] / off['task_window_duration_ns'] - 1),
               exec_delta_ms=delta('controller_exec_ns'),
               guest_delta_ms=delta('guest_action_sum_ns'),
               node_023_024_delta_ms=node_delta,
               outside_exec_delta_ms=delta('task_window_duration_ns') - delta('controller_exec_ns'),
               off_load1=off['host_context_before']['loadavg'][0],
               perf_load1=perf['host_context_before']['loadavg'][0],
               off_memory_psi_some_delta_us=off_psi,
               perf_memory_psi_some_delta_us=perf_psi)
    rows.append(row)

assert len(all_arms) == 20
task = [r['task_delta_ms'] for r in rows]
guest = [r['guest_delta_ms'] for r in rows]
node = [r['node_023_024_delta_ms'] for r in rows]
outside = [r['outside_exec_delta_ms'] for r in rows]
all_guest_ns = sum(a['guest_action_sum_ns'] for a in all_arms)
node_ns = sum((step_durations[x][i] for x in ('023', '024') for i in range(20))) * 1e6
same_mode_changes = {}
for mode in ('off', 'performance'):
    sequence = [next(a for a in pair['arms'] if a['mode'] == mode) for pair in series['pairs']]
    same_mode_changes[mode] = [100 * (sequence[i + 1]['task_window_duration_ns'] /
                                      sequence[i]['task_window_duration_ns'] - 1)
                               for i in range(0, 10, 2)]
metrics = {
    'task_delta_ms_median': statistics.median(task),
    'task_delta_ms_range': [min(task), max(task)],
    'outside_exec_delta_ms_median': statistics.median(outside),
    'outside_exec_delta_ms_range': [min(outside), max(outside)],
    'node_023_024_share_of_guest_action_time_percent': 100 * node_ns / all_guest_ns,
    'guest_delta_vs_task_delta_pearson_r': pearson(guest, task),
    'node_023_024_delta_vs_task_delta_pearson_r': pearson(node, task),
    'off_first_task_percent_median': statistics.median(r['task_delta_percent'] for r in rows if r['order'].startswith('off')),
    'performance_first_task_percent_median': statistics.median(r['task_delta_percent'] for r in rows if r['order'].startswith('performance')),
    'memory_psi_some_total_delta_us': sum(r['off_memory_psi_some_delta_us'] + r['perf_memory_psi_some_delta_us'] for r in rows),
    'load1_before_range': [min(min(r['off_load1'], r['perf_load1']) for r in rows),
                           max(max(r['off_load1'], r['perf_load1']) for r in rows)],
    'adjacent_pair_same_mode_changes_percent': same_mode_changes,
}
out = dict(status='PASS', source=str(result_dir / 'result.json'),
           source_sha256=hashlib.sha256(source_bytes).hexdigest(),
           analyzer_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
           pairs=rows, metrics=metrics,
           interpretation='descriptive decomposition only; correlations are not causal overhead estimates')
(result_dir / 'variance-analysis.json').write_text(json.dumps(out, indent=2) + '\n')

lines = ['# Exp1 10 对开销波动的离线分解', '',
         '读取每臂的原始 steps.tsv、计时文件、patch 与 audit，重新核对了 20 次重放和 26 步动作；没有启动沙箱或增加实验轮数。',
         '', '| 对 | 顺序 | 任务窗口差 (ms) | 工具调用差 (ms) | guest 动作差 (ms) | 023+024 差 (ms) | 窗口减工具调用之差 (ms) |',
         '| ---: | --- | ---: | ---: | ---: | ---: | ---: |']
for r in rows:
    lines.append(f"| {r['pair']} | {r['order']} | {r['task_delta_ms']:+.1f} | {r['exec_delta_ms']:+.1f} | {r['guest_delta_ms']:+.1f} | {r['node_023_024_delta_ms']:+.1f} | {r['outside_exec_delta_ms']:+.1f} |")
lines += ['',
          f"任务窗口差中位数 {metrics['task_delta_ms_median']:+.1f} ms，范围 {min(task):+.1f} 至 {max(task):+.1f} ms。监控启动/停止所在的窗口外段配对差中位数 {metrics['outside_exec_delta_ms_median']:+.1f} ms，范围 {min(outside):+.1f} 至 {max(outside):+.1f} ms；这仅量到工具调用计时之外的编排时间，不能当作监控总开销。",
          f"guest 动作差与任务窗口差的 Pearson r={metrics['guest_delta_vs_task_delta_pearson_r']:.2f}；第 023、024 步（Node/Prettier 调用）合计占 20 次重放的 guest 动作时间 {metrics['node_023_024_share_of_guest_action_time_percent']:.1f}%，其配对差与任务窗口差的 r={metrics['node_023_024_delta_vs_task_delta_pearson_r']:.2f}。这是样本内关联，不能证明动作波动由采集造成。",
          f"先运行无监控的 5 对，任务窗口相对变化中位数 {metrics['off_first_task_percent_median']:+.1f}%；先运行性能模式的 5 对为 {metrics['performance_first_task_percent_median']:+.1f}%。两组均有正负波动，现有 5+5 对不能排除顺序、缓存或时间趋势的影响。",
          f"相邻两对中的同模式运行也会变化：无监控的 5 个变化范围 {min(same_mode_changes['off']):+.1f}% 至 {max(same_mode_changes['off']):+.1f}%，性能模式为 {min(same_mode_changes['performance']):+.1f}% 至 {max(same_mode_changes['performance']):+.1f}%。它们中间穿插了另一模式，不能当作预先设计的 A/A 试验，但说明本次运行期间同配置时延并不稳定。",
          f"20 次运行前的主机 1 分钟 load average 为 {metrics['load1_before_range'][0]:.2f} 至 {metrics['load1_before_range'][1]:.2f}；各臂记录的 memory PSI some 累计增量之和为 {metrics['memory_psi_some_total_delta_us']} μs。没有记录当时的 CPU/I/O PSI 或 guest cache 状态，因此无法归因剩余波动；低 load 和零 memory PSI 不等于没有竞争。",
          '', '判断：10 对实验可靠地验证了重放与轻量采集可以共存，但其 5% 开销结论仍不确定。现有数据指向工具调用内部的可变耗时，而不是监控进程启动/停止时间，下一轮应先建立同配置 A/A 噪声基线，并在边界记录 CPU/I/O PSI；随后再决定是否需要更长任务窗口或更多 A/B 配对。新 A/A 与 A/B 不合并到本次 10 对统计中。',
          '', '差值均为性能模式减无监控；动作耗时之和与控制器工具调用耗时及端到端时间是不同口径。原始 1 秒 cgroup 样本只有每臂 4–5 条，不能定位亚秒级停顿。', '']
(result_dir / 'variance-analysis.md').write_text('\n'.join(lines))
print(f'status=PASS pairs={len(rows)} result_dir={result_dir}')
