"""Ten sequential, correctness-checked Exp1 pairs for light host-monitor overhead."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import random
import signal
import statistics
import subprocess
import sys
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
series_config = json.loads(pathlib.Path(sys.argv[1]).read_text())
base = json.loads((root / 'configs/exp1-agentenv.json').read_text())
assert series_config['pairs'] == 10
assert series_config['guest_sampling'] is False
assert series_config['light_host_monitor'] is True
assert series_config['generate_analysis'] is False
reference = root / base['output_root'] / 'raw/20260924T035910Z-1677178/replay/patch.diff'
assert reference.is_file(), reference
reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
series_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/exp1-monitor-series' / series_id
os.umask(0o077)
out.mkdir(parents=True)
(out / 'series-config.json').write_text(json.dumps(series_config, indent=2) + '\n')
(out / 'reference-patch-sha256.txt').write_text(reference_hash + '\n')


def host_context():
    return dict(loadavg=os.getloadavg(),
                memory_pressure=(pathlib.Path('/proc/pressure/memory').read_text().strip()
                                 if pathlib.Path('/proc/pressure/memory').exists() else 'unavailable'))


def stage(pair_number, mode):
    arm = out / 'pairs' / f'{pair_number:02}' / mode
    arm.mkdir(parents=True)
    sampled = mode == 'performance'
    cfg = dict(base, output_root=str(arm), variant=f'exp1-light-host-overhead-{mode}',
               guest_sampling=False, host_sampling=sampled,
               host_monitor_light=sampled,
               sample_interval_seconds=series_config['sample_interval_seconds'],
               generate_analysis=False)
    config_path = arm / 'effective-config.json'
    config_path.write_text(json.dumps(cfg, indent=2) + '\n')
    before = host_context()
    with (arm / 'controller.log').open('w') as log:
        process = subprocess.Popen(['python3', str(root / 'scripts/run_exp1.py'), str(config_path)],
                                   cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            rc = process.wait(timeout=690)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=70)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            rc = 124
    after = host_context()
    runs = list((arm / 'raw').iterdir()) if (arm / 'raw').exists() else []
    assert len(runs) == 1, (mode, rc, runs)
    raw = runs[0]
    audit = arm / 'audit' / raw.name
    checks = json.loads((audit / 'checks.json').read_text())
    result = json.loads((audit / 'result.json').read_text())
    assert rc == 0 and result['status'] == 'PASS', (mode, rc, checks)
    for check in ('input_hashes', 'runner', 'replay_correctness', 'cleanup', 'task_window'):
        assert checks[check] == 'PASS', (mode, check, checks)
    assert checks['guest_sampling'] == 'DISABLED'
    assert checks['host_sampling'] == ('PASS' if sampled else 'DISABLED')
    patch = (raw / 'replay/patch.diff').read_bytes()
    assert hashlib.sha256(patch).hexdigest() == reference_hash
    assert patch == reference.read_bytes(), f'{mode}: patch differs from full replay'
    with (raw / 'replay/steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    assert len(steps) == 26
    host_samples = []
    if sampled:
        with (raw / 'host/memory-samples.tsv').open() as f:
            host_samples = list(csv.DictReader(f, delimiter='\t'))
    if sampled:
        assert len(host_samples) >= 2, f'{mode}: fewer than two host samples'
    return dict(mode=mode, run_id=raw.name, raw_dir=str(raw), audit_dir=str(audit),
                patch_sha256=reference_hash, host_sample_count=len(host_samples),
                task_window_duration_ns=int((raw / 'task-window-duration-ns.txt').read_text()),
                controller_exec_ns=int((raw / 'controller-exec-duration-ns.txt').read_text()),
                controller_total_ns=result['controller_elapsed_ns'],
                cold_start_ns=int((raw / 'cold-start-duration-ns.txt').read_text()),
                guest_action_sum_ns=sum(int(x['duration_ns']) for x in steps),
                host_context_before=before, host_context_after=after)


def percent_pairs(pairs, metric):
    return [100 * (next(x for x in p['arms'] if x['mode'] == 'performance')[metric] /
                   next(x for x in p['arms'] if x['mode'] == 'off')[metric] - 1)
            for p in pairs]


def summary(values):
    median = statistics.median(values)
    rng = random.Random(0)
    boot = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(20000))
    return dict(median_percent=median, min_percent=min(values), max_percent=max(values),
                bootstrap_median_95_percent=[boot[499], boot[19499]],
                paired_percent=values)


def write_report(pairs, metrics):
    primary = metrics['task_window_duration_ns']
    low, high = primary['bootstrap_median_95_percent']
    threshold = series_config['acceptance_threshold_percent']
    verdict = ('within_5_percent' if high <= threshold else
               'above_5_percent' if low > threshold else 'inconclusive')
    report = dict(status='PASS', series_id=series_id, pairs=pairs,
                  metrics=metrics, threshold_verdict=verdict,
                  bootstrap='20,000 paired median resamples; seed 0; percentile 95% interval',
                  scope='light asynchronous 1 s service-cgroup sampling only; guest sampling off in both arms',
                  reference_patch_sha256=reference_hash)
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Exp1 轻量 host 监控开销：10 对', '',
             '每对串行重放相同的 26 个冻结动作；奇数对无监控→性能模式，偶数对反向。首对为 smoke，成功后才继续其余 9 对。',
             '性能模式每 1 秒异步采集所选 AgentENV 服务 cgroup 的 memory.current/stat；两臂均关闭 guest 采样；不扫描 VMM smaps。未清空缓存或重启服务。',
             '每臂输入哈希、动作顺序、预期失败、关键输出、最终 patch 和沙箱清理均通过；patch 与既有完整重放逐字节相同。', '',
             '| 对 | 顺序 | 无监控任务窗口 (ms) | 性能模式任务窗口 (ms) | 相对变化 | 性能模式样本数 |',
             '| ---: | --- | ---: | ---: | ---: | ---: |']
    for pair in pairs:
        off = next(x for x in pair['arms'] if x['mode'] == 'off')
        perf = next(x for x in pair['arms'] if x['mode'] == 'performance')
        pct = 100 * (perf['task_window_duration_ns'] / off['task_window_duration_ns'] - 1)
        lines.append(f"| {pair['number']} | {' → '.join(x['mode'] for x in pair['arms'])} | {off['task_window_duration_ns']/1e6:.1f} | {perf['task_window_duration_ns']/1e6:.1f} | {pct:+.1f}% | {perf['host_sample_count']} |")
    lines += ['', f"主要指标：任务窗口配对相对变化中位数 {primary['median_percent']:+.1f}%，范围 {primary['min_percent']:+.1f}% 至 {primary['max_percent']:+.1f}%；配对 bootstrap 中位数 95% 区间 {low:+.1f}% 至 {high:+.1f}%。相对于 5% 暂定目标，判定为 `{verdict}`。", '',
              '| 次要指标 | 配对变化中位数 | 范围 |', '| --- | ---: | ---: |']
    labels = {'controller_exec_ns': '控制器工具调用', 'controller_total_ns': '完整控制器流程',
              'cold_start_ns': '冷启动', 'guest_action_sum_ns': 'guest 动作耗时之和'}
    for key, label in labels.items():
        s = metrics[key]
        lines.append(f"| {label} | {s['median_percent']:+.1f}% | {s['min_percent']:+.1f}% 至 {s['max_percent']:+.1f}% |")
    lines += ['', '任务窗口从启动监控前至动作结束并停止监控后计时；不含冷启动、上传、下载和清理。完整控制器流程另列；guest 动作耗时之和不是端到端时间。',
              '该估计仅覆盖轻量 host cgroup 采集，不覆盖 guest 采样、VMM PSS 或精细物理页扫描。短任务、热缓存逐渐变化和其他主机负载仍会影响配对估计；bootstrap 区间不能消除这些系统偏差。',
              '服务 cgroup 指标含共享池开销，不能归因到单个沙箱。原始数据、每臂配置、日志、每臂开始和结束时的主机 loadavg/内存 PSI 均保留在本目录。', '']
    (out / 'report.md').write_text('\n'.join(lines))
    return verdict


print(f'status=RUNNING series_id={series_id} result_dir={out}', flush=True)
pairs = []
status = 'FAIL'
try:
    for number in range(1, series_config['pairs'] + 1):
        modes = ('off', 'performance') if number % 2 else ('performance', 'off')
        pair = dict(number=number, arms=[])
        pairs.append(pair)
        for mode in modes:
            pair['arms'].append(stage(number, mode))
            (out / 'partial-results.json').write_text(json.dumps(pairs, indent=2) + '\n')
        print(f'status=PAIR_PASS pair={number}/{series_config["pairs"]} result_dir={out}', flush=True)
    names = ('task_window_duration_ns', 'controller_exec_ns', 'controller_total_ns',
             'cold_start_ns', 'guest_action_sum_ns')
    metrics = {name: summary(percent_pairs(pairs, name)) for name in names}
    verdict = write_report(pairs, metrics)
    print(f'status=PASS verdict={verdict} median={metrics["task_window_duration_ns"]["median_percent"]:+.1f}% result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
    (out / 'partial-results.json').write_text(json.dumps(pairs, indent=2) + '\n')
finally:
    if status != 'PASS':
        print(f'status=FAIL completed_pairs={sum(len(p["arms"]) == 2 for p in pairs)} result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
