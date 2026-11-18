"""Offline final report for the exact twelve completed density runs."""
import argparse
import csv
import json
from pathlib import Path
from statistics import median

MIB=2**20
COUNTS=(1,2,4,7,10)


def fit(xs,ys):
    if len(xs)!=len(ys) or len(xs)<2:raise ValueError('fit requires paired observations')
    mx=sum(xs)/len(xs);my=sum(ys)/len(ys)
    slope=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs)
    intercept=my-slope*mx;residual=sum((y-intercept-slope*x)**2 for x,y in zip(xs,ys));total=sum((y-my)**2 for y in ys)
    return dict(slope_mib_per_guest=slope,intercept_mib=intercept,r_squared=1-residual/total if total else None,
        observed_median_mib=median(ys),points=len(xs))


def savings(shared,independent):
    return dict(difference=independent-shared,fraction=(independent-shared)/independent if independent else None)


def member_audit(root,row):
    """Check saved identities, original outcomes and sampling receipts; never query live pages."""
    storage=root/row['source_storage'];native=json.loads((storage/'result.json').read_text());task=json.loads((storage/'density/result.json').read_text())
    n=row['count'];c4=row['task']=='c4';layer_count=9 if c4 else 19
    assert native['status']==task['status']=='PASS' and len(task['results'])==len(task['guests'])==n
    assert task['actions']==n*(36 if c4 else 29)
    assert len(task['creation_batches'])==1 and task['creation_batches'][0]['count']==n
    assert len({g['sandbox_id'] for g in task['guests'].values()})==n
    assert task['individual_target_bind_mounts']=='ABSENT' and task['copy_independence']['status']=='PASS'
    assert task['identity']['status']=='PASS' and task['cleanup']['service_sandbox_list_empty']
    assert all(native['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    assert task['P0']['resident_bytes']==0
    objects=task['P1']['objects'];keys={(o['identity']['device'],o['identity']['inode']) for o in objects}
    assert len(keys)==len(objects)==layer_count*(n if row['mode']=='independent' else 1)
    assert all(len(o['users'])==(1 if row['mode']=='independent' else n) for o in objects)
    guest_samples=0;guest_cpu=0;replay_gaps=[]
    for child in task['results']:
        raw=storage/'density'/child['raw'];result=json.loads((raw/'result.json').read_text())
        assert result['status']=='PASS'
        if c4:
            assert result['actions']==36 and result['strict_exit_match'] and result['roundtrip_validation']==result['syntax_validation']=='PASS'
            events=json.loads((raw/'events.json').read_text());replay=next(e for e in events if e['stage']=='replay')
            with (raw/'tools.tsv').open() as stream:tools=list(csv.DictReader(stream,delimiter='\t'))
            span=int(tools[-1]['end_monotonic_ns'])-int(tools[0]['start_monotonic_ns'])
            managed=[e for e in events if e['stage'].startswith('monitor-collect-')];assert len(managed)==1
            replay_gaps.append(dict(guest=child['role'],host_replay_seconds=replay['duration_ns']/1e9,guest_tool_span_seconds=span/1e9,unallocated_seconds=(replay['duration_ns']-span)/1e9,
                managed_collector_request_seconds=managed[0]['duration_ns']/1e9,managed_collector_minus_replay_seconds=(managed[0]['duration_ns']-replay['duration_ns'])/1e9,raw=str(raw.relative_to(root))))
        else:
            official=json.loads((raw/'official-task-result.json').read_text())['parser_results']
            assert official['num_tests_passed']==0 and official['num_tests_failed']==2
        monitor=json.loads((raw/'runtime-monitor.json').read_text());assert monitor['profile']=='light' and not monitor['host_vmm_requested'] and len(monitor['sessions'])==1
        session=monitor['sessions'][0];receipt=session['receipt'];assert receipt['status']=='PASS' and receipt['interval_seconds']==.1 and session['collector_reaped']
        guest_samples+=receipt['samples'];guest_cpu+=receipt['cpu_user_seconds']+receipt['cpu_system_seconds']
    collector=row['collector'];assert collector['status']=='PASS' and collector['interval_seconds']==.1 and collector['pss_interval_seconds'] is None
    batch=task['creation_batches'][0];zero=batch['start_monotonic_ns']
    return dict(status='PASS',cgroup=json.loads((root/row['source_member']/'result.json').read_text())['cgroup'],
        source_storage=row['source_storage'],selected_files=len(keys),creation_batches=task['creation_batches'],
        creation_requests=[dict(index=i,sandbox_id=g['sandbox_id'],start_from_batch_seconds=(g['start_monotonic_ns']-zero)/1e9,end_from_batch_seconds=(g['end_monotonic_ns']-zero)/1e9) for i,g in enumerate(task['guests'].values())],
        guest_samples=guest_samples,guest_cpu_seconds=guest_cpu,host_samples=collector['samples'],host_cpu_seconds=collector['thread_cpu_ns']/1e9,
        input_conversion=task.get('input_conversion'),input_sync=native.get('persistent_input_preparation_sync'),
        cleanup=task['cleanup'],replay_gaps=replay_gaps,paths_finished_to_P1_end_seconds=(task['P1']['end_monotonic_ns']-task['paths_finished_monotonic_ns'])/1e9,
        minimum_volume_available_gib=row['volume_minimum_available_bytes']/2**30,
        P0_current_mib=row['cold_baseline']['current']/MIB,P0_file_mib=row['cold_baseline']['file']/MIB)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('results',type=Path);a=p.parse_args()
    batch=json.loads((a.batch/'result.json').read_text())
    expected={(t,n,m) for t,ns in (('prettier6604',COUNTS),('c4',(7,))) for n in ns for m in ('shared','independent')}
    actual={(r['task'],r['count'],r['mode']) for r in batch['runs']}
    if batch['status']!='PASS' or actual!=expected or len(batch['runs'])!=12:raise ValueError('all twelve completed members are required')
    records={}
    for key in sorted(expected):
        task,n,mode=key;folder=a.results/f'{task}-N{n}-{mode}';row=json.loads((folder/'summary.json').read_text())
        if row['identity']!='PASS' or row['copy_independence']!='PASS' or row['p0_resident_bytes']!=0:raise ValueError('member evidence incomplete')
        records[key]=row
    root=Path(__file__).resolve().parents[1]
    audits={key:member_audit(root,row) for key,row in records.items()}
    assert len({v['cgroup'] for v in audits.values()})==12
    n10=records['prettier6604',10,'independent'];p0=n10['residency_queries']['P0']
    with (a.results/'prettier6604-N10-independent/host-memory.tsv').open() as stream:host_rows=list(csv.DictReader(stream,delimiter='\t'))
    next_reconciled=next((r for r in host_rows if int(r['monotonic_ns'])>=p0['end_monotonic_ns'] and int(r['file'])<=int(r['current'])),None)
    p0_note=f"Prettier N=10独立组P0为current {n10['cold_baseline']['current']/MIB:.3f} MiB、file {n10['cold_baseline']['file']/MIB:.3f} MiB"
    if next_reconciled:
        p0_note+=f"；{(int(next_reconciled['monotonic_ns'])-p0['end_monotonic_ns'])/1e9:.3f}秒后的周期记录file为{int(next_reconciled['file'])/MIB:.3f} MiB"
    p0_note+='。采样代码先读memory.stat再读memory.current，两次读取非原子；本批保存的序列显示分项没有立即反映总量下降。保留原值，不用该P0分项作精确组成或计算组外容量；后续核对已确认运行内核采用分级统计刷新；具体P0事件仍未跟踪。原始路径为 `'+n10['source_member']+'/host-file/memory.tsv`。'
    fits={}
    for metric in ('current_peak_bytes','execution_current_peak_bytes','execution_current_median_bytes'):
        fits[metric]={m:fit(COUNTS,[records['prettier6604',n,m][metric]/MIB for n in COUNTS]) for m in ('shared','independent')}
    changes=[]
    for task,n in [('prettier6604',n) for n in COUNTS]+[('c4',7)]:
        s=records[task,n,'shared'];i=records[task,n,'independent']
        changes.append(dict(task=task,count=n,theoretical_selected_files_fraction=(n-1)/n,
            **{k:savings(s[k]/MIB,i[k]/MIB) for k in ('current_peak_bytes','execution_current_peak_bytes','p1_resident_bytes')}))
    summary=dict(status='PASS',runs=12,fits=fits,savings=changes,savings_difference_unit='MiB; fractions dimensionless',audits={'/'.join(map(str,key)):value for key,value in audits.items()},
        scope='memory.current includes preparation, controller, private service, loop backing caches and guests; mincore only fixed initial lower files',
        replication='One run per N/mode; within-run execution medians and across-N descriptive medians are not repeat-run estimates')
    lines=['# 共享与独立副本的 host 部署密度对照','',
        '## 并发创建与实验范围','',
        '固定 standalone 创建路径及配置没有数值型并发数量上限；旧配置的 7 是实验副本数量，不是平台容量限制。本轮 N=10 的两组均在同一服务、同一 cgroup 内一次提交全部 10 个请求，实际成功创建才计入结果。源码依据见[执行记录](../../docs/records/HOST_DENSITY_IMPLEMENTATION.md)。',
        '', 'Prettier 每 guest 同一条 29 动作路径，N=1／2／4／7／10，共十次；C4 按原 9 层及 36 步做 N=7 两次。每组新服务、新 cgroup，默认回收，两端普通计数 0.1 秒；只查 P0／P1，无 PSS 或周期 mincore。',
        '', '## Prettier：总记账峰值与执行阶段峰值','',
        '[完整单表：按N排列的共享／独立全部指标](prettier-comparison.tsv)。单位均为 MiB。每格依次为“全流程峰值／执行阶段峰值／执行阶段采样中位数”。执行阶段是所有 guest 原动作及下载的最早动作调用开始到最晚下载返回的窗口，不含创建和最后官方检查；分项见下一表。', '',
        '| N | 共享 memory.current | 独立 memory.current |','|---:|---:|---:|']
    shared10=records['prettier6604',10,'shared'];independent10=records['prettier6604',10,'independent']
    insight=['## 结果概述','',
        '[离线分析补充](../host-density-offline-20261102/ANALYSIS.md)：C4主要额外等待已定位到host本地日志记录路径；原始数值不改，控制调用计时不再解释为纯RPC耗时。','',
        f"12组成功点已齐，逐组身份、冷起点、原任务结果与清理通过。Prettier N=10执行阶段current峰值：共享{shared10['execution_current_peak_bytes']/MIB:.3f} MiB，独立{independent10['execution_current_peak_bytes']/MIB:.3f} MiB，共享组少{100*(1-shared10['execution_current_peak_bytes']/independent10['execution_current_peak_bytes']):.2f}%；目标文件P1驻留少{100*(1-shared10['p1_resident_bytes']/independent10['p1_resident_bytes']):.2f}%，接近90%的文件副本理论值。",'',
        'current的差异大部分在创建guest前的复制准备阶段已经形成，因此它衡量本实验整组布局的内存成本，不是仅由业务读页产生的收益。C4峰值与采样中位数的排序不同，慢请求及分项短时不一致在异常节保留；不剔除数据。','']
    index=lines.index('## Prettier：总记账峰值与执行阶段峰值');lines[index:index]=insight
    for n in COUNTS:
        values=[' / '.join(f"{records['prettier6604',n,m][k]/MIB:.3f}" for k in ('current_peak_bytes','execution_current_peak_bytes','execution_current_median_bytes')) for m in ('shared','independent')]
        lines.append(f'| {n} | {values[0]} | {values[1]} |')
    lines+=['','分项取执行阶段 current 达峰的同一采样点，不将各分项各自峰值相加；shmem 已包含在 file 内。P1 是下层目标文件的驻留量，与 current 不拼加。','',
        '| N | 组别 | file | anon | kernel | shmem | mincore P1 |','|---:|---|---:|---:|---:|---:|---:|']
    for n in COUNTS:
        for mode in ('shared','independent'):
            row=records['prettier6604',n,mode];v=row['components_at_execution_current_peak_bytes']
            lines.append(f"| {n} | {mode} | "+' | '.join(f'{v[k]/MIB:.3f}' for k in ('file','anon','kernel','shmem'))+f" | {row['p1_resident_bytes']/MIB:.3f} |")
    lines+=['','### 准备基线已形成多少差异','',
        'P0=0只说明精确目标文件没有驻留页，不表示整组冷缓存。复制准备留下的其他文件及loop后备缓存仍可记在组内。以下只相减同一种current计数，用于区分准备成本和创建／业务阶段的增长；不与mincore拼成总物理量。','',
        '| N | 独立减共享：P0 current MiB | 独立减共享：执行峰值 MiB | 共享峰值相对P0增量 MiB | 独立峰值相对P0增量 MiB |','|---:|---:|---:|---:|---:|']
    for n in COUNTS:
        shared=records['prettier6604',n,'shared'];independent=records['prettier6604',n,'independent']
        lines.append(f"| {n} | {(independent['cold_baseline']['current']-shared['cold_baseline']['current'])/MIB:.3f} | {(independent['execution_current_peak_bytes']-shared['execution_current_peak_bytes'])/MIB:.3f} | {(shared['execution_current_peak_bytes']-shared['cold_baseline']['current'])/MIB:.3f} | {(independent['execution_current_peak_bytes']-independent['cold_baseline']['current'])/MIB:.3f} |")
    lines+=['','本轮current差异包含真实的准备缓存成本；其斜率适用于本实验整组存储布局，不能全部归因于业务实际读取的新文件页，或直接外推长期部署的最大guest数量。','',
        '## current 对沙箱数量的直线拟合','',
        '斜率是本实验整组布局下每增加一个沙箱的边际记账量，包含输入副本、loop 后备缓存等准备成本，不是 guest 独占 RAM。每个 N／组别只有一次运行，不能估计重复运行的不确定度；同时给执行阶段采样中位数，以及五个 N 观测值的描述性中位数，不剔除慢组。','',
        '| 拟合量 | 组别 | 斜率 MiB/guest | 截距 MiB | R² | 五个 N 的中位数 MiB |','|---|---|---:|---:|---:|---:|']
    names=dict(current_peak_bytes='全流程current峰值',execution_current_peak_bytes='执行阶段current峰值',execution_current_median_bytes='执行阶段current采样中位数')
    for metric,pair in fits.items():
        for mode,v in pair.items():
            lines.append(f"| {names[metric]} | {mode} | {v['slope_mib_per_guest']:.3f} | {v['intercept_mib']:.3f} | {v['r_squared']:.5f} | {v['observed_median_mib']:.3f} |")
    lines+=['','![current 与 N 的观测及拟合](density.png)','','## Prettier：独立减共享','',
        '正值表示共享组较少。理论 (N−1)/N 只描述相同文件工作集完全重复时的文件副本节省，不是整组 current 的预期节省；默认回收也会改变 P1，不能要求两个百分比相等。','',
        '| N | 全流程 current 差 MiB | 执行 current 差 MiB | 执行 current 少 % | mincore 差 MiB | mincore 少 % | 文件理论 % |','|---:|---:|---:|---:|---:|---:|---:|']
    for r in changes[:5]:
        full=r['current_peak_bytes'];cur=r['execution_current_peak_bytes'];cache=r['p1_resident_bytes']
        lines.append(f"| {r['count']} | {full['difference']:.3f} | {cur['difference']:.3f} | {100*cur['fraction']:.2f} | {cache['difference']:.3f} | {100*cache['fraction']:.2f} | {100*r['theoretical_selected_files_fraction']:.2f} |")
    lines+=['','## C4：N=7 单独对照','',
        'C4 使用自己的 9／63 个下层文件及原 36 步，逐实例核对预期退出码、9898 文件内容／结构往返、分片约束与语法检查，不套用 Prettier 官方 0 通过 2 失败。C4 不参与上述拟合或平均。','',
        '| 组别 | current 全流程峰值 | 执行峰值 | 执行中位数 | file | anon | kernel | shmem | mincore P1 |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for mode in ('shared','independent'):
        r=records['c4',7,mode];v=r['components_at_execution_current_peak_bytes'];values=[r[k]/MIB for k in ('current_peak_bytes','execution_current_peak_bytes','execution_current_median_bytes')]+[v[k]/MIB for k in ('file','anon','kernel','shmem')]+[r['p1_resident_bytes']/MIB]
        lines.append('| '+mode+' | '+' | '.join(f'{x:.3f}' for x in values)+' |')
    r=changes[-1];lines+=['',f"C4 独立减共享：执行阶段 current {r['execution_current_peak_bytes']['difference']:.3f} MiB，目标 mincore {r['p1_resident_bytes']['difference']:.3f} MiB。两任务仅并列比较，不混算。",'','## 文件记账对账与采集成本','',
        '下表 file 为 P1 查询前／后两次读取，mincore 在两者之间。若 mincore 明显大于 file，差额给出所选文件记账不足的下界；若 mincore 较小，也可能由其他准备文件的记账掩盖遗漏，不能据此证明逐页都在组内。未查询逐页记账归属，不虚构组外容量或把差额当节省。','',
        '| 任务／N／组别 | P1 mincore MiB | 同期 file 前／后 MiB | 记账不足下界 MiB | P0 墙钟／CPU ms | P1 墙钟／CPU ms |','|---|---:|---:|---:|---:|---:|']
    for key,r in records.items():
        q=r['residency_queries'];p1=q['P1'];label='/'.join(map(str,key))
        lines.append(f"| {label} | {r['p1_resident_bytes']/MIB:.3f} | {p1['counters_before']['file']/MIB:.3f} / {p1['counters_after']['file']/MIB:.3f} | {r['selected_file_shortfall_lower_bound_bytes']/MIB:.3f} | {q['P0']['elapsed_ns']/1e6:.3f} / {q['P0']['cpu_ns']/1e6:.3f} | {p1['elapsed_ns']/1e6:.3f} / {p1['cpu_ns']/1e6:.3f} |")
    lines+=['','## 冷起点基线、身份和普通采样成本','',
        '目标 P0 驻留全部为 0，但组内其他准备缓存未清理。这里给同期 current／file，用于判断准备基线与执行差值。普通采样 CPU 是采样器本身用时，不包括启动／停止调用或业务耗时；guest 列为该组全部采样器之和。“路径全部结束至P1结束”包含结束后的身份核对及驻留查询，并不只有mincore；查询后的最终身份复查另在协调程序整轮内。每组一批创建的时间戳及逐请求时间差保存在 summary.json 的 audits 中。','',
        '| 任务／N／组别 | 不同 inode 数 | P0 current／file MiB | host 点数／CPU s | guest 点数／CPU s | 路径全部结束至P1结束 s | 最低卷内余量 GiB |','|---|---:|---:|---:|---:|---:|---:|']
    for key,v in audits.items():
        label='/'.join(map(str,key))
        lines.append(f"| {label} | {v['selected_files']} | {v['P0_current_mib']:.3f} / {v['P0_file_mib']:.3f} | {v['host_samples']} / {v['host_cpu_seconds']:.3f} | {v['guest_samples']} / {v['guest_cpu_seconds']:.3f} | {v['paths_finished_to_P1_end_seconds']:.3f} | {v['minimum_volume_available_gib']:.3f} |")
    lines+=['','## 逐次计时、磁盘与曲线','',
        '工具累计为各 guest 工具持续时间之和，不能当作并发墙钟。并发窗口含原动作及下载；创建请求跨度单列；表中控制整轮指沙箱协调程序，从进入该程序到完成删除，含创建、查询、检查及清理；Prettier的输入复制／同步在该程序之前，C4的本地镜像转换和副本准备在该程序之内。整组current曲线覆盖更外层的准备与收尾，不能用协调程序时长代替整组采集时长。图中 host 为 cgroup 记账，guest Cached 属于 guest RAM 中的另一层，二者不表示同一份文件缓存。','',
        '| 任务／N／组别 | 工具累计 s | 并发动作及下载 s | 创建跨度 s | 控制整轮 s | 卷 GiB | 最低 host 可用 GiB | 图及原值 |','|---|---:|---:|---:|---:|---:|---:|---|']
    for key,r in records.items():
        folder=f'{key[0]}-N{key[1]}-{key[2]}';t=r['timings_ns'];label='/'.join(map(str,key))
        lines.append(f"| {label} | "+' | '.join(f'{t[k]/1e9:.3f}' for k in ('tool_sum','actions_and_downloads','creation_request_span','controller'))+f" | {r['volume_capacity_bytes']/2**30:.0f} | {r['host_minimum_available_bytes']/2**30:.3f} | [host]({folder}/host.png) · [guest]({folder}/guest.png) · [工具]({folder}/tools.png) · [原值]({folder}/summary.json) |")
    lines+=['','## 准备与收尾耗时单列','',
        '下表保留各组准备同步或转换，以及协调程序有记录的逐个删除成本；这些成本不混入工具累计。并发删除超时的C4尝试列在下一节，未计为成功点。','',
        '| 任务／N／组别 | Prettier准备同步 s | C4转换及副本准备 s | 协调程序逐个删除累计 s |','|---|---:|---:|---:|']
    for key,v in audits.items():
        sync=v['input_sync'];conversion=v['input_conversion']
        sync_text=f"{(sync['end_monotonic_ns']-sync['start_monotonic_ns'])/1e9:.3f}" if sync else '不适用'
        conversion_text=f"{(conversion['end_monotonic_ns']-conversion['start_monotonic_ns'])/1e9:.3f}" if conversion else '不适用'
        clean=[r for r in v['cleanup'].values() if isinstance(r,dict) and r.get('status')=='PASS']
        cleanup_text=f"{sum(r['end_monotonic_ns']-r['start_monotonic_ns'] for r in clean)/1e9:.3f}" if clean else '原guest程序删除，未单独汇总'
        lines.append('| '+'/'.join(map(str,key))+f' | {sync_text} | {conversion_text} | {cleanup_text} |')
    lines+=['','## 异常读数和慢请求：原值保留','',
        '部分P0记录的memory.stat.file短时大于memory.current。'+p0_note,
        '', 'C4共享组两个执行请求明显较慢，下面比较控制调用计时（含本地日志）与guest首末工具跨度。两种时钟的时间戳不直接相减，只比较各自的持续时间；该计时包含子进程返回后的本地日志写入；后续离线核对已将主要余量定位到host日志记录路径，具体阻塞系统调用仍未记录。原业务均通过，保留慢组。','',
        '| 组别／guest | 控制调用计时 s | guest工具跨度 s | 未分解余量 s | 原始路径 |','|---|---:|---:|---:|---|']
    for key,v in audits.items():
        if key[0]!='c4':continue
        for gap in v['replay_gaps']:
            if gap['unallocated_seconds']>1:
                lines.append(f"| {key[2]} / {gap['guest']} | {gap['host_replay_seconds']:.3f} | {gap['guest_tool_span_seconds']:.3f} | {gap['unallocated_seconds']:.3f} | `{gap['raw']}` |")
    for key,v in audits.items():
        for gap in v['replay_gaps']:
            if gap['managed_collector_minus_replay_seconds']>1:
                lines+=['',f"另有{key[2]} / {gap['guest']}的后台采集管理请求存续{gap['managed_collector_request_seconds']:.3f}秒，比其执行请求长{gap['managed_collector_minus_replay_seconds']:.3f}秒；它是管理请求／等待的持续时间，不是采样CPU成本。原始路径同为 `{gap['raw']}/events.json`，后续核对已定位主要空档在events.json写入返回之前，见离线分析。"]
    cs=records['c4',7,'shared'];ci=records['c4',7,'independent']
    lines+=['',f"C4共享组执行峰值少{100*(ci['execution_current_peak_bytes']-cs['execution_current_peak_bytes'])/ci['execution_current_peak_bytes']:.2f}%，但执行窗口采样中位数为{cs['execution_current_median_bytes']/MIB:.3f} MiB，高于独立组的{ci['execution_current_median_bytes']/MIB:.3f} MiB，且窗口分别为{cs['timings_ns']['actions_and_downloads']/1e9:.3f}／{ci['timings_ns']['actions_and_downloads']/1e9:.3f}秒。峰值与中位数的排序不同，不能把单次峰值差当成所有阶段都节省同样比例。",'',
        '## 失败与异常原始路径','']
    failures=[r for r in batch.get('attempts',[]) if r['result']['status']!='PASS']
    for r in failures:
        value=r['result'];detail=value.get('error','见原始记录')
        if value.get('storage'):
            storage=Path(value['storage']);native=json.loads((storage/'result.json').read_text())
            detail+='；停止阶段 '+str(native.get('failed_stage','未记录'))+'；'+str(native.get('error',''))
            children=list((storage/'density').glob('density-guest-*/result.json'))
            if children:
                outcomes=[json.loads(p.read_text()) for p in children]
                actions=sum(v.get('actions',0) for v in outcomes)
                detail+=f'；另有{len(children)}个guest、{actions}个已执行原动作'
                if all(v.get('roundtrip_validation')=='PASS' and v.get('syntax_validation')=='PASS' and not v.get('exit_differences') for v in outcomes):
                    detail+='，业务／往返／语法通过，删除请求超时使该组收尾失败'
        lines.append(f"- `{r['result_dir']}`：{detail}。原始记录保留，不混入成功点。")
    lines+=['',f"12个成功点合计{sum(r['count'] for r in records.values())}个guest、{sum(r['actions'] for r in records.values())}个原动作；失败尝试的已执行动作单列，准备阶段停止不计为任务完成。"]
    lines+=['','各 N 只按约定运行一遍，全部慢组保留。完整逐次原值与采样成本见各组 summary.json；部署容量还受 vCPU、内存余量及未测任务制约，直线拟合不等于可直接部署的最大数量。','']
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
    for ax,metric,title in zip(axes,('current_peak_bytes','execution_current_peak_bytes'),('Whole workflow','Actions + downloads')):
        for mode in ('shared','independent'):
            ys=[records['prettier6604',n,mode][metric]/MIB for n in COUNTS];v=fits[metric][mode]
            line=ax.plot(COUNTS,ys,'o',label=mode)[0];ax.plot(COUNTS,[v['intercept_mib']+v['slope_mib_per_guest']*n for n in COUNTS],color=line.get_color(),linestyle='--')
        ax.set(xlabel='Guest count N',ylabel='Group charge memory.current (MiB)',title=title);ax.grid(alpha=.2);ax.legend()
    for name in ('summary.json','REPORT.md','density.png','prettier-comparison.tsv'):
        if (a.results/name).exists():raise FileExistsError(a.results/name)
    comparison=[]
    for n in COUNTS:
        row=dict(N=n)
        for mode in ('shared','independent'):
            value=records['prettier6604',n,mode]
            for key in ('current_peak_bytes','execution_current_peak_bytes','execution_current_median_bytes','p1_resident_bytes'):
                row[mode+'_'+key.removesuffix('_bytes')+'_MiB']=value[key]/MIB
            for key,amount in value['components_at_execution_current_peak_bytes'].items():row[mode+'_'+key+'_at_execution_peak_MiB']=amount/MIB
        comparison.append(row)
    with (a.results/'prettier-comparison.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(comparison[0]),delimiter='\t');writer.writeheader();writer.writerows(comparison)
    fig.savefig(a.results/'density.png',dpi=150);plt.close(fig)
    (a.results/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(a.results/'REPORT.md').write_text('\n'.join(lines))
    print('status=PASS result_dir='+str(a.results))


if __name__=='__main__':main()
