"""Saved four-condition timing/refault analysis only; no services or page queries."""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import median

from collect_host_density import ROOT,read,rows,table,write
from report_host_pressure import SEGMENTS


def difference(values):return values['D']-values['C']-values['B']+values['A']


def end(call):return call['start_monotonic_ns']+call['duration_ns']


def critical_parts(calls,tool_ns,group_start):
    calls=sorted(calls,key=lambda c:c['stage']);assert len(calls)==10
    assert all(end(a)<=b['start_monotonic_ns'] for a,b in zip(calls,calls[1:]))
    execution=sum(c['duration_ns'] for c in calls[::2]);download=sum(c['duration_ns'] for c in calls[1::2])
    gaps=sum(b['start_monotonic_ns']-end(a) for a,b in zip(calls,calls[1:]))
    offset=calls[0]['start_monotonic_ns']-group_start
    result=dict(start_offset_ns=offset,tools_ns=tool_ns,execution_residual_ns=execution-tool_ns,download_ns=download,call_gaps_ns=gaps)
    assert sum(result.values())==end(calls[-1])-group_start
    return result


def refault_intervals(host,start,finish,segments):
    selected=[r for r in host if start<=int(r['monotonic_ns'])<=finish];assert len(selected)>1
    result=[]
    for a,b in zip(selected,selected[1:]):
        left=int(a['monotonic_ns']);right=int(b['monotonic_ns'])
        active=sorted({s['segment'] for s in segments if s['start_monotonic_ns']<right and s['end_monotonic_ns']>left})
        delta=int(b['workingset_refault_file'])-int(a['workingset_refault_file'])
        result.append(dict(start_monotonic_ns=left,end_monotonic_ns=right,seconds_from_window_start=(right-start)/1e9,
            refault_delta=delta,active_segments='+'.join(active) if active else 'NO_EXECUTION_CALL',
            current_mib=int(b['current'])/2**20))
    assert sum(r['refault_delta'] for r in result)==int(selected[-1]['workingset_refault_file'])-int(selected[0]['workingset_refault_file'])
    return result


def analyze(source,output):
    saved=read(source/'pressure-summary.json');assert saved['status']=='PASS'
    assert [r['condition'] for r in saved['runs']]==list('ABCD')
    output.mkdir(parents=True,exist_ok=False)
    segment_rows=[];guest_steps=[];step_values=defaultdict(lambda:defaultdict(int));commands={};critical=[];critical_segments=[];intervals=[];post_intervals=[];contexts=[]
    for run in saved['runs']:
        condition=run['condition'];folder=source/f"{condition}-prettier6604-N10-{run['mode']}";published=read(folder/'summary.json')
        storage=ROOT/run['source_storage'];task=read(storage/'density/result.json');start,finish=published['execution_window_monotonic_ns']
        all_calls=[];by_guest={};by_segment=[];official_windows=[];tool_total=0
        for child in task['results']:
            guest=child['role'];raw=storage/'density'/child['raw'];events=read(raw/'events.json')
            calls=sorted([c for c in events if c['stage'].startswith('controller-call-')],key=lambda c:c['stage']);assert len(calls)==10
            official_windows.extend(dict(segment='OFFICIAL_TESTS',start_monotonic_ns=c['start_monotonic_ns'],end_monotonic_ns=end(c)) for c in events if c['stage']=='official-task-tests')
            all_calls+=calls;guest_tools=0
            for i,segment in enumerate(SEGMENTS):
                directory=raw/'rollouts/backbone'/segment;tools=rows(directory/'steps.tsv');actions=rows(directory/'actions.tsv')
                assert [t['step'] for t in tools]==[a['step'] for a in actions]
                total=sum(int(t['duration_ns']) for t in tools);tool_total+=total;guest_tools+=total
                for tool,action in zip(tools,actions):
                    key=(segment,tool['step']);command=action['command']
                    if key in commands:assert command==commands[key]
                    commands[key]=command;step_values[key][condition]+=int(tool['duration_ns'])
                    guest_steps.append(dict(condition=condition,guest=guest,segment=segment,step=tool['step'],duration_ns=int(tool['duration_ns']),exit_code=int(tool['exit_code'])))
                execute,download=calls[2*i:2*i+2]
                segment_event=next(e for e in events if e['stage']=='segment-'+segment)
                assert segment_event['start_monotonic_ns']<=execute['start_monotonic_ns'] and end(download)<=end(segment_event)
                row=dict(condition=condition,guest=guest,segment=segment,actions=len(tools),tools_ns=total,
                    execution_ns=execute['duration_ns'],download_ns=download['duration_ns'],execution_residual_ns=execute['duration_ns']-total,
                    start_monotonic_ns=execute['start_monotonic_ns'],end_monotonic_ns=end(download),
                    execution_end_monotonic_ns=end(execute),raw=str(directory.relative_to(ROOT)))
                segment_rows.append(row);by_segment.append(dict(segment=segment,start_monotonic_ns=execute['start_monotonic_ns'],end_monotonic_ns=end(execute),guest=guest))
            by_guest[guest]=dict(calls=calls,tools=guest_tools)
        assert tool_total==published['timings_ns']['tool_sum']
        assert min(c['start_monotonic_ns'] for c in all_calls)==start and max(end(c) for c in all_calls)==finish
        last=max(by_guest,key=lambda g:end(by_guest[g]['calls'][-1]));last_calls=by_guest[last]['calls']
        parts=critical_parts(last_calls,by_guest[last]['tools'],start)
        assert sum(parts.values())==published['timings_ns']['actions_and_downloads']
        critical.append(dict(condition=condition,guest=last,window_ns=finish-start,**parts))
        for i,segment in enumerate(SEGMENTS):
            a,b=last_calls[2*i:2*i+2];stop=last_calls[2*i+2]['start_monotonic_ns'] if i<4 else end(b)
            critical_segments.append(dict(condition=condition,guest=last,segment=segment,span_ns=stop-a['start_monotonic_ns']))
        host=rows(folder/'host-memory.tsv');window=refault_intervals(host,start,finish,by_segment)
        assert sum(r['refault_delta'] for r in window)==run['workingset_refault_file']
        intervals.extend(dict(condition=condition,**r) for r in window)
        selected=[r for r in host if start<=int(r['monotonic_ns'])<=finish]
        after=refault_intervals(host,int(selected[-1]['monotonic_ns']),int(host[-1]['monotonic_ns']),official_windows)
        for r in after:
            if r['active_segments']=='NO_EXECUTION_CALL':r['active_segments']='NO_OFFICIAL_TEST_CALL'
        post_intervals.extend(dict(condition=condition,**r) for r in after)
        contexts.append(dict(condition=condition,total_refault_delta=int(host[-1]['workingset_refault_file'])-int(host[0]['workingset_refault_file']),
            before_window=int(selected[0]['workingset_refault_file'])-int(host[0]['workingset_refault_file']),
            in_window=sum(r['refault_delta'] for r in window),after_window=int(host[-1]['workingset_refault_file'])-int(selected[-1]['workingset_refault_file'])))
    comparisons=[]
    for segment in SEGMENTS:
        totals={c:sum(r['tools_ns'] for r in segment_rows if r['condition']==c and r['segment']==segment) for c in 'ABCD'}
        comparisons.append(dict(segment=segment,**{c+'_seconds':v/1e9 for c,v in totals.items()},D_minus_B_seconds=(totals['D']-totals['B'])/1e9,
            D_minus_C_seconds=(totals['D']-totals['C'])/1e9,difference_of_differences_seconds=difference(totals)/1e9))
    step_rows=[dict(segment=segment,step=step,command=commands[segment,step],**{c+'_seconds':values[c]/1e9 for c in 'ABCD'},
        difference_of_differences_seconds=difference(values)/1e9) for (segment,step),values in step_values.items()]
    component_rows=[]
    for field in ('start_offset_ns','tools_ns','execution_residual_ns','download_ns','call_gaps_ns'):
        values={r['condition']:r[field] for r in critical};component_rows.append(dict(component=field,**{c+'_seconds':v/1e9 for c,v in values.items()},difference_of_differences_seconds=difference(values)/1e9))
    contribution=[]
    for segment in ('START_OFFSET',*SEGMENTS):
        values={r['condition']:r['start_offset_ns'] for r in critical} if segment=='START_OFFSET' else {r['condition']:r['span_ns'] for r in critical_segments if r['segment']==segment}
        contribution.append(dict(segment=segment,**{c+'_seconds':v/1e9 for c,v in values.items()},difference_of_differences_seconds=difference(values)/1e9))
    grouped=defaultdict(int)
    for r in intervals:grouped[r['condition'],r['active_segments']]+=r['refault_delta']
    combinations=[dict(condition=c,active_segments=label,refault_delta=value) for (c,label),value in grouped.items()]
    post_grouped=defaultdict(int)
    for r in post_intervals:post_grouped[r['condition'],r['active_segments']]+=r['refault_delta']
    post_combinations=[dict(condition=c,overlapping_call=label,refault_delta=value) for (c,label),value in post_grouped.items()]
    for r in contexts:assert sum(v['refault_delta'] for v in post_combinations if v['condition']==r['condition'])==r['after_window']
    guest_index={(r['condition'],r['guest'],r['segment'],r['step']):r['duration_ns'] for r in guest_steps}
    jest_rows=[]
    for (segment,step),command in commands.items():
        if 'yarn jest' not in command:continue
        for guest in sorted({r['guest'] for r in guest_steps}):
            values={c:guest_index[c,guest,segment,step] for c in 'ABCD'}
            jest_rows.append(dict(segment=segment,step=step,guest=guest,**{c+'_seconds':v/1e9 for c,v in values.items()},D_minus_B_seconds=(values['D']-values['B'])/1e9,
                difference_of_differences_seconds=difference(values)/1e9))
    expected_tools=saved['comparison']['latency']['tool_sum_seconds']['difference_of_differences_seconds']
    expected_window=saved['comparison']['latency']['actions_downloads_seconds']['difference_of_differences_seconds']
    assert abs(sum(r['difference_of_differences_seconds'] for r in comparisons)-expected_tools)<1e-7
    assert abs(sum(r['difference_of_differences_seconds'] for r in component_rows)-expected_window)<1e-7
    assert abs(sum(r['difference_of_differences_seconds'] for r in contribution)-expected_window)<1e-7
    for name,data in [('segments',segment_rows),('guest-steps',guest_steps),('jest-guests',jest_rows),('steps',step_rows),('segment-comparison',comparisons),('critical-path',critical),('critical-segments',critical_segments),('critical-components',component_rows),('critical-contributions',contribution),('refault-intervals',intervals),('refault-combinations',combinations),('refault-context',contexts),('post-window-refault',post_intervals),('post-window-combinations',post_combinations)]:table(output/(name+'.tsv'),data)
    jest_summary=[]
    for r in step_rows:
        if 'yarn jest' not in r['command']:continue
        selected=[g for g in jest_rows if (g['segment'],g['step'])==(r['segment'],r['step'])]
        jest_summary.append(dict(segment=r['segment'],step=r['step'],extra_seconds=r['difference_of_differences_seconds'],
            guests_slower_D_than_B=sum(g['D_minus_B_seconds']>0 for g in selected),
            guests_positive_difference_of_differences=sum(g['difference_of_differences_seconds']>0 for g in selected),
            median_D_minus_B_seconds=median(g['D_minus_B_seconds'] for g in selected)))
    value=dict(status='PASS_OFFLINE_ANALYSIS',source=str(source.relative_to(ROOT)),tool_extra_seconds=expected_tools,window_extra_seconds=expected_window,
        segments=comparisons,steps=step_rows,critical=critical,critical_components=component_rows,critical_contributions=contribution,
        refault_context=contexts,refault_combinations=combinations,post_window_combinations=post_combinations,jest_guests=jest_rows,jest_summary=jest_summary,
        jest_extra_seconds=sum(r['extra_seconds'] for r in jest_summary),source_storages=[r['source_storage'] for r in saved['runs']],
        timing_scope='Host monotonic execution/download calls include CLI, boundary operations and local logs; residual is not pure RPC. Guest step duration sums do not require guest/host clock alignment.',
        refault_scope='Each sampled counter increment counted once; label is the union of execution-call segments intersecting its host sampling interval. Calls overlap and memory.stat updates may lag: this does not attribute a page event to one guest or command.')
    evidence=ROOT/saved['runs'][3]['source_storage']/'density'/task['results'][7]['raw']/'code'
    with (output/'timing-source.txt').open('x') as stream:
        for name,first,last in [('run_prettier6604.py',88,103),('controller.py',207,238)]:
            p=evidence/name;source_lines=p.read_text().splitlines();stream.write('Source: '+str(p.relative_to(ROOT))+'\n')
            for line in range(first,last+1):stream.write(f'{line}: {source_lines[line-1]}\n')
            stream.write('\n')
    write(output/'summary.json',value);plots(segment_rows,comparisons,intervals,source,output);report(value,output)
    print('status=PASS_OFFLINE_ANALYSIS result_dir='+str(output),flush=True)
    return value


def plots(segments,comparison,intervals,source,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(10,4),constrained_layout=True)
    ax.bar(range(5),[r['difference_of_differences_seconds'] for r in comparison],color='#2563eb')
    ax.set(xticks=range(5),xticklabels=SEGMENTS,ylabel='Guest tool cumulative time difference (seconds)',title='Extra independent-vs-shared delay under pressure: (D-C)-(B-A)');ax.grid(axis='y',alpha=.2)
    fig.savefig(output/'segment-extra.png',dpi=140);plt.close(fig)
    data=[r for r in segments if r['condition']=='D'];ref=[r for r in intervals if r['condition']=='D'];start=min(r['start_monotonic_ns'] for r in data)
    colors=('#60a5fa','#a78bfa','#f59e0b','#ef4444','#10b981');fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,constrained_layout=True,gridspec_kw={'height_ratios':[2,1]})
    for i,segment in enumerate(SEGMENTS):
        for j,guest in enumerate(sorted({r['guest'] for r in data})):
            r=next(r for r in data if r['guest']==guest and r['segment']==segment)
            axes[0].barh(j,(r['execution_end_monotonic_ns']-r['start_monotonic_ns'])/1e9,left=(r['start_monotonic_ns']-start)/1e9,color=colors[i],label=segment if j==0 else None)
    axes[0].set(yticks=range(10),yticklabels=sorted({r['guest'] for r in data}),ylabel='Guest execution-call windows',title='D: host-clock call windows and sampled group refault increments');axes[0].invert_yaxis();axes[0].legend(ncol=3,fontsize=8,loc='upper left',bbox_to_anchor=(0,1.18))
    axes[1].step([r['seconds_from_window_start'] for r in ref],[r['refault_delta'] for r in ref],where='post',color='#dc2626');axes[1].set(xlabel='Host monotonic seconds from actions/downloads start',ylabel='workingset_refault_file increment\n(per sampled interval)')
    for ax in axes:ax.grid(alpha=.2)
    fig.savefig(output/'D-refault-timeline.png',dpi=140);plt.close(fig)


def report(value,output):
    parts={r['component']:r['difference_of_differences_seconds'] for r in value['critical_components']}
    contribution={r['segment']:r['difference_of_differences_seconds'] for r in value['critical_contributions']}
    late=sum(r['refault_delta'] for r in value['refault_combinations'] if r['condition']=='D' and 'backbone_finish' in r['active_segments'])
    context=next(r for r in value['refault_context'] if r['condition']=='D')
    official=sum(r['refault_delta'] for r in value['post_window_combinations'] if r['condition']=='D' and r['overlapping_call']=='OFFICIAL_TESTS')
    lines=['# 压力组D的额外耗时：五分段与refault离线分析','',
        f"**新增工具耗时主要在三次Jest测试，合计{value['jest_extra_seconds']:.6f}秒，占工具累计额外相对增量的{value['jest_extra_seconds']/value['tool_extra_seconds']:.2%}。** 最主要的是core_verify第005步。",'',
        f"工具累计的额外相对增量为{value['tool_extra_seconds']:.6f}秒；决定整组结束时间的窗口额外增量为{value['window_extra_seconds']:.6f}秒。两者分别核算，不把十guest累计当作墙钟。",'',
        '所有差分为(D−C)−(B−A)。A共享无压力、B独立无压力、C共享有压力、D独立有压力；只读取原40guest／1160动作的日志，不启动任务、不查询页面。','',
        '## 工具累计增量主要落在哪些段','', '| 分段 | A秒 | B秒 | C秒 | D秒 | D−B秒 | (D−C)−(B−A)秒 |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in value['segments']:lines.append('| '+r['segment']+' | '+' | '.join(f"{r[k]:.6f}" for k in ('A_seconds','B_seconds','C_seconds','D_seconds','D_minus_B_seconds','difference_of_differences_seconds'))+' |')
    lines+=['','### 三次Jest测试不是一个guest的离群值','','| 步骤 | 工具累计额外增量秒 | D比B慢的guest数／10 | 双差为正guest数／10 | D−B逐guest中位数秒 |','| --- | ---: | ---: | ---: | ---: |']
    for r in value['jest_summary']:lines.append(f"| {r['segment']}/{r['step']} | {r['extra_seconds']:.6f} | {r['guests_slower_D_than_B']} | {r['guests_positive_difference_of_differences']} | {r['median_D_minus_B_seconds']:.6f} |")
    lines+=['','core_verify/005运行typeOperator与indexedAccesType测试；backbone_finish/002运行新增parenthesizedType测试，/005运行整个conformance/types测试。[逐guest该步原值及差值](jest-guests.tsv)保留正负变化，不剔除。']
    lines+=['','## 末个完成guest决定的窗口分解','','每组取最后一次下载结束最晚的guest作为实际结束路径；起点仍是该组所有guest的最早调用，不固定用同一个guest。各调用按host单调时钟排序，分项精确加回原窗口。','','| 条件 | 最后guest | 起点偏移秒 | 工具累计秒 | 执行余量秒 | 下载秒 | 调用间隙秒 | 原窗口秒 |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in value['critical']:lines.append('| '+r['condition']+' | '+r['guest']+' | '+' | '.join(f"{r[k]/1e9:.6f}" for k in ('start_offset_ns','tools_ns','execution_residual_ns','download_ns','call_gaps_ns','window_ns'))+' |')
    lines+=['','执行余量包括工具边界读取、客户端／通信、调度和本地日志，不能全部称作纯RPC时间。分段窗口把下载后的下一段前间隙计入当前段；起点偏移另列。','','| 分项 | (D−C)−(B−A) 秒 |','| --- | ---: |']
    for r in value['critical_components']:lines.append(f"| {r['component']} | {r['difference_of_differences_seconds']:.6f} |")
    lines+=['','### 五个分段对窗口增量的贡献','','| 分段／偏移 | (D−C)−(B−A) 秒 |','| --- | ---: |']
    for r in value['critical_contributions']:lines.append(f"| {r['segment']} | {r['difference_of_differences_seconds']:.6f} |")
    lines+=['','## refault按采样时刻与调用窗口对齐','','host计数和调用均用同一台宿主的单调时钟，不转换guest墙钟。每个相邻采样间隔的计数增量只记一次；若多个分段重叠，保留组合，不分别加给每个分段或guest。memory.stat汇总可能滞后，组合只表示记录增量时重叠的调用，不能证明是该调用触发了页重入。','','| 条件 | 全程增量 | 窗口前 | 窗口内 | 窗口后 |','| --- | ---: | ---: | ---: | ---: |']
    for r in value['refault_context']:lines.append(f"| {r['condition']} | {r['total_refault_delta']} | {r['before_window']} | {r['in_window']} | {r['after_window']} |")
    lines+=['','D窗口内的非零分段组合：','','| 同一采样间隔重叠的执行分段 | refault增量 |','| --- | ---: |']
    for r in value['refault_combinations']:
        if r['condition']=='D' and r['refault_delta']:lines.append(f"| {r['active_segments']} | {r['refault_delta']} |")
    lines+=['','窗口后的计数另核对原官方检查调用，不混入29动作：','','| 条件 | 与official-task-tests调用重叠的区间增量 | 其余窗口后增量 |','| --- | ---: | ---: |']
    for c in 'ABCD':
        selected=[r for r in value['post_window_combinations'] if r['condition']==c]
        lines.append(f"| {c} | {sum(r['refault_delta'] for r in selected if r['overlapping_call']=='OFFICIAL_TESTS')} | {sum(r['refault_delta'] for r in selected if r['overlapping_call']!='OFFICIAL_TESTS')} |")
    lines+=['','窗口前／后按窗口内首末采样点划分，边界有一个采样间隔的粒度；调用重叠仍只是时间上的关系。']
    lines+=['','## 逐步耗时与证据','','[29步四条件汇总及完整命令](steps.tsv)、[200个guest分段](segments.tsv)、[完整窗口分解](critical-path.tsv)、[逐采样区间](refault-intervals.tsv)、[机器摘要](summary.json)。', '',
        '![分段工具增量](segment-extra.png)','', '![D调用与refault时间线](D-refault-timeline.png)','',
        '[当次冻结的计时源码摘录](timing-source.txt)。原始目录：'+ '、'.join('`'+p+'`' for p in value['source_storages'])+'。','',
        '## 可以确认的定位与仍未分开的原因','',
        f"决定整组结束的路径中，工具累计贡献{parts['tools_ns']:+.6f}秒，起点偏移{parts['start_offset_ns']:+.6f}秒，执行余量{parts['execution_residual_ns']:+.6f}秒、下载{parts['download_ns']:+.6f}秒、调用间隙{parts['call_gaps_ns']:+.6f}秒，精确合为{value['window_extra_seconds']:+.6f}秒。分段上core_verify贡献{contribution['core_verify']:+.6f}秒、backbone_finish贡献{contribution['backbone_finish']:+.6f}秒；新增延迟主要落在原工具计时内，不是下载或本地记录等待。",'',
        f"D窗口内{context['in_window']}个refault增量中，{late}个记录在至少有一个guest进入backbone_finish的采样间隔；最大工具耗时增量却在更早的core_verify/005。窗口后{context['after_window']}个增量中{official}个记录时有官方检查调用在执行。这些时间关系不足以把主要Jest减速唯一归因于host文件页重入或物理盘I/O。现有证据定位了慢步骤，并保留了压力／回收与延迟同现的观察；没有采集到的CPU等待、页事件与I/O服务时间不能补算。",'',
        '本轮离线定位完成，不追加实跑。若下一步要量化可多放多少guest，应先确定任务延迟预算，再另定固定内存预算下的N递增对照。','']
    (output/'REPORT.md').write_text('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,default=ROOT/'results/host-density-pressure-20261102');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    analyze(args.source.resolve(),args.output.resolve())
