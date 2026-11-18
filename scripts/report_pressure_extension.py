"""Saved N=4/7 pressure curve and N=10 repeats; original data remain separate."""
import argparse
from pathlib import Path
from statistics import mean,median,stdev

from collect_host_density import ROOT,MIB,read,rows,table,write
from report_host_density import fit
from report_host_pressure import summarize_run


def dispersion(values):
    if len(values)<2:raise ValueError('sample standard deviation requires repeats')
    return dict(count=len(values),minimum=min(values),maximum=max(values),range=max(values)-min(values),
        mean=mean(values),median=median(values),sample_stddev=stdev(values))


def repeat_pairs(old,new):
    by={r['condition']:r for r in new};a,b,c,d=old
    baseline=b['execution_current_peak_mib']-a['execution_current_peak_mib']
    baseline_mincore=b['p1_mincore_mib']-a['p1_mincore_mib']
    result=[]
    for index,(s,i) in enumerate(((c,d),(by['I'],by['J']),(by['L'],by['K'])),1):
        gap=i['execution_current_peak_mib']-s['execution_current_peak_mib']
        mine=i['p1_mincore_mib']-s['p1_mincore_mib']
        delay=i['actions_downloads_seconds']-s['actions_downloads_seconds']
        result.append(dict(repeat=index,source='original C/D' if index==1 else 'new',shared=s['condition'],independent=i['condition'],
            current_gap_mib=gap,current_retention_percent=100*gap/baseline if baseline else None,
            mincore_gap_mib=mine,mincore_retention_percent=100*mine/baseline_mincore if baseline_mincore else None,
            actions_window_gap_seconds=delay,actions_window_gap_percent=100*delay/s['actions_downloads_seconds'],
            extra_window_gap_vs_original_controls_seconds=delay-(b['actions_downloads_seconds']-a['actions_downloads_seconds'])))
    return result


def report(batch_path,output):
    batch=read(batch_path/'result.json')
    assert batch['status']=='PASS' and batch['source_restored_stopped']
    assert batch['experiment']=='prettier-density-pressure-extension-v1' and len(batch['runs'])==8
    assert [r['condition'] for r in batch['runs']]==list('EFGHIJKL')
    original=read(ROOT/'results/host-density-pressure-20261102/pressure-summary.json')
    old=original['runs'];runs=[];guests=[];audits=[];exceptions=[]
    for member in batch['runs']:
        folder=output/f"{member['condition']}-prettier6604-N{member['count']}-{member['mode']}"
        r,peers,audit,issues=summarize_run(member,folder)
        r.update(count=member['count'],dataset='new pressure extension');runs.append(r);guests+=peers;audits.append(audit);exceptions+=issues
    assert len({r['cgroup'] for r in runs})==8
    effective=all(r['effective_pressure'] for r in runs) and all(r['effective_pressure'] for r in old[2:])
    curve=[]
    for mode in ('shared','independent'):
        curve.extend(sorted([r for r in runs[:4] if r['mode']==mode]+[dict(next(r for r in old[2:] if r['mode']==mode),count=10,dataset='original C/D')],key=lambda r:r['count']))
    fits={m:fit([4,7,10],[r['execution_current_peak_mib'] for r in curve if r['mode']==m]) for m in ('shared','independent')} if effective else None
    nonmonotone=[]
    for mode in ('shared','independent'):
        points=[r for r in curve if r['mode']==mode]
        for a,b in zip(points,points[1:]):
            if b['execution_current_peak_mib']<a['execution_current_peak_mib']:
                nonmonotone.append(dict(mode=mode,lower_N=a['count'],higher_N=b['count'],
                    current_change_mib=b['execution_current_peak_mib']-a['execution_current_peak_mib'],
                    P0_lower_N_current_mib=a['cold_baseline_current_mib'],P0_higher_N_current_mib=b['cold_baseline_current_mib'],
                    lower_N_peak_file_mib=a['peak_components_mib']['file'],higher_N_peak_file_mib=b['peak_components_mib']['file'],
                    lower_N_peak_anon_mib=a['peak_components_mib']['anon'],higher_N_peak_anon_mib=b['peak_components_mib']['anon'],
                    lower_N_raw=a['source_storage'],higher_N_raw=b['source_storage']))
    residuals=[dict(mode=r['mode'],N=r['count'],source=r['dataset'],execution_current_peak_mib=r['execution_current_peak_mib'],
        residual_mib=r['execution_current_peak_mib']-fits[r['mode']]['intercept_mib']-fits[r['mode']]['slope_mib_per_guest']*r['count']) for r in curve] if fits else []
    fields=('current_peak_mib','execution_current_peak_mib','p1_mincore_mib','actions_downloads_seconds','tool_sum_seconds')
    repeats=[dict(r,count=10,dataset='original C/D') for r in old[2:]]+runs[4:]
    spread=[dict(mode=m,metric=f,**dispersion([r[f] for r in repeats if r['mode']==m])) for m in ('shared','independent') for f in fields]
    pairs=repeat_pairs(old,runs)
    pair_spread=[dict(metric=f,**dispersion([r[f] for r in pairs])) for f in ('current_retention_percent','mincore_retention_percent','actions_window_gap_seconds','extra_window_gap_vs_original_controls_seconds') if all(r[f] is not None for r in pairs)]
    def flatten(r):
        return dict(condition=r['condition'],N=r['count'],mode=r['mode'],source=r['dataset'],
            **{f:r[f] for f in fields},execution_current_median_mib=r['execution_current_median_mib'],
            **{k+'_mib':v for k,v in r['peak_components_mib'].items()},
            refault=r['workingset_refault_file'],activate=r['workingset_activate_file'],
            creation_span_seconds=r['creation_span_seconds'],controller_seconds=r['controller_seconds'],
            middle_available_percent=100*int(r['pressure_points']['middle']['MemAvailable'])/int(r['pressure_points']['middle']['MemTotal']),
            effective_pressure=r['effective_pressure'],minimum_disk_available_gib=r['minimum_disk_available_gib'],raw=r['source_storage'])
    query=[]
    for r in runs:
        folder=output/f"{r['condition']}-prettier6604-N{r['count']}-{r['mode']}";summary=read(folder/'summary.json')
        for point,v in summary['residency_queries'].items():query.append(dict(condition=r['condition'],point=point,elapsed_seconds=v['elapsed_ns']/1e9,cpu_seconds=v['cpu_ns']/1e9,resident_mib=v['resident_bytes']/MIB))
    for name,data in (('new-runs',[flatten(r) for r in runs]),('pressure-curve',[flatten(r) for r in curve]),('N10-repeats',[flatten(r) for r in repeats]),
        ('repeat-dispersion',spread),('paired-repeats',pairs),('paired-dispersion',pair_spread),('guest-spans',guests),('query-cost',query)):
        table(output/(name+'.tsv'),data)
    if residuals:table(output/'pressure-fit-residuals.tsv',residuals)
    if nonmonotone:table(output/'nonmonotone-curve.tsv',nonmonotone)
    value=dict(status='PASS' if effective else 'RUNS_COMPLETE_PRESSURE_INVALID',new_runs=runs,curve=curve,fits=fits,residuals=residuals,repeats=repeats,
        repeat_dispersion=spread,paired_repeats=pairs,paired_dispersion=pair_spread,audits=audits,exceptions=exceptions,
        attempts=batch['attempts'],nonmonotone_observations=nonmonotone,new_guests=sum(r['count'] for r in runs),new_actions=29*sum(r['count'] for r in runs),
        baseline_source='results/host-density-pressure-20261102/pressure-summary.json',
        no_pressure_curve_source='results/host-density-20261102/summary.json',
        uncertainty_scope='Three paired observations including original C/D; original A/B denominator held fixed; not a confidence interval or latency guarantee')
    plots(value,output);write_report(value,query,output);write(output/'pressure-extension-summary.json',value)
    print('status='+value['status']+' result_dir='+str(output),flush=True)
    return value


def plots(v,output):
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
    for mode,color in (('shared','#2563eb'),('independent','#d97706')):
        for ax,field in zip(axes,('execution_current_peak_mib','p1_mincore_mib')):
            old=[read(ROOT/f'results/host-density-20261102/prettier6604-N{n}-{mode}/summary.json') for n in (1,2,4,7,10)]
            key='execution_current_peak_bytes' if field=='execution_current_peak_mib' else 'p1_resident_bytes'
            ax.plot([1,2,4,7,10],[r[key]/MIB for r in old],color=color,linestyle=':',alpha=.5,label=mode+' no pressure (old)')
            r=[r for r in v['curve'] if r['mode']==mode]
            ax.plot([p['count'] for p in r],[p[field] for p in r],color=color,marker='o',label=mode+' pressure (N=10 reused)')
            extra=[r for r in v['new_runs'][4:] if r['mode']==mode]
            ax.scatter([10]*len(extra),[r[field] for r in extra],color=color,marker='x',s=50,label=mode+' new N=10 repeats')
            if field=='execution_current_peak_mib' and v['fits']:
                f=v['fits'][mode];ax.plot([4,10],[f['intercept_mib']+4*f['slope_mib_per_guest'],f['intercept_mib']+10*f['slope_mib_per_guest']],color=color,linestyle='--',alpha=.6)
    axes[0].set_ylabel('Group charge memory.current execution peak (MiB)');axes[1].set_ylabel('Selected lower-file resident pages, mincore P1 (MiB)')
    axes[0].set_title('Pressure: solid = observations, dashed = 3-point fit');axes[1].set_title('mincore P1: selected lower files only')
    for ax in axes:ax.set_xlabel('Sandbox count N');ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(output/'pressure-N-curves.png',dpi=140);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
    old=read(ROOT/'results/host-density-pressure-20261102/pressure-summary.json')['runs']
    for mode,color in (('shared','#2563eb'),('independent','#d97706')):
        selected=[r for r in v['repeats'] if r['mode']==mode]
        for ax,field in zip(axes,('execution_current_peak_mib','actions_downloads_seconds')):
            ax.plot(range(1,4),[r[field] for r in selected],marker='o',color=color,label=mode)
            baseline=next(r for r in old[:2] if r['mode']==mode)
            ax.axhline(baseline[field],linestyle=':',color=color,alpha=.6,label=mode+' original no-pressure baseline')
    axes[0].set_ylabel('Group charge memory.current execution peak (MiB)');axes[1].set_ylabel('Concurrent actions + downloads window (seconds)')
    for ax in axes:ax.set_xlabel('N=10 pressure repeat (1 = original C/D)');ax.set_xticks([1,2,3]);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(output/'N10-repeats.png',dpi=140);plt.close(fig)


def write_report(v,queries,output):
    lines=['# 压力下的沙箱数量曲线与N=10重复实验','',
        f"新增{len(v['new_runs'])}次运行、{v['new_guests']}个guest、{v['new_actions']}个原动作。批次状态：{v['status']}。新结果与原A～D、旧无压力五点分开列出；不覆盖原43.50%、5.866秒或其他历史值。",'']
    for field,label,unit in (('current_retention_percent','三次N=10的current差保留率','%'),('actions_window_gap_seconds','三次N=10独立减共享的动作窗口差','秒')):
        r=next(r for r in v['paired_dispersion'] if r['metric']==field)
        lines.append(f"- {label}为{r['minimum']:.3f}～{r['maximum']:.3f}{unit}，中位数{r['median']:.3f}{unit}，样本标准差{r['sample_stddev']:.3f}{'个百分点' if unit=='%' else unit}。")
    if v['nonmonotone_observations']:lines.append('- 压力曲线出现更高N反而更低current的点，准备缓存回收程度不同；不能据拟合斜率给出稳定的部署密度倍数。')
    reversed_pairs=[p for p in v['paired_repeats'] if p['current_gap_mib']<0]
    if reversed_pairs:lines.append('- 有N=10重复出现共享组执行current反而更高；指定文件驻留仍显著更少，不能将文件共享收益直接等同于整体执行记账收益。')
    lines+=['',
        '## 1. 新增八次运行','',
        'E／F为N=4，G／H为N=7；I／J及L／K是两对新增N=10。后一对按独立→共享执行，所有组完整保留，不剔除慢组。每次沿用创建前约20% MemAvailable目标、之后匿名分配固定持有，运行中的实际可用率另报。压力在实验cgroup外，无swap才计有效。', '',
        '| 条件 | N／组别 | current全程／执行峰值 MiB | 执行采样中位数 MiB | P1 mincore MiB | refault | 工具累计秒 | 动作及下载秒 | 创建跨度秒 | 协调程序整轮秒 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in v['new_runs']:lines.append(f"| {r['condition']} | {r['count']}／{r['mode']} | {r['current_peak_mib']:.3f}／{r['execution_current_peak_mib']:.3f} | {r['execution_current_median_mib']:.3f} | {r['p1_mincore_mib']:.3f} | {r['workingset_refault_file']} | {r['tool_sum_seconds']:.3f} | {r['actions_downloads_seconds']:.3f} | {r['creation_span_seconds']:.3f} | {r['controller_seconds']:.3f} |")
    lines+=['','current包含组内准备、服务、控制程序、loop后备缓存和guest，不是guest独占RAM。mincore只覆盖19／N×19个指定下层文件，与current分开，不拼总物理内存。全程峰值可在加压前准备阶段形成，压力比较使用执行峰值。分项取执行current峰值同点，见[new-runs.tsv](new-runs.tsv)。','','## 2. 压力曲线：新增N=4／7，复用原N=10一点','','本节每组别使用N=4／7新点及原C／D的N=10点，不把新增N=10重复塞进三点拟合。旧无压力五点拟合独立保留。不同N下固定压力进程的实际占用和运行可用率不同，报告的是原加压协议下的观察，不是严格恒定MemAvailable实验。','','| 条件 | 组别 | 斜率 MiB/guest | 截距 MiB | R² | 观测值中位数 MiB |','| --- | --- | ---: | ---: | ---: | ---: |']
    old=read(ROOT/'results/host-density-20261102/summary.json')['fits']['execution_current_peak_bytes']
    for source,fits in (('旧无压力N=1/2/4/7/10',old),('压力N=4/7/10',v['fits'])):
        if fits:
            for mode,f in fits.items():lines.append(f"| {source} | {mode} | {f['slope_mib_per_guest']:.3f} | {f['intercept_mib']:.3f} | {f['r_squared']:.6f} | {f['observed_median_mib']:.3f} |")
    if v['nonmonotone_observations']:
        lines+=['','### 沙箱更多但记账反而更低的点','','以下点完整保留。更高N的指定文件P1与anon仍须按原身份／结果核对，不能因current更低而当成少跑guest。压力进程只控制宿主可用内存，不限制每个实验组；本次观察到准备缓存回收程度不同。']
        for r in v['nonmonotone_observations']:
            lines+=['',f"{r['mode']}：N={r['higher_N']}比N={r['lower_N']}执行current低{-r['current_change_mib']:.3f} MiB；P0 current为{r['P0_higher_N_current_mib']:.3f}对{r['P0_lower_N_current_mib']:.3f} MiB，差异在创建guest前已存在。执行峰值file为{r['higher_N_peak_file_mib']:.3f}对{r['lower_N_peak_file_mib']:.3f} MiB，anon为{r['higher_N_peak_anon_mib']:.3f}对{r['lower_N_peak_anon_mib']:.3f} MiB。原始目录`{r['higher_N_raw']}`、`{r['lower_N_raw']}`。"]
        lines+=['','已有计数定位到不同的剩余准备缓存，未记录页级回收选择及完整旧缓存归属，为什么某一组被回收得更多仍不能确定。不能将这种曲线拟合的斜率当稳定每guest边际内存；尤其应同时看R²、原点表与残差，而不是只引用斜率。']
    lines+=['','[压力三点原值](pressure-curve.tsv)'+('及[拟合残差](pressure-fit-residuals.tsv)' if v['fits'] else '；存在无效压力条件，未给有效压力拟合')+'。截距是N=0外推量，不当固定对象容量；斜率只在本次N=4～10范围内描述记账变化。','','| N／来源 | 独立−共享执行current MiB | current少 % | 独立−共享mincore MiB | 独立−共享动作窗口秒 |','| --- | ---: | ---: | ---: | ---: |']
    for n in (4,7,10):
        s=next(r for r in v['curve'] if r['count']==n and r['mode']=='shared');i=next(r for r in v['curve'] if r['count']==n and r['mode']=='independent')
        lines.append(f"| {n}／{'原C/D' if n==10 else '新增'} | {i['execution_current_peak_mib']-s['execution_current_peak_mib']:.3f} | {100*(1-s['execution_current_peak_mib']/i['execution_current_peak_mib']):.2f}% | {i['p1_mincore_mib']-s['p1_mincore_mib']:.3f} | {i['actions_downloads_seconds']-s['actions_downloads_seconds']:+.6f} |")
    lines+=['','![压力和旧无压力曲线](pressure-N-curves.png)','','## 3. N=10三次重复与不确定度','','第一次为原C／D，后两次为本批新增；逐次值见[N10-repeats.tsv](N10-repeats.tsv)。下面标准差采用样本标准差（分母n−1），极差为最大减最小，不剔除慢值。','','| 组别 | 指标 | 最小 | 最大 | 极差 | 中位数 | 样本标准差 |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for r in v['repeat_dispersion']:lines.append(f"| {r['mode']} | {r['metric']} | {r['minimum']:.6f} | {r['maximum']:.6f} | {r['range']:.6f} | {r['median']:.6f} | {r['sample_stddev']:.6f} |")
    lines+=['','MiB指标为记账量或指定文件驻留量；seconds指标为秒。三对差值仍用原A／B无压力差作保留率分母，未包含无压力基线本身的不确定度。','','| 重复／来源 | current差 MiB | current差保留率 | mincore差保留率 | 独立−共享动作窗口秒 | 比原无压力窗口差再增加秒 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in v['paired_repeats']:lines.append(f"| {r['repeat']}／{r['source']} | {r['current_gap_mib']:.3f} | {r['current_retention_percent']:.3f}% | {r['mincore_retention_percent']:.3f}% | {r['actions_window_gap_seconds']:.6f} | {r['extra_window_gap_vs_original_controls_seconds']:.6f} |")
    lines+=['','| 差值指标 | 最小 | 最大 | 极差 | 中位数 | 样本标准差 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in v['paired_dispersion']:lines.append(f"| {r['metric']} | {r['minimum']:.6f} | {r['maximum']:.6f} | {r['range']:.6f} | {r['median']:.6f} | {r['sample_stddev']:.6f} |")
    lines+=['','以上是三次观察范围，不是总体置信区间或延迟上限；原43.50%和5.866秒保留为第一次结果。不同顺序与时刻可能影响结果，重复不自动建立refault导致减速的因果关系。']
    if reversed_pairs:
        lines+=['','### N=10记账差反转：已保留，未确定完整原因','']
        for p in reversed_pairs:
            s=next(r for r in v['repeats'] if r['condition']==p['shared']);i=next(r for r in v['repeats'] if r['condition']==p['independent'])
            lines.append(f"第{p['repeat']}对{s['condition']}／{i['condition']}：共享执行current {s['execution_current_peak_mib']:.3f} MiB，独立{i['execution_current_peak_mib']:.3f} MiB；独立反而低{-p['current_gap_mib']:.3f} MiB。指定文件P1仍为共享{s['p1_mincore_mib']:.3f}、独立{i['p1_mincore_mib']:.3f} MiB，两种量各自保留。")
            lines+=['',f"创建前P0 current共享{s['cold_baseline_current_mib']:.3f}、独立{i['cold_baseline_current_mib']:.3f} MiB；执行峰值file为{s['peak_components_mib']['file']:.3f}／{i['peak_components_mib']['file']:.3f} MiB，anon为{s['peak_components_mib']['anon']:.3f}／{i['peak_components_mib']['anon']:.3f} MiB。准备缓存剩余量和guest运行占用均不同，不能把反转只归给目标文件；为什么回收保留了不同对象，现有记录不能完整确定。原始目录`{s['source_storage']}`、`{i['source_storage']}`。"]
    zero=[r['condition'] for r in v['new_runs'] if r['workingset_refault_file']==0]
    lines+=['',f"新增组执行窗口内refault记录增量为0的条件：{'、'.join(zero)}。本批没有复现原D的63715个执行计数；但独立组的动作窗口仍有较慢记录，因此原D计数不能作为这些延迟差的共同解释。计数为0也不表示没有首次读盘或guest内部缺页。全流程计数单独保存在机器摘要，不代替执行窗口。",'',
        '![N=10重复](N10-repeats.png)','','## 4. 实际压力、采样与磁盘','','| 条件 | MemAvailable前／中／后 GiB | 中点可用率 | 压力匿名驻留最大 GiB | swap启用／使用字节 | 有效压力 | 最低剩余磁盘 GiB |','| --- | ---: | ---: | ---: | --- | --- | ---: |']
    for r in v['new_runs']:
        p=r['pressure_points'];values=[int(p[k]['MemAvailable'])/2**30 for k in ('before','middle','after')]
        lines.append(f"| {r['condition']} | {'／'.join(f'{x:.3f}' for x in values)} | {100*int(p['middle']['MemAvailable'])/int(p['middle']['MemTotal']):.2f}% | {r['pressure_anon_max_gib']:.3f} | {r['swap_enabled']}／{r['host_swap_used_max_bytes']} | {r['effective_pressure']} | {r['minimum_disk_available_gib']:.3f} |")
    lines+=['','前为分配前，已完成复制／同步；中为动作窗口中点；后为压力释放完成。每卷80 GiB。两端普通采样0.1秒，无PSS／周期mincore；P0与P1各一次，逐查询墙钟与CPU见[query-cost.tsv](query-cost.tsv)，逐guest工具累计／跨度及原始路径见[guest-spans.tsv](guest-spans.tsv)。释放阶段未读取的压力RSS留空。','','### 加压前后及创建前的组记账量','','| 条件 | 加压前current MiB | 加压后current MiB | P0后、创建前current MiB | 执行峰值file MiB | 执行峰值anon MiB |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in v['new_runs']:lines.append(f"| {r['condition']} | {r['prepared_before_pressure_current_mib']:.3f} | {r['prepared_after_pressure_current_mib']:.3f} | {r['cold_baseline_current_mib']:.3f} | {r['peak_components_mib']['file']:.3f} | {r['peak_components_mib']['anon']:.3f} |")
    lines+=['','原协议先加压再执行精确目标文件P0冷起点操作，两步均在guest创建前完成；因此加压后与P0后数值并不要求相同。运行中没有增加缓存控制。','','## 5. 部署密度应如何使用这些结果','','给定执行阶段组记账预算X，可先按[pressure-curve.tsv](pressure-curve.tsv)检查实测N=4／7／10哪些点满足X，再结合任务窗口选择满足延迟预算Y的点。准备峰值若也必须落在X内，应另用全程峰值检查，不能只看执行值。', '',
        '本批支持两项分开的观察：指定下层文件的共享驻留收益较稳定，N=10独立组的动作窗口在三对中均更慢；整体执行current差却波动且出现反转，尚不足以给出稳定的密度倍数。全程准备峰值另有明显差距，但压力是在准备后加入，不能用它证明压力下执行收益。', '',
        '压力直线只作为三点的描述；尤其独立组R²很低，不适合据其斜率换算可部署数量。延迟预算仍由使用者选择；原无压力对照来自之前批次，N=4／7并未新增同批无压力对照。按不同N或宿主状态外推时，须保留这一比较条件；不把0.190秒/GiB当通用换算率。','','### N=10四档延迟预算的重复观察','','分母仍用原A／B各自无压力窗口，按原C／D及新增两对分别判断。表中“符合”只是本次观测，不承诺以后都满足。','','| 允许相对无压力增加 | 共享符合次数 | 独立符合次数 | 同一对两组均符合次数 |','| --- | ---: | ---: | ---: |']
    controls=read(ROOT/'results/host-density-pressure-20261102/pressure-summary.json')['runs'][:2]
    for budget in (5,10,20,50):
        passes={m:[r['actions_downloads_seconds']<=next(c for c in controls if c['mode']==m)['actions_downloads_seconds']*(1+budget/100) for r in v['repeats'] if r['mode']==m] for m in ('shared','independent')}
        lines.append(f"| +{budget}% | {sum(passes['shared'])}/3 | {sum(passes['independent'])}/3 | {sum(a and b for a,b in zip(passes['shared'],passes['independent']))}/3 |")
    lines+=['','## 6. 异常与原始证据','']
    if v['exceptions']:lines.append(str(v['exceptions']))
    else:lines.append('八次任务、身份、冷起点、原官方结果、压力归属及清理核对通过；采样提示和慢组仍逐次保留如下。')
    for r in v['new_runs']:
        folder=f"{r['condition']}-prettier6604-N{r['count']}-{r['mode']}"
        lines+=['',f"{r['condition']}：[组记账曲线]({folder}/host.png) · [宿主压力及回收]({folder}/pressure.png) · [guest曲线]({folder}/guest.png) · [工具耗时]({folder}/tools.png)。原始目录`{r['source_storage']}`，控制记录`{r['source_member']}`。"]
        flags=any(p['file_exceeds_current'] for p in r['p0_counter_flags'].values())
        lines.append(f"P0 file>current提示：{flags}；执行窗口最大组／宿主采样间隔{r['sampling_gaps']['group']['execution_max_gap_seconds']:.6f}／{r['sampling_gaps']['global_host']['execution_max_gap_seconds']:.6f}秒；末次组current仍有{r['last_group_sample_mib']['current']:.3f} MiB，不作为归零时刻。")
    failures=[a for a in v['attempts'] if a['result']['status']!='PASS']
    lines+=['','失败尝试：'+str(failures) if failures else '','[机器摘要、身份核查与压力收据](pressure-extension-summary.json)','','## 复现入口','','```bash','bash scripts/run-host-density.sh --pressure-extension \\','  --output .artifacts/新的压力补跑目录 \\','  --daemon .artifacts/host-density-build-20261102/uvm-ublk-daemon \\','  --pressure-binary .artifacts/host-pressure-build-20261102/host-anon-pressure','```','','## 下一步建议','','先按使用者给定的X和Y筛选本次实测点；只有预算落在已有N／压力范围之外时，再讨论最小新增范围。本批不自动追加轮次。','']
    (output/'REPORT.md').write_text('\n'.join(lines))


if __name__=='__main__':
    import matplotlib
    matplotlib.use('Agg')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    report(a.batch.resolve(),a.output.resolve())
