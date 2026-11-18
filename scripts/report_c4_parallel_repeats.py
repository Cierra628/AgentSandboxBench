"""Report the explicit six-run C4 two-guest sampling comparison, offline."""
import argparse
import json
from pathlib import Path

from run_c4_parallel_pair import alternating_comparison
from run_c4_parallel import validate_parallel
from report_new_sampling_summary import length

MIB=2**20
NS=10**9
LABELS=dict(tool_duration_sum_ns='两个guest工具累计之和',schedule_elapsed_ns='组任务窗口（含创建）',
            actions_download_elapsed_ns='组动作及下载窗口',controller_elapsed_ns='组控制程序整轮')


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def details(run):
    task=run['task'];validate_parallel(task,run['mode']);raw=Path(run['raw'])
    deletion=[];children=[]
    for i,child in enumerate(task['children']):
        path=raw/f'c4-parallel-{i}';events=read(path/'events.json')
        event=next(e for e in events if e['stage']=='delete-own-guest')
        start,end=event['command_start_monotonic_ns'],event['command_end_monotonic_ns']
        assert event['exit_code']==0 and end-start==event['command_duration_ns']
        deletion.append([start,end])
        cold=next(e for e in events if e['stage']=='cold-create')
        row=dict(guest=i+1,sandbox_id=child['sandbox_id'],tools_seconds=child['tool_duration_sum_ns']/NS,
                 task_seconds=child['schedule_elapsed_ns']/NS,cold_create_seconds=cold['command_duration_ns']/NS,
                 replay_seconds=(child['replay_end_monotonic_ns']-child['replay_start_monotonic_ns'])/NS,
                 delete_seconds=(end-start)/NS,delete_start_monotonic_ns=start,delete_end_monotonic_ns=end)
        if run['mode']=='on':
            endpoint=child['endpoint_metrics'];pss,p1=endpoint['pss'],endpoint['p1'];vmm=child['host_vmm_collector']
            page_sizes={f['resident_bytes']//f['resident_pages'] for f in p1['files'] if f['resident_pages']}
            assert len(page_sizes)==1
            page_size=page_sizes.pop()
            assert all(f['resident_bytes']==f['resident_pages']*page_size for f in p1['files'])
            monitor=read(path/'runtime-monitor.json')
            assert monitor['profile']=='light' and monitor['interval_seconds']==.1
            assert vmm['interval_seconds']==.1 and vmm['pss_interval_seconds']==1
            assert all(s['collector_reaped'] and s['receipt']['status']=='PASS' for s in monitor['sessions'])
            row.update(pss_mib=pss['total_bytes']/MIB,p1_mib=p1['resident_bytes']/MIB,
                       p1_nonresident_mib=sum((f['pages']-f['resident_pages'])*page_size for f in p1['files'])/MIB,
                       lower_files=p1['unique_files'],pss_wall_seconds=(pss['end_monotonic_ns']-pss['start_monotonic_ns'])/NS,
                       p1_wall_seconds=(p1['end_monotonic_ns']-p1['start_monotonic_ns'])/NS,
                       pss_cpu_seconds=pss['cpu_ns']/NS,p1_cpu_seconds=p1['cpu_ns']/NS,
                       endpoint_wall_seconds=(endpoint['end_monotonic_ns']-endpoint['start_monotonic_ns'])/NS,
                       initialization_wall_seconds=(endpoint['initialization']['end_monotonic_ns']-endpoint['initialization']['start_monotonic_ns'])/NS,
                       vmm_cpu_seconds=vmm['cpu_user_seconds']+vmm['cpu_system_seconds'],
                       guest_cpu_seconds=sum(s['receipt']['cpu_user_seconds']+s['receipt']['cpu_system_seconds'] for s in monitor['sessions']),
                       vmm_samples=vmm['samples'],pss_samples=vmm['pss_samples'],
                       guest_samples=sum(s['receipt']['samples'] for s in monitor['sessions']))
        children.append(row)
    after=[[max(a,task['schedule_end_monotonic_ns']),b] for a,b in deletion if b>task['schedule_end_monotonic_ns']]
    short=min(c['replay_end_monotonic_ns']-c['replay_start_monotonic_ns'] for c in task['children'])
    assert task['replay_overlap_ns']>0
    return dict(index=run['index'],pair=run['pair'],mode=run['mode'],children=children,
                timings_seconds={k:v/NS for k,v in task.items() if k in LABELS},
                deletion_union_seconds=length(deletion)/NS,deletion_after_task_union_seconds=length(after)/NS,
                controller_minus_task_seconds=(task['controller_elapsed_ns']-task['schedule_elapsed_ns'])/NS,
                overlap_seconds=task['replay_overlap_ns']/NS,overlap_fraction=task['replay_overlap_ns']/short,
                host_cpu_seconds=run['host_collector']['thread_cpu_ns']/NS if run['mode']=='on' else None,
                min_disk_gib=run['min_disk_gib'],storage=run['storage'],workflow=run['workflow'],raw=run['raw'])


def report(base,out):
    state=read(base/'result.json');assert state['status']=='PASS' and state['source_stopped']
    comparison=alternating_comparison(state['runs']);assert comparison==state['comparison']
    runs=[details(row) for row in state['runs']]
    out.mkdir(parents=True,exist_ok=False)
    write(out/'summary.json',dict(status='PASS',comparison=comparison,runs=runs,source_batch=str(base),
                                 source_stopped=True,actions=432,guests=12))
    lines=['# C4双沙箱：三对交替采样开关对照','',
           '关→开、开→关、关→开，共6次运行、12个新guest、432个原动作。每次同一新私有服务内两个guest并发执行原36步；9898文件内容／结构往返、约束、语法、原退出序列及采集开关、身份和清理均通过。没有额外短测、任务重跑或慢组剔除。','']
    stats=comparison['summary']
    lines += [f"本轮三对开启组的工具累计和任务窗口均比关闭组长：变化中位数分别{stats['tool_duration_sum_ns']['paired_change_median_percent']:+.2f}%和{stats['schedule_elapsed_ns']['paired_change_median_percent']:+.2f}%，整轮{stats['controller_elapsed_ns']['paired_change_median_percent']:+.2f}%。同一轻量组合在双guest下出现几个百分点的任务差异，不能沿用单guest约0.2%的任务窗口变化；删除等待独立波动，整轮差值也不能直接当周期采样成本。",'',
           '## 三对变化的中位数与范围','',
           '| 指标 | 关闭中位数，秒 | 开启中位数，秒 | 逐对变化中位数 | 逐对变化范围 |',
           '| --- | ---: | ---: | ---: | ---: |']
    for k,label in LABELS.items():
        s=comparison['summary'][k];lo,hi=s['paired_change_range_percent']
        lines.append(f"| {label} | {s['off_median_seconds']:.6f} | {s['on_median_seconds']:.6f} | {s['paired_change_median_percent']:+.2f}% | {lo:+.2f}%～{hi:+.2f}% |")
    lines+=['','变化先逐对计算`(开启/关闭−1)×100%`，再取中位数，不是两个耗时中位数之比。负变化和慢组保留；三对仍受宿主调度、缓存及共享磁盘影响，不证明采样加速，也不把整轮全部差值归给周期采样。','',
            '## 六次原始耗时与删除时间','',
            '| 次序／对数／开关 | 工具累计，秒 | 组任务窗口，秒 | 动作及下载，秒 | 整轮，秒 | 删除请求并集，秒 | 任务结束后的删除并集，秒 |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        t=r['timings_seconds'];lines.append(f"| {r['index']}／{r['pair']}／{r['mode']} | {t['tool_duration_sum_ns']:.6f} | {t['schedule_elapsed_ns']:.6f} | {t['actions_download_elapsed_ns']:.6f} | {t['controller_elapsed_ns']:.6f} | {r['deletion_union_seconds']:.6f} | {r['deletion_after_task_union_seconds']:.6f} |")
    lines+=['','两个guest各自收尾和删除，可能与另一guest尾段任务并行。删除并集按host保存的命令起止求不重复的经过时间，不累加两个请求；任务结束后部分另列，不把窗口前重叠时间重复加入整轮。删除请求包含通信和后端处理，不是纯磁盘同步计时。','',
            '| 对数／顺序 | 工具累计变化 | 任务窗口变化 | 整轮变化 |',
            '| --- | ---: | ---: | ---: |']
    for p in comparison['pairs']:
        cells=[]
        for key in ('tool_duration_sum_ns','schedule_elapsed_ns','controller_elapsed_ns'):
            m=p['metrics'][key];cells.append(f"{m['delta_seconds']:+.6f}秒（{m['change_percent']:+.2f}%）")
        lines.append(f"| {p['pair']}／{'→'.join(p['order'])} | "+' | '.join(cells)+' |')
    lines+=['','## 逐guest时间及并发证据','',
            '| 次序／开关／guest | 工具累计，秒 | 自身任务窗口，秒 | 冷创建命令，秒 | 删除命令，秒 |',
            '| --- | ---: | ---: | ---: | ---: |']
    for r in runs:
        for c in r['children']:lines.append(f"| {r['index']}／{r['mode']}／{c['guest']} | {c['tools_seconds']:.6f} | {c['task_seconds']:.6f} | {c['cold_create_seconds']:.6f} | {c['delete_seconds']:.6f} |")
    lines+=['']
    for r in runs:lines.append(f"- 第{r['index']}次{r['mode']}：两次host重放调用重叠{r['overlap_seconds']:.6f}秒，占较短调用{100*r['overlap_fraction']:.2f}%。")
    lines+=['','工具累计来自guest时钟、包含并发重叠，不能除整轮当工具占比。host重放调用包含通信，不称guest纳秒级同步；两个guest时钟不直接相减。','',
            '## 开启组测量值和直接成本','',
            '| 次序／guest | 完成点PSS，MiB | 9文件P1，MiB | PSS墙钟／CPU，秒 | P1墙钟／CPU，秒 | VMM采样CPU，秒 | guest采样CPU，秒 |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        if r['mode']!='on':continue
        for c in r['children']:lines.append(f"| {r['index']}／{c['guest']} | {c['pss_mib']:.3f} | {c['p1_mib']:.3f} | {c['pss_wall_seconds']:.6f}／{c['pss_cpu_seconds']:.6f} | {c['p1_wall_seconds']:.6f}／{c['p1_cpu_seconds']:.6f} | {c['vmm_cpu_seconds']:.6f} | {c['guest_cpu_seconds']:.6f} |")
    lines+=['']
    for r in runs:
        if r['mode']=='on':lines.append(f"- 第{r['index']}次host整组普通计数CPU：{r['host_cpu_seconds']:.6f}秒。")
    lines+=['','普通guest meminfo／host组及VMM计数0.1秒，实际root VMM的smaps_rollup PSS每1秒；原路径及下载后，各自一次PSS和实际9个初始下层文件mincore P1。host整组只采一个，不开PSS全进程扫描、周期mincore或BPF诊断。关闭组无内存采集，保留逐工具计时、原检查及共同磁盘保护。','',
            '两个guest在各自完成点查询，没有等待统一时刻。PSS不相加成同步总量；每组两个guest引用相同9个设备号:inode，两次共享文件P1不相加。P1无P0，不代表任务新读入量，也不覆盖upper及全部guest缓存的host物理对应。current是含准备、控制程序、私有服务及guest的外层组记账；file、P1和PSS范围不同，不相加当总物理内存。采样CPU和查询时长不是任务减速率。','',
            '组任务窗口从协调程序启动两子程序前，到最后一个guest原结果下载返回，含编译、创建和准备；动作及下载从最早重放调用前开始。整轮另含终点查询、原检查、删除、原始记录复制及空服务核对，不含全部外层建卷／卸载时间。原工具路径内的测试属于任务，窗口后语法检查另列；采集启停和查询可能与另一guest尾段工作重叠。','',
            '## 资源、异常与原始证据','',
            f"每次沿用32 GiB卷，启动严格>58 GiB、运行中20 GiB保护不变。本轮最小可用磁盘{min(r['min_disk_gib'] for r in runs):.3f} GiB；6个成功卷和私有服务、挂载／loop均按原流程释放，源服务恢复停止，旧失败及中断卷保留。无联网、代理、清缓存、预读、强制驻留或回收设置修改。",'',
            '本轮数据与前次C4单guest三对、双guest一对、带BPF诊断的一对分开，不合并统计。此前15秒异常保留；本轮没有函数跟踪，不能把慢删除直接断言为某条fdatasync。逐guest最长删除及各轮差值在上表完整保留。','']
    largest=max((c['delete_seconds'],r['index'],r['mode']) for r in runs for c in r['children'])
    lines.append(f"本轮最长单次删除为第{largest[1]}次{largest[2]}组的{largest[0]:.6f}秒；没有再次出现15秒。其部分等待与另一guest任务尾段重叠，所以整轮差值可能掩盖任务阶段的差异，不删掉这次较慢关闭组来美化或放大百分比。")
    for r in runs:
        for c in r['children']:
            if c.get('p1_nonresident_mib',0)>0:
                lines.append(f"第{r['index']}次开启组guest{c['guest']}的9文件P1为{c['p1_mib']:.6f} MiB，页数口径未驻留{c['p1_nonresident_mib']:.6f} MiB；原始`{r['raw']}/c4-parallel-{c['guest']-1}/external-metrics.json`。文件身份及元数据稳定，两guest清单相同，保留实际驻留差异。")
    lines+=['本轮没有回收事件的时间对齐记录，不能把上述host文件驻留变化归因于guest DAMON或某个特定回收动作；没有P0，也不推算任务新读页。两guest查询时刻不同，少量P1差异不做补齐。','',
            '[耗时对照图](timing.png) · [逐次摘要和原路径](summary.json)。开启组每次各自的guest及host曲线：','']
    from run_host_external_workload import guest_plot
    from report_host_file import report as host_report
    for source,r in zip(state['runs'],runs):
        lines.append(f"- 第{r['index']}次（第{r['pair']}对，{r['mode']}）：原始`{r['raw']}`；外层`{r['workflow']}`。")
        if r['mode']=='on':
            dest=out/f"run-{r['index']:02}"
            for i in range(2):
                target=dest/f'guest-{i+1}';target.mkdir(parents=True)
                guest_plot(Path(r['raw'])/f'c4-parallel-{i}',target,title=f"C4 run {r['index']}, guest {i+1}",explicit_axes=True)
            host_report(Path(r['workflow'])/'host-file',None,dest/'host',creation_path=Path(r['storage'])/'result.json',explicit_axes=True)
            lines.append(f"  [guest1](run-{r['index']:02}/guest-1/guest.png) · [guest2](run-{r['index']:02}/guest-2/guest.png) · [host current及组成](run-{r['index']:02}/host/overview.png)。")
    lines+=['',f"批次`{base}`保存顺序、命令、源码快照、退出状态和失败边界。本轮驱动及报告代码仅增加显式重复和离线统计，未更换原服务二进制、默认入口或任务动作。",'',
            '## 下一步建议','',
            '本轮三对完成后收尾，不追加相同重复。下一步建议只离线对照这6次的逐动作增量与已有VMM／guest采集CPU，以找出具体成本来源、判断是否值得降低PSS频率并给出最小改进建议为完成条件；不启动沙箱或自动改频率。其他任务或并发数量另定范围。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
    names=['P1 off','P1 on','P2 on','P2 off','P3 off','P3 on']
    for ax,label in zip(axes.flat,LABELS):
        values=[r['timings_seconds'][label] for r in runs]
        ax.bar(names,values,color=['#87919b' if r['mode']=='off' else '#2563eb' for r in runs])
        titles={'tool_duration_sum_ns':'Sum of two guests tool durations','schedule_elapsed_ns':'Group task window including creation','actions_download_elapsed_ns':'Group action and download window','controller_elapsed_ns':'Whole group controller'}
        ax.set(title=titles[label],ylabel=titles[label]+', seconds',ylim=(0,max(values)*1.18));ax.bar_label(ax.containers[0],fmt='%.2f');ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'timing.png',dpi=140);plt.close(fig)
    print('status=PASS result_dir='+str(out))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('batch',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report(args.batch.resolve(),args.output.resolve())
