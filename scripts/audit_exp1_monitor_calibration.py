"""Recompute calibration series from raw calls, outputs, samples and cleanup receipts."""
import csv
import hashlib
import json
import pathlib
import re
import statistics
import sys

out = pathlib.Path(sys.argv[1]).resolve()
series_bytes = (out / 'result.json').read_bytes()
series = json.loads(series_bytes)
assert series['status'] == 'PASS' and len(series['runs']) == 4


def psi_some_delta(phase, resource):
    before = phase['host_context_before']['pressure'][resource]
    after = phase['host_context_after']['pressure'][resource]
    pattern = r'^some .*?total=(\d+)'
    a = re.search(pattern, before, re.M)
    b = re.search(pattern, after, re.M)
    assert a and b
    return int(b.group(1)) - int(a.group(1))


def ratio(phases, lhs, rhs):
    left = statistics.mean(phases[x]['tool_window_ns'] for x in lhs)
    right = statistics.mean(phases[x]['tool_window_ns'] for x in rhs)
    return 100 * (left / right - 1)


details = []
all_pressure = {'cpu': [], 'io': [], 'memory': []}
ids = []
call_count = 0
for item in series['runs']:
    run_path = pathlib.Path(item['result_path'])
    run = json.loads(run_path.read_text())
    assert run['status'] == 'PASS' and len(run['phases']) == 8
    assert (run_path.parent / 'cleanup-status.txt').read_text().strip() == 'PASS'
    ids.append(run['sandbox_id'])
    prep = run_path.parent / 'prep/replay'
    assert hashlib.sha256((prep / 'patch.diff').read_bytes()).hexdigest() == run['expected_patch_sha256']
    assert hashlib.sha256((prep / 'stdout/024.log').read_bytes()).hexdigest() == run['expected_stdout_024_sha256']
    phases = {}
    for phase in run['phases']:
        assert phase['name'] not in phases
        phases[phase['name']] = phase
        raw = pathlib.Path(phase['raw_dir'])
        with (raw / 'calls.tsv').open() as f:
            calls = list(csv.DictReader(f, delimiter='\t'))
        assert len(calls) == run['phase_iterations'] == 32
        assert [int(x['index']) for x in calls] == list(range(1, 33))
        assert sum(int(x['duration_ns']) for x in calls) == phase['call_duration_sum_ns']
        assert phase['tool_window_ns'] >= phase['call_duration_sum_ns']
        for call in calls:
            number = int(call['index'])
            stdout = (raw / 'stdout' / f'{number:03}.log').read_bytes()
            stderr = (raw / 'stderr' / f'{number:03}.log').read_bytes()
            assert hashlib.sha256(stdout).hexdigest() == call['stdout_sha256'] == run['expected_stdout_024_sha256']
            assert hashlib.sha256(stderr).hexdigest() == call['stderr_sha256']
            call_count += 1
        sample_path = raw / 'host-memory.tsv'
        if phase['mode'] == 'performance':
            with sample_path.open() as f:
                samples = list(csv.DictReader(f, delimiter='\t'))
            assert len(samples) == phase['host_sample_count'] >= 2
            assert all(int(x['firecracker_pid']) == 0 for x in samples)
        else:
            assert not sample_path.exists() and phase['host_sample_count'] == 0
        for resource in all_pressure:
            all_pressure[resource].append(psi_some_delta(phase, resource) / 1000)
    aa = ratio(phases, ('aa-B1', 'aa-B2'), ('aa-A1', 'aa-A2'))
    ab = ratio(phases, ('ab-performance1', 'ab-performance2'), ('ab-off1', 'ab-off2'))
    assert abs(aa - run['aa_placebo_percent']) < 1e-9
    assert abs(ab - run['ab_performance_vs_off_percent']) < 1e-9
    assert abs(aa - item['aa_placebo_percent']) < 1e-9
    assert abs(ab - item['ab_performance_vs_off_percent']) < 1e-9
    details.append(dict(index=item['index'], sandbox_id=run['sandbox_id'],
                        aa_placebo_percent=aa, ab_performance_vs_off_percent=ab,
                        phase_order=[x['name'] for x in run['phases']]))

assert len(ids) == len(set(ids)) == 4
assert call_count == 4 * 8 * 32
assert abs(statistics.median(x['ab_performance_vs_off_percent'] for x in details) - series['ab_median_percent']) < 1e-9
pressure = {name: dict(min_ms=min(values), median_ms=statistics.median(values), max_ms=max(values))
            for name, values in all_pressure.items()}
audit = dict(status='PASS', source_sha256=hashlib.sha256(series_bytes).hexdigest(),
             auditor_sha256=hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
             independent_sandboxes=len(ids), phases=32, repeated_calls=call_count,
             details=details, host_psi_some_per_phase=pressure,
             limitation='A/A bias and four VMs preclude a 5% acceptance claim; repeated action differs from full trajectory')
(out / 'raw-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
lines = ['# Exp1 同 VM 校准原始数据复核', '',
         '从 4 个独立沙箱的原始调用 TSV、每次 stdout/stderr、准备阶段 patch、host 样本和清理回执重新核对。',
         f'共核对 {call_count} 次重复调用、32 个阶段；各次 stdout 与原轨迹第 024 步逐字节一致，四个沙箱已清理。', '',
         '| VM | A/A 安慰剂变化 | A/B 采样变化 |', '| ---: | ---: | ---: |']
for d in details:
    lines.append(f"| {d['index']} | {d['aa_placebo_percent']:+.2f}% | {d['ab_performance_vs_off_percent']:+.2f}% |")
lines += ['',
          f"A/A 四次均为负，范围 {min(x['aa_placebo_percent'] for x in details):+.2f}% 至 {max(x['aa_placebo_percent'] for x in details):+.2f}%；A/B 范围 {min(x['ab_performance_vs_off_percent'] for x in details):+.2f}% 至 {max(x['ab_performance_vs_off_percent'] for x in details):+.2f}%。阶段顺序与单次调用耗时的波动仍需解释，不能把 A/B 小差值直接当作监控总开销上界。",
          f"主机 CPU PSI some 每阶段增量 {pressure['cpu']['min_ms']:.1f}–{pressure['cpu']['max_ms']:.1f} ms，I/O 为 {pressure['io']['min_ms']:.3f}–{pressure['io']['max_ms']:.3f} ms，内存为 {pressure['memory']['min_ms']:.3f}–{pressure['memory']['max_ms']:.3f} ms；这些值覆盖阶段边界，不足以单独归因工具耗时。",
          '该实验仅校准同 VM 暖状态下的单个重复工具动作，不能代替完整 26 步轨迹的 5% 验收。', '']
(out / 'raw-audit.md').write_text('\n'.join(lines))
print(f'status=PASS sandboxes={len(ids)} repeated_calls={call_count} result_dir={out}')
