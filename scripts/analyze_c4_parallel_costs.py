"""Offline decomposition of the six saved C4 runs; no live system queries."""
import argparse
import csv
import json
from pathlib import Path
import statistics

NS=10**9
HEAVY=(25,26,27,30,31,32,33)
LABELS={25:'整份C4压缩和约束检查',26:'整份解压、内容和目录校验',27:'重复解压／压缩及幂等检查',
        30:'整份压缩和损坏测试（大偏移）',31:'整份压缩和损坏测试（偏移1000）',
        32:'整份压缩和损坏测试（偏移20000）',33:'最终整份压缩、解压和逐文件校验'}
CONTROL={'monitor-upload-bundle','monitor-ready','monitor-finish','monitor-download'}


def read(path):return json.loads(Path(path).read_text())
def rows(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def thin_pss(points,seconds):
    """Select existing PSS points starting at the first; not a new sampling run."""
    points=[p for p in points if p['pss_kib']!='']
    assert points and seconds>0
    assert all(int(a['monotonic_ns'])<int(b['monotonic_ns']) for a,b in zip(points,points[1:]))
    selected=[];due=0
    for point in points:
        now=int(point['monotonic_ns'])
        if now>=due:selected.append(point);due=now+int(seconds*NS)
    peak=max(int(p['pss_kib']) for p in points);retained=max(int(p['pss_kib']) for p in selected)
    return dict(interval_seconds=seconds,original_points=len(points),retained_points=len(selected),
                original_peak_mib=peak/1024,retained_peak_mib=retained/1024,
                observed_peak_loss_percent=(1-retained/peak)*100)


def step_differences(off,on):
    assert len(off)==len(on)==36
    differences=[b-a for a,b in zip(off,on)]
    total=sum(differences);heavy=sum(differences[i-1] for i in HEAVY)
    assert abs(sum(on)-sum(off)-total)<1e-8
    return dict(total_seconds=total,heavy_seconds=heavy,other_seconds=total-heavy,
                heavy_fraction_percent=heavy/total*100 if total else None,differences_seconds=differences)


def analyze(base):
    state=read(base/'result.json');assert state['status']=='PASS' and state['source_stopped']
    assert [(r['pair'],r['mode']) for r in state['runs']]==[(1,'off'),(1,'on'),(2,'on'),(2,'off'),(3,'off'),(3,'on')]
    runs=[]
    for saved in state['runs']:
        task=saved['task'];assert task['status']=='PASS'
        run=dict(index=saved['index'],pair=saved['pair'],mode=saved['mode'],raw=saved['raw'],children=[],step_seconds=[0.]*36)
        costs=dict(vmm_cpu=0.,guest_cpu=0.,host_cpu=0.,initialization_cpu=0.,endpoint_cpu=0.,
                   pss_read_wall=0.,all_scan_wall=0.,ordinary_scan_wall=0.,pss_calls=0,ordinary_calls=0)
        pss_durations=[];ordinary_durations=[]
        for i,child in enumerate(task['children']):
            raw=Path(saved['raw'])/f'c4-parallel-{i}';tools=rows(raw/'tools.tsv');events=read(raw/'events.json')
            assert len(tools)==36 and [int(t['step']) for t in tools]==list(range(1,37))
            for j,t in enumerate(tools):
                assert int(t['duration_ns'])==int(t['end_monotonic_ns'])-int(t['start_monotonic_ns'])
                run['step_seconds'][j]+=int(t['duration_ns'])/NS
            assert sum(int(t['duration_ns']) for t in tools)==child['tool_duration_sum_ns']
            cold=next(e for e in events if e['stage']=='cold-create')
            start,finish=child['schedule_start_monotonic_ns'],child['schedule_end_monotonic_ns']
            cold_end=cold['command_end_monotonic_ns'];replay=child['replay_start_monotonic_ns'];replay_end=child['replay_end_monotonic_ns']
            parts=dict(create_and_preceding_seconds=(cold_end-start)/NS,setup_seconds=(replay-cold_end)/NS,
                       replay_call_seconds=(replay_end-replay)/NS,finish_and_download_seconds=(finish-replay_end)/NS)
            assert abs(sum(parts.values())-child['schedule_elapsed_ns']/NS)<1e-8
            control=[e for e in events if e['stage'] in CONTROL]
            details=dict(guest=i+1,sandbox_id=child['sandbox_id'],task_parts=parts,
                         management_commands=[dict(stage=e['stage'],seconds=e['command_duration_ns']/NS) for e in control],
                         management_command_sum_seconds=sum(e['command_duration_ns'] for e in control)/NS,
                         log_cpu_scope='Wall time only; command sums can overlap across guests',
                         ordinary_log_seconds=sum(e['stdout_log_duration_ns']+e['events_log_duration_ns']+e['event_lock_wait_ns'] for e in events)/NS,
                         final_events_flush_seconds=child['call_timing']['final_events_flush_ns']/NS)
            if saved['mode']=='on':
                endpoint=child['endpoint_metrics'];vmm=child['host_vmm_collector'];monitor=read(raw/'runtime-monitor.json')
                points=rows(raw/'host-vmm/external/host-vmm.tsv');assert len(points)==vmm['samples']
                assert sum(bool(p['pss_scan_duration_ns']) for p in points)==vmm['pss_samples']
                details.update(initialization_wall_seconds=(endpoint['initialization']['end_monotonic_ns']-endpoint['initialization']['start_monotonic_ns'])/NS,
                               endpoint_wall_seconds=(endpoint['end_monotonic_ns']-endpoint['start_monotonic_ns'])/NS,
                               pss_wall_seconds=(endpoint['pss']['end_monotonic_ns']-endpoint['pss']['start_monotonic_ns'])/NS,
                               p1_wall_seconds=(endpoint['p1']['end_monotonic_ns']-endpoint['p1']['start_monotonic_ns'])/NS,
                               thinning=[thin_pss(points,seconds) for seconds in (2,5)])
                costs['vmm_cpu']+=vmm['cpu_user_seconds']+vmm['cpu_system_seconds']
                costs['guest_cpu']+=sum(s['receipt']['cpu_user_seconds']+s['receipt']['cpu_system_seconds'] for s in monitor['sessions'])
                costs['initialization_cpu']+=endpoint['initialization']['cpu_ns']/NS;costs['endpoint_cpu']+=endpoint['cpu_ns']/NS
                for point in points:
                    scan=int(point['scan_duration_ns'])/NS;costs['all_scan_wall']+=scan
                    if point['pss_scan_duration_ns']:
                        duration=int(point['pss_scan_duration_ns'])/NS;assert duration<=scan
                        costs['pss_calls']+=1;costs['pss_read_wall']+=duration;pss_durations.append(duration)
                    else:
                        costs['ordinary_calls']+=1;costs['ordinary_scan_wall']+=scan;ordinary_durations.append(scan)
            run['children'].append(details)
        assert abs(sum(run['step_seconds'])-task['tool_duration_sum_ns']/NS)<1e-8
        run['group_parts']=dict(before_action_seconds=(task['schedule_elapsed_ns']-task['actions_download_elapsed_ns'])/NS,
                                actions_download_seconds=task['actions_download_elapsed_ns']/NS,
                                after_task_seconds=(task['controller_elapsed_ns']-task['schedule_elapsed_ns'])/NS)
        assert abs(sum(run['group_parts'].values())-task['controller_elapsed_ns']/NS)<1e-8
        if saved['mode']=='on':
            costs['host_cpu']=saved['host_collector']['thread_cpu_ns']/NS
            costs['periodic_cpu_total']=costs['vmm_cpu']+costs['guest_cpu']+costs['host_cpu']
            costs['vmm_fraction_periodic_cpu_percent']=100*costs['vmm_cpu']/costs['periodic_cpu_total']
            costs['pss_fraction_scan_wall_percent']=100*costs['pss_read_wall']/costs['all_scan_wall']
            costs['pss_median_read_wall_seconds']=statistics.median(pss_durations)
            costs['ordinary_median_scan_wall_seconds']=statistics.median(ordinary_durations)
            run['costs']=costs
        runs.append(run)
    pairs=[]
    for number in (1,2,3):
        off=next(r for r in runs if r['pair']==number and r['mode']=='off');on=next(r for r in runs if r['pair']==number and r['mode']=='on')
        diff=step_differences(off['step_seconds'],on['step_seconds'])
        original=state['comparison']['pairs'][number-1]['metrics']['tool_duration_sum_ns']['delta_seconds']
        assert abs(diff['total_seconds']-original)<1e-8
        pairs.append(dict(pair=number,**diff,group_delta_seconds={k:on['group_parts'][k]-off['group_parts'][k] for k in off['group_parts']}))
    off_runs=[r for r in runs if r['mode']=='off']
    baseline_total=sum(sum(r['step_seconds']) for r in off_runs)
    baseline_heavy=sum(sum(r['step_seconds'][i-1] for i in HEAVY) for r in off_runs)
    return dict(status='PASS',source_batch=str(base),runs=runs,pairs=pairs,heavy_steps=HEAVY,
                heavy_fraction_baseline_tools_percent=baseline_heavy/baseline_total*100,
                step_labels=LABELS,scope='Saved data only; no causal estimate of PSS-specific slowdown; thinning retains existing points, not a new run')


def report(value,out):
    out.mkdir(parents=True,exist_ok=False)
    (out/'summary.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    with (out/'steps.tsv').open('w') as stream:
        writer=csv.writer(stream,delimiter='\t');writer.writerow(['step','description','pair_1_delta_seconds','pair_2_delta_seconds','pair_3_delta_seconds','three_pairs_delta_sum_seconds'])
        for i in range(36):
            ds=[p['differences_seconds'][i] for p in value['pairs']]
            writer.writerow([i+1,LABELS.get(i+1,'其他原动作'),*ds,sum(ds)])
    lines=['# C4双沙箱采样成本：离线分解','',
           '只分析已完成的6次、12guest、432动作及当时源码快照，没有启动沙箱、服务、采样、驻留查询或改频率。原工具+3.64%、任务窗口+4.80%、整轮+3.21%的变化中位数保持。','',
           '**建议优先试验PSS从1秒改为2秒，而不是继续优化mincore或直接改成5秒。** VMM周期采集占已记录周期采集CPU约78%～80%，其中PSS读取消耗了扫描经过时间约86%～87%。三对工具增量主要落在整份C4压缩／解压／校验；这是时间落点和采集成本证据，还不能将全部工具增量归给PSS。当前设置未改。','',
           '## 工具增量主要落在整份数据处理','',
           '| 对数 | 工具累计增量，秒 | 7个整份数据动作增量，秒 | 其他29动作增量，秒 | 7动作占净增量 |',
           '| --- | ---: | ---: | ---: | ---: |']
    for p in value['pairs']:lines.append(f"| {p['pair']} | {p['total_seconds']:+.6f} | {p['heavy_seconds']:+.6f} | {p['other_seconds']:+.6f} | {p['heavy_fraction_percent']:.2f}% |")
    lines+=['','7个动作是25、26、27、30、31、32、33：反复整份压缩、解压、损坏输入验证及最终9898文件内容／结构校验。其他动作包含准备、小输入边界测试、uv调用和清理。第3对超过100%是其他动作净减少，不是把正增量累加后遗漏负值。',
            f"这些动作本来就占三次关闭组工具累计的{value['heavy_fraction_baseline_tools_percent']:.2f}%，因此增量集中在这里不等于已经证明某个采集操作导致了它们变慢。",'',
            '| 动作 | 操作 | 第1对增量，秒 | 第2对增量，秒 | 第3对增量，秒 |',
            '| --- | --- | ---: | ---: | ---: |']
    for step in HEAVY:
        ds=[p['differences_seconds'][step-1] for p in value['pairs']]
        lines.append(f"| {step} | {LABELS[step]} | {ds[0]:+.6f} | {ds[1]:+.6f} | {ds[2]:+.6f} |")
    lines+=['','每项先将同组两个guest的同一动作耗时相加，再做开启减关闭；guest序号不当固定快慢角色。完整36步正负变化在[steps.tsv](steps.tsv)。不同动作的工具累计可相加，不把它与并发墙钟相加或相减。','',
            '## 任务和整轮的增加不是同一件事','',
            '| 对数 | 首次重放前增加，秒 | 动作及下载增加，秒 | 任务结束后变化，秒 |',
            '| --- | ---: | ---: | ---: |']
    for p in value['pairs']:
        d=p['group_delta_seconds'];lines.append(f"| {p['pair']} | {d['before_action_seconds']:+.6f} | {d['actions_download_seconds']:+.6f} | {d['after_task_seconds']:+.6f} |")
    lines+=['','前两列之和对应组任务窗口增量，三列之和对应整轮增量。首次重放前包含两子程序启动、计时器编译、冷创建和采集准备，不全是采样启动。任务结束后包含终点观测、语法检查、删除和记录复制；删除可与另一guest尾段任务重叠。第1和第3对收尾更短，掩盖了一部分任务阶段增加，不能据此说采样更便宜。','',
            '## 周期采集CPU以VMM线程为主','',
            '| 开启运行 | 两VMM线程CPU，秒 | 两guest采样CPU，秒 | host组计数CPU，秒 | 周期合计CPU，秒 | VMM占比 |',
            '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['mode']!='on':continue
        c=r['costs'];lines.append(f"| {r['index']} | {c['vmm_cpu']:.6f} | {c['guest_cpu']:.6f} | {c['host_cpu']:.6f} | {c['periodic_cpu_total']:.6f} | {c['vmm_fraction_periodic_cpu_percent']:.2f}% |")
    lines+=['','CPU秒是线程／进程实际消耗，可跨独立采集线程相加；并发经过时间不能这样相加。这张表只含周期采集，不含全部控制程序、服务和任务CPU，也不等于任务减速。','',
            '| 开启运行 | PSS次数（两VMM） | PSS读取经过时间合计，秒 | 全部扫描经过时间合计，秒 | PSS占扫描时间 | PSS读取中位数，毫秒 | 不采PSS的普通扫描中位数，毫秒 |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['mode']!='on':continue
        c=r['costs'];lines.append(f"| {r['index']} | {c['pss_calls']} | {c['pss_read_wall']:.6f} | {c['all_scan_wall']:.6f} | {c['pss_fraction_scan_wall_percent']:.2f}% | {c['pss_median_read_wall_seconds']*1000:.3f} | {c['ordinary_median_scan_wall_seconds']*1000:.3f} |")
    lines+=['','当时`host_sandbox_monitor.py`在`HostMonitor.collect`保存每次`scan_duration_ns`；`read`额外保存读取并解析`/proc/PID/smaps_rollup`的`pss_scan_duration_ns`。全部扫描还含VMM身份、cgroup和普通进程计数核对。线程总CPU来自`RUSAGE_THREAD`，没有每次PSS的独立CPU计时，因此85%～87%的扫描时间占比不能改称同样比例的CPU归因。读取时长与工具增量也不能一秒对一秒相抵。','',
            '## 启停和终点查询不是主要扫描成本','',
            '| 开启运行／guest | 4次启停命令累计，秒 | VMM初始化，秒 | 终点总观测，秒 | 其中PSS，秒 | 其中P1，秒 |',
            '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['mode']!='on':continue
        for c in r['children']:lines.append(f"| {r['index']}／{c['guest']} | {c['management_command_sum_seconds']:.6f} | {c['initialization_wall_seconds']:.6f} | {c['endpoint_wall_seconds']:.6f} | {c['pss_wall_seconds']:.6f} | {c['p1_wall_seconds']:.6f} |")
    log_sums=[sum(c['ordinary_log_seconds'] for c in r['children']) for r in value['runs']]
    lines+=['','4次命令是上传采样代码、等待首样本就绪、停止并回收、下载采样数据。它们已经在任务窗口内，两guest可并发，不把两份命令累计再加到组任务时间。VMM初始化含身份和文件清单核对及首样本等待，不与已含它的准备间隔相加。终点总观测包含PSS和P1，不再额外加这两列。','',
            '`monitor-collect-*`的长命令在后台等待整段采样，与工具同时进行，不计成几十秒额外启停成本。',
            f"两guest全部调用的stdout／events写入和锁等待累计每组{min(log_sums):.6f}～{max(log_sums):.6f}秒，另有约毫秒级最终事件写入；这部分原日志不支持把几秒工具增量解释为本地日志成本。stderr仍在命令运行中直接写文件，未独立计时，不包含在此范围内。日志累计可与其他调用重叠，不当串行墙钟。",'',
            '## 先考虑2秒PSS，5秒会漏掉明显峰值','',
            '| 开启运行／guest | 原1秒PSS点数 | 保留2秒点数 | 2秒筛选漏掉的已观测峰值 | 保留5秒点数 | 5秒筛选漏掉的已观测峰值 |',
            '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['mode']!='on':continue
        for c in r['children']:
            a,b=c['thinning'];lines.append(f"| {r['index']}／{c['guest']} | {a['original_points']} | {a['retained_points']} | {a['observed_peak_loss_percent']:.2f}% | {b['retained_points']} | {b['observed_peak_loss_percent']:.2f}% |")
    lines+=['','筛选从各自第一个已保存PSS点开始，之后只保留距离上次保留点至少2／5秒的已有点，没有插值或新查询。原272点在2秒筛选下剩138点（少49.26%），6条曲线已观测峰值损失0～2.04%；5秒剩57点（少79.04%），最大损失18.15%。这是这一种起始位置下的旧数据筛选，不是新频率的真实执行结果或峰值误差保证；1秒本身也可能漏掉更短峰值。','',
            '2秒是优先候选：普通host current／file及guest Cached保持0.1秒，终点PSS／P1和身份校验不变，只减少周期PSS次数；保留主要曲线粒度，比5秒更少牺牲已有峰值。预计调用次数约减半，不据此承诺CPU、任务减速也减半。若只需要终点PSS，未来可另讨论不采周期PSS，但那会失去过程曲线，本轮不采用。','',
            '## 结论、来源与下一步','',
            '已确认增量主要出现在整份数据处理，且采集侧最重的工作是周期VMM扫描中的PSS。尚无PSS单独开关／频率对照，宿主调度、文件缓存及磁盘变化也可能影响压缩任务；不能将全部+3.64%归因PSS，或把不同guest时钟与host扫描时刻直接相减来声称某次扫描导致某步变慢。','',
            f"原始批次：`{value['source_batch']}`。每次原始目录、逐guest分段及成本、完整扫描统计和筛选值见[summary.json](summary.json)；逐动作表见[steps.tsv](steps.tsv)；[原三对报告](../c4-parallel-repeats-20261106/REPORT.md)及历史数值不改。源码证据采用该批次`code/host_sandbox_monitor.py`、`external_task_metrics.py`、`sandbox_runtime_monitor.py`和`run_external_workload.py`，没有拿当前工作区替代当时版本。",'',
            '使用已保存的原始数据复算到新目录：','',
            '```bash','python3 -B scripts/analyze_c4_parallel_costs.py \\\n  .artifacts/c4-parallel-repeats-20261106/live \\\n  results/c4-parallel-costs-新目录','```','',
            '下一步建议先离线增加显式PSS间隔参数，普通入口仍默认1秒；随后另行确认C4双guest的1秒／2秒各一次对照，普通计数0.1秒、终点查询及原任务保持，以原结果不变、PSS点数／成本及工具／任务耗时完整为完成条件。当前只交付分析，没有实现参数或启动运行。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('batch',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();value=analyze(args.batch.resolve());report(value,args.output.resolve())
    print('status=PASS result_dir='+str(args.output.resolve()))
