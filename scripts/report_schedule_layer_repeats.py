"""Report nine saved layer-enabled schedule runs; no live object queries."""
import argparse
import csv
import json
from pathlib import Path

from platform_tasks import ROOT
from report_schedule_cache_layers import collect, report as layer_report, seconds
from report_schedule_expanded_files import collect as expanded_collect
from report_schedule_inventory import image_objects, record
from report_schedule_metrics_offline import normalize, rows
from report_schedule_repeats import draw_run, stats
from run_schedule_repeats import inspect_run, run_plan
from run_schedule_smoke import SCHEDULES, read, write

MIB = 2**20
METRICS = {
    'current_peak_mib': '服务组memory.current全采集窗口峰值，MiB',
    'pss_per_guest_mib': '终点七VMM PSS/N，MiB/guest',
    'file_at_p1_mib': '原19文件P1前服务组file，MiB',
    'initial_lower_mib': '原19文件mincore P1，MiB',
    'regular_window_subtotal_mib': '普通文件各自查询窗口观测小计，MiB',
    'regular_strict_subtotal_mib': '普通文件原严格观测小计，MiB',
    'block_complete_mib': '块设备cachestat完整缓存索引量，MiB',
    'tmpfs_mib': 'TMPFS整后备缓存量，MiB',
    'loop_mib': '准确loop后备mincore驻留，MiB',
    'schedule_run_seconds': '调度执行窗口，秒',
    'action_download_window_seconds': '动作及下载窗口，秒',
    'tool_seconds': '实际工具累计，秒',
    'controller_seconds': '控制程序整轮，秒',
    'endpoint_wall_seconds': '全部终点查询墙钟，秒',
    'endpoint_cpu_seconds': '全部终点查询调用线程CPU，秒',
    'block_layer_seconds': '块设备整层墙钟（含登记及检查），秒',
    'block_core_seconds': '块设备核心查询合计，秒',
    'expanded_wall_seconds': '扩展普通文件整段墙钟，秒',
}


def describe(values):
    known = [v for v in values if v is not None]
    return dict(**(stats(known) if known else dict(count=0, mean=None, median=None,
                stdev=None, minimum=None, maximum=None)), missing=len(values)-len(known))


def cell(value, digits=3):
    if not value['count']: return '未知（0/3）'
    return (f"{value['median']:.{digits}f}（{value['minimum']:.{digits}f}～{value['maximum']:.{digits}f}）"
            + (f"；有效{value['count']}/3" if value['missing'] else ''))


def tools_curve(raw, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    by_role = {}
    for segment in rows(raw/'segments.tsv'):
        by_role.setdefault(segment['rollout'], []).extend(
            rows(raw/'rollouts'/segment['rollout']/segment['segment']/'steps.tsv'))
    fig, axes = plt.subplots(4, 2, figsize=(12, 10), layout='constrained')
    for ax, (role, data) in zip(axes.flat, sorted(by_role.items())):
        ax.plot(range(1, len(data)+1), [int(r['duration_ns'])/1e9 for r in data], marker='.', lw=1)
        ax.set(title=role, xlabel='Actual tool ordinal in this role (saved segment order)',
               ylabel='Tool call duration, seconds'); ax.grid(alpha=.2)
    axes.flat[-1].axis('off')
    fig.suptitle('Actually executed tools only; reused prefix calls are not synthesized')
    fig.savefig(output/'tools.png', dpi=130); plt.close(fig)


def overview(statistics, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    selected = [
        ('current_peak_mib', 'Service cgroup memory.current peak, MiB'),
        ('pss_per_guest_mib', 'Endpoint VMM PSS/N, MiB/guest'),
        ('regular_window_subtotal_mib', 'Ordinary-file mincore subtotal, MiB'),
        ('block_complete_mib', 'Block-device cachestat cache index, MiB'),
        ('schedule_run_seconds', 'Schedule execution window, seconds'),
        ('tool_seconds', 'Actual tool duration sum, seconds')]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), layout='constrained')
    for ax, (key, label) in zip(axes.flat, selected):
        for i, schedule in enumerate(SCHEDULES):
            s = statistics[schedule][key]
            if s['count']:
                ax.errorbar(i, s['median'], yerr=[[s['median']-s['minimum']],
                    [s['maximum']-s['median']]], fmt='o', capsize=5, color='C'+str(i))
        ax.set_xticks(range(3), [s.upper()+f"\n(n={statistics[s][key]['count']}/3)" for s in SCHEDULES])
        ax.set_ylabel(label); ax.grid(axis='y', alpha=.2)
    fig.suptitle('New runs only: median and full range of valid readings; distinct memory scopes')
    fig.savefig(output/'comparison.png', dpi=140); plt.close(fig)


def report(batch, out):
    saved = read(batch/'result.json'); plan = read(batch/'plan.json')
    assert saved['status'] == 'PASS' and len(saved['runs']) == 9
    assert [(r['round'], r['schedule']) for r in saved['runs']] == [(r['round'], r['schedule']) for r in run_plan()]
    assert plan['expanded_files'] and plan['cache_layers'] and plan['block_cache_method'] == 'cachestat'
    assert not out.exists(); out.mkdir(parents=True)
    # Reuse the established action/PSS/image checker with its three-schedule contract.
    normalized = {}
    for number in (1, 2, 3):
        selected = [read(Path(r['source']))['runs'][0] for r in saved['runs'] if r['round'] == number]
        source = out/f'round-{number}-source.json'
        write(source, dict(status='PASS', runs=selected))
        for r in normalize(source)['runs']:
            normalized[number, r['schedule']] = r
    result_rows = []; reference = None; anomalies = []
    for original in saved['runs']:
        source = Path(original['source']); driver = read(source)
        assert driver['status'] == original['status'] == 'PASS'
        checked = inspect_run(Path(original['storage']))
        for key, value in checked.items(): assert original[key] == value, key
        value = collect(source); expanded = expanded_collect(source)['runs'][0]
        row = record(driver['runs'][0], expanded, 'new_layer_repeats')
        raw = Path(row['raw']); metrics = read(Path(row['metrics_path'])); task = read(raw/'result.json')
        identity = image_objects(metrics)
        if reference is None: reference = identity
        assert reference == identity and len(identity) == 19
        d = value['layers']['block_devices']; e = value['expanded']; q = e['query_window_regular']
        assert d['method'] == 'cachestat' and d['method_totals']['mincore']['status'] == 'NOT_REQUESTED'
        assert sum(r['block_device'] for refs in metrics['drives'].values() for r in refs) == 14
        ordinary = {(r['device'], r['inode']) for r in e['files']}
        regular_drives = [r for refs in metrics['drives'].values() for r in refs if not r['block_device']]
        assert len(regular_drives) == 28 and all((r['device'], r['inode']) in ordinary for r in regular_drives)
        row.update(round=original['round'], source=str(source), storage=original['storage'],
            schedule_run_seconds=checked['schedule_run_seconds'], current_peak_mib=row['current_peak_bytes']/MIB,
            pss_per_guest_mib=row['pss_per_guest_bytes']/MIB, file_at_p1_mib=row['file_at_initial_p1_bytes']/MIB,
            initial_lower_mib=row['initial_lower_resident_bytes']/MIB,
            regular_window_subtotal_mib=row['regular_query_window_bytes']/MIB,
            regular_strict_subtotal_mib=row['regular_stable_subtotal_bytes']/MIB,
            block_complete_mib=None if row['block_cachestat']['complete_bytes'] is None else row['block_cachestat']['complete_bytes']/MIB,
            tmpfs_mib=None if row['tmpfs_cache_bytes'] is None else row['tmpfs_cache_bytes']/MIB,
            loop_mib=None if row['loop_resident_bytes'] is None else row['loop_resident_bytes']/MIB,
            cache_layers_status=value['layers']['status'],
            block_layer_seconds=seconds(d), block_layer_cpu_seconds=d['cpu_ns']/1e9,
            block_core_seconds=value['block_method_costs']['cachestat']['stages']['core_query']['wall_seconds'],
            block_cost=value['block_method_costs']['cachestat'],
            expanded_wall_seconds=seconds(e), expanded_cpu_seconds=e['cpu_ns']/1e9,
            minimum_disk_gib=checked['minimum_disk_gib'], regular_window_unknown_objects=q['unknown_objects'],
            regular_changed_before_query_objects=q['changed_before_query_objects'],
            regular_changed_before_query_bytes=q['changed_before_query_bytes'],
            block_query_errors=[dict(rdev=o['rdev'],method=method,**{k:query[k]
                for k in ('status','error','failed_reference','mapping_diagnostic') if k in query})
                for o in d['objects'] for method,query in o['queries'].items() if query['status'] != 'OBSERVED'],
            save_call_details=driver['runs'][0]['save_calls'], restore_call_details=driver['runs'][0]['restore_calls'],
            driver_seconds=original['driver_seconds'], cleanup=value['cleanup'], checks=value['checks'],
            source_stopped=value['source_stopped'], cache_released=value['cache_released'],
            per_guest_tools_seconds={role:p['guest_action_sum_ns']/1e9 for role,p in task['paths'].items()})
        normal = normalized[row['round'], row['schedule']]
        assert normal['actual_tool_sum_seconds'] == row['tool_seconds']
        assert normal['schedule_run_seconds'] == row['schedule_run_seconds']
        name = f"r{row['round']}-{row['schedule']}"; directory = out/name; directory.mkdir()
        row['curves'] = dict(directory=name, **draw_run(raw, directory))
        tools_curve(raw, directory)
        write(directory/'summary.json', value)
        (directory/'CACHE_LAYERS.md').write_text(layer_report(value))
        (directory/'REPORT.md').write_text('# '+name+'\n\n本页对应新版9次对照中的一次完整运行。\n\n'
            '[服务组current／file和逐VMM PSS曲线](host.png) · [逐guest Cached曲线](guest-cached.png) · '
            '[各角色实际工具耗时](tools.png) · [逐对象分层读数及错误](CACHE_LAYERS.md)\n\n'
            'host曲线覆盖检查与清理；guest各自首点对齐，保存恢复间隙断线。工具图只列实际动作，不补出复用前缀。\n\n'
            '原始目录：`'+str(raw.relative_to(ROOT))+'`。\n')
        issues = []
        for label, status in [('普通严格范围', row['regular_status']), ('普通查询窗口', q['status']),
                              ('块设备', row['block_cachestat']['status']), ('TMPFS', row['tmpfs_status']),
                              ('loop', row['loop_status'])]:
            if status != 'OBSERVED': issues.append(label+'='+status)
        if issues:
            anomalies.append(dict(run=name, issues=issues, source=str(source),
                raw=str(raw), detail=name+'/CACHE_LAYERS.md'))
        result_rows.append(row)
    assert sum(r['actual_actions'] for r in result_rows) == 1071
    labels=dict(METRICS)
    if any('loop_query_window_status' in r for r in result_rows):
        labels['loop_query_window_mib']='准确loop后备查询窗口驻留，MiB（独立字段）'
        for r in result_rows:
            value=r.get('loop_query_window_bytes')
            r.setdefault('loop_query_window_status','NOT_RECORDED');r.setdefault('loop_query_window_bytes',None)
            r['loop_query_window_mib']=None if value is None else value/MIB
    if any('fixed_ram_status' in r for r in result_rows):
        labels['fixed_ram_cache_mib']='独立固定RAM设备缓存索引量，MiB（不回填原严格值）'
        for r in result_rows:
            value=r.get('fixed_ram_cache_bytes')
            r.setdefault('fixed_ram_status','NOT_RECORDED');r.setdefault('fixed_ram_cache_bytes',None)
            r['fixed_ram_cache_mib']=None if value is None else value/MIB
    summaries = {s:{k:describe([r[k] for r in result_rows if r['schedule'] == s]) for k in labels} for s in SCHEDULES}
    result = dict(status='PASS', batch=str(batch.relative_to(ROOT)), plan=plan,
        runs=result_rows, statistics=summaries, metric_labels=labels, anomalies=anomalies,
        actual_actions=1071, logical_actions=1701, guest_instances=63, old_runs_included=False,
        host_scope='private_service_cgroup', full_host_physical_coverage=False, initial_reference=reference,
        endpoint_complete_values_status='OBSERVED' if all(
            r['block_cachestat']['status']==r['regular_query_window_status']==r['tmpfs_status']==r['loop_status']=='OBSERVED'
            for r in result_rows) else 'PARTIAL')
    write(out/'summary.json', result)
    with (out/'runs.tsv').open('x') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result_rows[0]), delimiter='\t'); writer.writeheader()
        for row in result_rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    overview(summaries, out)
    lines = ['# Prettier七沙箱：新版分层采集三调度各三次', '',
        '9次新运行、63个guest、1071个实际动作／1701个逻辑动作通过原任务、身份及清理检查。每实例官方结果仍0通过2失败；任务原检查失败与平台运行通过分别记录。旧九次和单次方法验证不计入统计。', '',
        '三轮顺序为GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO。每次新私有服务、cgroup及guest，固定本地镜像，普通计数0.1秒、周期PSS1秒，默认回收；分层查询仅终点一次。没有压力、清缓存、暂停、预读、强制驻留或联网。', '',
        '## 本批观察', '',
        'BPO／TVCache的终点PSS/N较低，但服务组current峰值较高。PSS只含七VMM，current还包含服务及后备设备提供进程等组内成本，不能仅用PSS下降判断整组内存下降。对应的服务组file也明显更高，具体数值见下表。', '',
        '前缀复用减少了实际工具累计，但本实现中的调度执行窗口更长；两者分别反映实际调用总时长和含创建／保存恢复／控制等待的墙钟时间。不能把工具累计减少直接当作任务更快。', '',
        '全部任务及主要计时／PSS／服务组计数都有3次结果。终点缓存完整量是否齐全按指标单列：缺失项不补零，也不与其他读数混算。', '',
        '## 主表：中位数与完整范围', '',
        '| 指标 | GRPO | BPO | TVCache |', '| --- | --- | --- | --- |']
    for key, label in labels.items():
        lines.append('| '+label+' | '+' | '.join(cell(summaries[s][key],6 if key=='block_core_seconds' else 3) for s in SCHEDULES)+' |')
    lines += ['', '[三次中位数与极差图](comparison.png)', '',
        'current是私有服务cgroup记账，含服务及VMM，不含组外RAM输入准备和外层控制程序；峰值覆盖完整采集窗口，含检查和清理。PSS/N是终点七VMM顺序读取的PSS之和除7，内核按映射次数分摊共享页，不是服务组总内存或七VMM峰值之和。', '',
        '普通文件小计包含原19文件、活跃lower／upper、env/run/tmp及进程FD/maps，按设备号:inode去重。查询窗口小计接受各自查询内部稳定的对象；登记后变化仍另列，原严格小计及PARTIAL保留。各对象不是同一时刻快照。块设备按活跃rdev去重，GRPO8个、恢复调度各10个；cachestat统计缓存索引页，包含内容未准备好的页，不与mincore驻留互换。', '',
        '普通文件、块设备、TMPFS、loop、file、current和PSS分别报告，不相加为总物理内存。file是组内记账，mincore是对象全局驻留；两者差额不当实际组外容量，也不加回current。', '',
        '## 均值、标准差与极差', '', '| 指标 | GRPO均值±样本标准差 | BPO均值±样本标准差 | TVCache均值±样本标准差 |', '| --- | --- | --- | --- |']
    for key, label in labels.items():
        def mean_cell(s):
            v=summaries[s][key]
            if not v['count']: return '未知'
            digits=6 if key=='block_core_seconds' else 3
            return (f"{v['mean']:.{digits}f} ± "+('未知' if v['stdev'] is None else f"{v['stdev']:.{digits}f}")
                +f"；极差{v['maximum']-v['minimum']:.{digits}f}"
                +(f"；有效{v['count']}/3" if v['missing'] else ''))
        lines.append('| '+label+' | '+' | '.join(mean_cell(s) for s in SCHEDULES)+' |')
    lines += ['', '每项有效次数及缺失数见summary.json；null不补零，缺少完整量时不拿成功小计代替。3次仅用于描述均值及波动，不给通用加速率或置信区间。所有慢值保留。', '',
        '## 逐次结果与曲线', '',
        '| 顺序／运行 | current峰值 MiB | PSS/N MiB | 普通窗口小计 MiB／状态 | 块设备成功／未知 | 调度窗口秒 | 工具累计秒 | 整轮秒 | 最低可用盘 GiB |',
        '| --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |']
    for i,r in enumerate(result_rows,1):
        name=r['curves']['directory']; b=r['block_cachestat']
        lines.append(f"| {i}／[{name}]({name}/REPORT.md) | {r['current_peak_mib']:.3f} | {r['pss_per_guest_mib']:.3f} | {r['regular_window_subtotal_mib']:.3f}／{r['regular_query_window_status']} | {b['observed_objects']}/{b['unknown_objects']} | {r['schedule_run_seconds']:.3f} | {r['tool_seconds']:.3f} | {r['controller_seconds']:.3f} | {r['minimum_disk_gib']:.3f} |")
    lines += ['', '每页有host current／file、逐VMM PSS、逐guest Cached和逐工具耗时图。host用保存的宿主墙钟；guest各自首点对齐，保存恢复间隙断线，不当共同宿主时间。', '',
        '## 调度行为和时间含义', '',
        'GRPO每次189实际／189逻辑动作，无保存恢复。BPO与TVCache每次84实际／189逻辑动作，各2次保存、6次恢复。BPO恢复后并发执行六个后缀；本项目TVCache按确定性前缀树逐子树推进，没有动态容量或淘汰。工具累计只统计实际调用，不折算到189次，不把七角色改成七次相同主干。', '',
        '调度窗口从调度器调用、创建guest前到原路径及下载完成；动作及下载窗口从第一段调用开始。工具累计是实际动作耗时之和，存在并发重叠，不等于墙钟。控制程序整轮另含终点查询、官方检查和清理；driver_seconds还含外层RAM输入准备及私有服务启停，不混作任务时间。', '',
        '| 运行 | 两次保存各秒 | 六次恢复各秒 | 全部终点墙钟／调用线程CPU秒 | 块设备整层墙钟／调用线程CPU秒 |',
        '| --- | --- | --- | --- | --- |']
    for r in result_rows:
        save='、'.join(f"{v['seconds']:.3f}" for v in r['save_call_details']) or '无'
        restore='、'.join(f"{v['seconds']:.3f}" for v in r['restore_call_details']) or '无'
        lines.append(f"| {r['curves']['directory']} | {save} | {restore} | {r['endpoint_wall_seconds']:.6f}／{r['endpoint_cpu_seconds']:.6f} | {r['block_layer_seconds']:.6f}／{r['block_layer_cpu_seconds']:.6f} |")
    lines += ['', '保存恢复按调用方计时；并发恢复的单次耗时不能相加当恢复窗口。查询成本互有包含，不逐列相加；没有关闭采样组，不能给减速率。TMPFS工作进程完整CPU未测，所列线程CPU不称为全部采集进程CPU。', '',
        '## 覆盖状态、变化及异常', '',
        '| 运行 | 普通严格状态 | 登记后变化对象／MiB | 普通查询窗口未知数 | 分层状态 |', '| --- | --- | --- | ---: | --- |']
    for r in result_rows:
        lines.append(f"| {r['curves']['directory']} | {r['regular_status']} | {r['regular_changed_before_query_objects']}／{r['regular_changed_before_query_bytes']/MIB:.6f} | {r['regular_window_unknown_objects']} | {r['cache_layers_status']} |")
    lines += ['', '详细变化、查询错误、路径及映射诊断逐项保存在各次CACHE_LAYERS.md、summary.json及原始schedule-smoke-metrics.json。查询失败保留未知，成功0保留0，没有自动重跑。', '']
    for a in anomalies:
        lines.append('- ['+a['run']+']('+a['detail']+')：'+'；'.join(a['issues'])+'；原始目录`'+a['raw']+'`。')
    for r in result_rows:
        for error in r['block_query_errors']:
            diagnostic=error.get('mapping_diagnostic',{})
            lines += ['', f"- {r['curves']['directory']}／rdev={error['rdev']}：`{error.get('error')}`；"
                f"诊断`{diagnostic.get('reason')}`。失败别名、原映射、实际重叠段和变化字段见该次逐对象报告及summary.json。"
                '该对象没有有效容量，不从其他别名补值，也不估算完整量。']
        loop=read(Path(r['metrics_path']))['cache_layers']['outer_loop']
        if loop.get('error'):
            residency=loop.get('residency',{})
            lines += ['', f"- {r['curves']['directory']}／loop：`{loop['error']}`。"
                f"前后loop配置一致={loop.get('loop_before')==loop.get('loop_after')}，"
                f"后备文件身份稳定={residency.get('identity_stable')}、大小稳定={residency.get('size_stable')}，"
                f"查询期间文件元数据变化={residency.get('metadata_changed')}。原始读数保留，但不作为有效驻留量；没有据此指定写入者或回收原因。"]
    if not anomalies: lines.append('本批登记范围内未出现非OBSERVED状态。')
    lines += ['', '无路径旧缓存、已释放且无持有者的对象、窗口外变化和guest Cached在host的完整物理归属仍不保证覆盖；这些读数用于分层观察，不直接证明部署密度。', '',
        '## 原始记录与复算', '', '批次：`'+str(batch.relative_to(ROOT))+'`。每次命令、源码快照、资源检查、原驱动结果和收尾均保留。', '']
    for r in result_rows:
        lines.append('- '+r['curves']['directory']+'：`'+str(Path(r['raw']).relative_to(ROOT))+'`。')
    lines += ['', '仅离线复算到新目录：', '', '```bash',
        'python3 -B scripts/report_schedule_layer_repeats.py \\',
        '  --batch '+str(batch.relative_to(ROOT))+' \\',
        '  --output results/新的独立目录', '```', '',
        '## 下一步', '', '本批9次完成后不追加轮次。先检查这四类指标及覆盖范围是否满足用途；若要推广三调度到其他任务，先确定一个任务的分支和可复用前缀，以保持任务语义、三种调度及采集可用为下一阶段完成条件。', '']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    for p in [out, *out.rglob('*')]: p.chmod(0o755 if p.is_dir() else 0o644)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); value=report(args.batch.resolve(),args.output.resolve())
    print('status='+value['status']+' result_dir='+str(args.output))
