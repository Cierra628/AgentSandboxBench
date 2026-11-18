"""Decompose the four saved local-input GRPO runs; no workload, service or page queries."""
import argparse
import csv
import os
from pathlib import Path
import re

from analyze_grpo_path_costs import ROOT,read,write,summarize,paired_paths
from run_grpo_light_pair import interval_union
from report_grpo_create_setup_repeats import control_breakdown


def label(event):return re.sub(r'^\d+-','',event['stage'])
def finish(event):return event['start_monotonic_ns']+event['duration_ns']


def controller_phases(task,events):
    """Partition the controller without inventing missing absolute start/end markers."""
    cold=[e for e in events if label(e).startswith('cold-')]
    calls=[e for e in events if label(e)=='controller-call']
    checks=[e for e in events if label(e).startswith(('source-diff-','oracle-','upload-official-','official-'))]
    assert len(cold)==7 and len(calls)==70 and len(checks)==28
    assert all(e['exit_code']==0 and e['duration_ns']>=0 for e in events)
    observed_start=min(e['start_monotonic_ns'] for e in events);observed_end=max(map(finish,events))
    cold_start=min(e['start_monotonic_ns'] for e in cold);cold_end=max(map(finish,cold))
    action_start=min(e['start_monotonic_ns'] for e in calls);action_end=max(map(finish,calls))
    check_start=min(e['start_monotonic_ns'] for e in checks);check_end=max(map(finish,checks))
    cleanup=task['light_cleanup_timing'];begin=cleanup['start_monotonic_ns'];end=cleanup['end_monotonic_ns']
    assert observed_start<=task['rollout_start_monotonic_ns']<=cold_start
    detail=dict(outside_recorded_events=task['controller_elapsed_ns']-(observed_end-observed_start),
        initial_to_cold=cold_start-observed_start,cold_span=cold_end-cold_start,
        after_cold_setup=action_start-cold_end,actions_and_downloads=action_end-action_start,
        verify_before_checks=check_start-action_end,final_check_calls_span=check_end-check_start,
        after_check_calls=task['rollout_end_monotonic_ns']-check_end,
        before_cleanup=begin-task['rollout_end_monotonic_ns'],cleanup=end-begin,after_cleanup=observed_end-end)
    assert all(v>=0 for v in detail.values()) and sum(detail.values())==task['controller_elapsed_ns']
    groups=dict(initial_and_other=sum(detail[k] for k in ('outside_recorded_events','initial_to_cold','before_cleanup','after_cleanup')),
        cold_span=detail['cold_span'],after_cold_setup=detail['after_cold_setup'],
        actions_and_downloads=detail['actions_and_downloads'],
        final_checks=sum(detail[k] for k in ('verify_before_checks','final_check_calls_span','after_check_calls')),
        cleanup=detail['cleanup'])
    assert sum(groups.values())==task['controller_elapsed_ns']
    final=[]
    for role in task['roles']:
        selected=[next(e for e in checks if label(e)==prefix+role) for prefix in ('source-diff-','oracle-','upload-official-','official-')]
        assert all(finish(a)<=b['start_monotonic_ns'] for a,b in zip(selected,selected[1:]))
        wall=finish(selected[-1])-selected[0]['start_monotonic_ns']
        final.append(dict(role=role,source_diff_ns=selected[0]['duration_ns'],oracle_ns=selected[1]['duration_ns'],
            upload_official_ns=selected[2]['duration_ns'],official_ns=selected[3]['duration_ns'],
            call_gap_ns=wall-sum(e['duration_ns'] for e in selected),path_window_ns=wall,
            start_offset_ns=selected[0]['start_monotonic_ns']-check_start,
            finish_offset_ns=finish(selected[-1])-check_start))
    return dict(detail_ns=detail,groups_ns=groups,controller_ns=task['controller_elapsed_ns'],final_checks=final,
        last_final_check_role=max(final,key=lambda r:r['finish_offset_ns'])['role'],
        boundaries=dict(observed_start=observed_start,observed_end=observed_end,cold_start=cold_start,cold_end=cold_end,
            action_start=action_start,action_end=action_end,check_start=check_start,check_end=check_end,
            cleanup_start=begin,cleanup_end=end))


def clipped_union(events,start,end):
    clipped=[]
    for e in events:
        a=max(start,e['start_monotonic_ns']);b=min(end,finish(e))
        if b>a:clipped.append(dict(start_monotonic_ns=a,duration_ns=b-a))
    return interval_union(clipped)


def analyze(source):
    assert source['status']=='PASS' and source['stage']=='complete' and len(source['runs'])==4
    assert [r['mode'] for r in source['runs']]==['off','on','on','off']
    grouped=dict(pairs=[dict(pair=i,order=[r['mode'] for r in source['runs'] if r['pair']==i],
        runs=[r for r in source['runs'] if r['pair']==i]) for i in (1,2)])
    paths=summarize(grouped,setup_schedule='per-guest-setup')
    phases=[]
    for run in source['runs']:
        raw=ROOT/run['raw'];task=read(raw/'result.json');events=read(raw/'events.json')
        value=controller_phases(task,events);bounds=value['boundaries']
        assert value['controller_ns']==run['timings_ns']['controller']
        assert value['groups_ns']['actions_and_downloads']==run['timings_ns']['frozen_tools_and_downloads_span']
        assert value['groups_ns']['cold_span']==run['timings_ns']['cold_request_span']
        assert value['groups_ns']['cleanup']==run['timings_ns']['cleanup']
        assert task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns']==run['timings_ns']['rollout']
        controls=[e for e in events if label(e).startswith('monitor-') and not label(e).startswith('monitor-collect-')]
        assert len(controls)==(35 if run['mode']=='on' else 0)
        costs=control_breakdown(events)
        assert sum(c['sum_ns'] for c in costs.values())==run['timings_ns']['monitor_control_call_sum']
        assert interval_union(controls)==run['timings_ns']['monitor_control_union']
        windows=dict(before_actions=(bounds['observed_start'],bounds['action_start']),
            actions=(bounds['action_start'],bounds['action_end']),checks=(bounds['action_end'],task['rollout_end_monotonic_ns']),
            after_checks=(task['rollout_end_monotonic_ns'],bounds['observed_end']))
        overlap={k:clipped_union(controls,*v) for k,v in windows.items()}
        assert sum(overlap.values())==interval_union(controls)
        value.update(index=run['index'],pair=run['pair'],mode=run['mode'],management=costs,management_union_by_phase_ns=overlap)
        phases.append(value)
    for pair in paths['pairs']:
        selected=[p for p in phases if p['pair']==pair['pair']]
        off=next(r for r in selected if r['mode']=='off');on=next(r for r in selected if r['mode']=='on')
        delta={k:on['groups_ns'][k]-off['groups_ns'][k] for k in off['groups_ns']}
        assert sum(delta.values())==on['controller_ns']-off['controller_ns']
        pair['controller_phase_difference_ns']=delta
    paths.update(status='PASS_GRPO_LOCAL_OFFLINE_BREAKDOWN',grpo_setup_schedule='per-guest-setup',controller_runs=phases,
        source_scope='Only the four completed current local-no-proxy host+guest runs; two preparation failures excluded',
        residual_scope='Exec minus guest command duration includes boundary reads, runner work, client/API and scheduling; not individually timed',
        controller_scope='Recorded host event boundaries; duration outside first/last recorded event is retained jointly, not assigned to either end')
    return paths


def table(path,values):
    with path.open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(values[0]),delimiter='\t');writer.writeheader();writer.writerows(values)


def report(value,out):
    original_report=Path(os.path.relpath(ROOT/'results/prettier-grpo-local-no-proxy-controls-20261101/REPORT.md',out.resolve())).as_posix()
    lines=['# Prettier七沙箱采样开销：四次实跑的离线分解','',
        '**并发任务变慢主要落在执行调用内部，冷创建和下载未增加。** 四次最后完成的都是late_1；两对这条路径的“执行调用减去原命令时间”分别增加0.785秒和0.695秒。额外检查和采样停止／下载／删除也有增加，按下面的独立窗口分列。','',
        '只读既有四次关→开、开→关结果，沿用原756动作、任务／官方失败和全部计时。未启动服务或沙箱、联网、查询驻留、改采样或读取任务文件。140个执行调用及140个下载均由日志唯一对应到角色和段，不按最近时间猜测。','',
        '## 控制程序的整轮增量可以逐项对上','',
        '| 阶段 | 第一对：关闭／开启／差值（秒） | 第二对：关闭／开启／差值（秒） |',
        '| --- | ---: | ---: |']
    names=dict(initial_and_other='初始化及其余收尾',cold_span='七次创建请求跨度',after_cold_setup='最后创建返回至首个动作调用',
        actions_and_downloads='并发原动作及下载窗口',final_checks='本地核对和额外检查／官方评测',cleanup='采样停止／下载及沙箱删除')
    for key,title in names.items():
        cells=[]
        for pair in value['pairs']:
            off=next(r for r in value['controller_runs'] if r['pair']==pair['pair'] and r['mode']=='off')
            on=next(r for r in value['controller_runs'] if r['pair']==pair['pair'] and r['mode']=='on')
            cells.append(f"{off['groups_ns'][key]/1e9:.6f}／{on['groups_ns'][key]/1e9:.6f}／{pair['controller_phase_difference_ns'][key]/1e9:+.6f}")
        lines.append('| '+title+' | '+' | '.join(cells)+' |')
    lines.append('| **整轮** | '+' | '.join(f"{next(r for r in value['controller_runs'] if r['pair']==p['pair'] and r['mode']=='off')['controller_ns']/1e9:.6f}／{next(r for r in value['controller_runs'] if r['pair']==p['pair'] and r['mode']=='on')['controller_ns']/1e9:.6f}／{sum(p['controller_phase_difference_ns'].values())/1e9:+.6f}" for p in value['pairs'])+' |')
    lines += ['', '按原始纳秒计算，两对分项之和精确等于整轮差值；表格显示值有舍入。创建跨度中可能夹着已启动guest的准备，不是纯服务端创建CPU。第三行仅表示最后创建返回后的准备，不能代表完整准备成本。初始化及其余收尾合并了记录外两端、创建前、本次清理前后的少量时间，不虚构缺失的控制程序绝对起止时刻。11项细分及边界见summary.json。本表不含外层归档准备、输入导入和最终驻留查询。','',
        '## late_1决定了四次并发任务何时结束','',
        '| late_1的分项 | 第一对增量／秒 | 第二对增量／秒 |',
        '| --- | ---: | ---: |']
    critical_names=dict(command_ns='原命令累计',exec_minus_command_min_ns='执行调用内部的非原命令部分',download_ns='下载调用累计',
        gap_min_ns='调用外间隔',start_offset_from_original_window_ns='路径开始相对整批起点的偏移')
    for key,title in critical_names.items():
        lines.append('| '+title+' | '+' | '.join(f"{p['critical_comparison']['components_ns'][key]/1e9:+.6f}" for p in value['pairs'])+' |')
    lines += ['| **并发窗口总差值** | '+' | '.join(f"{p['critical_comparison']['window_difference_ns']/1e9:+.6f}" for p in value['pairs'])+' |','',
        '第二对late_1的原命令更快约0.248秒，但调用内部其余部分增加约0.695秒，最终整批仍更慢。第一对原命令合计只增加约0.046秒；各段本身有正负波动，其中末段变慢、前面部分段变快，不能把单个慢命令直接当采样原因。六段差值完整保留在critical-segments.tsv。','',
        '执行调用指完整host aenv exec，包含guest执行脚本、原命令、客户端／API与调度。扣除guest命令的持续时间后，剩余还包含边界读取、脚本初始化和结束处理；日志未将它们分别计时。下载指完整aenv download，包括打包、传输、解包和客户端工作。没有将host与guest的绝对时钟相减。','',
        '## 管理调用没有落在原动作窗口内','',
        '时间并集是多次调用重叠的部分只计一次。这里的管理调用只指host同步发起的五类操作；任务脚本内部的边界读取和阶段文件写入不在此列，仍包含在执行调用内。','',
        '| 开启轮次 | 35次调用累计／秒 | 时间并集／秒 | 首动作前并集／秒 | 原动作窗口内并集／秒 | 检查期间并集／秒 | 检查后并集／秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for run in value['controller_runs']:
        if run['mode']!='on':continue
        overlap=run['management_union_by_phase_ns'];total=sum(c['sum_ns'] for c in run['management'].values())
        lines.append(f"| {run['index']} | {total/1e9:.6f} | {sum(overlap.values())/1e9:.6f} | "+' | '.join(f'{overlap[k]/1e9:.6f}' for k in ('before_actions','actions','checks','after_checks'))+' |')
    lines += ['', '上传、就绪、阶段标记、结束及下载五类调用，每类七次。七次阶段标记用于官方检查，落在上表检查期间；最后下载到首个检查调用的间隔也包含本地核对和这些标记，不能把它全部算成本地文件读取。并发调用累计与时间并集不是同一个量；即使取并集，它也与其他guest的准备或清理重叠，不能直接从整轮差值扣掉来计算“净采样开销”。后台collector的存活时间与任务重叠，已从这35次阻塞管理调用中排除。各类累计／并集及每实例最终检查见独立表。','',
        '## 现在值得优先优化什么','',
        '优先检查执行脚本重复的边界读取，不先优化下载，也不为这批数据改冷创建调度。开启组每次仍有448条边界meminfo和448条边界vmstat记录，关闭组均没有；late_1每种76条。实际保存的脚本在原命令计时前后执行这些读取，vmstat每次另启动date与awk，因此它们落在上面非原命令部分，而不在工具累计内。','',
        '这给出了明确的优化候选，但当前日志无法算出边界读取自身占了0.785／0.695秒中的多少，也无法分开后台采样、客户端、服务及guest调度的影响。host与guest采样一起开关，因而也不能把本批耗时变化单独归给host采样。','',
        '现有“跳过边界vmstat”选项仅支持旧的先全部创建再准备调度，新普通输入GRPO入口明确拒绝该组合。下一步建议仅离线核对如何在不改变本次调度、0.1秒周期采样、边界meminfo及原任务检查的前提下复用它，以最小接入位置和回归条件明确为完成标准；当前不改运行设置或启动对照。','',
        '## 数据与复算','',
        '表格文件中以_ns结尾的时间列单位为纳秒，报告中的表格已换算为秒。','',
        '- [逐组控制程序阶段](controller-phases.tsv) · [逐对阶段差值](phase-pairs.tsv)',
        '- [四次共28条路径](paths.tsv) · [两对共14条路径差值](path-pairs.tsv)',
        '- [late_1六段差值](critical-segments.tsv) · [采样管理调用分类](management.tsv) · [28条最终检查](final-checks.tsv)',
        '- [完整纳秒、边界、调用对应关系和来源](summary.json)',
        f'- [原四次实跑、采样成本和内存曲线]({original_report})','',
        '复算只读保存结果，输出目录必须不存在：','', '```bash',
        'bash scripts/analyze-grpo-local-no-proxy.sh \\',
        '  --source .artifacts/prettier-grpo-local-no-proxy-controls-20261101/live-3/result.json \\',
        '  --output .artifacts/grpo-local-breakdown-new', '```']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=analyze(read(args.source));out=args.output
    out.mkdir(parents=True,exist_ok=False);result['source']=str(args.source);write(out/'summary.json',result)
    table(out/'controller-phases.tsv',[dict(index=r['index'],pair=r['pair'],mode=r['mode'],
        **{k+'_ns':v for k,v in r['groups_ns'].items()},controller_ns=r['controller_ns']) for r in result['controller_runs']])
    table(out/'phase-pairs.tsv',[dict(pair=p['pair'],**{k+'_ns':v for k,v in p['controller_phase_difference_ns'].items()}) for p in result['pairs']])
    table(out/'paths.tsv',[dict(pair=p['pair'],mode=r['mode'],**{k:v for k,v in path.items() if k!='segments'}) for p in result['pairs'] for r in p['runs'] for path in r['paths']])
    table(out/'path-pairs.tsv',paired_paths(result))
    critical=[]
    for p in result['pairs']:
        off=next(r for r in p['runs'] if r['mode']=='off');on=next(r for r in p['runs'] if r['mode']=='on')
        a=next(r for r in off['paths'] if r['role']==off['critical_role']);b=next(r for r in on['paths'] if r['role']==on['critical_role'])
        assert a['role']==b['role']=='late_1'
        for x,y in zip(a['segments'],b['segments'],strict=True):
            assert x['segment']==y['segment'] and len(x['possible_exec_event_indices'])==len(y['possible_exec_event_indices'])==1
            critical.append(dict(pair=p['pair'],role='late_1',segment=x['segment'],command_difference_ns=y['command_ns']-x['command_ns'],
                exec_residual_difference_ns=(y['exec_min_ns']-y['command_ns'])-(x['exec_min_ns']-x['command_ns']),download_difference_ns=y['download_ns']-x['download_ns']))
    table(out/'critical-segments.tsv',critical)
    table(out/'management.tsv',[dict(index=r['index'],kind=k,**v) for r in result['controller_runs'] for k,v in r['management'].items()])
    table(out/'final-checks.tsv',[dict(index=r['index'],mode=r['mode'],**row) for r in result['controller_runs'] for row in r['final_checks']])
    report(result,out)
    print('status='+result['status']+' result_dir='+str(out))


if __name__=='__main__':main()
