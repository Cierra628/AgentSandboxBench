"""Ten alternating ABBA/BAAB blocks; reuse the passed first-block smoke."""
import datetime
import hashlib
import json
import os
import pathlib
import random
import shutil
import statistics
import subprocess
import sys
import traceback

root = pathlib.Path(__file__).resolve().parent.parent
plan = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert plan['blocks'] == 10 and plan['replays_per_block'] == 4
assert plan['odd_order'] == ['off', 'performance', 'performance', 'off']
assert plan['even_order'] == ['performance', 'off', 'off', 'performance']
report_only = len(sys.argv) == 4 and sys.argv[2] == '--report-only'
assert len(sys.argv) == 2 or report_only
if report_only:
    out = pathlib.Path(sys.argv[3]).resolve()
    assert out.is_dir() and out.parent.name == 'exp1-monitor-block-series'
    series_id = out.name
else:
    series_id = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
    out = root / '.artifacts/exp1-monitor-block-series' / series_id
os.umask(0o077)
if not report_only:
    out.mkdir(parents=True, exist_ok=False)
    (out / 'series-config.json').write_text(json.dumps(plan, indent=2) + '\n')
    snapshot = out / 'code-snapshot'
    snapshot.mkdir()
    for path in (pathlib.Path(__file__), root / 'scripts/run_exp1_monitor_block_smoke.py',
                 root / 'scripts/run_exp1.py', root / 'scripts/23-run-exp1-monitor-block-series.sh',
                 root / 'configs/exp1-monitor-block-series.json',
                 root / 'patches/incrementaldax-platform.patch'):
        shutil.copy2(path, snapshot / path.name)
    (snapshot / 'sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                      for p in snapshot.iterdir()}, indent=2) + '\n')


def check_block(path, number):
    block = json.loads(path.read_text())
    expected = plan['odd_order'] if number % 2 else plan['even_order']
    assert block['status'] == 'PASS' and block['order'] == expected
    assert len(block['arms']) == 4
    assert [a['mode'] for a in block['arms']] == expected
    assert sum(a['mode'] == 'off' for a in block['arms']) == 2
    assert sum(a['mode'] == 'performance' for a in block['arms']) == 2
    for arm in block['arms']:
        assert pathlib.Path(arm['raw_dir']).is_dir()
        assert pathlib.Path(arm['audit_dir']).is_dir()
        assert arm['host_sample_count'] >= 2 if arm['mode'] == 'performance' else arm['host_sample_count'] == 0
    means = {mode: statistics.mean(a['task_window_duration_ns'] for a in block['arms']
                                   if a['mode'] == mode) for mode in ('off', 'performance')}
    delta = 100 * (means['performance'] / means['off'] - 1)
    assert abs(delta - block['performance_vs_off_percent']) < 1e-9
    return dict(number=number, result_path=str(path), order=expected,
                mean_task_window_ns=means, performance_vs_off_percent=delta,
                run_ids=[a['run_id'] for a in block['arms']])


def aa_placebo_blocks():
    aa = json.loads((root / plan['aa_reference_result']).read_text())
    assert aa['status'] == 'PASS' and len(aa['pairs']) == 10
    values = []
    for i in range(0, 10, 2):
        arms = aa['pairs'][i]['arms'] + aa['pairs'][i + 1]['arms']
        assert [a['label'] for a in arms] == ['A', 'B', 'B', 'A']
        means = {label: statistics.mean(a['task_window_duration_ns'] for a in arms
                                        if a['label'] == label) for label in ('A', 'B')}
        values.append(100 * (means['B'] / means['A'] - 1))
    return values


def write_report(blocks):
    values = [b['performance_vs_off_percent'] for b in blocks]
    rng = random.Random(0)
    boot = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(20000))
    interval = [boot[499], boot[19499]]
    threshold = plan['acceptance_threshold_percent']
    verdict = ('within_5_percent' if interval[1] <= threshold else
               'above_5_percent' if interval[0] > threshold else 'inconclusive')
    placebo = aa_placebo_blocks()
    aa = json.loads((root / plan['aa_reference_result']).read_text())
    ab = json.loads((root / plan['ab_reference_result']).read_text())
    result = dict(status='PASS', series_id=series_id, blocks=blocks,
                  block_delta_percent=values,
                  median_block_delta_percent=statistics.median(values),
                  mean_block_delta_percent=statistics.mean(values),
                  median_absolute_block_delta_percent=statistics.median(map(abs, values)),
                  bootstrap_median_95_percent=interval,
                  threshold_verdict=verdict,
                  aa_placebo_block_delta_percent=placebo,
                  aa_placebo_median_absolute_percent=statistics.median(map(abs, placebo)),
                  reference_aa_series_id=aa['series_id'],
                  reference_ab_series_id=ab['series_id'],
                  initial_report_generation_failed=(out / 'failure.txt').exists(),
                  report_rebuilt_without_sandbox_rerun=report_only,
                  interpretation='block-level descriptive comparison; AA and AB periods differ')
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    lines = ['# Exp1 交错块轻量监控开销：10 块', '',
             '每块完整重放 4 次原 26 步轨迹，2 次无采样、2 次轻量 host cgroup 采样；奇数块 ABBA，偶数块 BAAB。各臂都是独立沙箱。',
             '第 1 块复用已通过的 smoke，其脚本快照保存在原结果目录；第 2–10 块的脚本快照分别保存在各块结果目录。', '',
             '| 块 | 顺序 | 无采样均值 (ms) | 采样均值 (ms) | 相对变化 |',
             '| ---: | --- | ---: | ---: | ---: |']
    for b in blocks:
        m = b['mean_task_window_ns']
        lines.append(f"| {b['number']} | {'→'.join(b['order'])} | {m['off']/1e6:.1f} | {m['performance']/1e6:.1f} | {b['performance_vs_off_percent']:+.1f}% |")
    lines += ['',
              f"10 块变化中位数 {result['median_block_delta_percent']:+.1f}%，均值 {result['mean_block_delta_percent']:+.1f}%，绝对变化中位数 {result['median_absolute_block_delta_percent']:.1f}%；配对 bootstrap 中位数 95% 区间 {interval[0]:+.1f}% 至 {interval[1]:+.1f}%。对 5% 暂定目标的判定为 `{verdict}`。",
              f"已有 A/A 原始数据可按相邻两对重分成 5 个 ABBA 安慰剂块，其绝对变化中位数 {result['aa_placebo_median_absolute_percent']:.1f}%。它与本批不在同一时段，且只有 5 块，不能作为因果校正。",
              '所有臂的原始结果、配置、日志和正确性核对保留在各自的 result_path；任务窗口含监控启停，不含冷启动、上传、下载和清理。',
              '本批仍是约 4 秒独立重放的块级均值；如果区间跨过 5%，只说明本协议尚不能确认目标，不应把无显著差异解释为零开销。', '']
    if result['initial_report_generation_failed']:
        lines += ['首次控制器已完成全部 40 次运行，但最终报告生成因未定义变量退出；失败栈保留在 failure.txt。修正汇总代码后通过 --report-only 从 10 块原始结果重建本报告，没有重跑沙箱。', '']
    (out / 'report.md').write_text('\n'.join(lines))
    return verdict


if report_only:
    prior = json.loads((out / 'partial-results.json').read_text())
    assert len(prior) == plan['blocks']
    checked = [check_block(pathlib.Path(b['result_path']), number)
               for number, b in enumerate(prior, 1)]
    verdict = write_report(checked)
    print(f'status=PASS report_only=1 verdict={verdict} result_dir={out}', flush=True)
    raise SystemExit(0)

print(f'status=RUNNING series_id={series_id} result_dir={out}', flush=True)
blocks = []
status = 'FAIL'
try:
    blocks.append(check_block(root / plan['first_block_result'], 1))
    (out / 'partial-results.json').write_text(json.dumps(blocks, indent=2) + '\n')
    print(f'status=BLOCK_PASS block=1/{plan["blocks"]} source=existing_smoke result_dir={out}', flush=True)
    for number in range(2, plan['blocks'] + 1):
        order = plan['odd_order'] if number % 2 else plan['even_order']
        block_dir = out / 'blocks' / f'{number:02}'
        block_plan = dict(blocks=1, order=order, guest_sampling=False,
                          performance_host_sampling=True,
                          sample_interval_seconds=plan['sample_interval_seconds'],
                          output_root=str(out / 'blocks'), block_label=f'{number:02}',
                          cache_policy='preserve guest and host caches; no drop_caches')
        plan_path = out / f'block-{number:02}-plan.json'
        plan_path.write_text(json.dumps(block_plan, indent=2) + '\n')
        with (out / f'block-{number:02}.log').open('w') as log:
            rc = subprocess.run(['python3', str(root / 'scripts/run_exp1_monitor_block_smoke.py'),
                                 str(plan_path)], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                timeout=3100).returncode
        assert rc == 0, (number, rc, str(out / f'block-{number:02}.log'))
        blocks.append(check_block(block_dir / 'result.json', number))
        (out / 'partial-results.json').write_text(json.dumps(blocks, indent=2) + '\n')
        print(f'status=BLOCK_PASS block={number}/{plan["blocks"]} result_dir={out}', flush=True)
    verdict = write_report(blocks)
    print(f'status=PASS verdict={verdict} median={statistics.median(b["performance_vs_off_percent"] for b in blocks):+.1f}% result_dir={out}', flush=True)
    status = 'PASS'
except Exception:
    (out / 'failure.txt').write_text(traceback.format_exc())
finally:
    if status != 'PASS':
        print(f'status=FAIL completed_blocks={len(blocks)}/{plan["blocks"]} result_dir={out}', flush=True)
raise SystemExit(0 if status == 'PASS' else 1)
