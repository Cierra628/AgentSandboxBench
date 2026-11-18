"""Analyze the two saved 2026-11-07 cache gaps; never query a live object."""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import statistics

MIB=2**20


def read(path):return json.loads(Path(path).read_text())
def duration(row):return (row['end_monotonic_ns']-row['start_monotonic_ns'])/1e9


def window_candidate(loop, permission):
    """Candidate for a distinct, non-atomic view, not old strict acceptance."""
    r=loop.get('residency',{});before=loop.get('loop_before',{})
    eligible=loop.get('status')=='OBSERVED' or loop.get('error')=="ValueError('loop/backing changed during query')"
    return bool(eligible and before and before==loop.get('loop_after') and
        r.get('identity_stable') and r.get('size_stable') and
        (r.get('device'),r.get('inode'))==(before.get('backing_device'),before.get('backing_inode')) and
        r.get('mapping_length')==r.get('size') and r.get('resident_bytes') is not None and
        permission.get('host_root_cap_fowner'))


def extract(source):
    saved=read(source);assert saved['status']=='PASS' and len(saved['runs'])==9
    runs=saved['runs'];ram_row=next(r for r in runs if (r['round'],r['schedule'])==(1,'tvcache'))
    loop_row=next(r for r in runs if (r['round'],r['schedule'])==(3,'grpo'))
    rm=read(ram_row['metrics_path']);lm=read(loop_row['metrics_path'])
    devices=rm['cache_layers']['block_devices']
    missing=[o for o in devices['objects'] if o['queries']['cachestat']['status']!='OBSERVED']
    assert len(missing)==1
    obj=missing[0];q=obj['queries']['cachestat'];diag=q['mapping_diagnostic'];expected=diag['expected']
    assert diag['reason']=='EXACT_RANGE_MISSING' and q['cache_index_bytes'] is None
    overlapping=diag['overlapping_regions']
    anonymous_cover=any(r['start']<=expected['start'] and r['end']>=expected['end'] and
        (r['device'],r['inode'],r['mapping_path'])==(0,0,'') for r in overlapping)
    ram=dict(run='r1-tvcache',metrics_path=ram_row['metrics_path'],source=ram_row['source'],
        rdev=obj['rdev'],identity=obj['identity'],capacity_bytes=obj['mapping_length'],
        aliases=dict(Counter(a['source'] for a in obj['aliases'])),
        open_fd_aliases=[a for a in obj['aliases'] if a['region'] is None],
        error=q['error'],diagnostic=diag,failed_reference=q['failed_reference'],
        expected_mapping_bytes=expected['end']-expected['start'],
        actual_overlap_bytes=[r['end']-r['start'] for r in overlapping],
        expected_range_fully_covered_by_anonymous_mapping=anonymous_cover,
        query_stages=q['stages'],core_query_ran='core_query' in q['stages'],
        raw_cache_counters_available='cache_counters' in q,
        query_window_seconds=duration(q),inventory_seconds=duration(devices['stages']['inventory']),
        inventory_begin_to_failure_upper_bound_seconds=(q['end_monotonic_ns']-devices['stages']['inventory']['start_monotonic_ns'])/1e9,
        inventory_end_to_failure_seconds=(q['end_monotonic_ns']-devices['stages']['inventory']['end_monotonic_ns'])/1e9,
        successful_subtotal_bytes=devices['method_totals']['cachestat']['observed_subtotal_bytes'],
        complete_bytes=devices['method_totals']['cachestat']['complete_bytes'],
        inventory_reference_errors=devices['errors'],process_recheck_errors=rm['cache_layers']['process_errors'],
        offline_recoverable_complete_value=False)
    loop=lm['cache_layers']['outer_loop'];r=loop['residency']
    assert loop['status']=='UNKNOWN' and loop['resident_bytes'] is None
    assert window_candidate(loop,lm['expanded_files']['permission_evidence'])
    assert r['metadata_changed'] and not r['stable'] and r['size_stable']
    loop_case=dict(run='r3-grpo',metrics_path=loop_row['metrics_path'],source=loop_row['source'],
        error=loop['error'],loop_before=loop['loop_before'],loop_after=loop['loop_after'],
        loop_configuration_unchanged=loop['loop_before']==loop['loop_after'],
        residency=r,old_strict_resident_bytes=loop['resident_bytes'],
        mtime_change_inferred_from_recorded_size_stability=True,final_mtime_ns=None,
        raw_window_readout_bytes=r['resident_bytes'],new_window_view_candidate=True,
        legacy_acceptance_unchanged=True,query_window_seconds=duration(loop),
        query_window_thread_cpu_seconds=loop['cpu_ns']/1e9,
        chunks=(r['mapping_length']+256*MIB-1)//(256*MIB),
        permission_evidence=lm['expanded_files']['permission_evidence'])
    timeline=[]
    for row,metrics,observation in ((ram_row,rm,q),(loop_row,lm,loop)):
        origin=metrics['original_paths_end_monotonic_ns'];events=read(Path(row['raw'])/'events.json')
        official=[e['start_monotonic_ns'] for e in events if '-official-' in e['stage'] and '-upload-official-' not in e['stage']]
        first=min(official);assert observation['end_monotonic_ns']<first
        timeline.append(dict(run=f"r{row['round']}-{row['schedule']}",
            query_begin_after_paths_seconds=(observation['start_monotonic_ns']-origin)/1e9,
            query_end_after_paths_seconds=(observation['end_monotonic_ns']-origin)/1e9,
            first_official_after_paths_seconds=(first-origin)/1e9,
            official_started_after_failure=True))
    strict_loop=[r['loop_mib'] for r in runs if r['schedule']=='grpo' and r['loop_mib'] is not None]
    candidate_loop=[]
    for run in runs:
        if run['schedule']!='grpo':continue
        metrics=read(run['metrics_path']);item=metrics['cache_layers']['outer_loop']
        assert window_candidate(item,metrics['expanded_files']['permission_evidence'])
        candidate_loop.append(item['residency']['resident_bytes']/MIB)
    assert len(strict_loop)==2 and len(candidate_loop)==3
    effects=dict(old_tvcache_block_valid=2,old_grpo_loop_valid=2,
        current_pss_and_timing_runs_per_schedule=3,old_strict_grpo_loop_median_mib=statistics.median(strict_loop),
        illustrative_window_values_mib=candidate_loop,
        illustrative_window_median_mib=statistics.median(candidate_loop),
        candidate_values_accepted_into_old_statistics=False,
        old_tvcache_block_statistics=saved['statistics']['tvcache']['block_complete_mib'],
        old_grpo_loop_statistics=saved['statistics']['grpo']['loop_mib'])
    assert not ram['core_query_ran'] and not ram['raw_cache_counters_available'] and anonymous_cover
    return dict(status='PASS',scope='saved JSON and source only; no new cache/process/service queries',
        source=str(source),ram=ram,loop=loop_case,timeline=timeline,effects=effects,
        runtime_modified=False,legacy_results_modified=False)


def source_evidence(value):
    excerpts=[];references=[]
    driver=Path(value['ram']['source']).parent/'code'
    requests=[(driver/'host_schedule_cache_layers.py',('check_process','check_pinned_device','query_device','observe_devices','observe_loop')),
              (driver/'host_deleted_backing.py',('_validate_region',)),
              (driver/'host_file_monitor.py',('mincore_file',)),
              (driver/'schedule_smoke_metrics.py',('observe',))]
    for file,names in requests:
        text=file.read_text();lines=text.splitlines();tree=ast.parse(text)
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
                reference=dict(path=str(file),function=node.name,start_line=node.lineno,end_line=node.end_lineno)
                references.append(reference);excerpts.append(f'=== {file}:{node.lineno}-{node.end_lineno}')
                excerpts.extend(f'{i+1}: {lines[i]}' for i in range(node.lineno-1,node.end_lineno))
                excerpts.append('')
    kernel=Path('/home/yxli/linux-6.19.9-ioctl')
    for relative,begin,end in [('include/generated/utsrelease.h',1,1),('mm/mincore.c',104,151),
                              ('mm/mincore.c',269,280),('mm/filemap.c',4734,4744),('block/bdev.c',1000,1008)]:
        file=kernel/relative;lines=file.read_text().splitlines()
        references.append(dict(path=str(file),start_line=begin,end_line=end))
        excerpts.append(f'=== {file}:{begin}-{end}')
        excerpts.extend(f'{i}: {lines[i-1]}' for i in range(begin,end+1));excerpts.append('')
    return references,'\n'.join(excerpts)


def report(v):
    a=v['ram'];b=v['loop'];e=v['effects'];loop=b['residency']
    lines=['# 新版9次对照：两个缓存缺项的离线分析','',
        '只读取已保存的JSON、实际运行源码副本和同版本内核源码，没有启动沙箱、服务、缓存查询或读取负载数据页。原9次报告、统计及UNKNOWN／PARTIAL保持。','',
        '## 结论：一个缺少查询值，一个已有窗口读数但未通过原严格条件','',
        '| 缺项 | 已确认的原因 | 能否离线补入原统计 | 最小建议 |',
        '| --- | --- | --- | --- |',
        '| r1-tvcache恢复RAM设备 | 原4 MiB文件映射消失，所在范围变成匿名映射；缓存系统调用未执行 | 不能，没有缓存原始计数 | 继续保留未知；后续将固定设备查询和来源映射关系分开设计 |',
        '| r3-grpo外层loop后备 | 文件身份、大小、loop配置不变；修改时间变化触发stable=false | 原严格统计不补；已有独立窗口观测候选值 | 优先新增显式查询窗口字段，保留旧严格字段及状态 |','',
        '## TVCache：真实映射变化，不是单纯地址段拆分','',
        f"设备/dev/ublkb113，rdev={a['rdev']}，逻辑容量{a['capacity_bytes']/MIB:.0f} MiB；登记时有{a['aliases']['open_block_fd']}个组内打开描述符和{a['aliases']['vmm_ram_mapping']}个RAM映射别名。设备被打开固定，容量也在查询前再次核对。登记没有引用错误，末端进程身份／cgroup复查也没有报错。",'',
        f"失败的是late_1 PID {a['failed_reference']['process']}、设备偏移3592 MiB处的4 MiB映射。诊断EXACT_RANGE_MISSING：原地址段已找不到，实际重叠段为10 MiB匿名映射（设备号0、inode 0、无文件路径），完整覆盖原4 MiB范围。这不是同一文件映射仅拆分或合并；原精确映射条件应继续拒绝。",'',
        '这里记录的是虚拟地址映射，不是驻留页数。不能据4 MiB地址长度推断回收了4 MiB物理页，也不能把10 MiB匿名地址段当实际匿名内存增量。实际重映射调用及发起者未记录，不指定DAMON或某个回收机制。','',
        f"设备登记整段{a['inventory_seconds']*1000:.3f}毫秒；从登记开始到拒绝最多{a['inventory_begin_to_failure_upper_bound_seconds']*1000:.3f}毫秒。登记结束到拒绝{a['inventory_end_to_failure_seconds']*1000:.3f}毫秒只是时间间隔，不是映射变化的精确发生窗口，因为没有每个别名最后一次成功检查的时间。",'',
        f"失败发生在identity_before，耗时{a['query_window_seconds']*1000:.3f}毫秒；没有core_query阶段，也没有cache_counters。成功9个设备小计{a['successful_subtotal_bytes']/MIB:.6f} MiB仍有效，该4 GiB设备的实际缓存容量未知，完整量保持null。不能拿其他重复、另一RAM设备或逻辑容量填补。",'',
        '源码中query_device先核对固定描述符及所有别名，再执行cachestat，最后复核身份。任一映射别名变化即终止整个设备查询，是本次没有原始计数的直接原因。cachestat本身通过打开文件的f_mapping查询设备缓存，不依赖某段guest地址仍存在；但“该设备可查询”和“所有原映射关系仍成立”是两个不同条件。','',
        '## GRPO：修改时间变化，不是loop换了后备文件','',
        f"前后LOOP_GET_STATUS64完全一致，准确后备设备号:inode为{loop['device']}:{loop['inode']}；文件身份、16 GiB大小保持。mincore已完成64个256 MiB分段，返回{loop['resident_pages']:,}个4 KiB驻留位置，原始窗口读数{loop['resident_bytes']:,}字节，即{loop['resident_bytes']/MIB:.6f} MiB。",'',
        f"整个loop观察窗口{b['query_window_seconds']*1000:.3f}毫秒，调用线程CPU {b['query_window_thread_cpu_seconds']*1000:.3f}毫秒，均含身份和配置检查，不等于纯内核查询时间。",'',
        'mincore_file的普通文件stable要求设备号／inode／rdev相同且size、mtime不变。记录显示identity_stable=true、size_stable=true、metadata_changed=true，所以可据代码确定是mtime变化。原记录只保存查询前mtime，没有查询后mtime数值，无法计算修改时间差或精确写入时刻。observe_loop又以stable=false统一报loop/backing changed during query，因此错误文字比实际变化范围更宽。','',
        '内核mincore按打开文件的缓存索引和uptodate标志判断驻留，不用mtime判断缓存页是否存在。文件修改时间变化不会自动让这份驻留位图失效；它说明这是一个仍在变化的文件。即使mtime不变，内核也明确允许检查后缓存页变化，mincore本来就不是原子快照。','',
        f"因此，{loop['resident_bytes']/MIB:.6f} MiB可作为“固定后备文件在顺序查询窗口内的驻留观测”候选，不能改判成旧的严格稳定读数。它不是文件内容校验，也不证明窗口内缓存始终不变。谁写入或何种后台操作更新了mtime，当前记录无法确定。",'',
        '## 两次均早于官方检查，不由本轮官方测试调用直接造成','',
        '| 运行 | 查询开始／结束，距原路径完成秒 | 首次官方测试开始，距原路径完成秒 |',
        '| --- | --- | ---: |']
    for row in v['timeline']:
        lines.append(f"| {row['run']} | {row['query_begin_after_paths_seconds']:.6f}／{row['query_end_after_paths_seconds']:.6f} | {row['first_official_after_paths_seconds']:.6f} |")
    lines+=['','实际源码先完成终点采集，再进入最终检查；事件时间也一致。guest、服务及采集器此时仍在运行，不能据“29／189动作已结束”推定文件和映射已静止。没有记录逐次内存重映射事件或loop后备写入事件，不能再指定发起者。','',
        '## 对本批统计的影响','',
        'current、PSS/N、任务及工具计时、普通文件查询窗口仍各有三次，不受这两个终点缺项影响。TVCache块设备完整量及GRPO loop严格驻留各保留2／3次；对应成功小计、完整量和未知仍分开，不剔除整次运行。','',
        f"GRPO原严格loop中位数{e['old_strict_grpo_loop_median_mib']:.6f} MiB来自两次有效值。仅为说明口径影响，若另建查询窗口视图并加入本次原始{loop['resident_bytes']/MIB:.6f} MiB，三次中位数会是{e['illustrative_window_median_mib']:.6f} MiB。这不是补齐后的正式统计：新视图还需明确接受条件，原报告数字不变。TVCache没有可做同类换算的原始计数。",'',
        '## 推荐的最小修改及验证条件','',
        '1. **先处理loop，显式增加查询窗口字段。** 不改mincore_file的旧stable含义，也不修改原resident_bytes／UNKNOWN。新字段只在固定描述符身份、容量、前后准确loop配置及挂载归属均符合时给值；mtime变化另列。补存前后mtime和变化字段，错误区分身份／大小／配置变化与只有元数据变化。所有旧入口默认行为保持。','',
        '   离线验证覆盖：仅mtime变化可得到新窗口读数但原严格值仍未知；inode、大小、loop后备、offset／sizelimit／有效direct I/O变化以及查询失败时，新值仍未知；句柄释放、旧正常路径和历史数据读回保持。','',
        '2. **RAM先拆清指标范围，再决定接入。** 若目标是“登记且组内仍持有的设备全局缓存”，可设计独立的固定描述符查询状态，并把各VMM映射关系变化另列；该状态不表示每个guest仍映射原地址，更不表示guest缓存物理归属。固定设备身份／容量、来源进程和组内持有关系仍须前后核对，不能遇错换别名或重试补值。原所有别名都稳定的严格状态不改。','',
        '   离线验证覆盖：同一固定设备有映射变化时仅新设备观测可用、原来源关系仍不完整；PID／cgroup、固定设备、容量或主要持有关系变化时新值未知；同rdev矛盾、查询前后失败、去重及未执行查询不补零。实际开销和有效性留到另行授权的真实验证。','',
        '先做第1项的显式离线实现最合适：已有原始读数和精确拒绝条件，不需要改动数据页或增加运行时等待。第2项涉及设备查询和映射归属的指标拆分，不能只删掉_validate_region检查来让报表通过。','',
        '## 证据与复算','',
        '原始批次`.artifacts/schedule-layer-repeats-20261107T084610Z/`；两次完整原始路径、诊断、计时、原统计及候选值在summary.json。source-evidence.txt保存实际执行源码函数和同版本内核摘录，带原始路径及行号。','',
        '```bash','python3 -B scripts/analyze_schedule_layer_gaps.py \\',
        '  --source results/prettier-schedule-layer-repeats-20261107/summary.json \\',
        '  --output results/新的离线分析目录','```','',
        '本轮到分析结束，没有改采集代码、接受条件或历史结果。下一步仅建议显式loop窗口字段的离线实现及上述回归，不启动沙箱；RAM指标拆分另按新范围接入，不自动补跑9次。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();assert not args.output.exists()
    value=extract(args.source.resolve());refs,text=source_evidence(value)
    value['source_references']=refs
    args.output.mkdir(parents=True)
    (args.output/'summary.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (args.output/'source-evidence.txt').write_text(text)
    (args.output/'REPORT.md').write_text(report(value))
    print('status=PASS result_dir='+str(args.output))


if __name__=='__main__':main()
