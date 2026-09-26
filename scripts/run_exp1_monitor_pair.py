"""One correctness-checked Exp1 pair: sampling off, then 100 ms sampling."""
import csv
import datetime
import hashlib
import json
import os
import pathlib
import signal
import subprocess

root = pathlib.Path(__file__).resolve().parent.parent
base = json.loads((root / 'configs/exp1-agentenv.json').read_text())
pair_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
out = root / '.artifacts/exp1-monitor-overhead' / pair_id
os.umask(0o077)
out.mkdir(parents=True)
reference = root / base['output_root'] / 'raw/20260924T035910Z-1677178/replay/patch.diff'
assert reference.is_file()


def stage(label, sampled):
    arm = out / 'arms' / label
    arm.mkdir(parents=True)
    cfg = dict(base, output_root=str(arm), variant=f'exp1-monitor-pilot-{label}',
               guest_sampling=sampled, host_sampling=sampled, generate_analysis=False)
    config = arm / 'effective-config.json'
    config.write_text(json.dumps(cfg, indent=2) + '\n')
    with (arm / 'controller.log').open('w') as log:
        process = subprocess.Popen(['python3', str(root / 'scripts/run_exp1.py'), str(config)],
                                   cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = process.wait(timeout=660)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=70)
            except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()
            rc = 124
    runs = list((arm / 'raw').iterdir())
    assert len(runs) == 1, (label, runs)
    raw = runs[0]
    audit = arm / 'audit' / raw.name
    checks = json.loads((audit / 'checks.json').read_text())
    assert rc == 0 and json.loads((audit / 'result.json').read_text())['status'] == 'PASS', label
    assert checks['replay_correctness'] == 'PASS' and checks['cleanup'] == 'PASS'
    assert checks['host_sampling'] == ('PASS' if sampled else 'DISABLED')
    assert checks['guest_sampling'] == ('PASS' if sampled else 'DISABLED')
    patch = (raw / 'replay/patch.diff').read_bytes()
    assert patch == reference.read_bytes(), f'{label}: patch differs from full replay'
    with (raw / 'replay/steps.tsv').open() as f:
        steps = list(csv.DictReader(f, delimiter='\t'))
    result = json.loads((audit / 'result.json').read_text())
    return dict(label=label, run_id=raw.name, patch_sha256=hashlib.sha256(patch).hexdigest(),
                guest_action_sum_ns=sum(int(x['duration_ns']) for x in steps),
                controller_exec_ns=int((raw / 'controller-exec-duration-ns.txt').read_text()),
                controller_total_ns=result['controller_elapsed_ns'],
                cold_start_ns=int((raw / 'cold-start-duration-ns.txt').read_text()),
                raw_dir=str(raw), audit_dir=str(audit))


print(f'status=RUNNING pair_id={pair_id} result_dir={out}', flush=True)
results = []
status = 'FAIL'
try:
    for label, sampled in (('no-monitor', False), ('diagnostic-100ms', True)):
        results.append(stage(label, sampled))
        (out / 'partial-results.json').write_text(json.dumps(results, indent=2) + '\n')
    base_arm, sampled_arm = results
    ratios = {}
    for key in ('guest_action_sum_ns', 'controller_exec_ns', 'controller_total_ns', 'cold_start_ns'):
        ratios[key] = 100 * (sampled_arm[key] / base_arm[key] - 1)
    report = dict(status='PASS', pair_id=pair_id, order=[x['label'] for x in results],
                  arms=results, sampled_vs_no_monitor_percent=ratios,
                  interpretation='one warm-cache sequential pilot pair; no variance or 5% acceptance claim',
                  cache_policy='preserve guest and host caches; no drop_caches',
                  source_image=next(line.split('=', 1)[1] for line in
                    (root / base['output_root'] / 'raw/20260924T035910Z-1677178/metadata.env').read_text().splitlines()
                    if line.startswith('workload_image=')))
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Exp1 监控开销单组验证', '',
             '顺序：无采样 → guest/host 每 100 ms 采样；两组使用同一固定动作、镜像 digest 和资源配额。',
             '两组的 26 步顺序、预期失败集合、关键输出、清理均通过，最终 patch 与既有完整重放逐字节一致。', '',
             '| 指标 | 无采样 (ms) | 100 ms 采样 (ms) | 相对变化 |', '| --- | ---: | ---: | ---: |']
    names = {'guest_action_sum_ns': 'guest 动作耗时之和',
             'controller_exec_ns': '控制器工具调用耗时',
             'controller_total_ns': '控制器端到端耗时',
             'cold_start_ns': '冷启动耗时'}
    for key, name in names.items():
        lines.append(f'| {name} | {base_arm[key]/1e6:.1f} | {sampled_arm[key]/1e6:.1f} | {ratios[key]:+.1f}% |')
    lines.extend(['', '本次只有顺序执行的一组配对。缓存继续升温和主机其他负载可能影响结果；相对变化仅用于验证采集与报告流程，不足以判断是否达到 5% 目标。',
                  'guest 动作耗时之和不等于控制器工具调用耗时，也不等于端到端时间。原始数据、配置和日志位于 arms/ 下各自的 run_id。', ''])
    (out / 'report.md').write_text('\n'.join(lines))
    status = 'PASS'
except Exception:
    import traceback
    (out / 'failure.txt').write_text(traceback.format_exc())
finally:
    print(f'status={status} pair_id={pair_id} result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
