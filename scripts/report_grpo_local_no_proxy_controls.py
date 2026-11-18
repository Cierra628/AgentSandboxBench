"""Publish saved fixed GRPO controls and curves; no service or residency queries."""
import argparse
import csv
import json
from pathlib import Path
import shutil

from run_grpo_local_no_proxy_controls import comparisons


def guest_curves(report,output):
    """Use the existing normalized points; keep seven guests on separate rows."""
    import matplotlib.pyplot as plt
    with (report/'memory.tsv').open() as stream:data=list(csv.DictReader(stream,delimiter='\t'))
    roles=('backbone','early_1','early_2','early_3','late_1','late_2','late_3')
    assert {r['role'] for r in data}==set(roles)
    fig,axes=plt.subplots(7,2,figsize=(12,14),sharex=True,sharey='col')
    for i,role in enumerate(roles):
        points=[r for r in data if r['role']==role]
        assert len({r['sandbox_id'] for r in points})==1
        chunks=[[]]
        for row in points:
            if chunks[-1] and float(row['elapsed_seconds'])-float(chunks[-1][-1]['elapsed_seconds'])>.5:chunks.append([])
            chunks[-1].append(row)
        for ax,field,color in zip(axes[i],('memory_used_bytes','cached_bytes'),('#2563eb','#0f766e')):
            for chunk in chunks:
                ax.plot([float(r['elapsed_seconds']) for r in chunk],[int(r[field])/2**20 for r in chunk],color=color,linewidth=1.3)
            ax.set_ylabel(role+'\nMiB');ax.grid(alpha=.2)
    for column,field in enumerate(('memory_used_bytes','cached_bytes')):
        axes[0,column].set_ylim(0,1.08*max(int(r[field])/2**20 for r in data))
    axes[0,0].set_title('Guest memory: MemTotal - MemAvailable')
    axes[0,1].set_title('Guest Cached (includes Shmem)')
    for ax in axes[-1]:ax.set_xlabel('Monotonic seconds from first monitor session')
    fig.suptitle('Prettier GRPO: one guest per row; logical guest metrics')
    fig.tight_layout(rect=(0,0,1,.98));fig.savefig(output,dpi=130);plt.close(fig)


def boundary_report(runs,pairs,output,*,meminfo=False):
    field='boundary_meminfo_mode' if meminfo else 'boundary_vmstat_mode'
    labels={'separate':'独立读取','merged':'合并读取'} if meminfo else {'keep':'保留','skip':'跳过'}
    a,b=('separate','merged') if meminfo else ('keep','skip')
    lines=['# Prettier七沙箱GRPO：独立／合并边界meminfo' if meminfo else '# Prettier七沙箱GRPO：保留／跳过边界vmstat','',
        '两组都开启0.1秒host／guest轻量采样并跳过边界vmstat。独立读取用单独的date＋awk，合并读取复用动作计时的Python进程。固定独立→合并、合并→独立四次，共28个新guest、756个原动作，保留448条边界记录及原调度／默认回收。' if meminfo else '两组都开启0.1秒host／guest轻量采样，只比较是否执行边界vmstat读取。固定保留→跳过、跳过→保留四次，共28个新guest、756个原动作；普通输入、默认回收、逐guest准备及原并发调度相同。','',
        '| 对照 | 指标 | 独立／秒 | 合并／秒 | 合并减独立／秒 | 变化 |' if meminfo else '| 对照 | 指标 | 保留／秒 | 跳过／秒 | 跳过减保留／秒 | 变化 |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for p in pairs:
        for key,title in (('tool_sum','工具累计'),('frozen_tools_and_downloads_span','并发动作及下载窗口'),
                          ('cold_request_span','创建请求跨度'),('rollout','创建前至检查结束'),('controller','控制程序整轮')):
            v=p['timings'][key]
            lines.append(f"| {p['pair']} | {title} | {v[a+'_seconds']:.3f} | {v[b+'_seconds']:.3f} | {v['change_seconds']:+.3f} | {v['change_percent']:+.2f}% |")
    lines += ['', '![逐对用时](timings.png)','',
        '工具累计是并发命令持续时间之和，不是整批墙钟。动作窗口含执行和下载；控制程序还含创建、准备、额外检查／官方评测和删除。组内流程、来源准备、导入和最终查询另列于runs.tsv，各窗口有包含和重叠，不相加。全部正负差值保留，两对不作为稳定总体减速率。','',
        '## 实际开关与成本','',
        ('| 轮次 | 边界meminfo |' if meminfo else '| 轮次 | 边界vmstat |')+' 边界内存／vmstat条数 | host／guest点数 | host CPU／秒 | 七guest CPU／秒 | 管理调用累计／并集秒 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        s=r['sampling'];t=r['timings_ns'];label=labels[r[field]]
        lines.append(f"| {r['index']} | {label} | {s['boundary_rows']['guest-memory.tsv']}／{s['boundary_rows']['guest-vmstat.tsv']} | {s['host_samples']}／{s['guest_samples']} | {s['host_cpu_seconds']:.6f} | {s['guest_cpu_seconds']:.6f} | {t['monitor_control_call_sum']/1e9:.3f}／{t['monitor_control_union']/1e9:.3f} |")
    lines += ['', '两组vmstat均仅表头，标记NOT_MEASURED，不是页故障或I/O为零。448条边界meminfo、阶段、原计时口径和周期采样保持；合并方法复用378次Python计时调用，减少756次date／awk是源码推导，不是CPU或秒数。后台直接CPU和并发管理调用分别记录，不直接当新增等待。' if meminfo else '跳过组vmstat仅表头，结果标记NOT_MEASURED；0条记录不是页故障或I/O为零。448条边界meminfo、阶段写入和周期采样保持。后台直接CPU不包含执行脚本的边界操作；管理调用有并发重叠，累计与时间并集分列，不能直接当新增等待。所有四组都有真实内存曲线。','',
        '## host内存与输入查询','',
        ('| 轮次 | 边界meminfo |' if meminfo else '| 轮次 | 边界vmstat |')+' current基线／峰值／创建后增量 MiB | 输入驻留 MiB | 查询／秒 |',
        '| --- | --- | ---: | ---: | ---: |']
    for r in runs:
        m=r['host_memory'];label=labels[r[field]]
        lines.append(f"| {r['index']} | {label} | {m['current_baseline_bytes']/2**20:.3f}／{m['current_peak_bytes']/2**20:.3f}／{m['current_peak_increase_bytes']/2**20:.3f} | {r['input_resident_bytes']/2**20:.3f} | {r['timings_ns']['final_input_query']/1e9:.3f} |")
    lines += ['', 'current覆盖输入、控制程序、私有服务和七guest的组内记账，仍有组外与未记账分配的范围限制。guest MemTotal−MemAvailable和Cached是guest逻辑量，Cached含Shmem，不与host相加。结束后一次105文件mincore只覆盖导入输入，逐页归属仍未查，不是运行中完整文件并集或共享收益。','']
    for r in runs:
        i=r['index'];label=labels[r[field]]
        lines.append(f'- 第{i}轮（{label}）：[host总量](run-{i}-on-host.png) · [增量](run-{i}-on-host-increase.png) · [逐guest曲线](run-{i}-on-guest.png) · [曲线点表](run-{i}-on-memory.tsv)')
    lines += ['', '原189步、七角色路径、关键输出、补丁、独立检查和每实例官方原0通过2失败均保持。七guest无代理创建及环境检查、只读输入／写边界和自有清理通过；本地registry元数据方式及默认DNS例外不变，网络流量未测。没有PSS、额外暂停、预读或缓存控制。','',
        '[逐轮数据](runs.tsv) · [逐对差值](pairs.tsv) · [完整指标](summary.json)。本报告只比较边界meminfo的独立／合并读取，与整套采样开关及边界vmstat对照分开。固定两对完成后停止，不为更好百分比追加。' if meminfo else '[逐轮数据](runs.tsv) · [逐对差值](pairs.tsv) · [完整指标](summary.json)。本报告是两组均采样的边界vmstat对照，与原整套采样关闭／开启的数据分开。固定两对完成后停止，不为更好百分比追加。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def publish(source,output):
    value=json.loads((source/'result.json').read_text())
    assert value['status']=='PASS' and value['stage']=='complete' and value['source_restored_stopped']
    assert not value['remaining_firecrackers'] and not value['remaining_running_containers']
    comparison=value.get('comparison','sampling');boundary=comparison=='boundary-vmstat';meminfo=comparison=='boundary-meminfo'
    runs=value['runs'];pairs=comparisons(runs,comparison=comparison);assert pairs==value['pairs']
    assert len(runs)==4 and sum(r['actual_actions'] for r in runs)==756
    import matplotlib
    matplotlib.use('Agg')
    output.mkdir(parents=True,exist_ok=False)
    public=[]
    for r in runs:
        row={k:r[k] for k in ('index','pair','mode','actual_actions','timings_ns','cold_requests',
            'cold_request_union_ns','sampling','input_files','input_resident_bytes','input_group_file_bytes',
            'query_cpu_seconds','ownership','sessions','grpo_setup_schedule')}
        if 'host_memory' in r:row['host_memory']=r['host_memory']
        if boundary:
            row.update(boundary_vmstat_mode=r['boundary_vmstat_mode'],light_skip_boundary_vmstat=r.get('light_skip_boundary_vmstat',False),
                boundary_vmstat=r.get('boundary_vmstat'))
        if meminfo:
            row.update(boundary_meminfo_mode=r['boundary_meminfo_mode'],light_skip_boundary_vmstat=True,boundary_vmstat=r['boundary_vmstat'],
                light_merge_boundary_meminfo=r.get('light_merge_boundary_meminfo',False),boundary_meminfo=r.get('boundary_meminfo'))
        row['correctness']='Original 189 actions, paths, patches, per-guest checks and original 0 passed / 2 failed official outcome retained'
        public.append(row)
        if r['mode']=='on':
            report=Path(r['report']);stem='run-'+str(r['index'])+'-on-'
            guest_curves(report,output/(stem+'guest.png'))
            shutil.copyfile(report/'memory.tsv',output/(stem+'memory.tsv'))
            for name,src in (('host.png',report/'host-file/overview.png'),
                             ('host-increase.png',report/'host-file/after-service-ready.png')):
                shutil.copyfile(src,output/(stem+name))
    summary=dict(status='PASS',runs=public,pairs=pairs,actual_actions=756,
        source_result=str(source/'result.json'),start_utc=value['start_utc'],finish_utc=value['finish_utc'],
        scope='Four single-service seven-guest GRPO runs; fresh imported local inputs, default reclaim, 0.1-second host/guest sampling switched together')
    if boundary:summary.update(comparison=comparison,scope='Both sampled at 0.1 seconds; keep/skip boundary vmstat only; four fresh seven-guest GRPO runs')
    if meminfo:summary.update(comparison=comparison,scope='Both sampled at 0.1 seconds and boundary vmstat skipped; separate/merged boundary meminfo only')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    table=[]
    for r in public:
        row={k:r[k] for k in ('index','pair','mode','actual_actions','input_files','input_resident_bytes','input_group_file_bytes','query_cpu_seconds')}
        if boundary:row['boundary_vmstat_mode']=r['boundary_vmstat_mode']
        if meminfo:row['boundary_meminfo_mode']=r['boundary_meminfo_mode']
        row.update({k+'_seconds':v/1e9 for k,v in r['timings_ns'].items()})
        for k in ('host_samples','guest_samples','host_cpu_seconds','guest_cpu_seconds','host_cpu_percent','host_max_gap_seconds'):
            row[k]=r['sampling'].get(k)
        row.update({k:r.get('host_memory',{}).get(k) for k in ('current_baseline_bytes','current_peak_bytes','current_peak_increase_bytes','file_peak_bytes')})
        table.append(row)
    with (output/'runs.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(table[0]),delimiter='\t');writer.writeheader();writer.writerows(table)
    pair_rows=[dict(pair=p['pair'],order='-'.join(p['order']),metric=k,**v) for p in pairs for k,v in p['timings'].items()]
    with (output/'pairs.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(pair_rows[0]),delimiter='\t');writer.writeheader();writer.writerows(pair_rows)
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(14,4.5))
    for ax,(metric,title) in zip(axes,(('tool_sum','Tool duration sum'),('frozen_tools_and_downloads_span','Concurrent actions + downloads'),('controller','Task controller'))):
        labels=('separate','merged') if meminfo else ('keep','skip') if boundary else ('off','on')
        for offset,mode,color in ((-.18,labels[0],'#64748b'),(.18,labels[1],'#0284c7')):
            ys=[p['timings'][metric][mode+'_seconds'] for p in pairs]
            bars=ax.bar([i+offset for i in (1,2)],ys,width=.34,color=color,label=mode)
            ax.bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
        ax.set_xticks([1,2],['separate → merged','merged → separate'] if meminfo else ['keep → skip','skip → keep'] if boundary else ['off → on','on → off']);ax.set_ylabel('Seconds');ax.set_title(title)
        ax.set_ylim(0,max(ax.get_ylim())*1.12);ax.grid(axis='y',alpha=.2)
    fig.legend(*axes[0].get_legend_handles_labels(),loc='lower center',ncol=2,frameon=False)
    fig.suptitle('Prettier GRPO: 7 guests, 189 actions per run'+('; both sampled' if boundary else ''))
    fig.tight_layout(rect=(0,.08,1,.94));fig.savefig(output/'timings.png',dpi=140);plt.close(fig)
    if boundary or meminfo:
        boundary_report(public,pairs,output,meminfo=meminfo)
        for p in output.iterdir():p.chmod(0o644)
        output.chmod(0o755)
        return summary
    lines=['# Prettier七沙箱GRPO：本地无代理轻量采样两对对照','',
        '固定关→开、开→关四次，共28个新guest、756个原动作。每次新统计组／105输入／私有服务，原七个4 GiB、2 vCPU guest并发调度和逐guest准备顺序不变，AgentENV默认回收。两端采样和原边界读取一起开关，无PSS或广泛收尾扫描。','',
        '## 任务耗时变化','',
        '| 对照 | 指标 | 关闭／秒 | 开启／秒 | 开启减关闭／秒 | 变化 |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for p in pairs:
        for key,label in (('tool_sum','工具累计'),('frozen_tools_and_downloads_span','并发动作及下载窗口'),('cold_request_span','七次创建请求跨度'),('rollout','创建前至全部检查结束'),('controller','任务控制程序整轮')):
            v=p['timings'][key];lines.append(f"| {p['pair']}（{'→'.join(p['order'])}） | {label} | {v['off_seconds']:.3f} | {v['on_seconds']:.3f} | {v['change_seconds']:+.3f} | {v['change_percent']:+.2f}% |")
    lines += ['', '![逐对用时](timings.png)','',
        '工具累计是七条路径的命令时间之和，不是整批墙钟用时。并发动作及下载窗口从首个执行调用开始至最后下载结束；创建跨度从首个冷创建请求到最后一个返回，其中可能穿插其他guest的准备。控制程序包含准备、创建、原动作、七实例额外检查／官方评测及删除；各项有包含和重叠，不相加，也不把控制程序减创建当纯采样时间。慢值及正负变化全部保留，两对不作为稳定总体减速率。','',
        '## 采样成本与覆盖','',
        '| 开启轮次 | host／guest点数 | host直接CPU／秒 | host单核比例 | 七guest采样CPU合计／秒 | 35次管理调用累计／秒 | 管理调用时间并集／秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in public:
        if r['mode']!='on':continue
        s=r['sampling'];t=r['timings_ns']
        lines.append(f"| {r['index']} | {s['host_samples']}／{s['guest_samples']} | {s['host_cpu_seconds']:.6f} | {s['host_cpu_percent']:.3f}% | {s['guest_cpu_seconds']:.6f} | {t['monitor_control_call_sum']/1e9:.3f} | {t['monitor_control_union']/1e9:.3f} |")
    lines += ['', '每个guest五次采样管理调用，七实例并发，因此累计与时间并集分列。后台直接CPU不含Shell的边界读取；开启每轮448条内存和448条vmstat边界记录，关闭均无记录。关闭内存和采样CPU未测，不填零；两组结束后的输入查询均保留，不属于运行中周期采样开关。逐guestCPU、采样间隔及峰值RSS见summary.json。','',
        '## host current主曲线与guest曲线','',
        '| 开启轮次 | 创建前基线／MiB | current观测峰值／MiB | 创建后峰值增量／MiB |',
        '| --- | ---: | ---: | ---: |']
    for r in public:
        if r['mode']=='on':
            m=r['host_memory'];lines.append(f"| {r['index']} | {m['current_baseline_bytes']/2**20:.3f} | {m['current_peak_bytes']/2**20:.3f} | {m['current_peak_increase_bytes']/2**20:.3f} |")
    lines += ['', 'current包含组内导入输入、控制程序、私有服务和七个guest；基线已含输入和服务，增量仍不是guest独占量。组外页及未记账分配不覆盖。file是host文件缓存记账，guest Cached在另一层，不能相加。这里没有共享／独立副本对照，不计算物理共享收益。','']
    for r in public:
        if r['mode']=='on':lines.append(f"- 第{r['index']}轮：[host总量](run-{r['index']}-on-host.png) · [创建后增量](run-{r['index']}-on-host-increase.png) · [逐guest内存／Cached](run-{r['index']}-on-guest.png) · [原曲线点表](run-{r['index']}-on-memory.tsv)")
    lines += ['', '## 结束后共同输入查询','',
        '| 轮次 | 模式 | 唯一文件数 | 驻留／MiB | 核对组file／MiB | 查询／秒 | 查询CPU／秒 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in public:
        lines.append(f"| {r['index']} | {r['mode']} | {r['input_files']} | {r['input_resident_bytes']/2**20:.3f} | {r['input_group_file_bytes']/2**20:.3f} | {r['timings_ns']['final_input_query']/1e9:.3f} | {r['query_cpu_seconds']:.3f} |")
    lines += ['', '查询发生在隔离运行及自有资源清理后、输入释放前；按设备号／inode去重，不读取数据页。四次容量不足均0，逐页记账归属仍NOT_QUERIED；它不是运行中的全文件并集，不加到current。','',
        '## 原任务状态与资源收尾','',
        '四组原189步、七角色路径、退出码、关键输出、最终补丁、独立检查和每实例官方0通过2失败均保持。任务重放／测量通过与官方原失败分开记录。全部28个guest身份独立；关闭没有运行采集，开启七个采样器均回收。七guest都使用无代理创建请求并检查环境；实际regctl仅本地version／manifest，无下载或17891回退日志。保留默认DNS例外，未测网络请求量。只读commits绑定、实际写边界、固定卷及成功清理检查通过，源服务恢复停止。','',
        '组外归档准备、组内导入／流程和最终查询的逐轮数据见[runs.tsv](runs.tsv)，逐对差值见[pairs.tsv](pairs.tsv)，脱敏完整指标见[summary.json](summary.json)。这些数据与既有单沙箱／旧GRPO版本分开，不混算。','',
        '本阶段按两对结束，不增加轮次。下一步先离线核对创建和动作窗口的差值落点，再决定是否值得优化；无需为解释保存日志已经能回答的问题重跑。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    for p in output.iterdir():p.chmod(0o644)
    output.chmod(0o755)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();publish(args.source,args.output)
    print('status=PASS result_dir='+str(args.output))


if __name__=='__main__':main()
