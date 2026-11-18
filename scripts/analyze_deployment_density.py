"""Read saved density data: intercept identifiability, bounded clocks and budget endpoints."""
import argparse
from pathlib import Path

from collect_host_density import ROOT,MIB,read,rows,table,write
from report_host_density import COUNTS,fit
from report_host_pressure import SEGMENTS


def intercept_weights(xs):
    n=len(xs);sx=sum(xs);sxx=sum(x*x for x in xs);denominator=n*sxx-sx*sx
    if not denominator:raise ValueError('distinct N values required')
    return [(sxx-sx*x)/denominator for x in xs]


def offset_bounds(calls,boundaries,padding=0):
    low=max(c['start_monotonic_ns']-b[0] for c,b in zip(calls,boundaries))
    high=min(c['start_monotonic_ns']+c['duration_ns']-b[1] for c,b in zip(calls,boundaries))
    if low>high:raise ValueError('constant clock-offset bounds do not intersect')
    return low-padding,high+padding


def count_bounds(intervals,start_low,start_high,end_low,end_high):
    """Bounds for recorded increments, not physical page events or per-guest attribution."""
    if any(int(r['refault_delta'])<0 for r in intervals):raise ValueError('nonmonotone counter cannot give nonnegative overlap bounds')
    definite=sum(int(r['refault_delta']) for r in intervals if int(r['start_monotonic_ns'])>=start_high and int(r['end_monotonic_ns'])<=end_low)
    possible=sum(int(r['refault_delta']) for r in intervals if int(r['end_monotonic_ns'])>start_low and int(r['start_monotonic_ns'])<end_high)
    return definite,possible


def budget_table(runs,budgets=(5,10,20,50)):
    a,b,c,d=runs;shared=(c['actions_downloads_seconds']/a['actions_downloads_seconds']-1)*100
    independent=(d['actions_downloads_seconds']/b['actions_downloads_seconds']-1)*100
    baseline=b['execution_current_peak_mib']-a['execution_current_peak_mib'];pressured=d['execution_current_peak_mib']-c['execution_current_peak_mib']
    result=[]
    for budget in budgets:
        feasible=shared<=budget and independent<=budget
        result.append(dict(budget_percent=budget,shared_pressure_change_percent=shared,independent_pressure_change_percent=independent,
            shared_endpoint_feasible=shared<=budget,independent_endpoint_feasible=independent<=budget,
            pressure_pair_feasible=feasible,observed_feasible_gap_reduction_mib=baseline-pressured if feasible else None,
            observed_feasible_retained_gap_mib=pressured if feasible else None,baseline_gap_mib=baseline,
            maximum_acceptable_reduction='UNKNOWN; no intermediate or higher-pressure points'))
    return result


def analyze(output):
    output.mkdir(parents=True,exist_ok=False)
    old=ROOT/'results/host-density-20261102';records={};fits={};component_fits={};series=[]
    for mode in ('shared','independent'):
        data=[read(old/f'prettier6604-N{n}-{mode}'/'summary.json') for n in COUNTS];records[mode]=data
        fits[mode]={name:fit(COUNTS,[fn(r)/MIB for r in data]) for name,fn in (
            ('execution_peak',lambda r:r['execution_current_peak_bytes']),('P0',lambda r:r['cold_baseline']['current']),
            ('peak_minus_P0',lambda r:r['execution_current_peak_bytes']-r['cold_baseline']['current']))}
        component_fits[mode]={key:fit(COUNTS,[r['components_at_execution_current_peak_bytes'][key]/MIB for r in data]) for key in ('file','anon','kernel')}
        residual=[r['execution_current_peak_bytes']-sum(r['components_at_execution_current_peak_bytes'][k] for k in ('file','anon','kernel')) for r in data]
        component_fits[mode]['reconciliation_residual']=fit(COUNTS,[x/MIB for x in residual])
        for n,r in zip(COUNTS,data):series.append(dict(mode=mode,N=n,execution_peak_mib=r['execution_current_peak_bytes']/MIB,P0_current_mib=r['cold_baseline']['current']/MIB,
            file_at_peak_mib=r['components_at_execution_current_peak_bytes']['file']/MIB,anon_at_peak_mib=r['components_at_execution_current_peak_bytes']['anon']/MIB,
            kernel_at_peak_mib=r['components_at_execution_current_peak_bytes']['kernel']/MIB,P0_selected_file_mincore_mib=r['p0_resident_bytes']/MIB,
            P1_selected_file_mincore_mib=r['p1_resident_bytes']/MIB,selected_file_count=r['selected_file_count'],source=str((old/f'prettier6604-N{n}-{mode}'/'summary.json').relative_to(ROOT))))
    old_fits=read(old/'summary.json')['fits']['execution_current_peak_bytes']
    for mode in fits:
        assert fits[mode]['execution_peak']==old_fits[mode]
        assert abs(fits[mode]['P0']['intercept_mib']+fits[mode]['peak_minus_P0']['intercept_mib']-fits[mode]['execution_peak']['intercept_mib'])<1e-8
    intercept_delta=fits['shared']['execution_peak']['intercept_mib']-fits['independent']['execution_peak']['intercept_mib']
    weights=[]
    for n,w,a,b in zip(COUNTS,intercept_weights(COUNTS),records['shared'],records['independent']):
        delta=(a['execution_current_peak_bytes']-b['execution_current_peak_bytes'])/MIB
        weights.append(dict(N=n,shared_minus_independent_mib=delta,intercept_weight=w,contribution_to_intercept_difference_mib=w*delta))
    assert abs(sum(r['contribution_to_intercept_difference_mib'] for r in weights)-intercept_delta)<1e-8
    decomposition=[]
    for key in ('P0','peak_minus_P0'):
        a=fits['shared'][key]['intercept_mib'];b=fits['independent'][key]['intercept_mib'];decomposition.append(dict(quantity=key,shared_intercept_mib=a,independent_intercept_mib=b,difference_mib=a-b))
    components=[]
    for key in component_fits['shared']:
        a=component_fits['shared'][key]['intercept_mib'];b=component_fits['independent'][key]['intercept_mib'];components.append(dict(quantity=key,shared_intercept_mib=a,independent_intercept_mib=b,difference_mib=a-b))
    missing=[dict(object='服务及控制程序',status='NOT_IDENTIFIABLE',available='全组current、anon和kernel，未单独保存服务峰值快照',missing_fields=['同一峰值时刻服务、控制程序与guest各自的anon／kernel记账量']),
        dict(object='共享镜像下层文件',status='PARTLY_OBSERVED',available='指定文件P0驻留为0及P1驻留；P1不是执行current峰值时刻',missing_fields=['峰值时刻下层文件驻留量及实际记账cgroup']),
        dict(object='loop后备文件',status='NOT_IDENTIFIABLE',available='80 GiB卷容量与路径；容量不等于驻留',missing_fields=['峰值时刻loop后备文件驻留量及实际记账cgroup']),
        dict(object='其他非任务文件',status='NOT_IDENTIFIABLE',available='file聚合值包含输入、副本、依赖／日志及后备缓存',missing_fields=['完整且互不重叠的设备号:inode分类','峰值时刻其余文件的驻留与记账量'])]
    pressure_source=ROOT/'results/host-density-pressure-20261102';pressure=read(pressure_source/'pressure-summary.json');runs=pressure['runs']
    delay_source=ROOT/'results/pressure-delay-offline-20261102';delay=read(delay_source/'summary.json')
    intervals=[r for r in rows(delay_source/'refault-intervals.tsv') if r['condition']=='D']
    d=runs[3];storage=ROOT/d['source_storage'];task=read(storage/'density/result.json');published=read(pressure_source/'D-prettier6604-N10-independent/summary.json');start,finish=published['execution_window_monotonic_ns']
    action_rows=[];clock_rows=[];critical_calls=None
    step_durations={(r['condition'],r['guest'],r['segment'],r['step']):int(r['duration_ns']) for r in rows(delay_source/'guest-steps.tsv')}
    for child in task['results']:
        raw=storage/'density'/child['raw'];events=read(raw/'events.json');calls=sorted([e for e in events if e['stage'].startswith('controller-call-')],key=lambda e:e['stage'])[::2]
        boundaries=[];by_segment={}
        for segment in SEGMENTS:
            points=rows(raw/'rollouts/backbone'/segment/'guest-memory.tsv');before=[int(r['timestamp_ns']) for r in points if r['phase']=='before'];after=[int(r['timestamp_ns']) for r in points if r['phase']=='after']
            boundaries.append((before[0],after[-1]));by_segment[segment]=points
        monitor=read(raw/'runtime-monitor.json')['sessions'][0];samples=rows(raw/monitor['raw_directory']/'guest-memory.tsv')
        dual=[int(r['timestamp_ns'])-int(r['monotonic_ns']) for r in samples];padding=max(dual)-min(dual)
        low,high=offset_bounds(calls,boundaries,padding)
        clock_rows.append(dict(guest=child['role'],host_minus_guest_wall_low_ns=low,host_minus_guest_wall_high_ns=high,width_seconds=(high-low)/1e9,
            observed_guest_dual_clock_offset_range_ns=padding,model='Constant offset over this short replay, padded by observed guest dual-clock variation; no midpoint treated as exact'))
        for segment,points in by_segment.items():
            steps=sorted({r['step'] for r in points if r['phase']=='before'})
            for step in steps:
                before=int(next(r['timestamp_ns'] for r in points if r['phase']=='before' and r['step']==step));after=int(next(r['timestamp_ns'] for r in points if r['phase']=='after' and r['step']==step))
                a,b,c,e=before+low,before+high,after+low,after+high
                inside=count_bounds(intervals,a,b,c,e);prior=count_bounds(intervals,a-10**9,b-10**9,a,b)
                action_rows.append(dict(guest=child['role'],segment=segment,step=step,start_low_seconds=(a-start)/1e9,start_high_seconds=(b-start)/1e9,
                    end_low_seconds=(c-start)/1e9,end_high_seconds=(e-start)/1e9,tool_seconds=step_durations['D',child['role'],segment,step]/1e9,
                    recorded_refault_overlap_min=inside[0],recorded_refault_overlap_max=inside[1],recorded_refault_preceding_1s_min=prior[0],recorded_refault_preceding_1s_max=prior[1]))
        if child['role']=='guest-07':critical_calls=calls
    assert len(action_rows)==290 and critical_calls is not None
    bounds=[start,*[c['start_monotonic_ns'] for c in critical_calls],finish];labels=['START_OFFSET',*SEGMENTS];partitions=[]
    critical_contributions={r['segment']:r['difference_of_differences_seconds'] for r in delay['critical_contributions']}
    assessments=['起点偏移缩短，未记录refault增长', '小幅变慢，仅56个计数；不足以对应延迟尖峰',
        '变慢但本分区无计数增长，不吻合', '小幅变慢与少量计数共现，未建立先后关系',
        '主要延迟在此；有计数共现，但最大计数峰尚未出现', '计数最多却不是主要新增延迟段，不呈比例对应']
    for i,label in enumerate(labels):
        left,right=bounds[i:i+2];assigned=[r for r in intervals if left<int(r['end_monotonic_ns'])<=right]
        partitions.append(dict(partition=label,start_seconds=(left-start)/1e9,end_seconds=(right-start)/1e9,span_seconds=(right-left)/1e9,
            critical_window_extra_seconds=critical_contributions[label],recorded_refault_delta=sum(int(r['refault_delta']) for r in assigned),
            increments_in_boundary_crossing_intervals=sum(int(r['refault_delta']) for r in assigned if int(r['start_monotonic_ns'])<left),
            temporal_assessment=assessments[i]))
    assert sum(r['recorded_refault_delta'] for r in partitions)==d['workingset_refault_file']
    peak=max(intervals,key=lambda r:int(r['refault_delta']));peak_start=(int(peak['start_monotonic_ns'])-start)/1e9;peak_end=(int(peak['end_monotonic_ns'])-start)/1e9
    jest_keys={(r['segment'],r['step']) for r in delay['jest_summary']};jest_actions=[r for r in action_rows if (r['segment'],r['step']) in jest_keys]
    main_jest=[r for r in jest_actions if (r['segment'],r['step'])==('core_verify','005')]
    budgets=budget_table(runs)
    for name,data in [('old-N-series',series),('intercept-weights',weights),('intercept-stage-decomposition',decomposition),('intercept-stat-decomposition',components),
        ('clock-bounds',clock_rows),('D-action-alignment',action_rows),('D-Jest-alignment',jest_actions),('D-partition-alignment',partitions),('latency-budgets',budgets)]:table(output/(name+'.tsv'),data)
    result=dict(status='PASS_READ_ONLY_ANALYSIS',scope='New analysis files only; no sandbox, service, page query or sampling',old_curve_fits=fits,
        intercept_difference_mib=intercept_delta,intercept_stage_decomposition=decomposition,intercept_stat_decomposition=components,missing_object_fields=missing,
        N1_actual_peak_difference_mib=weights[0]['shared_minus_independent_mib'],N1_P0_difference_mib=(records['shared'][0]['cold_baseline']['current']-records['independent'][0]['cold_baseline']['current'])/MIB,
        clock_bounds=clock_rows,partitions=partitions,jest_summary=delay['jest_summary'],critical_guest_Jest=[r for r in jest_actions if r['guest']=='guest-07'],refault_record_peak=dict(start_seconds=peak_start,end_seconds=peak_end,increment=int(peak['refault_delta']),
            main_Jest_guests_definitely_finished_before_peak=sum(r['end_high_seconds']<peak_start for r in main_jest),main_Jest_latest_end_upper_seconds=max(r['end_high_seconds'] for r in main_jest)),
        budgets=budgets,pressure_point=runs,source_paths=[str(p.relative_to(ROOT)) for p in (old/'summary.json',pressure_source/'pressure-summary.json',delay_source/'summary.json')],
        outstanding_runs=dict(priority_1='N=4 and N=7, shared/independent once each: 4 runs',priority_2='N=10 pressure shared/independent twice more: 4 runs',authorization='Await user confirmation after this analysis delivery'),
        protocol_note='Original allocator targets 20% MemAvailable before guest creation, then holds; 18.59%/18.91% are N=10 execution-midpoint observations, not its startup target. Preserve this protocol for comparability, report actual N-dependent pressure.')
    write(output/'summary.json',result);figures(series,fits,jest_actions,intervals,output);report(result,output)
    print('status=PASS_READ_ONLY_ANALYSIS result_dir='+str(output),flush=True)
    return result


def figures(series,fits,actions,intervals,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(10,5),constrained_layout=True)
    for mode,color in (('shared','#2563eb'),('independent','#d97706')):
        points=[r for r in series if r['mode']==mode];fit_value=fits[mode]['execution_peak'];a=fit_value['intercept_mib'];b=fit_value['slope_mib_per_guest']
        ax.scatter([r['N'] for r in points],[r['execution_peak_mib'] for r in points],color=color,label=mode+' measured')
        ax.plot([1,10],[a+b,a+10*b],color=color);ax.plot([0,1],[a,a+b],color=color,linestyle=':');ax.scatter([0],[a],color=color,marker='x')
    ax.axvspan(0,1,color='gray',alpha=.1,label='Unmeasured extrapolation');ax.set(xlabel='Sandbox count N',ylabel='Group charge memory.current execution peak (MiB)',title='Fitted intercept at N=0 is not an observed fixed resource cost');ax.grid(alpha=.2);ax.legend();fig.savefig(output/'intercept-extrapolation.png',dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(12,8),sharex=True,constrained_layout=True,gridspec_kw={'height_ratios':[2,1]})
    keys=[('core_verify','005'),('backbone_finish','002'),('backbone_finish','005')]
    for k,color in zip(keys,('#ef4444','#f59e0b','#10b981')):
        for j,guest in enumerate(sorted({r['guest'] for r in actions})):
            r=next(r for r in actions if r['guest']==guest and (r['segment'],r['step'])==k)
            axes[0].barh(j,r['end_low_seconds']-r['start_high_seconds'],left=r['start_high_seconds'],color=color,label='/'.join(k) if j==0 else None)
            axes[0].barh(j,r['start_high_seconds']-r['start_low_seconds'],left=r['start_low_seconds'],color=color,alpha=.25)
            axes[0].barh(j,r['end_high_seconds']-r['end_low_seconds'],left=r['end_low_seconds'],color=color,alpha=.25)
    axes[0].set(yticks=range(10),yticklabels=sorted({r['guest'] for r in actions}),ylabel='D guest action-boundary windows',title='Three Jest actions; pale edges show bounded clock-offset uncertainty');axes[0].invert_yaxis();axes[0].legend(ncol=3,loc='lower left',bbox_to_anchor=(0,1.04))
    widths=[(int(r['end_monotonic_ns'])-int(r['start_monotonic_ns']))/1e9 for r in intervals]
    axes[1].bar([float(r['seconds_from_window_start'])-w for r,w in zip(intervals,widths)],[int(r['refault_delta']) for r in intervals],width=widths,align='edge',color='#dc2626');axes[1].set(xlabel='Host monotonic seconds from D actions/downloads start',ylabel='workingset_refault_file increment\n(per sampled interval)')
    for ax in axes:ax.grid(alpha=.2)
    fig.savefig(output/'D-Jest-refault-bounds.png',dpi=140);plt.close(fig)


def report(v,output):
    lines=['# 从现有数据到部署密度：只读分析','',
        '本次只分析保存结果，不启动沙箱、不查询页驻留或重新采样。旧N曲线、四条件新对照及新增分析分开；历史数值不改写。八次补跑尚未开始，等待确认。','',
        '## 1. 多出的截距是回归量，不是已测得的固定物理对象','',
        f"旧曲线共享截距{v['old_curve_fits']['shared']['execution_peak']['intercept_mib']:.6f} MiB、独立截距{v['old_curve_fits']['independent']['execution_peak']['intercept_mib']:.6f} MiB，精确差为{v['intercept_difference_mib']:.6f} MiB（约3730 MiB）。N=0没有运行，不能把这项直接解释成共享服务多用这么多内存。",'',
        f"反例是实际N=1：P0共享减独立只有{v['N1_P0_difference_mib']:.6f} MiB，执行峰值差{v['N1_actual_peak_difference_mib']:.6f} MiB，并没有观察到3730 MiB的额外固定项。OLS截距是各N点的加权和，N=10权重为负；[逐点权重与贡献](intercept-weights.tsv)精确复现差值。高R²不把外推量变成对象归属证据。",'',
        '### 可以准确复算的两种数学拆分','',
        '峰值=P0 current+(峰值−P0)。对两项分别使用原五个N、同一OLS拟合，截距可相加：','',
        '| 回归量 | 共享截距 MiB | 独立截距 MiB | 差 MiB |','| --- | ---: | ---: | ---: |']
    for r in v['intercept_stage_decomposition']:lines.append(f"| {r['quantity']} | {r['shared_intercept_mib']:.6f} | {r['independent_intercept_mib']:.6f} | {r['difference_mib']:.6f} |")
    lines+=['','下面按执行current峰值同次读取的聚合分项拟合。它是统计分项的回归拆分，不是按服务或文件对象拆分：','','| 分项回归量 | 共享截距 MiB | 独立截距 MiB | 差 MiB |','| --- | ---: | ---: | ---: |']
    for r in v['intercept_stat_decomposition']:lines.append(f"| {r['quantity']} | {r['shared_intercept_mib']:.6f} | {r['independent_intercept_mib']:.6f} | {r['difference_mib']:.6f} |")
    lines+=['','reconciliation_residual是current减file／anon／kernel的对账余量，不是另一类物理内存；分项不构成原子快照。以上数字是原数据的数学投影，不能把file截距差改叫loop成本。','','### 服务、下层文件、loop与其他文件还缺什么','','| 对象 | 已保存 | 缺失字段／证据 |','| --- | --- | --- |']
    for r in v['missing_object_fields']:lines.append(f"| {r['object']} | {r['available']} | {'、'.join(r['missing_fields'])} |")
    lines+=['','因此无法从现有summary将3730 MiB逐项分给这四类对象。精确目标文件P0驻留为0已知，P1只在任务结束查询；80 GiB卷大小不是驻留量。需要同一时点、按设备号:inode去重且确认记账归属的互斥分类，不能把进程映射、file记账和mincore重叠相加。本次不为填这个表新增采样。','','![截距的外推性质](intercept-extrapolation.png)','',
        '## 2. D组refault与慢动作：时间邻近不等于因果','','[已有分段分析](../pressure-delay-offline-20261102/REPORT.md)的91.00%结论保持。这里进一步利用每guest的五次host执行调用和guest动作边界时间，为guest墙钟→host单调时钟建立offset区间；不假设两边墙钟相等，也不把区间中点当精确时刻。保存的guest双时钟差变化用来扩大区间。','','[10个guest的时钟区间](clock-bounds.tsv)、[D的290步边界对齐](D-action-alignment.tsv)、[三次Jest的30个窗口](D-Jest-alignment.tsv)保留起止上下界。refault重叠计数也给上下界：它仍是全组计数，不是该guest的归属；逐行不能求和。动作开始前1秒仅作邻近检查窗，不是延迟预算或因果判据。','','对齐模型在这段短运行内假设host与guest时钟只差固定偏移，以每段host调用的开始／结束包住guest首次before和末次after求交集，再用已观测双时钟差变化扩大区间；区间宽约0.089～0.181秒。它不是精确同步；动作边界跨度包含边界记录，不等同于工具计时。','','### 按D最后完成的guest-07路径分区','','每个计数增量按采样结束点落入唯一分区，合计仍为63715；跨分区边界的区间增量另列。其他guest同时可能处于不同分段，不能视为全组唯一阶段。窗口额外增量定义为(D−C)−(B−A)；各条件使用各自最后完成guest的同名分段，含该段执行、下载及到下一段的间隙，合计4.203187秒，不是全guest工具累计。','','| 分区 | 相对D窗口秒 | 该分区跨度秒 | 窗口额外增量秒 | 全组refault记录增量 | 其中跨边界区间增量 | 是否吻合 |','| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in v['partitions']:lines.append(f"| {r['partition']} | {r['start_seconds']:.6f}～{r['end_seconds']:.6f} | {r['span_seconds']:.6f} | {r['critical_window_extra_seconds']:+.6f} | {r['recorded_refault_delta']} | {r['increments_in_boundary_crossing_intervals']} | {r['temporal_assessment']} |")
    lines+=['','### 三次Jest的实际落点','','下表选D最后完成的guest-07展示动作起止范围，耗时是该guest工具计时；最后一列是十guest该动作的工具累计额外增量(D−C)−(B−A)。全30个动作窗口见TSV，不能将并发的refault重叠计数相加。','','| 动作 | 开始范围秒 | 结束范围秒 | 工具耗时秒 | 开始前1秒全组refault下／上界 | 动作内全组refault下／上界 | 十guest工具额外增量秒 |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r,j in zip(v['critical_guest_Jest'],v['jest_summary']):
        assert (r['segment'],r['step'])==(j['segment'],j['step'])
        lines.append(f"| {r['segment']}/{r['step']} | {r['start_low_seconds']:.6f}～{r['start_high_seconds']:.6f} | {r['end_low_seconds']:.6f}～{r['end_high_seconds']:.6f} | {r['tool_seconds']:.6f} | {r['recorded_refault_preceding_1s_min']}/{r['recorded_refault_preceding_1s_max']} | {r['recorded_refault_overlap_min']}/{r['recorded_refault_overlap_max']} | {j['extra_seconds']:.6f} |")
    lines+=['','三次合计32.560456秒，占35.780726秒工具累计额外增量的91.00%，与已有离线定位一致。core_verify/005是主要慢动作；backbone_finish/002与最大计数峰重叠，只能确认部分测试与计数增长在时间上相伴。']
    peak=v['refault_record_peak'];lines+=['',f"最大单个采样增量{peak['increment']}记录在{peak['start_seconds']:.6f}～{peak['end_seconds']:.6f}秒。主要慢动作core_verify/005的最晚结束上界为{peak['main_Jest_latest_end_upper_seconds']:.6f}秒，{peak['main_Jest_guests_definitely_finished_before_peak']}/10个guest确定在该计数峰前结束。因此没有建立‘refault记录峰先发生、主要慢动作紧随其后’的关系。",'',
        '后段新增测试与计数增长可同段共现；但memory.stat汇总可能滞后，记录时刻不是页事件时刻，动作耗时也不是内部停顿轨迹。现有数据支持压力下缓存重入增加且Jest动作变慢，不能确定重读、CPU或调度各贡献多少。窗口后89270个增量另含88962个与官方检查重叠，不计入原29动作。','','![带时钟边界的Jest与refault](D-Jest-refault-bounds.png)','',
        '## 3. 多档延迟预算：只判断已测端点，不插值','','本表仅用新四条件A／B／C／D，不混入旧N曲线。预算分母是每种组别自己的无压力动作窗口：共享看C/A−1，独立看D/B−1。它不同于历史5.866秒对应的D−C或29.30%的D/C−1。','','| 组别 | 无压力动作及下载窗口秒 | 有压力窗口秒 | 变化秒 | 变化比例 |','| --- | ---: | ---: | ---: | ---: |']
    for base,pressed in ((v['pressure_point'][0],v['pressure_point'][2]),(v['pressure_point'][1],v['pressure_point'][3])):
        a=base['actions_downloads_seconds'];b=pressed['actions_downloads_seconds']
        lines.append(f"| {base['mode']} | {a:.6f} | {b:.6f} | {b-a:+.6f} | {(b/a-1)*100:+.2f}% |")
    lines+=['','下面“仍有的记账差”指同压力条件下独立减共享的执行current峰值差，不是可直接分配的空闲内存。两组峰值发生时刻也不要求相同。','','| 允许相对无压力增加 | 共享压力端点 | 独立压力端点 | 已观测且满足预算的成对记账差收窄 MiB | 对应仍有的记账差 MiB |','| --- | --- | --- | --- | --- |']
    for r in v['budgets']:
        shared=f"{r['shared_pressure_change_percent']:+.2f}%／{'符合' if r['shared_endpoint_feasible'] else '不符合'}";independent=f"{r['independent_pressure_change_percent']:+.2f}%／{'符合' if r['independent_endpoint_feasible'] else '不符合'}"
        reduction=f"已测可行{r['observed_feasible_gap_reduction_mib']:.3f}；上限未知" if r['pressure_pair_feasible'] else '未知：压力成对端点不满足；只有基线收窄0已测'
        retained=f"压力端点{r['observed_feasible_retained_gap_mib']:.3f}（43.50%）" if r['pressure_pair_feasible'] else f"仅基线{r['baseline_gap_mib']:.3f}已测；中间点未知"
        lines.append(f"| +{r['budget_percent']}% | {shared} | {independent} | {reduction} | {retained} |")
    lines+=['','+20%／+50%仅证明同一个已测压力端点可行，不说明较宽预算能换更多内存。+5%／+10%不能按0.190秒/GiB倒算部分回收；缺少中间压力点。共享端点单独符合预算时，也不能拿压力共享与无压力独立相减来冒充同一内存条件的共享收益。单次共享窗口略短不是加压会提速的稳定结论。这里的“符合”只指一次已测值，不是重复测量后的延迟保证。','','该端点为N=10、任务中点可用率18.59%／18.91%；记账差收窄22604.953 MiB，保留17405.098 MiB，指定文件mincore差仍有2246.445 MiB（保留93.45%）。0.190404秒/GiB只描述这一次对照，不作预算内插或通用边际换算，不与current相加。','','## 确认后再执行的最小运行','','优先N=4／7共享和独立各一次，共4次；随后N=10有压力共享／独立各再两次，共4次。新结果与原数据分节，N=10的三次重复给极差和样本标准差，43.50%与5.866秒原值保留。分析交付后等待确认，当前没有启动。','','三次N=10按原C／D及两对新增共享／独立逐对列出；用同一原A／B无压力差作参照，报告保留率及窗口差的极差、样本标准差。该范围没有包含无压力基线本身的不确定度，三次重复也不作为延迟上限保证。不剔除慢组，不额外增加运行次数。','','需确认协议含义：原分配器在创建guest前以MemAvailable=MemTotal的20%为目标，之后固定持有；18.59%／18.91%是N=10任务中点实测值。为保持同协议并复用旧N=10点，推荐仍用创建前20%目标，逐N报告实际运行可用率。若改成创建前18.6%，那是新配置，旧N=10不能直接混合拟合；不能静默把两者视为相同。','','只有压力N=10一点时不拟合压力斜率。补齐N=4／7后先给三点拟合与残差；重复不确定度另列，不与旧无压力五点或其他任务混算。X必须说明是执行阶段组current预算还是包含准备峰值的预算，Y按本表各档分别判断。固定对象归属缺口仍保留，不用拟合截距伪装实测对象容量。','','## 原始证据与复现','','旧N曲线各组summary来源见[逐N表](old-N-series.tsv)的source列；原拟合为[旧摘要](../host-density-20261102/summary.json)，四条件为[压力摘要](../host-density-pressure-20261102/pressure-summary.json)。D组原始目录为`.artifacts/storage-isolated-service/20261102T123512Z-3639855/`，对齐读取其`density/density-guest-XX/events.json`、`rollouts/backbone/*/guest-memory.tsv`、`runtime-monitor.json`指向的双时钟采样，未读负载文件。计数来源为D发布目录`host-memory.tsv`经既有离线报告生成的`refault-intervals.tsv`。','','复现到一个尚不存在的新目录：','','```bash','python3 -B scripts/analyze_deployment_density.py --output results/新的离线分析目录','```','','四项纯算术回归测试覆盖OLS截距权重、时钟区间相交、计数重叠上下界及预算不插值，不接触服务或设备。','']
    (output/'REPORT.md').write_text('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();analyze(args.output.resolve())
