"""Read the completed C4 timing pair; preserve old windows and report new phases separately."""
import argparse
import json
from pathlib import Path
from statistics import median

from collect_host_density import ROOT,MIB,read,rows,table,write,memory_metrics
from report_host_density import member_audit


def call_rows(events):
    result=[]
    for event in events:
        if event.get('call_timing_version')!=2:raise ValueError('new call timing is missing')
        if event['exit_code']!=0 or not all(event[k] for k in ('command_returned','stdout_log_completed','events_log_completed')):
            raise ValueError('command or log writing did not complete')
        start=event['start_monotonic_ns'];end=event['call_end_monotonic_ns']
        assert start<=event['command_start_monotonic_ns']<=event['command_end_monotonic_ns']<=event['stdout_log_start_monotonic_ns']<=event['stdout_log_end_monotonic_ns']<=end
        assert event['command_duration_ns']==event['command_end_monotonic_ns']-event['command_start_monotonic_ns']
        assert event['stdout_log_duration_ns']==event['stdout_log_end_monotonic_ns']-event['stdout_log_start_monotonic_ns']
        assert event['call_elapsed_ns']==end-start and 0<=event['duration_ns']<=event['call_elapsed_ns']
        phases={key:event[key] for key in ('command_duration_ns','stdout_log_duration_ns','event_lock_wait_ns','events_log_duration_ns')}
        assert all(value>=0 for value in phases.values())
        other=event['call_elapsed_ns']-sum(phases.values());assert other>=0
        result.append(dict(stage=event['stage'],**phases,other_ns=other,legacy_duration_ns=event['duration_ns'],call_elapsed_ns=event['call_elapsed_ns']))
    return result


def timing_plot(guests,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(13,7),sharey=True,constrained_layout=True)
    y=list(range(len(guests)));values=[g['replay_command_ns']/1e9 for g in guests]
    bars=axes[0].barh(y,values,color='#2563eb');axes[0].bar_label(bars,fmt='%.2f',padding=3,fontsize=8)
    axes[0].set(yticks=y,yticklabels=[g['mode']+' '+g['guest'] for g in guests],xlabel='Command call wall time (seconds)',title='Command call, before stdout logging')
    axes[0].set_xlim(0,max(values)*1.14);axes[0].invert_yaxis()
    left=[0.]*len(guests)
    for field,label,color in [('replay_stdout_ns','stdout write','#f59e0b'),('replay_lock_wait_ns','Event-lock wait','#94a3b8'),('replay_events_ns','events.json write','#10b981')]:
        values=[g[field]/1e6 for g in guests];axes[1].barh(y,values,left=left,label=label,color=color);left=[a+b for a,b in zip(left,values)]
    axes[1].set(xlabel='Host logging wall time (milliseconds)',title='Logging after the command returns');axes[1].legend(loc='upper right')
    for ax in axes:ax.grid(axis='x',alpha=.2)
    fig.suptitle('C4 N=7: command and logging use separate scales')
    fig.savefig(output/'replay-timing.png',dpi=140);plt.close(fig)


def report(batch_path,output):
    batch=read(batch_path/'result.json')
    assert batch['status']=='PASS' and batch['source_restored_stopped']
    assert batch['experiment']=='c4-call-timing-pair-v2' and len(batch['runs'])==2
    assert batch['plan']==[dict(task='c4',count=7,mode=mode) for mode in ('shared','independent')]
    calls=[];guests=[];runs=[];audits=[]
    for member in batch['runs']:
        mode=member['mode'];folder=output/f'c4-N7-{mode}';row=read(folder/'summary.json')
        assert row['task']=='c4' and row['count']==7 and row['mode']==mode
        audit=member_audit(ROOT,row);audits.append(audit)
        storage=ROOT/row['source_storage'];task=read(storage/'density/result.json');replays=[];mode_guests=[]
        for child in task['results']:
            raw=storage/'density'/child['raw'];value=read(raw/'result.json');events=read(raw/'events.json');timing=value['call_timing']
            parsed=call_rows(events);replay=next(e for e in events if e['stage']=='replay');replays.append(replay)
            assert timing['version']==2 and timing['replay']==replay
            for key,field in (('stdout_log_cumulative_ns','stdout_log_duration_ns'),('events_log_cumulative_ns','events_log_duration_ns'),('event_lock_wait_cumulative_ns','event_lock_wait_ns')):
                assert timing[key]==sum(e[field] for e in events)
            assert 0<=timing['final_events_flush_ns']<=value['controller_elapsed_ns']
            for call in parsed:calls.append(dict(mode=mode,guest=child['role'],**call))
            tools=rows(raw/'tools.tsv');span=int(tools[-1]['end_monotonic_ns'])-int(tools[0]['start_monotonic_ns'])
            g=dict(mode=mode,guest=child['role'],raw=str(raw.relative_to(ROOT)),tool_span_ns=span,
                replay_command_ns=replay['command_duration_ns'],replay_stdout_ns=replay['stdout_log_duration_ns'],
                replay_lock_wait_ns=replay['event_lock_wait_ns'],replay_events_ns=replay['events_log_duration_ns'],
                replay_call_ns=replay['call_elapsed_ns'],controller_ns=value['controller_elapsed_ns'],
                stdout_cumulative_ns=timing['stdout_log_cumulative_ns'],events_cumulative_ns=timing['events_log_cumulative_ns'],
                lock_wait_cumulative_ns=timing['event_lock_wait_cumulative_ns'],final_events_flush_ns=timing['final_events_flush_ns'])
            guests.append(g);mode_guests.append(g)
        assert len(mode_guests)==7
        begin=min(e['command_start_monotonic_ns'] for e in replays);end=max(e['command_end_monotonic_ns'] for e in replays)
        command_memory=memory_metrics(rows(folder/'host-memory.tsv'),begin,end)
        runs.append(dict(mode=mode,source_member=row['source_member'],source_storage=row['source_storage'],
            tool_sum_seconds=row['timings_ns']['tool_sum']/1e9,legacy_actions_downloads_seconds=row['timings_ns']['actions_and_downloads']/1e9,
            replay_command_window_seconds=(end-begin)/1e9,controller_seconds=row['timings_ns']['controller']/1e9,
            replay_command_median_seconds=median(g['replay_command_ns'] for g in mode_guests)/1e9,
            replay_command_max_seconds=max(g['replay_command_ns'] for g in mode_guests)/1e9,
            **{key[:-3]+'_seconds':sum(g[key] for g in mode_guests)/1e9 for key in ('stdout_cumulative_ns','events_cumulative_ns','lock_wait_cumulative_ns','final_events_flush_ns')},
            maximum_call_log_seconds=max(c['stdout_log_duration_ns']+c['event_lock_wait_ns']+c['events_log_duration_ns'] for c in calls if c['mode']==mode)/1e9,
            legacy_current_peak_mib=row['execution_current_peak_bytes']/MIB,legacy_current_median_mib=row['execution_current_median_bytes']/MIB,
            command_window_current_peak_mib=command_memory['execution_current_peak_bytes']/MIB,command_window_current_median_mib=command_memory['execution_current_median_bytes']/MIB,
            p0_current_mib=row['cold_baseline']['current']/MIB,p1_mincore_mib=row['p1_resident_bytes']/MIB,p0_counter_flags=row['p0_counter_flags'],
            host_minimum_available_gib=row['host_minimum_available_bytes']/2**30))
    assert len({a['cgroup'] for a in audits})==2
    table(output/'calls.tsv',calls);table(output/'guests.tsv',guests)
    value=dict(status='PASS',guests=14,actions=504,runs=runs,audits=audits,
        scope='One shared/independent pair; logging durations are measured wall intervals, not a sampling-on/off slowdown. Concurrent cumulative costs overlap. Original density windows are retained; the command-only replay window is a separate statistic.')
    write(output/'timing-summary.json',value)
    timing_plot(guests,output)
    write_report(value,output)
    print('status=PASS result_dir='+str(output),flush=True)
    return value


def write_report(value,output):
    runs=value['runs'];audits=value['audits']
    preparation=[(a['input_conversion']['end_monotonic_ns']-a['input_conversion']['start_monotonic_ns'])/1e9 for a in audits]
    deletion=[sum((v['end_monotonic_ns']-v['start_monotonic_ns'])/1e9 for v in a['cleanup'].values() if isinstance(v,dict) and 'end_monotonic_ns' in v) for a in audits]
    lines=['# C4七沙箱：命令与本地日志计时验证','',
        '共享／独立各一组完成，共14个guest、504个原动作。身份、9／63个下层文件、P0=0／P1、原36步预期退出码、9898文件往返／约束／语法及逐个删除和服务收尾通过。',
        '', f"新字段已在真实并发下完整保存。普通调用日志写入／锁等待最大为共享{runs[0]['maximum_call_log_seconds']*1000:.3f} ms、独立{runs[1]['maximum_call_log_seconds']*1000:.3f} ms；收尾最终事件写入另列。", '', '## 时间与日志成本', '',
        '工具累计是七个guest之和；并发命令窗口从最早replay命令开始到最晚命令返回，不含之后日志与下载。原动作／下载窗口仍保留。日志列为七个guest所有调用累计墙钟，可能与其他调用重叠，不作为整轮额外减速。', '',
        '| 组别 | 工具累计 s | 并发命令窗口 s | 原动作／下载窗口 s | 协调程序整轮 s | stdout累计 s | events累计 s | 锁等待累计 s | 最终events累计 s |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        lines.append('| '+r['mode']+' | '+' | '.join(f"{r[k]:.6f}" for k in ('tool_sum_seconds','replay_command_window_seconds','legacy_actions_downloads_seconds','controller_seconds','stdout_cumulative_seconds','events_cumulative_seconds','lock_wait_cumulative_seconds','final_events_flush_seconds'))+' |')
    lines+=['', f"输入转换、复制和同步准备：共享{preparation[0]:.3f}秒、独立{preparation[1]:.3f}秒，相差{preparation[1]-preparation[0]:.3f}秒；两边逐个删除合计{deletion[0]:.3f}／{deletion[1]:.3f}秒。该准备阶段包含在协调程序整轮里，不归因于采样或日志。", '', '## 内存窗口分开解释','','原窗口按旧字段取动作及下载；新窗口只看replay命令调用。两者是同一轮数据的不同观察区间，不覆盖历史结果。current覆盖整组准备、服务、控制及guest；mincore只覆盖指定下层文件。','','| 组别 | P0 current MiB | 原窗口current峰值／中位数 MiB | 命令窗口current峰值／中位数 MiB | P1 mincore MiB | 最低host余盘 GiB |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        lines.append(f"| {r['mode']} | {r['p0_current_mib']:.3f} | {r['legacy_current_peak_mib']:.3f} / {r['legacy_current_median_mib']:.3f} | {r['command_window_current_peak_mib']:.3f} / {r['command_window_current_median_mib']:.3f} | {r['p1_mincore_mib']:.3f} | {r['host_minimum_available_gib']:.3f} |")
    lines+=['','## 逐guest检查与原始记录','','[逐次调用](calls.tsv)、[逐guest汇总](guests.tsv)、[完整数值与身份／收尾证据](timing-summary.json)、[replay计时图](replay-timing.png)。命令包含客户端、输出接收及运行中的stderr写入；文件写入没有额外fsync。']
    for r in runs:
        folder='c4-N7-'+r['mode'];lines+=['',f"### {r['mode']}",'',f"[host整轮内存]({folder}/host.png) · [guest内存／缓存]({folder}/guest.png) · [36步工具时延]({folder}/tools.png)",'',f"原始目录：`{r['source_storage']}`。单次调用日志／锁等待最大合计{r['maximum_call_log_seconds']:.6f}秒；replay命令中位数／最大值{r['replay_command_median_seconds']:.6f}／{r['replay_command_max_seconds']:.6f}秒。",'',f"P0 file>current提示：`{json.dumps(r['p0_counter_flags'])}`。原值保留，不据此补出组外容量。"]
    lines+=['','## 使用范围与下一步','','本轮验证计时和并发数据完整性；只有一对运行，未做新旧计时开关对照，累计日志时间不等于总体减速。旧异常原因仍以原离线分析的证据范围为准。','','下一步建议只离线统一推荐入口和新旧计时口径，保留本轮成本及旧异常说明；不再追加日志优化或同类实跑。若以后再次出现明显日志等待，再依据新字段定位受影响调用，决定是否补系统调用证据。本轮已结束。','']
    (output/'REPORT.md').write_text('\n'.join(lines))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('output',type=Path);args=p.parse_args()
    report(args.batch.resolve(),args.output.resolve())
