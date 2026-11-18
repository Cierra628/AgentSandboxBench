"""Inventory saved schedule metrics; no live process, cache, or service queries."""
import argparse
import csv
import json
from pathlib import Path

from report_schedule_metrics_offline import normalize, read, rows
from report_schedule_expanded_files import collect as expanded_collect
from report_schedule_cache_layers import collect as layers_collect


MIB = 2**20


def block_metric(layers, method):
    """Keep not collected, not requested, partial and actual zero distinct."""
    devices = layers.get('block_devices', {})
    total = devices.get('method_totals', {}).get(method)
    if total is not None:
        return dict(total)
    if method == 'mincore' and devices:
        return dict(status=devices['status'], observed_subtotal_bytes=devices['stable_resident_bytes'],
                    complete_bytes=devices['stable_resident_bytes'] if devices['status'] == 'OBSERVED' else None)
    return dict(status='NOT_COLLECTED', observed_subtotal_bytes=None, complete_bytes=None)


def record(run, expanded, cohort):
    raw = Path(run['raw']); metrics = read(raw/'schedule-smoke-metrics.json')
    task = read(raw/'result.json'); host = rows(raw/'host/memory-samples.tsv')
    assert run['status'] == task['status'] == 'PASS' and host
    assert task['schedule'] == run['schedule']
    assert len(task['paths']) == metrics['pss']['denominator'] == 7
    assert all((p['official_passed'], p['official_failed']) == (0, 2) for p in task['paths'].values())
    pss = metrics['pss']; p1 = metrics['p1']; e = metrics['expanded_files']
    assert all(metrics['cgroup'] == '/sys/fs/cgroup'+p['identity']['cgroup'] for p in pss['processes'])
    assert run['pss_total_mib']*MIB == pss['total_bytes']
    assert run['pss_per_sandbox_mib']*MIB == pss['per_sandbox_bytes']
    assert run['mincore_p1_mib']*MIB == p1['resident_bytes']
    assert expanded['stable_regular_mib']*MIB == e['stable_regular_resident_bytes']
    query_window = e.get('query_window_regular')
    layers = metrics.get('cache_layers', {})
    result=dict(cohort=cohort, schedule=run['schedule'], raw=str(raw),
        metrics_path=str(raw/'schedule-smoke-metrics.json'),
        host_samples_path=str(raw/'host/memory-samples.tsv'),
        host_scope='private_service_cgroup', cgroup=metrics['cgroup'], n=7,
        current_peak_bytes=max(int(r['cgroup_current_bytes']) for r in host),
        current_peak_scope='whole recorded host sampling window, not execution-only',
        current_at_initial_p1_bytes=p1['counters_before']['current'],
        file_at_initial_p1_bytes=p1['counters_before']['file'],
        outside_cgroup_bytes=None, host_physical_total_bytes=None,
        pss_total_bytes=pss['total_bytes'], pss_per_guest_bytes=pss['per_sandbox_bytes'],
        initial_lower_resident_bytes=p1['resident_bytes'], initial_lower_objects=p1['unique_files'],
        regular_status=e['status'], regular_objects=e['unique_objects'],
        regular_disk_objects=sum(r['status'] != 'SEPARATE_SHMEM_OR_HUGETLB' for r in e['files']),
        regular_stable_subtotal_bytes=e['stable_regular_resident_bytes'],
        regular_changed_objects=sum(r['status'] == 'CHANGED_DURING_QUERY' for r in e['files']),
        regular_unknown_or_changed_objects=e['unknown_objects'],
        regular_changed_readout_bytes=round(expanded['changed_file_readout_mib']*MIB),
        regular_raw_unresolved_sources=len(e['unresolved']),
        regular_query_window_status=query_window['status'] if query_window else 'NOT_RECORDED',
        regular_query_window_bytes=query_window['observed_subtotal_bytes'] if query_window else None,
        block_mincore=block_metric(layers, 'mincore'), block_cachestat=block_metric(layers, 'cachestat'),
        tmpfs_status=layers.get('deleted_tmpfs', {}).get('status', 'NOT_COLLECTED'),
        tmpfs_cache_bytes=layers.get('deleted_tmpfs', {}).get('whole_backing_cache_bytes'),
        loop_status=layers.get('outer_loop', {}).get('status', 'NOT_COLLECTED'),
        loop_resident_bytes=layers.get('outer_loop', {}).get('resident_bytes'),
        actual_actions=run['actual_actions'], logical_actions=run['logical_actions'],
        save_calls=len(run['save_calls']), restore_calls=len(run['restore_calls']),
        controller_seconds=run['controller_seconds'], tool_seconds=run['tool_seconds'],
        action_download_window_seconds=run['action_download_window_seconds'],
        endpoint_wall_seconds=run['endpoint_wall_seconds'], endpoint_cpu_seconds=run['endpoint_cpu_seconds'])
    window=layers.get('outer_loop',{}).get('query_window')
    if window is not None:
        result.update(loop_query_window_status=window['status'],loop_query_window_bytes=window['resident_bytes'])
    fixed=layers.get('block_devices',{}).get('fixed_ram_totals')
    if fixed is not None:
        result.update(fixed_ram_status=fixed['status'],fixed_ram_cache_bytes=fixed['complete_bytes'])
    return result


def image_objects(metrics):
    keys={(f['device'], f['inode']) for f in metrics['p1']['files']}
    return sorted((r['reference']['digest'], r['reference']['size'])
        for r in next(iter(metrics['rootfs_inventory']['guests'].values()))['lowers']
        if (r.get('device'), r.get('inode')) in keys)


def collect(source, bpo_layers_source=None):
    # These existing readers validate saved task actions, identities, PSS and file deduplication.
    normalized = normalize(source); expanded = expanded_collect(source); batch = read(source)
    by_schedule = {r['schedule']: r for r in expanded['runs']}
    assert batch['source_returned_to_stopped'] and batch['cache_released']
    main = [record(run, by_schedule[run['schedule']], 'same_batch') for run in batch['runs']]
    for row in main:
        assert not read(Path(row['metrics_path'])).get('cache_layers'), 'main batch must retain its original ordinary-file scope'
    supplement = []; supplement_checks = None
    if bpo_layers_source is not None:
        checked = layers_collect(bpo_layers_source)
        run = read(bpo_layers_source)['runs'][0]
        e = dict(checked['regular'], changed_file_readout_mib=checked['expanded']['query_window_regular']['changed_before_query_bytes']/MIB)
        supplement = [record(run, e, 'separate_bpo_validation')]
        assert image_objects(read(Path(main[0]['metrics_path']))) == image_objects(read(Path(supplement[0]['metrics_path'])))
        supplement_checks = checked['checks']
    return dict(status='PASS', scope='OFFLINE_SAVED_RESULTS_ONLY',
        sources=dict(main=str(source), bpo_layers=str(bpo_layers_source) if bpo_layers_source else None),
        same_batch=main, separate_bpo_validation=supplement,
        checks=dict(main_normalized=normalized['status'], main_file_inventory=expanded['batch_status'],
                    supplemental=supplement_checks),
        units='bytes; seconds; report MiB = bytes / 2**20; no cross-run aggregation')


def number(value, scale=1):
    return '—' if value is None else f'{value/scale:.3f}'


def tables(records):
    lines=['| 调度 | 服务组current全采集窗口峰值 MiB | 原19文件P1前current MiB | 同点file MiB | 七VMM终点PSS MiB | PSS/N MiB |',
           '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in records:
        values=[number(r[k], MIB) for k in ('current_peak_bytes','current_at_initial_p1_bytes','file_at_initial_p1_bytes','pss_total_bytes','pss_per_guest_bytes')]
        lines.append('| '+r['schedule'].upper()+' | '+' | '.join(values)+' |')
    lines+=['', '| 调度 | 原19文件mincore MiB | 普通文件严格小计 MiB／状态 | 块设备cachestat完整量 MiB／状态 |',
            '| --- | ---: | --- | --- |']
    for r in records:
        b=r['block_cachestat']
        lines.append(f"| {r['schedule'].upper()} | {number(r['initial_lower_resident_bytes'],MIB)} | {number(r['regular_stable_subtotal_bytes'],MIB)}／{r['regular_status']} | {number(b['complete_bytes'],MIB)}／{b['status']} |")
    lines+=['', '| 调度 | 实际／逻辑动作 | 保存／恢复 | 动作及下载窗口秒 | 实际工具累计秒 | 控制程序整轮秒 | 全部终点查询秒 |',
            '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for r in records:
        values=[number(r[k]) for k in ('action_download_window_seconds','tool_seconds','controller_seconds','endpoint_wall_seconds')]
        lines.append(f"| {r['schedule'].upper()} | {r['actual_actions']}/{r['logical_actions']} | {r['save_calls']}/{r['restore_calls']} | "+' | '.join(values)+' |')
    if any('loop_query_window_status' in r for r in records):
        lines+=['','| 调度 | loop原严格状态／驻留 MiB | loop查询窗口状态／驻留 MiB |', '| --- | --- | --- |']
        for r in records:
            lines.append(f"| {r['schedule'].upper()} | {r['loop_status']}／{number(r['loop_resident_bytes'],MIB)} | "
                f"{r.get('loop_query_window_status','NOT_RECORDED')}／{number(r.get('loop_query_window_bytes'),MIB)} |")
        lines+=['','loop两列来自同一次查询，不相加；新窗口允许mtime变化，原严格UNKNOWN和缺项规则保持。旧数据未记录时不追补。']
    if any('fixed_ram_status' in r for r in records):
        lines+=['','| 调度 | 独立固定RAM设备查询状态 | 完整缓存索引量 MiB |', '| --- | --- | ---: |']
        for r in records:
            lines.append(f"| {r['schedule'].upper()} | {r.get('fixed_ram_status','NOT_RECORDED')} | {number(r.get('fixed_ram_cache_bytes'),MIB)} |")
        lines+=['','独立设备查询不回填原严格设备小计；NOT_APPLICABLE／NOT_RECORDED都不是实测0。']
    return lines


def report(value):
    lines=['# 三调度现有指标统一表：离线核对','',
        '主表使用2026-11-07普通文件扩展批次，GRPO／BPO／TVCache各一次；后续BPO分层验证另表。没有新运行，没有跨批次合并、平均或补值。全部每实例官方结果仍0通过2失败。','',
        '## 同一批次三调度：四类核心量已可取，缓存范围有限','']
    lines+=tables(value['same_batch'])
    lines+=['', 'current取私有服务cgroup，不是包含外层准备／控制程序的整个实验组。峰值覆盖完整host采集窗口，含检查及清理；这里没有推算执行阶段峰值。终点current和file取原19文件P1查询前的一次计数，PSS在它之前顺序读取，不是同一瞬间快照。', '',
        'PSS/N只含七VMM。原19文件已包含在普通文件小计中；普通文件全局驻留、服务组file和current不能相加。三组尚未启用块设备、TMPFS整后备及准确loop查询，因此未测值保留null，不从后续BPO借数填入。表中—表示没有该读数，NOT_COLLECTED为未采集，NOT_RECORDED为旧版本未记录；均不是实测0。', '',
        '## 普通文件覆盖：保留原始PARTIAL','',
        '| 调度 | 登记对象／磁盘普通文件 | 变化对象 | 变化文件查询读数 MiB | 原始未解析来源 | 各文件查询窗口小计 MiB／状态 |',
        '| --- | --- | ---: | ---: | ---: | --- |']
    for r in value['same_batch']:
        lines.append(f"| {r['schedule'].upper()} | {r['regular_objects']}/{r['regular_disk_objects']} | {r['regular_changed_objects']} | {number(r['regular_changed_readout_bytes'],MIB)} | {r['regular_raw_unresolved_sources']} | {number(r['regular_query_window_bytes'],MIB)}／{r['regular_query_window_status']} |")
    lines+=['', '三组原始各14项未解析已在原覆盖报告中离线确认为非普通文件排除项；原始标记和PARTIAL保持，不重写旧JSON。BPO另8个upper在登记后、各自查询前变化，24.906 MiB单列；不混入原严格小计。旧三组没有query_window_regular字段，本表不按新方法追补。', '',
        '三组保留目录枚举只覆盖env，run／tmp当时仅可能经FD/maps被发现；已关闭且没有引用的保留文件不保证覆盖。未列路径旧缓存、已释放对象、窗口外变化及guest Cached的实际host物理归属仍未知。', '',
        '## 后续单次BPO：补充方法验证，不替代主表','']
    if value['separate_bpo_validation']:
        lines+=tables(value['separate_bpo_validation'])
        r=value['separate_bpo_validation'][0]
        lines+=['', f"本次块设备cachestat为{r['block_cachestat']['observed_objects']}个成功、{r['block_cachestat']['unknown_objects']}个未知，完整量{number(r['block_cachestat']['complete_bytes'],MIB)} MiB。块设备mincore状态{r['block_mincore']['status']}，读数null，不是驻留0。普通文件还枚举env/run/tmp，各文件查询窗口小计{number(r['regular_query_window_bytes'],MIB)} MiB；原严格小计与PARTIAL独立保留。", '',
            f"TMPFS整后备{number(r['tmpfs_cache_bytes'],MIB)} MiB／{r['tmpfs_status']}、准确loop后备{number(r['loop_resident_bytes'],MIB)} MiB／{r['loop_status']}，各自独立，不与普通文件或块设备相加。", '',
            f"这次只支持‘BPO中显式分层查询可用’；GRPO／TVCache没有同组合结果，不能把该行数据移到另两行。全部终点{r['endpoint_wall_seconds']:.6f}秒是这次观测成本，不是三调度通用成本或减速率。"]
    else:lines+=['本次未提供补充来源，所有补充结果保持缺失。']
    lines+=['', '## 当前能交付什么、还缺什么','',
        '三种调度的PSS/N、指定文件驻留、任务窗口和工具累计都有保存结果；服务组current和file曲线也存在。三个旧普通文件覆盖状态均PARTIAL，不表示任务或采集器失败。', '',
        '无路径旧缓存和实际页记账归属仍缺证据。file小于指定文件全局驻留不等于已确认组外容量，也不能用净差修正current。不同调度执行动作数量及共享前缀行为不同，本表每种仅一次，不排名、不计算部署密度或采样减速。已有九次正式重复保持独立，不与本表平均。', '',
        '## 原始来源','']
    for r in value['same_batch']+value['separate_bpo_validation']:
        cohort='主表' if r['cohort']=='same_batch' else '后续单次验证'
        lines+=['- '+cohort+'／'+r['schedule'].upper()+'：`'+r['raw']+'`。']
    lines+=['', '[原三调度文件覆盖报告](../schedule-mincore-coverage-20261107/REPORT.md) · [后续BPO分层报告](../schedule-block-cachestat-20261107/REPORT.md) · [独立的九次正式重复](../prettier-schedule-repeats-20261106/REPORT.md)', '',
        '参数、字节原值、状态、实际cgroup和保存文件路径在summary.json和runs.tsv。复算需要本机保留的原始结果，只有Git代码不能重建这些读数；输出目录必须是新的。以下命令只读取保存文件，不启动服务或查询真实缓存：', '',
        '```bash',
        'python3 -B scripts/report_schedule_inventory.py \\',
        '  --source '+value['sources']['main']+' \\',
        *(['  --bpo-layers-source '+value['sources']['bpo_layers']+' \\'] if value['sources']['bpo_layers'] else []),
        '  --output results/schedule-metrics-inventory-新的唯一批次',
        '```', '',
        '本轮离线完成。若下一步要让三调度都具有相同的分层缓存查询结果，最小范围是GRPO和TVCache各一次完整验证，沿用显式cachestat及原身份／任务／清理条件，不做重复；需另获确认后再启动。本表仍不能据此保证全部host缓存无遗漏。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--bpo-layers-source',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();assert not args.output.exists(), 'output must be new'
    value=collect(args.source,args.bpo_layers_source)
    args.output.mkdir(parents=True)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    (args.output/'REPORT.md').write_text(report(value))
    with (args.output/'runs.tsv').open('w') as f:
        all_rows=value['same_batch']+value['separate_bpo_validation']
        fields=list(all_rows[0]);w=csv.DictWriter(f,fieldnames=fields,delimiter='\t');w.writeheader()
        for row in all_rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v for k,v in row.items()})
    print('status=PASS result_dir='+str(args.output))


if __name__=='__main__':main()
