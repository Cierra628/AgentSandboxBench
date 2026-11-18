"""Decompose saved keep/skip boundary-vmstat controls; no live operations."""
import argparse
import os
from pathlib import Path

from analyze_grpo_path_costs import ROOT,read,write,rows,analyze_run
from analyze_grpo_local_no_proxy import controller_phases,clipped_union,label,table
from report_grpo_create_setup_repeats import control_breakdown
from run_grpo_light_pair import interval_union
from run_grpo_local_no_proxy_controls import comparisons

COMPONENTS=dict(command_ns='原命令累计',exec_minus_command_min_ns='执行调用减原命令',
    download_ns='下载调用',gap_min_ns='调用外间隔',start_offset_from_original_window_ns='路径开始相对整批起点的偏移')
PHASES=dict(initial_and_other='初始化及其余收尾',cold_span='七次创建请求跨度',
    after_cold_setup='最后创建返回至首个动作调用',actions_and_downloads='并发动作及下载窗口',
    final_checks='本地核对及额外检查／官方评测',cleanup='采样停止／下载及沙箱删除')


def critical_difference(keep,skip):
    a=next(p for p in keep['paths'] if p['role']==keep['critical_role'])
    b=next(p for p in skip['paths'] if p['role']==skip['critical_role'])
    assert a['exec_assignment']==b['exec_assignment']=='UNIQUE'
    delta={k:b[k]-a[k] for k in COMPONENTS}
    assert sum(delta.values())==skip['window_ns']-keep['window_ns']
    return dict(keep_role=a['role'],skip_role=b['role'],components_ns=delta,
        window_difference_ns=skip['window_ns']-keep['window_ns'])


def analyze(source):
    assert source['status']=='PASS' and source['stage']=='complete'
    assert source['comparison']=='boundary-vmstat' and source['source_restored_stopped']
    assert not source['remaining_firecrackers'] and not source['remaining_running_containers']
    assert comparisons(source['runs'],comparison='boundary-vmstat')==source['pairs']
    assert len({g for r in source['runs'] for g in r['guest_ids']})==28
    runs=[]
    for saved in source['runs']:
        raw=ROOT/saved['raw'];task=read(raw/'result.json');events=read(raw/'events.json')
        variant=saved['boundary_vmstat_mode'];skip=variant=='skip'
        assert task.get('light_skip_boundary_vmstat',False)==skip
        value=analyze_run(raw,'on',setup_schedule='per-guest-setup',skip_boundary_vmstat=skip)
        assert value['window_ns']==saved['timings_ns']['frozen_tools_and_downloads_span']
        assert value['command_sum_ns']==saved['timings_ns']['tool_sum']
        assert value['exec_calls']==value['unique_exec_assignments']==35
        assert value['boundary_memory_rows']==448 and value['boundary_vmstat_rows']==(0 if skip else 448)
        phases=controller_phases(task,events);bounds=phases['boundaries']
        assert phases['controller_ns']==saved['timings_ns']['controller']
        assert phases['groups_ns']['actions_and_downloads']==value['window_ns']
        assert phases['groups_ns']['cold_span']==saved['timings_ns']['cold_request_span']
        assert phases['groups_ns']['cleanup']==saved['timings_ns']['cleanup']
        assert task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns']==saved['timings_ns']['rollout']
        controls=[e for e in events if label(e).startswith('monitor-') and not label(e).startswith('monitor-collect-')]
        assert len(controls)==35
        costs=control_breakdown(events)
        assert sum(c['sum_ns'] for c in costs.values())==saved['timings_ns']['monitor_control_call_sum']
        assert interval_union(controls)==saved['timings_ns']['monitor_control_union']
        windows=dict(before_actions=(bounds['observed_start'],bounds['action_start']),
            actions=(bounds['action_start'],bounds['action_end']),
            checks=(bounds['action_end'],task['rollout_end_monotonic_ns']),
            after_checks=(task['rollout_end_monotonic_ns'],bounds['observed_end']))
        overlap={k:clipped_union(controls,*v) for k,v in windows.items()}
        assert sum(overlap.values())==interval_union(controls)
        value.update(index=saved['index'],pair=saved['pair'],boundary_vmstat_mode=variant,
            controller=phases,management=costs,management_union_by_phase_ns=overlap)
        runs.append(value)
    pairs=[];path_pairs=[];segment_pairs=[];command_pairs=[]
    for number in (1,2):
        keep=next(r for r in runs if r['pair']==number and r['boundary_vmstat_mode']=='keep')
        skip=next(r for r in runs if r['pair']==number and r['boundary_vmstat_mode']=='skip')
        critical=critical_difference(keep,skip)
        delta={k:skip['controller']['groups_ns'][k]-keep['controller']['groups_ns'][k] for k in PHASES}
        assert sum(delta.values())==skip['controller']['controller_ns']-keep['controller']['controller_ns']
        pairs.append(dict(pair=number,order=[r['boundary_vmstat_mode'] for r in runs if r['pair']==number],
            critical_comparison=critical,controller_phase_difference_ns=delta))
        for a in keep['paths']:
            b=next(p for p in skip['paths'] if p['role']==a['role'])
            assert a['steps']==b['steps'] and a['exec_assignment']==b['exec_assignment']=='UNIQUE'
            path_pairs.append(dict(pair=number,role=a['role'],steps=a['steps'],
                keep_path_ns=a['path_window_ns'],skip_path_ns=b['path_window_ns'],
                path_difference_ns=b['path_window_ns']-a['path_window_ns'],
                **{k.replace('_ns','_difference_ns'):b[k]-a[k] for k in COMPONENTS},
                critical_keep=a['role']==keep['critical_role'],critical_skip=b['role']==skip['critical_role']))
        # Segment/command differences below compare the same critical role, not different paths.
        assert critical['keep_role']==critical['skip_role']
        role=critical['keep_role'];a=next(p for p in keep['paths'] if p['role']==role);b=next(p for p in skip['paths'] if p['role']==role)
        for x,y in zip(a['segments'],b['segments'],strict=True):
            assert x['segment']==y['segment']
            segment=x['segment']
            segment_pairs.append(dict(pair=number,role=role,segment=segment,
                command_difference_ns=y['command_ns']-x['command_ns'],
                exec_residual_difference_ns=(y['exec_min_ns']-y['command_ns'])-(x['exec_min_ns']-x['command_ns']),
                download_difference_ns=y['download_ns']-x['download_ns']))
            xs=rows(ROOT/keep['raw']/'rollouts'/role/segment/'steps.tsv')
            ys=rows(ROOT/skip['raw']/'rollouts'/role/segment/'steps.tsv')
            actions=rows(ROOT/keep['raw']/'rollouts'/role/segment/'actions.tsv')
            assert actions==rows(ROOT/skip['raw']/'rollouts'/role/segment/'actions.tsv')
            commands={c['step']:c['command'] for c in actions}
            assert len(commands)==len(actions)==len(xs)
            for u,v in zip(xs,ys,strict=True):
                assert {k:z for k,z in u.items() if k!='duration_ns'}=={k:z for k,z in v.items() if k!='duration_ns'}
                command_pairs.append(dict(pair=number,role=role,segment=segment,step=u['step'],command=commands[u['step']],exit_code=u['exit_code'],
                    keep_ns=int(u['duration_ns']),skip_ns=int(v['duration_ns']),difference_ns=int(v['duration_ns'])-int(u['duration_ns'])))
        assert sum(c['difference_ns'] for c in command_pairs if c['pair']==number)==critical['components_ns']['command_ns']
        for k,c in (('command_difference_ns','command_ns'),('exec_residual_difference_ns','exec_minus_command_min_ns'),('download_difference_ns','download_ns')):
            assert sum(s[k] for s in segment_pairs if s['pair']==number)==critical['components_ns'][c]
    return dict(status='PASS_BOUNDARY_VMSTAT_OFFLINE_BREAKDOWN',comparison='boundary-vmstat',runs=runs,pairs=pairs,
        path_pairs=path_pairs,critical_segments=segment_pairs,critical_commands=command_pairs,
        exec_calls=sum(r['exec_calls'] for r in runs),unique_exec_assignments=sum(r['unique_exec_assignments'] for r in runs),
        downloads=sum(r['exec_calls'] for r in runs),new_live_guests=0,new_live_actions=0,
        residual_scope='Whole exec minus guest command durations: boundary reads, runner work, API/client and scheduling; not individually timed',
        clock_scope='Host monotonic event windows and guest duration sums; no cross-clock absolute subtraction')


def report(value,out):
    link=os.path.relpath(ROOT/'results/grpo-local-vmstat-controls-20261101/REPORT.md',out.resolve())
    roles=sorted({p['critical_comparison'][key] for p in value['pairs'] for key in ('keep_role','skip_role')})
    role_text='、'.join(roles)
    reduced=all(p['critical_comparison']['components_ns']['exec_minus_command_min_ns']<0 for p in value['pairs'])
    finding='关键路径的执行调用余量两对都减少。' if reduced else '关键路径的执行调用余量变化见下表。'
    lines=['# 跳过边界vmstat：两对结果为何方向不同','',
        '**'+finding+'** 这里的余量指完整执行调用扣除原命令用时后的部分。原命令时间和调用余量共同决定并发用时。保持原默认，跳过保留为可选项；这些观察没有单独计时vmstat自身，不能换算为它的固定收益。','',
        '只读刚完成的保留→跳过、跳过→保留四次日志。两边都为0.1秒采样、原七沙箱GRPO、逐guest准备和默认回收。140次执行调用和140次下载均唯一对应到角色与段，全部分项按纳秒精确加和。没有启动服务或沙箱、联网、驻留查询或设置改动。','',
        '## 并发动作窗口的差值来自哪里','',
        '下表统一为跳过减保留，负数表示耗时更少。四次最后完成的角色为'+role_text+'；关键路径是决定整批结束时间的那条路径。','',
        '| 关键路径分项 | 第一对／秒 | 第二对／秒 |','| --- | ---: | ---: |']
    for key,title in COMPONENTS.items():lines.append('| '+title+' | '+' | '.join(f"{p['critical_comparison']['components_ns'][key]/1e9:+.6f}" for p in value['pairs'])+' |')
    lines+=['| **并发窗口差值** | '+' | '.join(f"{p['critical_comparison']['window_difference_ns']/1e9:+.6f}" for p in value['pairs'])+' |','']
    for p in value['pairs']:
        d=p['critical_comparison']['components_ns']
        lines.append(f"第{p['pair']}对原命令变化{d['command_ns']/1e9:+.6f}秒，调用余量{d['exec_minus_command_min_ns']/1e9:+.6f}秒，下载{d['download_ns']/1e9:+.6f}秒，加调用外间隔及起点偏移后，整批变化{p['critical_comparison']['window_difference_ns']/1e9:+.6f}秒。")
    lines+=['', '工具累计是七条并发路径的命令时间之和；其他路径可以在关键路径变快时同时变慢。执行调用包含guest执行脚本、原命令、客户端／API和调度。减去原命令持续时间后，还保留边界读取、脚本初始化和结束处理等。日志没有单独测量这些操作，无法确认各项占比，也不能排除系统调度波动。','',
        '### 哪些原动作变化较大','',
        '以下列出每对关键路径中绝对变化最大的三个动作；完整各段和每个动作都保留在表中。step为原段内编号，没有更改原命令或退出码。','',
        '| 对照 | 段／step | 保留／秒 | 跳过／秒 | 差值／秒 |','| --- | --- | ---: | ---: | ---: |']
    for number in (1,2):
        for c in sorted((c for c in value['critical_commands'] if c['pair']==number),key=lambda c:abs(c['difference_ns']),reverse=True)[:3]:
            lines.append(f"| {number} | {c['segment']}／{c['step']} | {c['keep_ns']/1e9:.6f} | {c['skip_ns']/1e9:.6f} | {c['difference_ns']/1e9:+.6f} |")
    lines+=['','## 整轮差值还包含检查及准备','',
        '| 阶段 | 第一对：保留／跳过／差值 秒 | 第二对：保留／跳过／差值 秒 |','| --- | ---: | ---: |']
    for key,title in PHASES.items():
        cells=[]
        for p in value['pairs']:
            a=next(r for r in value['runs'] if r['pair']==p['pair'] and r['boundary_vmstat_mode']=='keep')['controller']
            b=next(r for r in value['runs'] if r['pair']==p['pair'] and r['boundary_vmstat_mode']=='skip')['controller']
            cells.append(f"{a['groups_ns'][key]/1e9:.6f}／{b['groups_ns'][key]/1e9:.6f}／{p['controller_phase_difference_ns'][key]/1e9:+.6f}")
        lines.append('| '+title+' | '+' | '.join(cells)+' |')
    lines+=['| **整轮差值** | '+' | '.join(f"{sum(p['controller_phase_difference_ns'].values())/1e9:+.6f}" for p in value['pairs'])+' |','',
        '初始化及其余收尾合并记录外两端、创建前和清理前后的少量时间，不虚构控制程序绝对起止点。创建跨度内可穿插其他guest准备；最后创建后的准备不等于完整准备成本。整轮不含外层来源准备、输入导入或最终驻留查询。各组35次采样管理调用在原动作窗口内的时间并集均为零，调用累计和并发时间并集另列，不能直接从整轮扣除。','',
        '## 结论与下一步','',
        '此次差值已能对应：'+finding+'整批是否更快还受原命令及检查等变化影响。已有证据足以保留可选跳过功能；不足以将本批整轮百分比作为稳定采样收益。继续保持原默认，不为改善百分比追加实跑。','',
        '下一步建议只离线核对448次边界meminfo的输出使用位置，判断是否存在不影响峰值和阶段记录的重复Shell工作；以保留条件及可减少的具体操作明确为完成条件，不改变设置或启动新实验。','',
        '## 数据与复算','',
        '所有表格的_ns列均为纳秒，差值均为跳过减保留。','',
        '- [四次控制程序阶段](controller-phases.tsv) · [逐对阶段差值](phase-pairs.tsv)',
        '- [28条路径](paths.tsv) · [14条路径差值](path-pairs.tsv)',
        '- [关键路径六段差值](critical-segments.tsv) · [逐动作差值](critical-commands.tsv)',
        '- [管理调用分类及阶段并集](management.tsv) · [完整原精度数据](summary.json)',
        f'- [原四次任务、采样及内存曲线]({link})','',
        '复算只读保存日志，输出目录必须不存在：','', '```bash',
        'bash scripts/analyze-grpo-boundary-vmstat.sh \\',
        '  --source .artifacts/grpo-local-vmstat-controls-20261101/result.json \\',
        '  --output .artifacts/grpo-local-vmstat-breakdown-new','```']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


def publish(source,out):
    value=analyze(read(source));out.mkdir(parents=True,exist_ok=False)
    value['source']=str(source);write(out/'summary.json',value)
    table(out/'controller-phases.tsv',[dict(index=r['index'],pair=r['pair'],boundary_vmstat_mode=r['boundary_vmstat_mode'],
        **{k+'_ns':v for k,v in r['controller']['groups_ns'].items()},controller_ns=r['controller']['controller_ns']) for r in value['runs']])
    table(out/'phase-pairs.tsv',[dict(pair=p['pair'],**{k+'_ns':v for k,v in p['controller_phase_difference_ns'].items()}) for p in value['pairs']])
    table(out/'paths.tsv',[dict(index=r['index'],pair=r['pair'],boundary_vmstat_mode=r['boundary_vmstat_mode'],
        **{k:v for k,v in p.items() if k!='segments'}) for r in value['runs'] for p in r['paths']])
    for name,key in (('path-pairs','path_pairs'),('critical-segments','critical_segments'),('critical-commands','critical_commands')):
        table(out/(name+'.tsv'),value[key])
    table(out/'management.tsv',[dict(index=r['index'],boundary_vmstat_mode=r['boundary_vmstat_mode'],kind=k,**c,
        **{phase+'_union_ns':n for phase,n in r['management_union_by_phase_ns'].items()}) for r in value['runs'] for k,c in r['management'].items()])
    report(value,out)
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=publish(args.source,args.output)
    print('status='+value['status']+' result_dir='+str(args.output))


if __name__=='__main__':main()
