"""Recompute instruction-loop calibration and settled Exp1 diagnostic counters."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from run_exp1_monitor_balanced import PATCH


def verify_counters(rows,tids):
    assert len(rows)==2*len(tids)
    assert {(r['tid'],r['scope']) for r in rows}=={(t,s) for t in tids for s in ('host','guest')}
    for r in rows:
        assert r['enabled_ns']>0 and .95<=r['running_ns']/r['enabled_ns']<=1
        assert r['cycles']>=0 and r['instructions']>=0


def loop_errors(phases):
    assert [r['iterations'] for r in phases]==[0,1000000000,2000000000,1000000000,0]
    baseline=(phases[0]['guest_instructions']+phases[-1]['guest_instructions'])/2
    return [100*((r['guest_instructions']-baseline)/(2*r['iterations'])-1) for r in phases[1:4]]


def audit(directory):
    result=json.loads((directory/'result.json').read_text())
    assert result['status']=='PASS' and result['checks']['cleanup']=='PASS'
    assert json.loads((directory/'list-after.stdout').read_text())==[]
    native=json.loads((directory/'native.json').read_text())
    assert sum(r['instructions'] for r in native['counters'] if r['scope']=='guest')==0
    assert sum(r['instructions'] for r in native['counters'] if r['scope']=='host')>0
    phases=[]
    for i,phase in enumerate(result['phases'],1):
        name=f"step-{phase['step']}-counters" if result.get('exp1') else f'counts-{i}'
        original=json.loads((directory/(name+'.json')).read_text())
        assert phase['counters']==original['counters']
        verify_counters(phase['counters'],result['identity']['tids'])
        instructions=sum(r['instructions'] for r in phase['counters'] if r['scope']=='guest')
        assert instructions==phase['guest_instructions']
        if result.get('exp1'):
            step=phase['step']
            times=list(map(float,(directory/f'replay/cpu-{step}-time.tsv').read_text().split()))
            assert times==[phase['cpu_time'][k] for k in ('wall_seconds','user_seconds','system_seconds')]
            cycles=sum(r['cycles'] for r in phase['counters'] if r['scope']=='guest')
            assert cycles==phase['guest_cycles']
            phases.append(dict(step=step,guest_instructions=instructions,guest_cycles=cycles,
                vm_guest_cycles_per_instruction=cycles/instructions,
                tool_call_seconds=phase['duration_ns']/1e9,action_wall_seconds=times[0],action_cpu_seconds=sum(times[1:]),
                counter_coverage_min=min(r['running_ns']/r['enabled_ns'] for r in phase['counters'])))
    if result.get('exp1'):
        for phase,summary in zip(result['phases'],phases):
            guest=[r for r in phase['counters'] if r['scope']=='guest']
            if all('reference_cycles' in r for r in guest):
                reference=sum(r['reference_cycles'] for r in guest)
                assert reference>0
                summary['guest_reference_cycles']=reference
                summary['cycles_per_reference_cycle']=summary['guest_cycles']/reference
            if 'host_runtime_delta_ns' in phase:
                raw=json.loads((directory/f"step-{phase['step']}-counters.json").read_text())
                assert raw['host_runtime_delta_ns']==phase['host_runtime_delta_ns']
                assert all(v>=0 for v in phase['host_runtime_delta_ns'].values())
                summary['host_vcpu_runtime_seconds']=sum(phase['host_runtime_delta_ns'].values())/1e9
            if 'scheduler_wait' in phase:
                wait=json.loads((directory/f"step-{phase['step']}-counters-wait.json").read_text())
                assert wait==phase['scheduler_wait'] and wait['status']=='PASS'
                assert {r['tid'] for r in wait['threads']}==set(result['identity']['tids'])
                summary['observed_runnable_wait_seconds']=sum(r['runnable_wait_ns'] for r in wait['threads'])/1e9
                summary['wait_window_scope']=wait['scope']
        with (directory/'replay/steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
        assert [s['step'] for s in steps]==[f'{i:03}' for i in range(1,27)]
        assert {s['step']:int(s['exit_code']) for s in steps if int(s['exit_code'])}=={'008':127,'016':127}
        assert hashlib.sha256((directory/'replay/patch.diff').read_bytes()).hexdigest()==PATCH
        check=json.loads((directory/'independent.stdout').read_text())
        assert set(check)=={'svg','nested_svg','html_control'}
        assert all(x['javascript_formatted'] and x['idempotent'] for x in check.values())
        return dict(kind='exp1',status='PASS',run_id=directory.name,phases=phases,settle_seconds=result['settle_seconds'])
    errors=loop_errors(result['phases'])
    assert all(abs(e)<10 for e in errors)
    assert errors==result['calibration']['errors_percent']
    return dict(kind='scope_calibration',status='PASS',run_id=directory.name,errors_percent=errors)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    rows=[audit(p.resolve()) for p in args.directories]
    summaries={}
    for step in ('023','024'):
        chosen=[p for r in rows if r['kind']=='exp1' for p in r['phases'] if p['step']==step]
        if chosen:
            summaries[step]={key:dict(minimum=min(p[key] for p in chosen),maximum=max(p[key] for p in chosen),
                max_vs_min_percent=100*(max(p[key] for p in chosen)/min(p[key] for p in chosen)-1))
                for key in ('guest_instructions','guest_cycles','vm_guest_cycles_per_instruction','action_wall_seconds','action_cpu_seconds')}
    for step in ('023','024'):
        chosen=[p for r in rows if r['kind']=='exp1' for p in r['phases'] if p['step']==step]
        for key in ('guest_reference_cycles','cycles_per_reference_cycle','host_vcpu_runtime_seconds','observed_runnable_wait_seconds'):
            if chosen and all(key in p for p in chosen):
                lo=min(p[key] for p in chosen);hi=max(p[key] for p in chosen)
                summaries[step][key]=dict(minimum=lo,maximum=hi,max_vs_min_percent=100*(hi/lo-1) if lo else None)
    result=dict(status='PASS',runs=rows,ranges=summaries,
                limitations=['diagnostic only; no monitoring overhead acceptance','VM-wide counters include tool handling and background guest execution',
                             'action CPU time has a narrower scope than VM cycles; dividing the two does not measure exact frequency',
                             '10-second settling is a configuration variant, not proof of quiescence or attribution to a particular guest service',
                             'full official task tests NOT_RUN'])
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['# 等待启动后的 Exp1 硬件计数诊断','',
           '原有内存采样关闭；另行开启 CPU 诊断。每次新建 2 vCPU / 4 GiB 沙箱，工具通道可用后等待 10 秒。执行 26 步固定动作，在第 023、024 步工具调用前后读取本次 VM 两个 vCPU 的 guest/host 分离计数。','',
           '| 运行 | 步骤 | 实际耗时 (ms) | 动作 CPU 合计 (ms) | VM guest 指令 (十亿) | VM guest 周期 (十亿) |',
           '| --- | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        for p in row.get('phases',[]):
            lines.append(f"| {row['run_id']} | {p['step']} | {1000*p['action_wall_seconds']:.0f} | {1000*p['action_cpu_seconds']:.0f} | {p['guest_instructions']/1e9:.3f} | {p['guest_cycles']/1e9:.3f} |")
    lines+=['','以上是诊断配置的观察值，不能与原先不等待的整段轨迹直接作公平性能比较。guest 指令数是整个 VM 在工具调用期间的执行量，并非 Node 进程单独的指令数。CPU 时间属于动作进程及子进程，两者边界不同，不将周期除以该时间当作精确频率。',
            '完整轨迹、最终 patch、独立 SVG 和清理检查通过；完整官方测试未运行。频率、缓存和并行执行如何影响剩余差异，尚不能从这些计数直接确定。','']
    if any('cycles_per_reference_cycle' in p for r in rows for p in r.get('phases',[])):
        lines += ['| 运行 | 步骤 | 周期/参考周期 | 两个 vCPU 运行合计 (ms) | 观测到的可运行等待合计 (ms) |',
                  '| --- | --- | ---: | ---: | ---: |']
        for row in rows:
            for p in row.get('phases',[]):
                if 'observed_runnable_wait_seconds' in p:
                    lines.append(f"| {row['run_id']} | {p['step']} | {p['cycles_per_reference_cycle']:.3f} | {1000*p['host_vcpu_runtime_seconds']:.1f} | {1000*p['observed_runnable_wait_seconds']:.2f} |")
        lines += ['', '周期/参考周期是同一 guest 计数范围内的频率指标，未换算为 GHz。运行合计来自宿主机线程 schedstat 的运行计数，包括 VM exit 处理；它不是两个 vCPU 的端到端时间。等待由筛选本次线程的 sched_switch/sched_wakeup 事件计算，仅含可运行排队，不含睡眠。其窗口比硬件计数略宽，起点前已经开始且没有被观察到的排队区间无法重建。', '']
    (args.output/'report.md').write_text('\n'.join(lines))
    print(f'status=PASS result_dir={args.output}')


if __name__=='__main__':main()
