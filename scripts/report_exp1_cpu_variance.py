"""Offline CPU diagnostic summary; no performance acceptance or causal correction."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
from run_exp1_monitor_balanced import PATCH


def report(directory):
    result=json.loads((directory/'result.json').read_text())
    assert result['status']=='PASS'
    rows=[]
    for i,run in enumerate(result['runs'],1):
        raw=Path(run['raw_dir'])
        audit=Path(run['audit_dir'])
        checks=json.loads((audit/'checks.json').read_text())
        assert checks==run['checks'] and checks['cleanup']=='PASS'
        assert checks['host_sampling']==checks['guest_sampling']=='DISABLED'
        assert hashlib.sha256((raw/'replay/patch.diff').read_bytes()).hexdigest()==PATCH
        with (raw/'replay/steps.tsv').open() as f: steps=list(csv.DictReader(f,delimiter='\t'))
        assert [s['step'] for s in steps]==[f'{n:03}' for n in range(1,27)]
        assert {s['step']:int(s['exit_code']) for s in steps if int(s['exit_code'])}=={'008':127,'016':127}
        host=json.loads((directory/f'run-{i:02}-off/host-cpu.json').read_text())
        assert host['status']=='PASS' and host['identity']=='MMDS instanceID and service cgroup match'
        assert host['sandbox_id']==(raw/'sandbox-id.txt').read_text().strip()
        all_threads=[t for sample in host['samples'] for t in sample['threads']]
        tids=sorted({t['tid'] for t in all_threads})
        assert len(tids)==2
        locations={str(tid):sorted({t['cpu'] for t in all_threads if t['tid']==tid}) for tid in tids}
        frequencies=[t['reported_frequency_khz'] for t in all_threads if t['reported_frequency_khz'] is not None]
        row=dict(number=i,checks=checks,steps={},task_window_ms=run['task_window_ns']/1e6,
                 host_vcpu_count=len(tids),vcpu_observed_cpu_counts=[len(v) for v in locations.values()],
                 frequency_scope=host['frequency_scope'],host_observed_frequency_khz_range=[min(frequencies),max(frequencies)],
                 sample_count=len(host['samples']),collection_ms_median=statistics.median(s['collection_ns']/1e6 for s in host['samples']),
                 collection_ms_max=max(s['collection_ns']/1e6 for s in host['samples']),
                 scheduler_wait_valid=host['scheduler_wait_valid'])
        for step in ('023','024'):
            timing=dict(zip(('wall_seconds','user_seconds','system_seconds'),map(float,(raw/f'replay/cpu-{step}-time.tsv').read_text().split())))
            assert timing==run['cpu_times'][step]
            timing['cpu_seconds']=timing['user_seconds']+timing['system_seconds']
            row['steps'][step]=timing
        rows.append(row)
    summary=dict(status='PASS',run_count=len(rows),runs=rows,
                 interpretation='diagnostic instrumentation; no 5% overhead decision; summed CPU time can exceed wall time for multithreaded Node',
                 frequency_causality='NOT_VALIDATED; samples include idle/waiting phases, clocks not aligned between guest and host',
                 scheduler_wait='NOT_MEASURED: sched_schedstats disabled',independent_task_tests='NOT_RUN')
    (directory/'cpu-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    lines=['# Exp1 CPU 执行波动诊断','',
           '原有 guest/host 内存采样关闭，但启用了专门的 CPU 诊断，因此不能把本批当作无监控性能基线。动作内容、输出分流和预期退出码保留。',
           '虚拟机归属由服务资源组与 MMDS 的 instanceID 双重确认；MMDS 原文不落盘。每 50 ms 尝试读取两个 vCPU 的运行位置和所在 CPU 的频率读数；实际间隔还包括采集耗时。', '',
           '| 次数 | 步骤 | 实际耗时 (ms) | 用户态 CPU (ms) | 内核态 CPU (ms) | CPU 合计 (ms) |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        for step,t in row['steps'].items():
            lines.append(f"| {row['number']} | {step} | {1000*t['wall_seconds']:.0f} | {1000*t['user_seconds']:.0f} | {1000*t['system_seconds']:.0f} | {1000*t['cpu_seconds']:.0f} |")
    lines+=['','Node 有多个线程，CPU 合计可以超过实际耗时，不能用两者相减当成等待时间。完整调度等待统计未开启，等待量标记为未测量。',
            'CPU 频率是线程最后运行位置的 scaling_cur_freq 读数，覆盖任务和空闲阶段，不是该线程执行指令时的有效频率。guest 与 host 时钟未校准，不能按时间戳直接把频率关联到单个动作并作因果判断。',
            '每次 26 步动作顺序、预期失败、关键输出、patch 和清理通过；官方完整测试未运行。','']
    (directory/'cpu-report.md').write_text('\n'.join(lines))
    print(f'status=PASS runs={len(rows)} result_dir={directory}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory.resolve())
