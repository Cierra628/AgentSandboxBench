"""One ABBA block of four independent Exp1 replays; method smoke only."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import signal
import statistics
import subprocess
import sys
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
plan = json.loads(pathlib.Path(sys.argv[1]).read_text())
base = json.loads((root / 'configs/exp1-agentenv.json').read_text())
valid_orders = (['off', 'performance', 'performance', 'off'],
                ['performance', 'off', 'off', 'performance'])
assert plan['blocks'] == 1 and plan['order'] in valid_orders
assert plan['guest_sampling'] is False and plan['performance_host_sampling'] is True
reference = root / base['output_root'] / 'raw/20260924T035910Z-1677178/replay/patch.diff'
assert reference.is_file()
reference_sha = hashlib.sha256(reference.read_bytes()).hexdigest()
block_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / plan.get('output_root', '.artifacts/exp1-monitor-block-smoke') / plan.get('block_label', block_id)
os.umask(0o077)
out.mkdir(parents=True, exist_ok=False)
(out / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
snapshot = out / 'code-snapshot'
snapshot.mkdir()
for path in (pathlib.Path(__file__), root / 'scripts/run_exp1.py',
             root / 'scripts/22-run-exp1-monitor-block-smoke.sh',
             root / 'configs/exp1-monitor-block-smoke.json',
             root / 'patches/incrementaldax-platform.patch'):
    shutil.copy2(path, snapshot / path.name)
(snapshot / 'sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in snapshot.iterdir()}, indent=2) + '\n')


def host_context():
    return dict(loadavg=os.getloadavg(),
                pressure={name: (pathlib.Path('/proc/pressure') / name).read_text().strip()
                          for name in ('cpu', 'io', 'memory')})


def stop_group(process):
    children = subprocess.run(['pgrep', '-P', str(process.pid)], capture_output=True, text=True).stdout.split()
    groups = set()
    for child in children:
        try:
            groups.add(os.getpgid(int(child)))
        except ProcessLookupError:
            pass
    groups.add(process.pid)
    for group in groups:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=70)
    except subprocess.TimeoutExpired:
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()


def stage(index, mode):
    arm = out / 'arms' / f'{index:02}-{mode}'
    arm.mkdir(parents=True)
    sampled = mode == 'performance'
    cfg = dict(base, output_root=str(arm), variant=f'exp1-monitor-block-smoke-{mode}',
               guest_sampling=False, host_sampling=sampled, host_monitor_light=sampled,
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
    assert len(runs) == 1, (index, mode, rc, runs)
    raw = runs[0]
    audit = arm / 'audit' / raw.name
    checks = json.loads((audit / 'checks.json').read_text())
    result = json.loads((audit / 'result.json').read_text())
    assert rc == 0 and result['status'] == 'PASS', (index, mode, rc, checks)
    assert all(checks[x] == 'PASS' for x in
               ('input_hashes', 'runner', 'replay_correctness', 'cleanup', 'task_window'))
    assert checks['guest_sampling'] == 'DISABLED'
    assert checks['host_sampling'] == ('PASS' if sampled else 'DISABLED')
    patch = (raw / 'replay/patch.diff').read_bytes()
    assert hashlib.sha256(patch).hexdigest() == reference_sha and patch == reference.read_bytes()
    with (raw / 'replay/steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    assert len(steps) == 26
    if sampled:
        with (raw / 'host/memory-samples.tsv').open() as f:
            count = len(list(csv.DictReader(f, delimiter='\t')))
        assert count >= 2
    else:
        assert not (raw / 'host/memory-samples.tsv').exists()
        count = 0
    return dict(index=index, mode=mode, run_id=raw.name, raw_dir=str(raw), audit_dir=str(audit),
                host_sample_count=count, patch_sha256=reference_sha,
                task_window_duration_ns=int((raw / 'task-window-duration-ns.txt').read_text()),
                controller_exec_ns=int((raw / 'controller-exec-duration-ns.txt').read_text()),
                controller_total_ns=result['controller_elapsed_ns'],
                guest_action_sum_ns=sum(int(x['duration_ns']) for x in steps),
                host_context_before=before, host_context_after=after)


print(f'status=RUNNING block_id={block_id} result_dir={out}', flush=True)
arms = []
status = 'FAIL'
try:
    for index, mode in enumerate(plan['order'], 1):
        arms.append(stage(index, mode))
        (out / 'partial-results.json').write_text(json.dumps(arms, indent=2) + '\n')
        print(f'status=ARM_PASS arm={index}/4 result_dir={out}', flush=True)
    means = {mode: statistics.mean(a['task_window_duration_ns'] for a in arms if a['mode'] == mode)
             for mode in ('off', 'performance')}
    block_percent = 100 * (means['performance'] / means['off'] - 1)
    report = dict(status='PASS', purpose='method smoke; not a 5% acceptance test', block_id=block_id,
                  order=plan['order'], arms=arms, mean_task_window_ns=means,
                  performance_vs_off_percent=block_percent,
                  reference_patch_sha256=reference_sha)
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    order_text = '→'.join(plan['order'])
    lines = ['# Exp1 四次重放交错块 smoke', '',
             f'顺序为 {order_text}。四次独立沙箱均完成原来的 26 步冻结轨迹；各自 patch 与完整重放逐字节一致，且清理通过。',
             '两次无采样和两次采样分别取任务窗口均值；首尾一种模式、居中另一种模式的安排旨在减弱近似线性时间漂移。', '',
             '| 臂 | 模式 | 任务窗口 (ms) | guest 动作之和 (ms) | host 样本数 |',
             '| ---: | --- | ---: | ---: | ---: |']
    for arm in arms:
        lines.append(f"| {arm['index']} | {arm['mode']} | {arm['task_window_duration_ns']/1e6:.1f} | {arm['guest_action_sum_ns']/1e6:.1f} | {arm['host_sample_count']} |")
    lines += ['', f"本块采样/无采样任务窗口均值变化 {block_percent:+.1f}%。单个块没有方差估计，不能据此判断 5% 目标。",
              '四次运行的负载压力快照在 result.json 中，快照边界覆盖完整控制器运行而非仅动作窗口。原始日志和生效配置在 arms/ 下。', '']
    (out / 'report.md').write_text('\n'.join(lines))
    print(f'status=PASS block_delta={block_percent:+.1f}% result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
finally:
    if status != 'PASS':
        print(f'status=FAIL completed_arms={len(arms)}/4 result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
