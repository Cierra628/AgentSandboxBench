"""Recompute Git command regions and guest counters for frozen step 025."""
import argparse
import hashlib
import json
from pathlib import Path
from report_fixed_frequency_monitor import report as audit_fixed
from owned_thread_wait import host_wait_bound


def commands(events):
    sessions={}
    for event in events:
        sid=event['sid']
        if event['event']=='start':
            assert sid not in sessions
            sessions[sid]=dict(argv=event['argv'],regions=[],data=[])
        if event['event']=='region_leave':
            sessions[sid]['regions'].append({k:event[k] for k in ('category','label','t_rel','nesting')})
        if event['event']=='data':
            sessions[sid]['data'].append({k:event[k] for k in ('category','key','value')})
        if event['event']=='exit':
            assert event['code']==0
            sessions[sid]['wall_seconds']=event['t_abs']
    result=list(sessions.values())
    assert [x['argv'] for x in result]==[['git','add','-A'],['git','diff','--cached']]
    assert all('wall_seconds' in x for x in result)
    return result


def counters(path):
    return {row.split()[0]:int(row.split()[1]) for row in path.read_text().splitlines()}


def report(directory):
    directory=Path(directory).resolve()
    result=json.loads((directory/'result.json').read_text());assert result['git_diagnostic']
    audit=audit_fixed(directory)
    rows=[]
    for row in result['runs']:
        raw=Path(row['raw_dir']);replay=raw/'replay'
        events=[json.loads(v) for v in (replay/'git-025-events.jsonl').read_text().splitlines()]
        cmd=commands(events)
        wall,user,system=map(float,(replay/'git-025-time.tsv').read_text().split())
        before=counters(replay/'git-025-before.vmstat');after=counters(replay/'git-025-after.vmstat')
        cpu_before=list(map(int,(replay/'git-025-before.stat').read_text().splitlines()[0].split()[1:]))
        cpu_after=list(map(int,(replay/'git-025-after.stat').read_text().splitlines()[0].split()[1:]))
        rows.append(dict(position=row['position'],label=row['label'],raw_dir=str(raw),wall_seconds=wall,user_seconds=user,system_seconds=system,wall_minus_summed_cpu_seconds=wall-user-system,commands=cmd,vmstat_delta={k:after[k]-before[k] for k in ('pgfault','pgmajfault','pgpgin','pgpgout','pswpin','pswpout')},guest_iowait_ticks_delta=cpu_after[4]-cpu_before[4],stdout_sha256=hashlib.sha256((replay/'stdout/025.log').read_bytes()).hexdigest()))
    if result.get('git_clock'):
        for row in rows:
            clock=json.loads((Path(row['raw_dir'])/'replay/git-025-clocks.json').read_text())
            assert clock['exit_code']==0 and clock['monotonic_raw_ns']>0
            row['command_clock']=clock
            row['realtime_minus_monotonic_ms']=(clock['realtime_ns']-clock['monotonic_raw_ns'])/1e6
        assert len(rows)==len(result['runs'])
    if result.get('host_wait'):
        for row,source in zip(rows,result['runs']):
            row['host_wait_bound']=host_wait_bound(Path(row['raw_dir']),source['frequency'])
    summary=dict(status='PASS',runs=rows,all_step_stdout_equal=len({x['stdout_sha256'] for x in rows})==1,scope='Git diagnostic only',limitations=['Trace overhead unquantified; nested region durations overlap and cannot be summed','Whole-action user/system time includes Git worker threads; wall minus CPU is not exact blocked time','Guest iowait and vmstat cover the VM and do not attribute per-process I/O','No host runnable-wait trace or per-process syscall trace in this batch; do not exclude unmeasured waits','Full official task tests NOT_RUN'])
    if result.get('git_clock'):
        summary['clock_check']=dict(max_abs_realtime_minus_monotonic_ms=max(abs(r['realtime_minus_monotonic_ms']) for r in rows),scope='same C wrapper interval around timeout/bash; excludes shell setup and outer date calls')
    if result.get('host_wait'):
        summary['limitations'][3]='Host runnable wait measured over the full capture; step 025 is bounded, not precisely attributed; no per-process syscall trace'
    (directory/'git-report.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# 第 025 步 Git 诊断','', '| 次序 | 动作耗时 (ms) | user+system (ms) | git add (ms) | git diff (ms) | guest iowait ticks |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:lines.append(f"| {row['position']} | {row['wall_seconds']*1000:.0f} | {(row['user_seconds']+row['system_seconds'])*1000:.0f} | {row['commands'][0]['wall_seconds']*1000:.1f} | {row['commands'][1]['wall_seconds']*1000:.1f} | {row['guest_iowait_ticks_delta']} |")
    lines+=['','原冻结动作未修改。Git 自带跟踪写入独立文件，记录 CPU 时间和 VM 整体计数；采集开销尚未量化，不能纳入监控性能验收。Git 各嵌套阶段时间可能重叠，不相加当总耗时。','']
    if result.get('git_clock'):
        lines += [f"同一区间实时时钟与单调时钟的最大差值为 {summary['clock_check']['max_abs_realtime_minus_monotonic_ms']:.6f} ms。它覆盖命令包装区间，不覆盖外层 date 调用和全部任务窗口。",'']
    if result.get('host_wait'):
        lines += ['宿主机等待探针覆盖整个任务窗口及部分准备、停止时间。以下为两个本次虚拟 CPU 的等待合计，它只是第 025 步可运行排队等待的上界，不能当作该步骤实测等待或任务总耗时。初始未记录的排队区间已通过任务开始前的非运行状态检查限定在窗口外。','']
        for row in rows:
            lines += [f"第 {row['position']} 次：等待合计上界 {row['host_wait_bound']['summed_runnable_wait_upper_bound_ns']/1e6:.3f} ms。"]
        lines += ['','该探针不覆盖睡眠、guest 文件 I/O/锁或宿主机 I/O 服务线程；未测部分不能记为零。','']
    (directory/'git-report.md').write_text('\n'.join(lines))
    print(f'status=PASS result_dir={directory}')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory)


if __name__=='__main__':main()
