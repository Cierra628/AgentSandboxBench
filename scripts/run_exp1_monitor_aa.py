"""Ten correctness-checked Exp1 A/A pairs with both sampling modes disabled."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import random
import shutil
import signal
import statistics
import subprocess
import sys
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
plan = json.loads(pathlib.Path(sys.argv[1]).read_text())
base = json.loads((root / 'configs/exp1-agentenv.json').read_text())
assert plan['pairs'] == 10
assert plan['guest_sampling'] is False and plan['host_sampling'] is False
assert plan['generate_analysis'] is False
reference = root / base['output_root'] / 'raw/20260924T035910Z-1677178/replay/patch.diff'
assert reference.is_file()
reference_sha = hashlib.sha256(reference.read_bytes()).hexdigest()
series_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/exp1-monitor-aa' / series_id
os.umask(0o077)
out.mkdir(parents=True)
(out / 'series-config.json').write_text(json.dumps(plan, indent=2) + '\n')
snapshot = out / 'code-snapshot'
snapshot.mkdir()
for path in (pathlib.Path(__file__), root / 'scripts/run_exp1.py',
             root / 'scripts/21-run-exp1-monitor-aa.sh',
             root / 'configs/exp1-monitor-aa.json',
             root / 'patches/incrementaldax-platform.patch'):
    shutil.copy2(path, snapshot / path.name)
(snapshot / 'sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in snapshot.iterdir()}, indent=2) + '\n')
(snapshot / 'root-git-head.txt').write_text(subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root,
                                                           capture_output=True, text=True, check=True).stdout)


def host_context():
    pressure = {}
    for resource in ('cpu', 'io', 'memory'):
        pressure[resource] = (pathlib.Path('/proc/pressure') / resource).read_text().strip()
    return dict(loadavg=os.getloadavg(), pressure=pressure)


def stop_group(process):
    children = subprocess.run(['pgrep', '-P', str(process.pid)], capture_output=True, text=True).stdout.split()
    for child in children:
        try:
            os.killpg(os.getpgid(int(child)), signal.SIGTERM)
        except ProcessLookupError:
            pass
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=70)
    except subprocess.TimeoutExpired:
        for child in children:
            try:
                os.killpg(os.getpgid(int(child)), signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def stage(number, label):
    arm = out / 'pairs' / f'{number:02}' / label
    arm.mkdir(parents=True)
    cfg = dict(base, output_root=str(arm), variant='exp1-monitor-aa-identical',
               guest_sampling=False, host_sampling=False, host_monitor_light=False,
               sample_interval_seconds=plan['sample_interval_seconds'],
               generate_analysis=False)
    cfg_path = arm / 'effective-config.json'
    cfg_path.write_text(json.dumps(cfg, indent=2) + '\n')
    before = host_context()
    with (arm / 'controller.log').open('w') as log:
        process = subprocess.Popen(['python3', str(root / 'scripts/run_exp1.py'), str(cfg_path)],
                                   cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            rc = process.wait(timeout=730)
        except subprocess.TimeoutExpired:
            stop_group(process)
            rc = 124
    after = host_context()
    runs = list((arm / 'raw').iterdir()) if (arm / 'raw').exists() else []
    assert len(runs) == 1, (number, label, rc, runs)
    raw = runs[0]
    audit = arm / 'audit' / raw.name
    checks = json.loads((audit / 'checks.json').read_text())
    result = json.loads((audit / 'result.json').read_text())
    assert rc == 0 and result['status'] == 'PASS', (number, label, rc, checks)
    assert all(checks[x] == 'PASS' for x in
               ('input_hashes', 'runner', 'replay_correctness', 'cleanup', 'task_window'))
    assert checks['host_sampling'] == checks['guest_sampling'] == 'DISABLED'
    patch = (raw / 'replay/patch.diff').read_bytes()
    assert hashlib.sha256(patch).hexdigest() == reference_sha and patch == reference.read_bytes()
    with (raw / 'replay/steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    assert len(steps) == 26
    return dict(label=label, run_id=raw.name, raw_dir=str(raw), audit_dir=str(audit),
                patch_sha256=reference_sha,
                task_window_duration_ns=int((raw / 'task-window-duration-ns.txt').read_text()),
                controller_exec_ns=int((raw / 'controller-exec-duration-ns.txt').read_text()),
                controller_total_ns=result['controller_elapsed_ns'],
                cold_start_ns=int((raw / 'cold-start-duration-ns.txt').read_text()),
                guest_action_sum_ns=sum(int(x['duration_ns']) for x in steps),
                host_context_before=before, host_context_after=after)


def paired_percent(pairs, key):
    values = []
    for pair in pairs:
        arms = {a['label']: a for a in pair['arms']}
        values.append(100 * (arms['B'][key] / arms['A'][key] - 1))
    return values


def summary(values):
    rng = random.Random(0)
    boot = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(20000))
    return dict(median_percent=statistics.median(values),
                median_absolute_percent=statistics.median(map(abs, values)),
                range_percent=[min(values), max(values)],
                bootstrap_median_95_percent=[boot[499], boot[19499]],
                paired_percent=values)


def write_report(pairs, metrics):
    reference_path = root / plan['ab_reference_result']
    ab = json.loads(reference_path.read_text())
    ab_values = ab['metrics']['task_window_duration_ns']['paired_percent']
    aa = metrics['task_window_duration_ns']
    report = dict(status='PASS', series_id=series_id, pairs=pairs, metrics=metrics,
                  reference_ab_result=str(reference_path), reference_ab_series_id=ab['series_id'],
                  reference_patch_sha256=reference_sha,
                  ab_task_median_absolute_percent=statistics.median(map(abs, ab_values)),
                  interpretation='A/A placebo noise; separate time period from A/B; no subtraction as causal effect')
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Exp1 无采样 A/A 噪声基线：10 对', '',
             '两臂的运行配置相同，均关闭 host/guest 采样；A/B 只是用于计算有符号差值的标签。奇数对 A→B，偶数对 B→A。首对为 smoke。',
             '每臂完整重放 26 个冻结动作，保留缓存，不运行 drop_caches；独立 run_id、配置、日志和原始数据均在 pairs/ 下。', '',
             '| 对 | 顺序 | A 任务窗口 (ms) | B 任务窗口 (ms) | B/A 变化 |',
             '| ---: | --- | ---: | ---: | ---: |']
    for pair in pairs:
        arms = {a['label']: a for a in pair['arms']}
        a, b = arms['A'], arms['B']
        pct = 100 * (b['task_window_duration_ns'] / a['task_window_duration_ns'] - 1)
        lines.append(f"| {pair['number']} | {'→'.join(x['label'] for x in pair['arms'])} | {a['task_window_duration_ns']/1e6:.1f} | {b['task_window_duration_ns']/1e6:.1f} | {pct:+.1f}% |")
    lo, hi = aa['bootstrap_median_95_percent']
    lines += ['',
              f"A/A 任务窗口有符号变化中位数 {aa['median_percent']:+.1f}%，绝对变化中位数 {aa['median_absolute_percent']:.1f}%，范围 {aa['range_percent'][0]:+.1f}% 至 {aa['range_percent'][1]:+.1f}%；配对 bootstrap 中位数 95% 区间 {lo:+.1f}% 至 {hi:+.1f}%。",
              f"此前 A/B 10 对的任务窗口绝对变化中位数为 {report['ab_task_median_absolute_percent']:.1f}%。两批次不是同一时段，不能直接相减或将 A/A 作为 A/B 的因果校正；此比较仅用于判断当前短任务的噪声量级。",
              '20 臂的动作输入、顺序、预期失败、关键输出、patch、无采样和清理均已核对。每臂开始/结束时记录主机 loadavg 与 CPU/I/O/内存 PSI；这些边界包围完整控制器运行，并不精确等于动作窗口。',
              'A/A 不能测出监控开销；5% 目标仍须由另行设计的 A/B 检验。', '']
    (out / 'report.md').write_text('\n'.join(lines))


print(f'status=RUNNING series_id={series_id} result_dir={out}', flush=True)
pairs = []
status = 'FAIL'
try:
    for number in range(1, plan['pairs'] + 1):
        order = ('A', 'B') if number % 2 else ('B', 'A')
        pair = dict(number=number, arms=[])
        pairs.append(pair)
        for label in order:
            pair['arms'].append(stage(number, label))
            (out / 'partial-results.json').write_text(json.dumps(pairs, indent=2) + '\n')
        print(f'status=PAIR_PASS pair={number}/{plan["pairs"]} result_dir={out}', flush=True)
    names = ('task_window_duration_ns', 'controller_exec_ns', 'controller_total_ns',
             'cold_start_ns', 'guest_action_sum_ns')
    metrics = {name: summary(paired_percent(pairs, name)) for name in names}
    write_report(pairs, metrics)
    print(f'status=PASS median={metrics["task_window_duration_ns"]["median_percent"]:+.1f}% result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
    (out / 'partial-results.json').write_text(json.dumps(pairs, indent=2) + '\n')
finally:
    if status != 'PASS':
        print(f'status=FAIL completed_pairs={sum(len(p["arms"]) == 2 for p in pairs)} result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
