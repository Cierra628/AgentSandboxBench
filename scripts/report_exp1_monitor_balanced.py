"""Recalculate balanced full-trajectory contrasts from stored per-run timings."""
import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path
from run_exp1_monitor_balanced import PATCH, schedule, summarize


def report(directory):
    result = json.loads((directory/'result.json').read_text())
    assert result['status']=='PASS'
    rows = result['runs']
    protocol = result['protocol']
    assert protocol['orders']==schedule(protocol['seed'], protocol['groups'])
    assert len(rows)==8*protocol['groups']
    for i, row in enumerate(rows):
        assert row['group']==i//8+1 and row['position']==i%8+1
        assert row['label']==protocol['orders'][i//8][i%8]
        raw = Path(row['raw_dir'])
        checks = json.loads((Path(row['audit_dir'])/'checks.json').read_text())
        assert checks==row['checks'] and checks['cleanup']=='PASS'
        assert hashlib.sha256((raw/'replay/patch.diff').read_bytes()).hexdigest()==PATCH
        assert int((raw/'task-window-duration-ns.txt').read_text())==row['task_window_ns']
        assert int((raw/'controller-exec-duration-ns.txt').read_text())==row['controller_exec_ns']
        with (raw/'replay/steps.tsv').open() as stream:
            steps=list(csv.DictReader(stream, delimiter='\t'))
        assert [s['step'] for s in steps]==[f'{n:03}' for n in range(1,27)]
        assert {s['step']:int(s['exit_code']) for s in steps if int(s['exit_code'])}=={'008':127,'016':127}
        assert sum(int(s['duration_ns']) for s in steps)==row['guest_action_sum_ns']
        row['_steps_ns']={s['step']:int(s['duration_ns']) for s in steps}
    analysis=summarize(rows)
    assert analysis==result['analysis']
    group=1+max(range(protocol['groups']), key=lambda i:abs(analysis['control']['group_percent'][i]))
    selected={label:[r for r in rows if r['group']==group and r['label']==label]
              for label in ('control_A','control_B')}
    def difference(metric):
        return statistics.mean(metric(r) for r in selected['control_B'])-statistics.mean(metric(r) for r in selected['control_A'])
    delta=difference(lambda r:r['task_window_ns'])/1e6
    step_delta={step:difference(lambda r:r['_steps_ns'][step])/1e6 for step in ('023','024')}
    diagnosis=dict(group=group, comparison='control_B versus control_A; both sampling off',
                   task_window_difference_ms=delta,
                   guest_action_sum_difference_ms=difference(lambda r:r['guest_action_sum_ns'])/1e6,
                   step_difference_ms=step_delta,
                   steps_023_024_fraction_of_window_difference=sum(step_delta.values())/delta if delta else None,
                   cause='NOT_IDENTIFIED; scheduling, CPU frequency and cache explanations are hypotheses')
    (directory/'variance-diagnostic.json').write_text(json.dumps(diagnosis,indent=2)+'\n')
    lines=['# Exp1 同批对照与运行顺序检查','',
           '每组八次独立沙箱，前四次随机排序，后四次反向执行。control_A、control_B、off 都关闭采样；light 每秒读取一次服务 cgroup。虚拟机内部采样关闭，不清缓存。',
           '主要指标是完整 26 步任务窗口，包含 host 采样启停，不含创建、上传、下载及删除。每次均核对动作哈希、顺序、预期失败、关键输出、最终 patch 和沙箱删除。完整任务测试未运行。', '',
           '| 组 | 开启/关闭采样变化 | 两次关闭采样的对照变化 |', '| --- | ---: | ---: |']
    for i,(monitor, control) in enumerate(zip(analysis['monitor']['group_percent'],analysis['control']['group_percent']),1):
        lines.append(f'| {i} | {monitor:+.2f}% | {control:+.2f}% |')
    lines += ['',f"判定：`{analysis['verdict']}`。"]
    for key, label in [('monitor','采样比较'),('control','关闭采样对照')]:
        item=analysis[key]
        lines.append(f"{label}的几何均值为 {item['geometric_mean_percent']:+.2f}%，五组对数比值均值的 t 区间为 {item['mean_log_ratio_t95_percent']}（95%；单组 smoke 不计算）。")
    lines += ['', '规则在运行前固定：关闭采样对照的区间须完全位于 −5% 至 +5%，采样比较的区间上界须不超过 +5%。这是特定任务、采样频率和服务配置下的判断，依赖组间近似独立及对数比值近似正态的假设；五组不能可靠检验这些假设。',
              '对称位置可以抵消按运行次数近似线性的变化，不能保证消除非线性时间变化、缓存干扰或其他主机负载。对照不通过时不作数值相减修正，也不自动增加轮数。TrEnv-X 与 guest 采样开销不在本次范围内。',
              '', f'原始记录：`{directory}`。离线逐项复算通过。','']
    (directory/'report.md').write_text('\n'.join(lines))
    (directory/'offline-audit.json').write_text(json.dumps(dict(status='PASS', analysis=analysis, run_count=len(rows)),indent=2)+'\n')
    # Standalone figure: ordered observations and group-level comparisons.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    figure, axes = plt.subplots(2, 1, figsize=(10, 6), constrained_layout=True)
    colors = {'control_A':'#777777', 'control_B':'#bbbbbb', 'off':'#2878b5', 'light':'#d64f38'}
    for label, color in colors.items():
        chosen = [(i+1, r['task_window_ns']/1e6) for i,r in enumerate(rows) if r['label']==label]
        axes[0].scatter([x for x,y in chosen], [y for x,y in chosen], label=label, color=color)
    axes[0].set(xlabel='Replay order', ylabel='Task window (ms)')
    axes[0].legend(ncol=4)
    for key, marker in [('monitor', 'o'), ('control', 's')]:
        values=analysis[key]['group_percent']
        axes[1].plot(range(1,len(values)+1), values, marker=marker, label=key)
    axes[1].axhline(5, color='red', linestyle='--', label='5% threshold')
    axes[1].axhline(0, color='gray', linewidth=.7)
    axes[1].set(xlabel='Group', ylabel='Relative difference (%)')
    axes[1].legend()
    figure.savefig(directory/'monitor-order.png', dpi=160)
    figure.savefig(directory/'monitor-order.svg')
    plt.close(figure)
    print(f'status=PASS verdict={analysis["verdict"]} result_dir={directory}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory.resolve())
