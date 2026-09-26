"""Four independent same-VM calibrations with balanced phase order."""
import datetime
import hashlib
import json
import os
import pathlib
import random
import re
import shutil
import signal
import statistics
import subprocess
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
first = root / '.artifacts/exp1-monitor-calibration/20260924T131644Z-1769928/result.json'
base = json.loads((root / 'configs/exp1-monitor-calibration.json').read_text())
series_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/exp1-monitor-calibration-series' / series_id
os.umask(0o077)
out.mkdir(parents=True, exist_ok=False)
(out / 'design.json').write_text(json.dumps(dict(runs=4, phase_iterations=32,
    layouts=['AA first / ABBA', 'AB first / BAAB', 'AA first / BAAB', 'AB first / ABBA'],
    first_result=str(first)), indent=2) + '\n')
snapshot = out / 'code-snapshot'
snapshot.mkdir()
for path in (pathlib.Path(__file__), root / 'scripts/run_exp1_monitor_calibration.py',
             root / 'scripts/25-run-exp1-monitor-calibration-series.sh',
             root / 'configs/exp1-monitor-calibration.json'):
    shutil.copy2(path, snapshot / path.name)
(snapshot / 'sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in snapshot.iterdir()}, indent=2) + '\n')


def block(group, orientation):
    if group == 'AA':
        names = ('aa-A1', 'aa-B1', 'aa-B2', 'aa-A2') if orientation == 'ABBA' else (
                 'aa-B1', 'aa-A1', 'aa-A2', 'aa-B2')
        return [dict(name=name, mode='off') for name in names]
    names = ('ab-off1', 'ab-performance1', 'ab-performance2', 'ab-off2') if orientation == 'ABBA' else (
             'ab-performance1', 'ab-off1', 'ab-off2', 'ab-performance2')
    return [dict(name=name, mode='performance' if 'performance' in name else 'off') for name in names]


layouts = [(('AA', 'AB'), 'ABBA'), (('AB', 'AA'), 'BAAB'),
           (('AA', 'AB'), 'BAAB'), (('AB', 'AA'), 'ABBA')]


def check_result(path, index):
    data = json.loads(path.read_text())
    assert data['status'] == 'PASS' and data['variant'] == 'calibration'
    assert data['phase_iterations'] == 32 and len(data['phases']) == 8
    expected = [p['name'] for group in layouts[index - 1][0]
                for p in block(group, layouts[index - 1][1])]
    assert [p['name'] for p in data['phases']] == expected
    assert (path.parent / 'cleanup-status.txt').read_text().strip() == 'PASS'
    for phase in data['phases']:
        raw = pathlib.Path(phase['raw_dir'])
        with (raw / 'calls.tsv').open() as f:
            calls = f.read().splitlines()
        assert len(calls) == 33
        assert phase['host_sample_count'] >= 2 if phase['mode'] == 'performance' else phase['host_sample_count'] == 0
    return dict(index=index, result_path=str(path), series_id=data['series_id'],
                aa_placebo_percent=data['aa_placebo_percent'],
                ab_performance_vs_off_percent=data['ab_performance_vs_off_percent'],
                phase_order=expected)


def run_one(index):
    groups, orientation = layouts[index - 1]
    plan = dict(base, phases=[p for group in groups for p in block(group, orientation)])
    plan_path = out / f'run-{index:02}-config.json'
    plan_path.write_text(json.dumps(plan, indent=2) + '\n')
    log_path = out / f'run-{index:02}.log'
    with log_path.open('w') as log:
        process = subprocess.Popen(['python3', str(root / 'scripts/run_exp1_monitor_calibration.py'),
                                    str(plan_path)], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            rc = process.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            rc = 124
    text = log_path.read_text()
    match = re.search(r'status=(?:PASS|FAIL) series_id=\S+ result_dir=(\S+)', text)
    assert match, (index, rc, str(log_path))
    result_dir = pathlib.Path(match.group(1))
    assert rc == 0, (index, rc, str(result_dir), str(log_path))
    return check_result(result_dir / 'result.json', index)


def write_report(runs):
    aa = [x['aa_placebo_percent'] for x in runs]
    ab = [x['ab_performance_vs_off_percent'] for x in runs]
    rng = random.Random(0)
    boot = sorted(statistics.median(rng.choices(ab, k=len(ab))) for _ in range(20000))
    interval = [boot[499], boot[19499]]
    result = dict(status='PASS', series_id=series_id, runs=runs,
                  aa_placebo_percent=aa, ab_performance_vs_off_percent=ab,
                  aa_median_percent=statistics.median(aa),
                  ab_median_percent=statistics.median(ab),
                  ab_median_95_percent=interval,
                  interpretation='same-VM repeated action 024; separate from full Exp1 trajectory acceptance')
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    lines = ['# Exp1 action 024 同 VM 监控校准：4 次独立沙箱', '',
             '每个沙箱先完整重放 26 步建立相同文件状态，暖机后在同 VM 执行八个 32 次调用阶段；其中四阶段为无采样 A/A，四阶段为轻量 host cgroup 采集 A/B。四种阶段组顺序和内部 ABBA/BAAB 顺序各出现一次。',
             '所有重复调用的 stdout SHA-256 与原轨迹第 024 步一致；工具窗口单独计时，监控启动与停止另计。', '',
             '| VM | 阶段顺序 | A/A 安慰剂变化 | A/B 采样变化 |',
             '| ---: | --- | ---: | ---: |']
    for r in runs:
        groups, orientation = layouts[r['index'] - 1]
        lines.append(f"| {r['index']} | {'→'.join(groups)} / {orientation} | {r['aa_placebo_percent']:+.2f}% | {r['ab_performance_vs_off_percent']:+.2f}% |")
    lines += ['', f"A/A 中位数 {result['aa_median_percent']:+.2f}%；A/B 中位数 {result['ab_median_percent']:+.2f}%，4 个 VM 的 bootstrap 中位数 95% 区间 {interval[0]:+.2f}% 至 {interval[1]:+.2f}%。样本仍少，区间只作描述性参考。",
              '此实验隔离了原轨迹第 024 步在同一 VM、暖缓存下的重复工具调用，不能替代完整 26 步轨迹的监控开销验收。原始结果、每次 stdout/stderr、host 样本、配置和清理证据见各 result_path。', '']
    (out / 'report.md').write_text('\n'.join(lines))


print(f'status=RUNNING series_id={series_id} result_dir={out}', flush=True)
runs = []
status = 'FAIL'
try:
    runs.append(check_result(first, 1))
    (out / 'partial-results.json').write_text(json.dumps(runs, indent=2) + '\n')
    print(f'status=RUN_PASS run=1/4 source=existing result_dir={out}', flush=True)
    for index in range(2, 5):
        runs.append(run_one(index))
        (out / 'partial-results.json').write_text(json.dumps(runs, indent=2) + '\n')
        print(f'status=RUN_PASS run={index}/4 result_dir={out}', flush=True)
    write_report(runs)
    print(f'status=PASS ab_median={statistics.median(r["ab_performance_vs_off_percent"] for r in runs):+.2f}% result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
finally:
    if status != 'PASS':
        print(f'status=FAIL completed_runs={len(runs)}/4 result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
