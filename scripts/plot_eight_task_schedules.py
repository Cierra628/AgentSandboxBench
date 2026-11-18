"""只读现有八任务索引，生成三调度的中位数、完整范围和逐次点对照图。"""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

from report_eight_task_schedules import ROOT, TASKS

SCHEDULES = ('grpo', 'bpo', 'tvcache')
COLORS = ('#2463A6', '#17836D', '#D17B19')
METRICS = {
    'current_full_peak_mib': ('全采集窗口 current 峰值', '组内存记账量 memory.current，MiB'),
    'current_schedule_peak_mib': ('调度窗口 current 峰值', '组内存记账量 memory.current，MiB'),
    'pss_per_guest_mib': ('终点七 VMM 的 PSS/N', '比例分摊驻留量 PSS/N，MiB/guest'),
    'lower_p1_mib': ('原镜像活跃文件驻留 P1', '指定文件 mincore 驻留量，MiB'),
    'schedule_seconds': ('调度窗口耗时', '创建前至动作及下载结束，秒'),
    'tools_seconds': ('实际工具累计耗时', '实际执行工具的时长之和，秒'),
}
OVERVIEWS = (
    ('current_full_peak_mib', '01-current'),
    ('pss_per_guest_mib', '02-pss'),
    ('lower_p1_mib', '03-file-cache'),
    ('schedule_seconds', '04-task-time'),
)
NOTES = {
    'prettier6604': '原七角色；原19文件P1；未保存同格式单调边界，调度窗口current峰值不估算。',
    'sphinx7590': '原七角色；P1物理文件数为13／26／26；原诊断验收通过，指定测试24通过／1失败。',
    'xarray6992': '原七角色；P1物理文件数为13／26／26；全窗口峰值在后续独立pytest阶段，非调度峰值。',
    'django16819': '七实例同原28步；18／26步保存；首轮GRPO跨宿主重启复用，缓存起点未统一。',
    'c4': '七实例同原36步；24／29步保存；空心方块为获准复用的首轮GRPO，原清理超时FAIL保留。',
    'cython': '七实例同原83步；60／75步保存；三处TLS异常保留，网络波动不直接归因于调度。',
    'matplotlib24177': '七实例同原95步；73／87步保存；原两轮各60 passed，其他原检查范围不变。',
    'text_editing': '七实例同原50步；33／46步保存；保留原完整逐字节比较及两种solve检查。',
}


def validate(source):
    data = json.loads(source.read_text())
    runs = data['runs_detail']
    assert len(runs) == len({r['raw'] for r in runs}) == 72
    saved = {(s['task'], s['schedule']): s['metrics'] for s in data['statistics']}
    points, groups = [], {}
    for task in TASKS:
        for metric in METRICS:
            for schedule in SCHEDULES:
                selected = sorted((r for r in runs if r['task'] == task and r['schedule'] == schedule), key=lambda r: r['round'])
                assert [r['round'] for r in selected] == [1, 2, 3]
                known = [r[metric] for r in selected if r[metric] is not None]
                expected = saved[task, schedule][metric]
                assert expected['count'] == len(known)
                if known:
                    for key, value in (('median', statistics.median(known)), ('minimum', min(known)), ('maximum', max(known))):
                        assert math.isclose(expected[key], value, rel_tol=1e-12, abs_tol=1e-9)
                groups[task, metric, schedule] = selected
                for r in selected:
                    assert (ROOT/r['raw']/'result.json').is_file()
                    points.append(dict(task=task, schedule=schedule, round=r['round'], metric=metric,
                        value=r[metric], unit='秒' if metric.endswith('seconds') else 'MiB/guest' if metric == 'pss_per_guest_mib' else 'MiB',
                        plotted=r[metric] is not None, raw=r['raw'], summary_source=r['summary_source'],
                        raw_status=r['raw_status'], accepted_status=r['status'],
                        marker='hollow_square' if r['status'] == 'ACCEPTED_CLEANUP_TIMEOUT' else 'circle',
                        lower_physical_files=r['lower_physical_files'],
                        regular_strict_status=r['regular_strict_status'],regular_window_status=r['regular_window_status']))
    assert len(points) == 432 and sum(p['plotted'] for p in points) == 423
    return groups, points


def panel(ax, task, metric, groups):
    all_values = [r[metric] for s in SCHEDULES for r in groups[task, metric, s] if r[metric] is not None]
    ax.set_xticks(range(3), ['GRPO', 'BPO', 'TVCache'])
    ax.set_xlim(-.55, 2.55)
    ax.set_ylabel(METRICS[metric][1], fontsize=10)
    ax.spines[['top', 'right']].set_visible(False)
    ax.spines[['left', 'bottom']].set_color('#CED5DD')
    ax.tick_params(axis='both', length=0, pad=7, labelsize=10)
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.grid(axis='y', color='#E7ECF1', linewidth=.8)
    ax.set_axisbelow(True)
    if not all_values:
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        ax.text(.5, .5, '未记录\n不估算、不记零', ha='center', va='center', transform=ax.transAxes, color='#697582', fontsize=13)
        return
    ax.set_ylim(0, max(all_values)*1.30)
    for index, (schedule, color) in enumerate(zip(SCHEDULES, COLORS)):
        selected = groups[task, metric, schedule]
        known = [r[metric] for r in selected if r[metric] is not None]
        if not known:
            continue
        mid, low, high = statistics.median(known), min(known), max(known)
        ax.errorbar(index, mid, yerr=[[mid-low], [high-mid]], color=color, fmt='D',
            markersize=6, elinewidth=2, capsize=6, capthick=1.7, zorder=4)
        for r in selected:
            if r[metric] is None:
                continue
            exception = r['status'] == 'ACCEPTED_CLEANUP_TIMEOUT'
            ax.scatter(index+(r['round']-2)*.13, r[metric], s=35 if exception else 22,
                marker='s' if exception else 'o', facecolors='white' if exception else color,
                edgecolors=color, alpha=1 if exception else .55, linewidths=1.2, zorder=5)
        label = f'{mid:,.1f}'
        ax.annotate(label, (index, high), xytext=(0, 11), textcoords='offset points',
            ha='center', fontsize=11, color=color, weight='bold')
    if metric == 'lower_p1_mib':
        counts = [sorted({r['lower_physical_files'] for r in groups[task, metric, s]}) for s in SCHEDULES]
        ax.text(.5, .96, '物理文件数 G／B／T：'+'／'.join(','.join(map(str,n)) for n in counts),
            transform=ax.transAxes, ha='center', va='top', color='#697582', fontsize=9)


def style_figure(fig, title, subtitle, footer):
    fig.suptitle(title, x=.065, y=.977, ha='left', fontsize=21, weight='bold', color='#162C42')
    fig.text(.065, .924, subtitle, fontsize=11, color='#536576')
    handles = [Line2D([], [], marker='D', color='#435467', markersize=6, label='菱形：中位数；竖线：最小～最大（非置信区间）'),
        Line2D([], [], marker='o', linestyle='none', color='#7C8A96', markersize=5, label='小圆点：三次原始读数，横向偏移依次为第1／2／3次')]
    fig.legend(handles=handles, loc='lower left', bbox_to_anchor=(.06, .052), frameon=False, ncol=1, fontsize=10)
    fig.text(.065, .026, footer, fontsize=10, color='#536576')


def save(fig, out, name):
    fig.savefig(out/(name+'.png'), dpi=150, facecolor='white')
    fig.savefig(out/(name+'.svg'), facecolor='white')
    plt.close(fig)


def report(source, out):
    assert not out.exists(), '不能覆盖既有目录，请指定新的输出路径'
    groups, points = validate(source)
    plt.rcParams.update({'font.family':'Noto Sans CJK JP', 'axes.unicode_minus':False,
        'svg.fonttype':'path', 'axes.titlesize':13, 'text.color':'#233B50'})
    out.mkdir(parents=True)
    for metric, name in OVERVIEWS:
        fig, axes = plt.subplots(2, 4, figsize=(17, 9))
        fig.subplots_adjust(left=.065, right=.985, top=.85, bottom=.19, wspace=.40, hspace=.48)
        for ax, task in zip(axes.flat, TASKS):
            ax.set_title(TASKS[task][0], loc='left', pad=15, weight='bold')
            panel(ax, task, metric, groups)
        subtitle='七沙箱；每种调度三次；各子图纵轴均从0起，但刻度独立，只在同一任务内对照。'
        footer='C4空心方块为获准复用的清理超时记录；Django／C4存在跨重启复用；慢组全部保留。'
        if metric == 'current_full_peak_mib':
            footer='全采集窗口含结果检查／清理，Xarray峰值在独立pytest阶段；current不覆盖组外缓存。C4空心方块为清理超时复用。'
        elif metric == 'pss_per_guest_mib':
            footer='终点七VMM的PSS之和除7，非服务组总内存；C4空心方块为清理超时复用。'
        elif metric == 'lower_p1_mib':
            footer='P1只覆盖图中注明的镜像文件，非全部host缓存；各层不相加。C4空心方块为清理超时复用。'
        style_figure(fig, METRICS[metric][0]+'｜八任务三调度', subtitle, footer)
        save(fig, out, name)
    for task in TASKS:
        fig, axes = plt.subplots(2, 3, figsize=(15, 9))
        fig.subplots_adjust(left=.075, right=.98, top=.85, bottom=.20, wspace=.35, hspace=.48)
        for ax, metric in zip(axes.flat, METRICS):
            ax.set_title(METRICS[metric][0], loc='left', pad=15, weight='bold')
            panel(ax, task, metric, groups)
        style_figure(fig, TASKS[task][0]+'｜三调度对照',
            '七沙箱；每调度三次；中位数与完整范围；内存、驻留与耗时分别报告，不相加。', NOTES[task])
        save(fig, out, task)
    with (out/'points.tsv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(points[0]),delimiter='\t')
        writer.writeheader(); writer.writerows(points)
    provenance=dict(status='PASS',source=str(source.resolve().relative_to(ROOT)),
        source_runs=72,plotted_point_records=423,missing_point_records=9,
        missing_scope='Prettier current_schedule_peak_mib only; omitted, not zero',
        figures=12,metrics=list(METRICS),
        method='Median with min/max and all repeat points; no interpolation, normalization or cache sums',
        c4_exception='r1 GRPO shown as hollow square; raw FAIL preserved; controller not plotted',
        data_validation='All medians/ranges agree with saved statistics; all raw result paths exist')
    (out/'provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+'\n')
    lines=['# 八任务三调度对照图','',
        '这些图使用[统一数据索引](../eight-task-schedules-20261110/REPORT.md)的72份已有记录，未重新运行任务或采样。71次全流程通过，C4有1次获准复用的清理超时记录，原FAIL不改。','',
        '## 先选要看的量','',
        '- **看整组内存记账：**[current总览](01-current.png)。全采集窗口峰值包含检查和清理；执行阶段见各任务第二子图。',
        '- **看VMM平均比例驻留：**[PSS/N总览](02-pss.png)。七个VMM的PSS之和除7，不是完整服务组成本。',
        '- **看指定镜像文件缓存：**[mincore P1总览](03-file-cache.png)。每个子图注明物理文件数量，不是全部host page cache。',
        '- **看完成任务用了多久：**[调度耗时总览](04-task-time.png)。创建前至原路径及下载结束；工具累计另在各任务第六子图。','',
        '![组内存记账对照](01-current.png)','',
        '## 每个任务的六项对照','',
        '每张图依次为：全窗口current峰值、调度窗口current峰值、终点PSS/N、原镜像文件驻留P1、调度窗口耗时、实际工具累计耗时。','',
        '| 任务 | PNG预览 | SVG矢量图 |','| --- | --- | --- |']
    for task in TASKS:
        lines.append(f'| {TASKS[task][0]} | [查看]({task}.png) | [下载]({task}.svg) |')
    lines+=['','## 怎么读','',
        '1. 菱形是三次中位数，竖线覆盖最小到最大，数字是中位数；不是误差估计或置信区间。小圆点是每次原值，向左／居中／向右依次为第1／2／3次。读数相同时会重叠，未增加假波动。',
        '2. 每幅子图的纵轴从0开始；不同任务刻度独立，不按图中高度跨任务比较。每个量单独画，current、PSS与文件缓存不相加。',
        '3. Prettier执行阶段current缺少同格式单调边界，该子图显示“未记录”，没有按零画点。Xarray全窗口峰值发生在后续独立pytest期间，不等于任务执行峰值。',
        '4. C4空心方块是首轮GRPO清理超时例外，图内的六项指标按既有授权纳入三次。异常整轮143.440秒仍保留，本次未画正常整轮统计，不因此隐藏该错误。Django／C4首轮跨宿主重启复用、Cython网络异常也原样保留。',
        '5. 原七角色任务是Prettier、Sphinx、Xarray，其余五项是同路径前缀复用。BPO／TVCache实际执行工具更少，工具累计的差值不能直接解释为相同实际动作的纯加速。当前TVCache是确定性前缀树实现，无动态容量或淘汰。',
        '6. PSS/N是终点读数而非全程峰值；P1只覆盖已注明镜像文件。扩展缓存PARTIAL／UNKNOWN仍在统一索引，图未把它们补零或拼成全部host缓存。','',
        '## 每个点的来源','',
        '[points.tsv](points.tsv)列出432条任务／指标／轮次记录，其中423条有值、9条为Prettier缺失项；每条关联统一索引行键及原始目录、原状态和文件范围。[provenance.json](provenance.json)记录输入、校验和作图规则。总览与单任务图复用相同点，不是额外重复。四份总览也各有同名.svg矢量版本。','',
        '复现到新目录：','', '```bash',
        'python3 -B scripts/plot_eight_task_schedules.py \\',
        '  --source results/eight-task-schedules-20261110/summary.json \\',
        '  --output results/新的作图目录', '```','',
        '## 下一步建议','',
        '先选定展示任务与指标，使用这里的SVG或PNG做报告；若要回答部署密度，另引用既有共享／独立及压力实验，不从本组三调度图直接推算能多放几个沙箱。本段已完成，不追加运行。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(f'status=PASS figures=12 formats=PNG,SVG plotted_points=423 missing=9 output={out}')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'results/eight-task-schedules-20261110/summary.json')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report(args.source,args.output)
