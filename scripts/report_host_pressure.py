"""Offline four-condition pressure report; current and selected-file residency stay separate."""
import argparse
import os
from pathlib import Path
import shutil
from statistics import median

from collect_host_density import ROOT,MIB,read,rows,table,write
from host_pressure import require_outside
from report_host_density import member_audit

SEGMENTS=('read_repo','read_typescript','core_patch','core_verify','backbone_finish')


def gap_ratio(a,b,c,d):
    reference=b-a;pressured=d-c
    return dict(no_pressure_gap=reference,pressure_gap=pressured,compression_ratio=pressured/reference if reference else None)


def latency_difference(shared,independent):
    return dict(seconds=independent-shared,percent=100*(independent/shared-1) if shared else None)


def counter_delta(points,field):
    return int(points[-1][field])-int(points[0][field])


def tradeoff(gap,latency):
    recovered=gap['no_pressure_gap']-gap['pressure_gap']
    extra=latency['difference_of_differences_seconds']
    return dict(gap_recovered_mib=recovered,extra_delay_seconds=extra,
        seconds_per_recovered_gib=extra/(recovered/1024) if recovered>0 else None)


def sampling_details(points,start,end):
    pairs=list(zip(points,points[1:]))
    largest=max(pairs,key=lambda p:int(p[1]['monotonic_ns'])-int(p[0]['monotonic_ns']))
    during=[(a,b) for a,b in pairs if start<=int(a['monotonic_ns'])<=int(b['monotonic_ns'])<=end]
    return dict(max_gap_seconds=(int(largest[1]['monotonic_ns'])-int(largest[0]['monotonic_ns']))/1e9,
        execution_max_gap_seconds=max(int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in during)/1e9,
        largest_gap_start_monotonic_ns=int(largest[0]['monotonic_ns']),largest_gap_phase=largest[0].get('pressure_phase'),
        largest_gap_before_execution=int(largest[1]['monotonic_ns'])<start)


def normalize_pressure_release(points,enabled):
    """Retain original files; v1 release rows detached the PID and did not measure RSS."""
    result=[dict(row) for row in points]
    if enabled:
        for row in result:
            if row['pressure_phase']=='RELEASING':
                for field in ('pressure_rss_bytes','pressure_anon_bytes','pressure_swap_bytes'):row[field]=''
    return result


def summarize_run(member,folder):
    """Audit one saved pressure run at its actual N, preserving the original metrics."""
    exceptions=[]
    condition=member['condition'];row=read(folder/'summary.json')
    audit=member_audit(ROOT,row)
    assert row['condition']==condition and row['count']==member['count'] and row['pressure_enabled']==member['pressure']
    storage=ROOT/row['source_storage'];task=read(storage/'density/result.json');raw_member=Path(member['result_dir'])
    pressure=read(raw_member/'pressure/result.json');assert pressure['enabled']==member['pressure']
    host=rows(folder/'host-memory.tsv');start,end=row['execution_window_monotonic_ns']
    window=[r for r in host if start<=int(r['monotonic_ns'])<=end];assert len(window)>1
    peak=max(window,key=lambda r:int(r['current']))
    global_rows=rows(raw_member/'host-global.tsv');global_window=[r for r in global_rows if start<=int(r['monotonic_ns'])<=end]
    assert len(global_window)>1 and read(raw_member/'host-global.json')['status']=='PASS'
    middle=min(global_window,key=lambda r:abs(int(r['monotonic_ns'])-(start+end)//2))
    swap_enabled=any(int(r['SwapTotal'])>0 for r in global_rows)
    swap_used=max(int(r['SwapTotal'])-int(r['SwapFree']) for r in global_rows)
    max_worker_swap=max(int(r['pressure_swap_bytes']) for r in global_rows if r['pressure_phase']!='RELEASING')
    valid=None
    if member['pressure']:
        require_outside(pressure['worker']['cgroup'],pressure['prepared']['cgroup'])
        assert pressure['at_ready']['monotonic_ns']<task['creation_batches'][0]['start_monotonic_ns']
        assert pressure['before_release']['monotonic_ns']>end and pressure['before_release']['pressure_pid']==pressure['worker']['pid']
        assert all(int(r['pressure_pid'])==pressure['worker']['pid'] and r['pressure_phase']=='HOLDING' for r in global_window)
        valid=pressure['allocator']['target_reached'] and not swap_enabled and max_worker_swap==0
        if not valid:exceptions.append(dict(condition=condition,issue='Pressure target missed or swap enabled/used; no effective-pressure conclusion',raw=str(raw_member)))
    peers=[]
    for child in task['results']:
        raw=storage/'density'/child['raw'];before=[];after=[];tools=[];calls=read(raw/'events.json')
        action_calls=[c for c in calls if c['stage'].startswith('controller-call-')];assert len(action_calls)==10
        for segment in SEGMENTS:
            directory=raw/'rollouts/backbone'/segment;tools+=rows(directory/'steps.tsv')
            boundaries=rows(directory/'guest-memory.tsv')
            before += [int(r['timestamp_ns']) for r in boundaries if r['phase']=='before']
            after += [int(r['timestamp_ns']) for r in boundaries if r['phase']=='after']
        assert len(tools)==len(before)==len(after)==29
        assert all(a<=b for a,b in zip(before,after)) and all(b<=a for b,a in zip(after,before[1:]))
        peers.append(dict(condition=condition,guest=child['role'],tool_sum_seconds=sum(int(r['duration_ns']) for r in tools)/1e9,
            guest_boundary_span_seconds=(after[-1]-before[0])/1e9,
            host_actions_downloads_seconds=(max(c['start_monotonic_ns']+c['duration_ns'] for c in action_calls)-min(c['start_monotonic_ns'] for c in action_calls))/1e9,
            raw=str(raw.relative_to(ROOT))))
    center=median(g['guest_boundary_span_seconds'] for g in peers)
    for g in peers:g.update(relative_to_group_median=g['guest_boundary_span_seconds']/center,slow_within_group=g['guest_boundary_span_seconds']>1.5*center)
    scan_fields=[f for f in global_rows[0] if f.startswith(('pgscan_','pgsteal_'))]
    stats={key:counter_delta(window,key) for key in ('workingset_refault_file','workingset_activate_file')}
    if any(v<0 for v in stats.values()):exceptions.append(dict(condition=condition,issue='Negative sampled cgroup counter delta retained',deltas=stats,raw=str(raw_member)))
    r=dict(condition=condition,mode=member['mode'],pressure_enabled=member['pressure'],effective_pressure=valid,
        source_member=row['source_member'],source_storage=row['source_storage'],cgroup=audit['cgroup'],
        current_peak_mib=row['current_peak_bytes']/MIB,execution_current_peak_mib=row['execution_current_peak_bytes']/MIB,
        execution_current_median_mib=row['execution_current_median_bytes']/MIB,
        peak_components_mib={key:int(peak[key])/MIB for key in ('file','anon','kernel','shmem','file_dirty','file_writeback')},
        p1_mincore_mib=row['p1_resident_bytes']/MIB,p0_resident_bytes=row['p0_resident_bytes'],p0_counter_flags=row['p0_counter_flags'],
        prepared_before_pressure_current_mib=row['before_pressure']['current']/MIB,
        prepared_after_pressure_current_mib=row['after_pressure']['current']/MIB,cold_baseline_current_mib=row['cold_baseline']['current']/MIB,
        **stats,full_counter_deltas={key:counter_delta(host,key) for key in stats},
        tool_sum_seconds=row['timings_ns']['tool_sum']/1e9,actions_downloads_seconds=row['timings_ns']['actions_and_downloads']/1e9,
        creation_span_seconds=row['timings_ns']['creation_request_span']/1e9,controller_seconds=row['timings_ns']['controller']/1e9,
        group_observation_seconds=(int(global_rows[-1]['monotonic_ns'])-int(global_rows[0]['monotonic_ns']))/1e9,
        guest_span_median_seconds=center,guest_span_max_seconds=max(g['guest_boundary_span_seconds'] for g in peers),
        host_available_median_gib=median(int(x['MemAvailable']) for x in global_window)/2**30,
        host_available_min_gib=min(int(x['MemAvailable']) for x in global_window)/2**30,
        host_available_max_gib=max(int(x['MemAvailable']) for x in global_window)/2**30,
        sampling_gaps=dict(group=sampling_details(host,start,end),global_host=sampling_details(global_rows,start,end)),
        last_group_sample_mib={key:int(host[-1][key])/MIB for key in ('current','file','anon','kernel')},
        pressure_points=dict(before=pressure['before'],middle=middle,after=pressure['after_release']),pressure_receipt=pressure,
        pressure_anon_max_gib=max(int(x['pressure_anon_bytes']) for x in global_rows if x['pressure_phase']!='RELEASING')/2**30,
        swap_enabled=swap_enabled,host_swap_used_max_bytes=swap_used,pressure_swap_max_bytes=max_worker_swap,
        host_reclaim_execution_deltas={f:counter_delta(global_window,f) for f in scan_fields},
        host_reclaim_allocation_deltas={f:pressure['at_ready'][f]-pressure['before'][f] for f in scan_fields},
        volume_capacity_gib=row['volume_capacity_bytes']/2**30,minimum_disk_available_gib=row['host_minimum_available_bytes']/2**30,
        sampler=read(raw_member/'host-global.json'))
    shutil.copy2(raw_member/'host-global.tsv',folder/'host-global.tsv')
    table(folder/'host-global-normalized.tsv',normalize_pressure_release(global_rows,member['pressure']))
    pressure_plot(global_rows,folder,start,end)
    return r,peers,audit,exceptions


def report(batch_path,output):
    batch=read(batch_path/'result.json');assert batch['status']=='PASS' and batch['source_restored_stopped']
    assert batch['experiment']=='prettier-density-pressure-v1' and len(batch['runs'])==4
    assert [r['condition'] for r in batch['runs']]==list('ABCD')
    runs=[];guest_rows=[];audits=[];exceptions=[]
    for member in batch['runs']:
        r,peers,audit,issues=summarize_run(member,output/f"{member['condition']}-prettier6604-N10-{member['mode']}")
        runs.append(r);guest_rows+=peers;audits.append(audit);exceptions+=issues
    assert len({r['cgroup'] for r in runs})==4
    for g in guest_rows:
        reference=next(r for r in runs if r['condition']==('A' if g['condition'] in ('A','C') else 'B'))['guest_span_median_seconds']
        g.update(relative_to_no_pressure_median=g['guest_boundary_span_seconds']/reference,slow_vs_no_pressure_median=g['guest_boundary_span_seconds']>1.5*reference)
    a,b,c,d=runs
    comparison=dict(current=gap_ratio(*(r['execution_current_peak_mib'] for r in runs)),
        mincore=gap_ratio(*(r['p1_mincore_mib'] for r in runs)),
        latency={field:dict(no_pressure=latency_difference(a[field],b[field]),pressure=latency_difference(c[field],d[field]),
            difference_of_differences_seconds=(d[field]-c[field])-(b[field]-a[field])) for field in ('tool_sum_seconds','actions_downloads_seconds','controller_seconds','creation_span_seconds','guest_span_median_seconds')})
    comparison['tradeoff']={field:tradeoff(comparison['current'],comparison['latency'][field]) for field in ('tool_sum_seconds','actions_downloads_seconds')}
    comparison['observed_direction']=('记账差距收窄，独立相对共享的任务延迟差增大' if comparison['current']['pressure_gap']<comparison['current']['no_pressure_gap'] and comparison['latency']['actions_downloads_seconds']['difference_of_differences_seconds']>0
        else '记账差距收窄，未观察到额外相对延迟增加' if comparison['current']['pressure_gap']<comparison['current']['no_pressure_gap'] else '本压力点的记账差距未收窄')
    effective=all(r['effective_pressure'] for r in runs if r['pressure_enabled'])
    if not effective:comparison['observed_direction']='未形成两组有效压力，数值只按实际达成情况列出'
    value=dict(status='PASS',runs=runs,comparison=comparison,guests=40,actions=1160,audits=audits,exceptions=exceptions,
        effective_pressure_comparison=effective,
        control_reuse='NONE; all four are fresh, host cache/load changed',page_size=os.sysconf('SC_PAGE_SIZE'),
        slow_guest_definition='Descriptive flag: boundary span > 1.5 times group median or matching no-pressure group median; no guest is removed.',
        conclusion_scope='One pressure point, one run per condition; measured charge/latency tradeoff, not a maximum deployment capacity test.')
    table(output/'guest-spans.tsv',guest_rows);write(output/'pressure-summary.json',value);comparison_plot(runs,output)
    write_report(value,guest_rows,output)
    print('status=PASS result_dir='+str(output),flush=True)
    return value


def pressure_plot(data,folder,start,end):
    import matplotlib.pyplot as plt
    zero=int(data[0]['monotonic_ns']);x=[(int(r['monotonic_ns'])-zero)/1e9 for r in data]
    group=rows(folder/'host-memory.tsv')
    fig,axes=plt.subplots(4,1,figsize=(11,10),sharex=True,constrained_layout=True)
    axes[0].plot([(int(r['monotonic_ns'])-zero)/1e9 for r in group],[int(r['current'])/MIB for r in group],label='Experiment memory.current')
    axes[0].set_ylabel('Group charge memory.current (MiB)')
    axes[1].plot(x,[int(r['MemAvailable'])/2**30 for r in data],label='Host MemAvailable')
    axes[1].axhline(int(data[0]['MemTotal'])/5/2**30,color='gray',linestyle='--',label='20% of MemTotal');axes[1].set_ylabel('Host MemAvailable (GiB)')
    pressured=any(r['pressure_phase']=='HOLDING' for r in data)
    axes[2].plot(x,[float('nan') if pressured and r['pressure_phase']=='RELEASING' else int(r['pressure_anon_bytes'])/2**30 for r in data],label='External process RssAnon');axes[2].set_ylabel('Pressure process anon RSS (GiB)')
    for field in ('pgscan_kswapd','pgscan_direct','pgsteal_kswapd','pgsteal_direct'):
        axes[3].plot(x,[(int(r[field])-int(data[0][field]))/1e6 for r in data],label=field)
    axes[3].set(ylabel='Host reclaim counter delta (million)',xlabel='Host monotonic elapsed seconds')
    allocation=[int(r['monotonic_ns']) for r in data if r['pressure_phase']=='ALLOCATING']
    holding=[int(r['monotonic_ns']) for r in data if r['pressure_phase']=='HOLDING']
    for ax in axes:
        if allocation and holding:ax.axvspan((min(allocation)-zero)/1e9,(min(holding)-zero)/1e9,alpha=.12,color='orange',label='Anonymous allocation')
        ax.axvspan((start-zero)/1e9,(end-zero)/1e9,alpha=.1,color='green',label='Actions + downloads');ax.grid(alpha=.2);ax.legend(fontsize=8)
    for ax in axes[:3]:ax.set_ylim(bottom=0)
    fig.suptitle(folder.name+'; allocator is outside the experiment group')
    fig.savefig(folder/'pressure.png',dpi=130);plt.close(fig)


def comparison_plot(runs,output):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
    for mode,color in (('shared','#2563eb'),('independent','#d97706')):
        selected=[r for r in runs if r['mode']==mode];xs=[r['host_available_median_gib'] for r in selected]
        for ax,field in zip(axes,('execution_current_peak_mib','tool_sum_seconds')):
            ax.plot(xs,[r[field] for r in selected],color=color,label=mode)
            for r in selected:
                x=r['host_available_median_gib'];ax.errorbar([x],[r[field]],xerr=[[x-r['host_available_min_gib']],[r['host_available_max_gib']-x]],fmt='s' if r['pressure_enabled'] else 'o',color=color,capsize=3)
                ax.annotate(r['condition'],(x,r[field]),xytext=(6,7),textcoords='offset points')
    axes[0].set_ylabel('Group charge memory.current execution peak (MiB)');axes[1].set_ylabel('Guest tool cumulative wall time (seconds)')
    for ax in axes:ax.set_xlabel('Host MemAvailable during execution (GiB, median)');ax.grid(alpha=.2);ax.legend()
    fig.suptitle('Prettier N=10: circles = no pressure, squares = anonymous pressure; x-bars = observed range')
    fig.savefig(output/'pressure-comparison.png',dpi=140);plt.close(fig)


def write_report(value,guests,output):
    runs=value['runs'];comparison=value['comparison']
    labels=dict(tool_sum_seconds='工具累计',actions_downloads_seconds='动作及下载窗口',controller_seconds='协调程序整轮',creation_span_seconds='创建请求跨度',guest_span_median_seconds='guest跨度中位数')
    lines=['# N=10：内存压力下共享与独立副本的记账量和延迟','',
        f"**本轮观察：{comparison['observed_direction']}。** current差距保留{comparison['current']['compression_ratio']:.2%}，指定文件mincore差距保留{comparison['mincore']['compression_ratio']:.2%}。独立相对共享的动作及下载窗口差，从无压力的{comparison['latency']['actions_downloads_seconds']['no_pressure']['seconds']:.3f}秒变成有压力的{comparison['latency']['actions_downloads_seconds']['pressure']['seconds']:.3f}秒。",'',
        'A／B为本批新跑的无压力对照，C／D为有压力组；不复用11-02旧值。40个guest、1160个原动作，逐组身份、19／190个目标文件、P0=0／P1及原官方0通过2失败和清理通过。',
        '', '压力在复制／同步完成后、P0及guest创建前启动，位于实验组外，触碰匿名页一次后固定持有至任务和收尾结束。全流程峰值包含加压前准备，差距压缩比使用执行阶段峰值。current与mincore分开，不拼成总物理量。',
        '', '## 四条件结果','',
        '| 条件 | 组别／压力 | current全程／执行峰值 MiB | file／anon（执行峰值同点）MiB | P1 mincore MiB | refault增量 | 工具累计 s | 动作及下载 s | 协调程序整轮 s |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:lines.append(f"| {r['condition']} | {'共享' if r['mode']=='shared' else '独立'}／{'有' if r['pressure_enabled'] else '无'} | {r['current_peak_mib']:.3f} / {r['execution_current_peak_mib']:.3f} | {r['peak_components_mib']['file']:.3f} / {r['peak_components_mib']['anon']:.3f} | {r['p1_mincore_mib']:.3f} | {r['workingset_refault_file']} | {r['tool_sum_seconds']:.3f} | {r['actions_downloads_seconds']:.3f} | {r['controller_seconds']:.3f} |")
    lines+=['','## 差距是否压缩','', '| 口径 | 无压力：独立−共享 MiB | 有压力：独立−共享 MiB | 有压力差距／无压力差距 |','| --- | ---: | ---: | ---: |']
    for field in ('current','mincore'):
        g=comparison[field];ratio='未定义（分母为零）' if g['compression_ratio'] is None else f"{g['compression_ratio']:.6f}"
        lines.append(f"| {field} | {g['no_pressure_gap']:.3f} | {g['pressure_gap']:.3f} | {ratio} |")
    lines+=['','## 延迟代价是否转移','', '| 指标 | 无压力：独立−共享 秒／% | 有压力：独立−共享 秒／% | 两个增量之差 秒 |','| --- | ---: | ---: | ---: |']
    for field,v in comparison['latency'].items():lines.append(f"| {labels[field]} | {v['no_pressure']['seconds']:.3f} / {v['no_pressure']['percent']:.2f}% | {v['pressure']['seconds']:.3f} / {v['pressure']['percent']:.2f}% | {v['difference_of_differences_seconds']:.3f} |")
    for field,v in comparison['tradeoff'].items():
        conversion='差距未缩小，不计算秒/GiB' if v['seconds_per_recovered_gib'] is None else f"每缩小1 GiB记账差距，对照延迟差改变{v['seconds_per_recovered_gib']:.6f}秒/GiB"
        lines+=['',f"{labels[field]}：相对无压力条件，记账差距缩小{v['gap_recovered_mib']:.3f} MiB，独立相对共享的额外延迟差改变{v['extra_delay_seconds']:.6f}秒；{conversion}。"]
    lines+=['','协调程序整轮从准备完毕后开始，包含加压等待、创建和任务／清理，不含此前的输入复制／同步；机器摘要另列组运行观察跨度group_observation_seconds，覆盖组内导入、准备、任务和释放压力。工具累计是十guest之和，并发窗口不是累计值。refault表示文件页重新进入缓存的计数，不直接等于物理磁盘重读字节。','',
        '## 实际压力和准备缓存变化','', '| 条件 | MemAvailable前／中／后 GiB | 中间可用比例 | 压力匿名RSS前／中／后 GiB | SwapFree前／中／后 GiB（启用） | 准备current加压前／后／P0 MiB |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        points=r['pressure_points'];mem=[int(points[k]['MemAvailable'])/2**30 for k in ('before','middle','after')];fraction=int(points['middle']['MemAvailable'])/int(points['middle']['MemTotal'])
        swap=[int(points[k]['SwapFree'])/2**30 for k in ('before','middle','after')]
        lines.append(f"| {r['condition']} | {' / '.join(f'{x:.3f}' for x in mem)} | {fraction:.2%} | 0 / {int(points['middle']['pressure_anon_bytes'])/2**30:.3f} / 0 | {' / '.join(f'{x:.3f}' for x in swap)}（{r['swap_enabled']}） | {r['prepared_before_pressure_current_mib']:.3f} / {r['prepared_after_pressure_current_mib']:.3f} / {r['cold_baseline_current_mib']:.3f} |")
    lines+=['','### 回收、重新进入缓存和写回','',
        'refault／activate是实验cgroup执行窗口内首末采样的计数增量，全程增量另在JSON保存；pgscan／pgsteal是整个宿主，不归因于单一组。每个计数字段单列，不将按来源和按文件类型的重复分类相加。dirty／writeback取执行current峰值同一采样点。','',
        '| 条件 | refault／activate增量 | 加压期间宿主pgscan_kswapd／direct | 执行期间宿主pgscan_kswapd／direct | 执行期间宿主pgsteal_kswapd／direct | file_dirty／writeback MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        alloc=r['host_reclaim_allocation_deltas'];execution=r['host_reclaim_execution_deltas'];components=r['peak_components_mib']
        lines.append(f"| {r['condition']} | {r['workingset_refault_file']} / {r['workingset_activate_file']} | {alloc['pgscan_kswapd']} / {alloc['pgscan_direct']} | {execution['pgscan_kswapd']} / {execution['pgscan_direct']} | {execution['pgsteal_kswapd']} / {execution['pgsteal_direct']} | {components['file_dirty']:.3f} / {components['file_writeback']:.3f} |")
    lines+=['','### 组运行时间和创建请求跨度','', '| 条件 | 组运行观察跨度 s | 协调程序整轮 s | 创建请求跨度 s |','| --- | ---: | ---: | ---: |']
    for r in runs:lines.append(f"| {r['condition']} | {r['group_observation_seconds']:.3f} | {r['controller_seconds']:.3f} | {r['creation_span_seconds']:.3f} |")
    lines+=['','组运行观察跨度取宿主采样首末时间，覆盖组内导入／复制／同步至压力释放，不含组外输入还原；协调程序整轮沿用旧定义。两者均不直接作为纯任务延迟。']
    lines+=['','前为分配前、复制／同步后；中为动作窗口中点最近的0.1秒样本；后为压力进程释放后的即时值。压力匿名RSS前／后为0，因为进程尚未创建／已经退出；中读取实际RssAnon。完整扫描／回收序列和进程驻留见每组host-global.tsv及机器摘要。swap开启、目标未达到或压力提前退出的异常不用于有效压力结论。','',
        '## 逐guest工具跨度','', '跨度取原guest边界文件中首次before至末次after的时间，包含命令间隔／边界记录，和纯工具累计分列。标记规则为超过本组或对应无压力组中位数1.5倍，仅用于定位，不剔除。', '',
        '| 条件 | guest | 工具累计 s | guest边界跨度 s | 对本组中位数倍数 | 对无压力中位数倍数 | 慢标记 |','| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for g in guests:lines.append(f"| {g['condition']} | {g['guest']} | {g['tool_sum_seconds']:.3f} | {g['guest_boundary_span_seconds']:.3f} | {g['relative_to_group_median']:.3f} | {g['relative_to_no_pressure_median']:.3f} | {g['slow_within_group'] or g['slow_vs_no_pressure_median']} |")
    baseline=next(r for r in runs if r['condition']=='B');pressured=next(r for r in runs if r['condition']=='D')
    lines+=['',f"D的guest跨度中位数为{pressured['guest_span_median_seconds']:.3f}秒，对应无压力B为{baseline['guest_span_median_seconds']:.3f}秒；中位数也变慢，延迟变化并非只来自一个最慢guest。"]
    lines+=['','## 对部署密度的判断','',
        f"独立组D的准备current从{pressured['prepared_before_pressure_current_mib']:.3f}降到{pressured['prepared_after_pressure_current_mib']:.3f} MiB，说明相当一部分准备缓存可以回收。旧无压力current差不能全部视为不可回收的内存需求。",'',
        f"本压力点仍保留{comparison['current']['pressure_gap']:.3f} MiB的执行峰值记账差和{comparison['mincore']['pressure_gap']:.3f} MiB的指定文件驻留差。同时独立相对共享的任务窗口多{comparison['latency']['actions_downloads_seconds']['pressure']['seconds']:.3f}秒，D的执行窗口refault增量为{pressured['workingset_refault_file']}，说明本轮存在缓存重入；仅凭计数不足以确定具体动作的减速原因，物理盘I/O耗时未单独采集。",'',
        '因此，本次结果支持“共享在此压力条件下仍同时更少记账、任务更快”，也确认部分原差距可以用回收来压缩，并伴随更大的相对延迟。秒/GiB是这次固定N=10对照的描述性换算，不外推成通用边际成本。可多部署多少guest，需要另定统一延迟预算并在固定内存预算下增加N。']
    lines+=['','## 图和原始证据','', '![按宿主可用内存比较](pressure-comparison.png)', '', '[机器摘要、分项、回收计数及完整身份检查证据](pressure-summary.json) · [逐guest表](guest-spans.tsv)']
    for r in runs:
        folder=f"{r['condition']}-prettier6604-N10-{r['mode']}";lines+=['',f"{r['condition']}：[组内存曲线]({folder}/host.png) · [压力及宿主回收]({folder}/pressure.png) · [guest曲线]({folder}/guest.png) · [工具时延]({folder}/tools.png)。原始目录：`{r['source_storage']}`。"]
    lines+=['','## 异常与结论边界','',str(value['exceptions']) if value['exceptions'] else '四组任务与身份验收通过。以下保留采样缺口和统计观察，不补写原始值。', '',
        '这四次给出一个约20%宿主可用内存条件下的记账量／延迟对照，实际是否回收目标组缓存还须结合准备current和回收计数判断。即使差距保持，也只说明该压力点的差距，不能单独证明最大可部署guest数。每条件一次，不估计稳定总体减速或置信区间。','']
    flagged=[r['condition'] for r in runs if any(v['file_exceeds_current'] for v in r['p0_counter_flags'].values())]
    lines+=['', '### 分项和采样尾部观察', '']
    if flagged:lines.append('P0 file>current提示出现在'+ '、'.join(flagged)+'。分项和总量不是同步快照，基线分项不作精确拆账。')
    for r in runs:
        gaps=r['sampling_gaps']
        if max(g['max_gap_seconds'] for g in gaps.values())>.2:
            lines+=['',f"{r['condition']}宿主最大采样间隔{gaps['global_host']['max_gap_seconds']:.3f}秒、组采样间隔{gaps['group']['max_gap_seconds']:.3f}秒；发生在执行窗口之前：{gaps['global_host']['largest_gap_before_execution']}／{gaps['group']['largest_gap_before_execution']}。执行窗口内最大间隔{gaps['global_host']['execution_max_gap_seconds']:.6f}／{gaps['group']['execution_max_gap_seconds']:.6f}秒。具体阻塞调用未追踪，见该组host-global.tsv／host-memory.tsv。"]
    tail=max(runs,key=lambda r:r['last_group_sample_mib']['current'])
    lines+=['',f"末点current最大为{tail['condition']}：{tail['last_group_sample_mib']['current']:.3f} MiB，其中file {tail['last_group_sample_mib']['file']:.3f} MiB。采样未延伸至尾部缓存归零，不将末点当释放后稳态；精确释放时刻未知，原始目录为`{tail['source_storage']}`。"]
    lines+=['','C／D的RELEASING阶段没有继续读取压力进程RSS，原始v1记录的零是占位，不能当作真实占用。原host-global.tsv保留，新增host-global-normalized.tsv将该阶段RSS／VmSwap留空，图中留空；HOLDING任务阶段及释放完成后的数据有效。采样代码已修正为该阶段输出空值，不重跑任务。']
    if value.get('postrun_resources',{}).get('status')=='PASS':lines+=['','随后已确认各实验组目录、自有进程、挂载和成功卷均已移除；该检查不回填采样末点。']
    lines+=['','## 下一步建议','','只离线比较D组五个分段的调用时延和refault时间序列，定位额外任务窗口主要出现在哪些分段；以现有日志能支持的解释为完成条件，不再启动沙箱。','']
    (output/'REPORT.md').write_text('\n'.join(lines))


if __name__=='__main__':
    import matplotlib
    matplotlib.use('Agg')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    report(a.batch.resolve(),a.output.resolve())
