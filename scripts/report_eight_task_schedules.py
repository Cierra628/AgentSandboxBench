"""只读汇总已完成的八任务三调度记录，不访问运行中的服务或内存。"""
import argparse
from collections import Counter
import csv
import io
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
MIB = 2**20
SOURCES = (
    'prettier-schedule-layer-repeats-20261107',
    'python-schedule-matrix-final-20261108T160912Z',
    'django-schedule-matrix-resume-20261109T061236Z',
    'c4-schedule-matrix-final-20261109T095839Z',
    'matplotlib-schedule-matrix-resume-20261109T111200Z',
    'text-editing-schedule-matrix-observefix-20261109T125928Z',
    'cython-schedule-matrix-retry-20261109T160946Z-475206',
)
# 顺序为名称、GRPO实际动作、恢复调度实际动作、原镜像层数、负载类型。
TASKS = {
    'prettier6604': ('Prettier #6604', 189, 84, 19, '原七角色'),
    'sphinx7590': ('Sphinx #7590', 230, 104, 13, '原七角色'),
    'xarray6992': ('Xarray #6992', 230, 104, 13, '原七角色'),
    'django16819': ('Django #16819', 196, 64, 10, '七实例相同原28步，18／26步保存'),
    'c4': ('C4', 252, 93, 9, '七实例相同原36步，24／29步保存'),
    'cython': ('Cython', 581, 176, 7, '七实例相同原83步，60／75步保存'),
    'matplotlib24177': ('Matplotlib #24177', 665, 185, 10, '七实例相同原95步，73／87步保存'),
    'text_editing': ('大规模文本编辑', 350, 113, 8, '七实例相同原50步，33／46步保存'),
}
METRICS = (
    'current_full_peak_mib', 'current_schedule_peak_mib', 'pss_per_guest_mib',
    'lower_p1_mib', 'file_before_p1_mib', 'regular_window_subtotal_mib',
    'schedule_seconds', 'actions_download_seconds', 'tools_seconds',
    'controller_seconds', 'endpoint_wall_seconds', 'endpoint_cpu_seconds',
)


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    text = Path(path).read_text()
    # 早期host表实际保存了字面量反斜杠t，按原报告的格式解释。
    if '\t' not in text.splitlines()[0] and r'\t' in text.splitlines()[0]:
        text = text.replace(r'\t', '\t')
    return list(csv.DictReader(io.StringIO(text), delimiter='\t'))


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), (a, b)


def collect(original, source):
    r = original
    task_id = r.get('task', 'prettier6604')
    _, grpo_actions, restored_actions, layers, _ = TASKS[task_id]
    prettier = task_id == 'prettier6604'
    raw = Path(r['raw'])
    task = read(raw/'result.json')
    metrics = read(raw/'schedule-smoke-metrics.json')
    monitor = read(raw/'runtime-monitor.json')
    outer = read(Path(r['storage'])/'result.json')
    accepted = r.get('status') == 'ACCEPTED_CLEANUP_TIMEOUT'
    assert accepted == (task_id == 'c4' and r['round'] == 1 and r['schedule'] == 'grpo')
    assert task['status'] == outer['status'] == ('FAIL' if accepted else 'PASS')
    assert monitor['status'] == 'PASS' and metrics['status'] == 'OBSERVED'
    assert all(v == 'PASS' for v in task['checks'].values())
    if not accepted:
        assert all(task['cleanup'].values())
    else:
        assert r['controller_statistics_excluded'] and r['original_status'] == 'FAIL'
    restored = r['schedule'] != 'grpo'
    assert r['actual_actions'] == task['actual_action_count'] == (restored_actions if restored else grpo_actions)
    assert r['logical_actions'] == grpo_actions
    assert monitor['interval_seconds'] == .1 and monitor['host_pss_interval_seconds'] == 1
    ids = {v['role']: v['sandbox_id'] for v in rows(raw/'sandbox-ids.tsv')}
    pss = metrics['pss']
    assert len(ids) == len(set(ids.values())) == len(pss['processes']) == pss['denominator'] == 7
    assert {p['role']: p['sandbox_id'] for p in pss['processes']} == ids
    assert len({p['identity']['pid'] for p in pss['processes']}) == 7
    assert set(metrics['bindings']) == set(metrics['drives']) == set(ids) == set(task['paths'])
    assert all(p['uid'] == 0 and p['identity'] == metrics['bindings'][p['role']] for p in pss['processes'])
    close(sum(p['pss_kib'] for p in pss['processes'])*1024, pss['total_bytes'])
    close(pss['total_bytes']/7, pss['per_sandbox_bytes'])
    close(pss['per_sandbox_bytes']/MIB, r['pss_per_guest_mib'])
    p1 = metrics['p1']
    keys = {(p['device'], p['inode']) for p in p1['files']}
    assert len(keys) == len(p1['files']) == p1['unique_files']
    assert len(keys) >= layers and all(p['stable'] for p in p1['files'])
    close(sum(p['resident_bytes'] for p in p1['files']), p1['resident_bytes'])
    lower = r['initial_lower_mib'] if prettier else r['lower_p1_mib']
    close(lower, p1['resident_bytes']/MIB)
    samples_path = Path(r['host_samples_path']) if prettier else raw/'host-file/memory.tsv'
    samples = rows(samples_path)
    current_key = 'cgroup_current_bytes' if prettier else 'current'
    close(max(int(p[current_key]) for p in samples)/MIB, r['current_peak_mib'])
    schedule_peak = None
    if not prettier:
        selected = [p for p in samples if task['schedule_start_monotonic_ns'] <= int(p['monotonic_ns']) <= task['original_paths_end_monotonic_ns']]
        schedule_peak = max(int(p['current']) for p in selected)/MIB
        close(schedule_peak, r['current_schedule_peak_mib'])
    sessions = []
    for session in monitor['sessions']:
        host, guest = session['host_receipt'], session['receipt']
        assert session['status'] == host['status'] == guest['status'] == 'PASS'
        assert session['collector_reaped'] and host['pss_samples'] > 0 and guest['samples'] > 0
        guest_table = raw/session['raw_directory']/'guest-memory.tsv'
        host_table = raw/session['host_directory']/'host-vmm.tsv'
        assert guest_table.is_file() and host_table.is_file()
        sessions.append(dict(role=session['role'], guest_table=relative(guest_table),
            host_table=relative(host_table), guest_samples=guest['samples'],
            host_samples=host['samples'], pss_samples=host['pss_samples'],
            guest_collector_cpu_seconds=guest['cpu_user_seconds']+guest['cpu_system_seconds'],
            host_collector_cpu_seconds=host['cpu_user_seconds']+host['cpu_system_seconds']))
    saves = r['save_call_details'] if prettier else r['saves']
    restores = r['restore_call_details'] if prettier else r['restores']
    assert (len(saves), len(restores)) == ((2, 6) if restored else (0, 0))
    tools = sum(p['guest_action_sum_ns' if prettier else 'tool_ns'] for p in task['paths'].values())/1e9
    close(tools, r['tool_seconds' if prettier else 'tools_seconds'])
    close(task['controller_elapsed_ns']/1e9, r['controller_seconds'])
    e, b, loop = metrics['expanded_files'], metrics['cache_layers']['block_devices'], metrics['cache_layers']['outer_loop']
    q = e['query_window_regular']
    cache = dict(regular_strict_status=e['status'], regular_window_status=q['status'],
        regular_window_unknown_objects=q['unknown_objects'], regular_unresolved_sources=q['unresolved_sources'],
        regular_changed_before_query_objects=q['changed_before_query_objects'],
        block_strict_status=b['status'], block_unknown_objects=b['method_totals']['cachestat']['unknown_objects'],
        fixed_ram_status=b.get('fixed_ram_totals', {}).get('status', 'NOT_RECORDED'),
        loop_strict_status=loop['status'], loop_window_status=loop.get('query_window', {}).get('status', 'NOT_RECORDED'),
        tmpfs_status=metrics['cache_layers']['deleted_tmpfs']['status'])
    curves = [ROOT/'results'/source/f'r{r["round"]}-{r["schedule"]}'/f for f in ('host.png', 'guest-cached.png', 'tools.png')] if prettier else [Path(r['curve'])]
    assert all(p.is_file() for p in curves)
    result = dict(task=task_id, round=r['round'], schedule=r['schedule'], n=7,
        status='ACCEPTED_CLEANUP_TIMEOUT' if accepted else 'PASS',
        raw_status=task['status'], summary_source=f'results/{source}/summary.json',
        raw=relative(raw), storage=relative(r['storage']), curves=[relative(p) for p in curves],
        actual_actions=r['actual_actions'], logical_actions=r['logical_actions'],
        save_count=len(saves), restore_count=len(restores),
        current_full_peak_mib=r['current_peak_mib'], current_schedule_peak_mib=schedule_peak,
        pss_per_guest_mib=r['pss_per_guest_mib'], lower_p1_mib=lower,
        lower_logical_layers=layers, lower_physical_files=len(keys),
        file_before_p1_mib=p1['counters_before']['file']/MIB,
        file_after_p1_mib=p1['counters_after']['file']/MIB,
        regular_window_subtotal_mib=q['observed_subtotal_bytes']/MIB,
        regular_window_complete_mib=None if q['complete_bytes'] is None else q['complete_bytes']/MIB,
        schedule_seconds=r['schedule_run_seconds' if prettier else 'schedule_seconds'],
        actions_download_seconds=r['action_download_window_seconds' if prettier else 'actions_download_seconds'],
        tools_seconds=tools, controller_seconds=r['controller_seconds'],
        controller_statistics_excluded=accepted,
        endpoint_wall_seconds=r['endpoint_wall_seconds'], endpoint_cpu_seconds=r['endpoint_cpu_seconds'],
        host_sample_count=len(samples), sessions=sessions,
        boot_ids=sorted({p['identity']['boot_id'] for p in pss['processes']}),
        strict_replay_status=task.get('strict_replay_status', 'NOT_RECORDED'),
        official_by_role={role: ({k: v[k] for k in ('official_task_status','official_passed','official_failed')} if prettier else v['official']) for role,v in task['paths'].items()},
        task_checks=task['checks'], cleanup=task['cleanup'], outer_cleanup=outer['cleanup'], **cache)
    # 精确异常信息仅从现存记录引用，不重新查询对象。
    result['cache_diagnostics'] = dict(block_errors=b['errors'], unresolved=e['unresolved'],
        unknown_objects=e['unknown_objects'], loop_error=loop.get('error'),
        loop_strict_rejection=loop.get('query_window', {}).get('strict_rejection_reason'))
    return result


def describe(runs, key):
    selected = [r[key] for r in runs if r[key] is not None and not (key == 'controller_seconds' and r['controller_statistics_excluded'])]
    return dict(count=len(selected), missing=sum(r[key] is None for r in runs),
        excluded=sum(key == 'controller_seconds' and r['controller_statistics_excluded'] for r in runs),
        mean=statistics.mean(selected) if selected else None,
        median=statistics.median(selected) if selected else None,
        minimum=min(selected) if selected else None, maximum=max(selected) if selected else None,
        sample_stddev=statistics.stdev(selected) if len(selected)>1 else None)


def write_tsv(path, records):
    with path.open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(records)


def report(out):
    assert not out.exists(), '输出目录必须是新目录，不能覆盖已有报告'
    runs = []
    for source in SOURCES:
        saved = read(ROOT/'results'/source/'summary.json')
        assert saved['status'] in ('PASS', 'PASS_WITH_CLEANUP_EXCEPTION')
        runs.extend(collect(r, source) for r in saved['runs'])
    for task in TASKS:
        assert {(r['round'],r['schedule']) for r in runs if r['task']==task} == {(n,s) for n in (1,2,3) for s in ('grpo','bpo','tvcache')}
    assert len(runs) == len({r['raw'] for r in runs}) == 72
    assert sum(r['actual_actions'] for r in runs)==13617
    assert sum(r['logical_actions'] for r in runs)==24237
    assert Counter(r['status'] for r in runs)=={'PASS':71, 'ACCEPTED_CLEANUP_TIMEOUT':1}
    stats = []
    for task in TASKS:
        for schedule in ('grpo','bpo','tvcache'):
            group=[r for r in runs if r['task']==task and r['schedule']==schedule]
            stats.append(dict(task=task, schedule=schedule, metrics={k:describe(group,k) for k in METRICS}))
    out.mkdir(parents=True)
    summary=dict(status='OFFLINE_INVENTORY_COMPLETE_WITH_RECORDED_EXCEPTIONS',
        scope='Saved records only; no network, live /proc, cgroup, mincore, services or guest calls',
        tasks=8, runs=72, instances=504, actual_actions=13617, logical_actions=24237,
        full_flow_pass=71, accepted_cleanup_timeout=1,
        sources=[f'results/{s}/summary.json' for s in SOURCES],runs_detail=runs,statistics=stats)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    flat=[]
    for r in runs:
        flat.append({k:v for k,v in r.items() if not isinstance(v,(dict,list))} | dict(curves=';'.join(r['curves'])))
    write_tsv(out/'runs.tsv',flat)
    write_tsv(out/'statistics.tsv',[dict(task=s['task'],schedule=s['schedule'],metric=k,**v) for s in stats for k,v in s['metrics'].items()])
    (out/'REPORT.md').write_text(markdown(runs,stats))
    print(f'status=OFFLINE_INVENTORY_COMPLETE tasks=8 runs=72 full_pass=71 cleanup_exception=1 output={out}')


def markdown(runs,stats):
    table={(s['task'],s['schedule']):s['metrics'] for s in stats}
    lines=['# 八任务三调度：已有数据统一索引（2026-11-10）','',
        '**8种任务、每种GRPO／BPO／TVCache各3次，共72份测量记录已齐。71次全流程PASS，另1次C4首轮GRPO为用户批准的清理超时测量复用，原FAIL保留。** 本次只读取保存数据，没有启动沙箱、重新采样、联网或清理文件。','',
        '这份索引用于查数据、比较同一任务和识别范围差异。未将不同任务的内存、延迟混成一个平均值；八种任务的主要四类数据可取，不代表所有附加缓存量完整，亦不直接给出部署密度结论。','',
        '## 先看数据的含义','',
        '| 数据 | 取得方法和准确含义 | 使用限制 |','| --- | --- | --- |',
        '| host current | 私有服务cgroup的memory.current；服务及VMM后代的内存记账 | 物理内存的组内记账，不含组外输入准备、外层控制或记在别组的旧缓存；不是完整宿主物理并集 |',
        '| PSS/N | 对七个身份已绑定的root VMM读smaps_rollup，Pss之和除7 | 按映射比例分摊驻留页；不含未映射缓存和其他服务进程，不是每guest独占内存 |',
        '| 原镜像mincore P1 | 对明确文件按设备号:inode去重，再查询页驻留 | 实际缓存驻留观测，只覆盖列出的文件；相同内容不同inode不合并 |',
        '| 扩展普通文件窗口合计 | lower、upper及其他已枚举文件各查一次，去重 | 包含P1，不是P1外增量；不同对象查询时刻不同，未知不补零 |',
        '| 块设备cachestat | 已识别块设备的缓存索引页数 | 可含内容尚未准备好的页，不等同有效文件内容驻留；RAM、loop、TMPFS分列，不跨层相加 |',
        '| guest内存／Cached | guest的/proc/meminfo计数；内存曲线沿用MemTotal−MemAvailable | guest层的估计／记账；Cached不是host对应页的物理驻留，不能跨guest简单相加作为物理量 |',
        '| 任务与工具时间 | 单调时钟测得的秒数 | 是实测时长，不是内存；工具累计包含并发重叠，只统计实际执行的动作 |',
        '| 逻辑动作和配置容量 | 调度完成语义、请求的内存等元数据 | 不是物理占用；不将581逻辑动作或8 GiB配置画成实测内存 |','',
        'current、PSS、file、mincore、RAM后备和loop可能重叠，不能相加构造总物理内存。current−file不是anon，anon直接读memory.stat。file包含shmem，不再重复相加。MiB为字节除以2²⁰；内核kB按KiB处理。','',
        '## 每种任务完成了什么','',
        '| 任务 | 三调度次数 | 负载 | GRPO／BPO／TVCache实际动作／次 | 每次逻辑动作 | 原层数；P1物理文件数G／B／T | 原报告 |','| --- | --- | --- | --- | ---: | --- | --- |']
    for task,(name,g,b,l,kind) in TASKS.items():
        rs=[r for r in runs if r['task']==task]
        counts=['／'.join(map(str,sorted({r['lower_physical_files'] for r in rs if r['schedule']==s}))) for s in ('grpo','bpo','tvcache')]
        source=Path(rs[0]['summary_source']).parent.name
        lines.append(f'| {name} | 3／3／3 | {kind} | {g}／{b}／{b} | {g} | {l}；'+ '／'.join(counts)+f' | [报告](../{source}/REPORT.md) |')
    lines+=['',
        '共13617个实际动作、24237个逻辑动作、504个最终实例。每次GRPO无保存恢复；BPO／TVCache均2保存6恢复。恢复期间采样会话断开重建，每组可有9份会话，但终点PSS始终只取7个活跃VMM，不按会话数分母计算。','',
        'Prettier #6604与Sphinx／Xarray沿用原七角色分支；其余五种是经确认的七实例相同路径前缀复用。BPO／TVCache复用前缀使实际动作减少，不能按相同实际工作量算纯加速比。本项目TVCache为确定性前缀树逐子树推进，没有动态容量或淘汰；它不是另一个未在本平台实现的完整缓存算法。','',
        '## 内存主表：三次均值，各指标分开','',
        '统一使用**全采集窗口current观测峰值**，包含终点检查／清理；不是任务执行窗口峰值。后7种任务还可在statistics.tsv读取单独的调度窗口峰值。Prettier旧表只有墙钟采样，未保存同格式的单调时间边界，本次不估算调度峰值，不与其他任务的执行峰值混用。','',
        '| 任务 | 调度 | 全窗口current峰值 MiB | 终点PSS/N MiB/guest | 原镜像P1 MiB | P1前file MiB |','| --- | --- | ---: | ---: | ---: | ---: |']
    for task,(name,*_) in TASKS.items():
        for s in ('grpo','bpo','tvcache'):
            m=table[task,s]
            lines.append(f'| {name} | {s.upper()} | '+ ' | '.join(f'{m[k]["mean"]:.3f}' for k in ('current_full_peak_mib','pss_per_guest_mib','lower_p1_mib','file_before_p1_mib'))+' |')
    lines+=['',
        'Xarray九次全窗口峰值均发生在原动作结束后的独立pytest检查中；部署任务运行窗口分析应使用另列调度峰值。Prettier P1固定查询原19文件；Sphinx／Xarray恢复组P1覆盖26个活跃文件，其余任务按各自实际活跃文件，不用19文件统一套算。P1是终点驻留，不是新读入量，没有统一P0冷起点。','',
        '若P1明显大于同期cgroup file，表明二者范围／记账不一致，不能据current低就说全host内存更低；净差不等于已确认组外容量，不能加回current。尤其Prettier的原19文件P1为5308.066 MiB，GRPO组file仅约82 MiB，这项缺口仍须按原边界解释。','',
        '## 时间与查询成本：三次均值','',
        '| 任务 | 调度 | 调度窗口 秒 | 动作及下载窗口 秒 | 工具累计 秒 | 正常整轮 秒（有效数） | 终点查询墙钟 秒 |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for task,(name,*_) in TASKS.items():
        for s in ('grpo','bpo','tvcache'):
            m=table[task,s]
            cells=[f'{m[k]["mean"]:.3f}' for k in ('schedule_seconds','actions_download_seconds','tools_seconds')]
            cells.append(f'{m["controller_seconds"]["mean"]:.3f}（{m["controller_seconds"]["count"]}）')
            cells.append(f'{m["endpoint_wall_seconds"]["mean"]:.6f}')
            lines.append(f'| {name} | {s.upper()} | '+' | '.join(cells)+' |')
    lines+=['',
        '调度窗口从创建前至原路径和下载结束，含创建／准备、保存恢复和调度；动作及下载窗口从第一段调用开始，不是纯工具时长。工具累计逐动作相加，可能大于整轮。控制程序整轮还包含终点查询、结果检查和清理，不包含外层卷／镜像准备；外层driver计时不混入。','',
        'C4 r1 GRPO原整轮143.439769594秒保留在runs.tsv，不进入正常整轮统计，因此该项n=2；其余指标按用户批准纳入三次。其他慢值全部保留。statistics.tsv同时给出每项均值、中位数、极值、样本标准差、缺失数和排除数，缺失不补零。','',
        '普通host／guest采样0.1秒、周期PSS1秒，另有终点查询；采样间隙不补点，观测峰值不保证捕获瞬时峰值。终点CPU列沿用调用线程CPU，不代表所有查询子进程的总CPU；新批次部分子进程另有独立成本，不能倒填旧批次。summary.json逐会话列guest／host采样器CPU、点数和原始表路径。查询成本不是任务减速率：这72份不是采样开关配对，不能从本表计算采样减速。','',
        '## 附加缓存覆盖：按原状态计数','',
        'OBSERVED=该定义下已取得，PARTIAL=仅部分确认，UNKNOWN=没有严格有效完整值，NOT_RECORDED=原版本没有记录该字段。窗口OBSERVED也不表示对象是同时查询的。','',
        '| 任务 | 普通严格OBSERVED／PARTIAL | 普通窗口OBSERVED／PARTIAL | 块设备OBSERVED／PARTIAL | loop严格OBSERVED／UNKNOWN | loop窗口OBSERVED／未记录 | 独立RAM补充OBSERVED／不适用／未记录 |','| --- | --- | --- | --- | --- | --- | --- |']
    for task,(name,*_) in TASKS.items():
        rs=[r for r in runs if r['task']==task]
        def counts(k,states):
            c=Counter(r[k] for r in rs)
            return '／'.join(str(c[s]) for s in states)
        vals=[counts('regular_strict_status',('OBSERVED','PARTIAL')),counts('regular_window_status',('OBSERVED','PARTIAL')),
            counts('block_strict_status',('OBSERVED','PARTIAL')),counts('loop_strict_status',('OBSERVED','UNKNOWN')),
            counts('loop_window_status',('OBSERVED','NOT_RECORDED')),counts('fixed_ram_status',('OBSERVED','NOT_APPLICABLE','NOT_RECORDED'))]
        lines.append(f'| {name} | '+' | '.join(vals)+' |')
    lines+=['',
        '主要附加缺项：Django r2 BPO普通文件窗口还有1对象变化；C4五份恢复组普通窗口存在1～3个未知对象，五份恢复组块设备存在1～2个未知设备；Prettier r1 TVCache、Xarray r1 BPO、文本编辑r1 BPO也各有块设备严格缺项。loop严格UNKNOWN通常对应查询期间mtime变化，独立窗口值不回填严格量。Prettier旧版没有独立RAM完整量和loop窗口字段，留未记录，不能拿后续任务的新字段拼补。逐次状态和错误在summary.json的cache_diagnostics及原始schedule-smoke-metrics.json。','',
        '仍不保证覆盖无路径旧缓存、已释放且无持有者的对象、清单之外及窗口之外变化；也没有完成guest Cached全部页在host的来源归属。主指标齐全与全部物理缓存测全是两件事。','',
        '## 原结果、复用和异常不能合并成一个PASS','',
        '- Prettier每实例官方0通过／2失败保持。Sphinx指定测试为24通过／1失败；Xarray主干、early和late_1为945通过／12失败，late_2／late_3为946通过／11失败。Sphinx／Xarray严格重放仍FAIL，原诊断验收通过，不能改称严格重放或全部官方测试通过。',
        '- Django仅原补丁／语法检查，没有完整官方测试。C4为9898文件往返和约束；Cython为两种目录各18 passed、版本及扩展；Matplotlib为原两轮各60 passed／导入／完整补丁；文本编辑为原完整逐字节比较和两种solve。各自原检查范围保留。',
        '- Django首轮GRPO和C4首轮GRPO跨宿主重启复用，缓存起点不统一；数据不删除，差值不能直接归因调度。72份不是同日同冷起点的统一性能实验。',
        '- C4首轮GRPO原清理超时FAIL及143.440秒异常整轮保留，不称72次全流程PASS。旧失败、创建前资源停止和报告检查器错误不冒充新增运行；每条仅引用最终清单中的一次真实记录，不重复计数。',
        '- Cython新九份中三处TLS异常保留，其中r2 GRPO late_2原45的Git错误被原管道退出0掩盖；最终检查通过不等于中间网络输出完整。原59步planarity 0.8安装失败但管道退出0、60步最终版本未记录，也原样保留。错误原文见[Cython审计](../cython-schedule-matrix-retry-20261109T160946Z-475206/audit.json)。','',
        '## 如何定位原始数据','',
        '- [runs.tsv](runs.tsv)：72行逐次指标、实际／逻辑动作、原始目录、曲线和完整状态；C4异常整轮没有删除。',
        '- [statistics.tsv](statistics.tsv)：同任务同调度的三次描述统计，不跨任务平均。',
        '- [summary.json](summary.json)：72份来源、结果／清理状态、七身份核对、会话路径、成本和缓存错误。',
        '- [verification.json](verification.json)：本次离线核对的数量、范围、缺失值处理和链接检查；该回执不代替各次原验收。',
        '- 每条raw下的result.json是原任务结果，schedule-smoke-metrics.json是终点PSS和分层缓存，runtime-monitor.json指向guest-memory.tsv／host-vmm.tsv；逐动作在rollouts下对应分段steps.tsv。',
        '- Prettier曲线含host、guest Cached和工具图；其余任务现有curves.png主要画current／file、PSS及工具，guest Cached原始表仍有，但不宣称这张三图中已画出Cached。这些图沿用原时间对齐及保存恢复空白，本次不重绘。','',
        '可复算到新的目录：','',
        '```bash','python3 -B scripts/report_eight_task_schedules.py --output results/新的目录','```','',
        '## 当前可交付与下一步','',
        '可交付八任务三调度重复数据及明确的采集范围，支持查看真实组内记账、VMM比例驻留、已知文件驻留和实测耗时。若要写“共享提高部署密度”，仍需使用专门的共享／独立及内存压力对照，不能用本轮PSS下降替代整组节省或把组外遗漏当收益。历史84.12%、4,270 MiB、6.8倍等保持原来源和口径，本次没有折算或改写。','',
        '下一步建议：先按此索引选择要展示的任务和指标，只用已有记录制作同任务三调度对照图，明确current窗口、PSS分母和缓存覆盖。完成条件是图中每个点可回到runs.tsv原始路径；不增加运行、不自动重测未知缓存。']
    return '\n'.join(lines)+'\n'


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    report(parser.parse_args().output)
