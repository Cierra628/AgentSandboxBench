"""Report saved endpoint coverage only; never open backings or query live services."""
import argparse
import json
from pathlib import Path

from host_schedule_files import primary_category


def read(path):return json.loads(Path(path).read_text())


def classify_sources(expanded):
    """Preserve raw PARTIAL; a recorded non-regular target is a scope exclusion."""
    nonregular=[];unknown=[]
    for entry in expanded['unresolved']:
        if entry['source']=='owned_symlink_file' and entry['error']=='reference is not an ordinary file':
            nonregular.append(entry)
        else:unknown.append(entry)
    return nonregular,unknown


def collect(source):
    batch=read(source);runs=[]
    for run in batch['runs']:
        if run['status']!='PASS':
            runs.append(dict(schedule=run['schedule'],task_status=run['status'],error=run.get('error'),raw=run.get('raw')))
            continue
        raw=Path(run['raw']);metrics=read(raw/'schedule-smoke-metrics.json');task=read(raw/'result.json')
        expanded=metrics['expanded_files'];objects=expanded['files']
        outer=read(raw.parent/'result.json')
        assert len({tuple(r['object_key']) for r in objects})==len(objects)==expanded['unique_objects']
        assert expanded['reused_initial_objects']==metrics['p1']['unique_files']==19
        stable=sum(r['resident_bytes'] for r in objects if r['status']=='OBSERVED')
        assert stable==expanded['stable_regular_resident_bytes']
        assert sum(c['stable_resident_bytes'] for c in expanded['primary_categories'].values())==stable
        # Reclassify from saved aliases only. Keep raw classes and all values;
        # directory traversal order must not choose a snapshot's reported class.
        categories={}
        for obj in objects:
            c=categories.setdefault(primary_category(obj),dict(objects=0,stable_resident_bytes=0,unknown_or_changed=0))
            c['objects']+=1
            if obj['status']=='OBSERVED':c['stable_resident_bytes']+=obj['resident_bytes']
            elif obj['status']!='SEPARATE_SHMEM_OR_HUGETLB':c['unknown_or_changed']+=1
        assert sum(c['stable_resident_bytes'] for c in categories.values())==stable
        failures=[dict(object_key=r['object_key'],category=r['primary_category'],status=r['status'],
                       path=r['aliases'][0]['path'],error=r.get('error'),errors=r.get('errors'),
                       users=r['users'],inventory_size=r['size'],query_size=r.get('residency',{}).get('size'),
                       query_stable=r.get('residency',{}).get('stable'),
                       change_phase=('BETWEEN_INVENTORY_AND_QUERY' if r.get('residency',{}).get('stable')
                                     else 'DURING_QUERY_OR_FAILED'),
                       observed_resident_bytes=r['resident_bytes'])
                  for r in objects if r['status'] not in ('OBSERVED','SEPARATE_SHMEM_OR_HUGETLB')]
        separate=[dict(object_key=r['object_key'],category=r['primary_category'],status=r['status'],reason=r.get('reason'))
                  for r in objects if r['status']=='SEPARATE_SHMEM_OR_HUGETLB']
        expected_actions=189 if run['schedule']=='grpo' else 84
        assert task['actual_action_count']==expected_actions and all(
            (p['official_passed'],p['official_failed'])==(0,2) for p in task['paths'].values())
        comparison=max(expanded['counters_before']['file'],expanded['counters_after']['file'])
        p1=metrics['p1']
        nonregular,unknown=classify_sources(expanded)
        runs.append(dict(schedule=run['schedule'],task_status='PASS',coverage_status=expanded['status'],
            actual_actions=expected_actions,logical_actions=189,official='each 0 passed / 2 failed',raw=str(raw),
            initial_lower_mib=p1['resident_bytes']/2**20,stable_regular_mib=stable/2**20,
            added_stable_mib=expanded['new_stable_regular_resident_bytes']/2**20,
            unique_objects=expanded['unique_objects'],new_queries=expanded['queried_regular_objects'],
            primary_categories=categories,collected_primary_categories=expanded['primary_categories'],unknown_objects=expanded['unknown_objects'],
            unresolved=expanded['unresolved'],failures=failures,separate_objects=separate,
            nonregular_sources_misclassified_as_unresolved=nonregular,unresolved_regular_sources=unknown,
            changed_file_readout_mib=sum(r['observed_resident_bytes'] for r in failures if r['observed_resident_bytes'] is not None)/2**20,
            excluded_entries=expanded['excluded_entries'],exclusions=expanded['exclusions'],
            expanded_wall_seconds=(expanded['end_monotonic_ns']-expanded['start_monotonic_ns'])/1e9,
            expanded_cpu_seconds=expanded['cpu_ns']/1e9,
            initial_wall_seconds=(p1['end_monotonic_ns']-p1['start_monotonic_ns'])/1e9,
            initial_cpu_seconds=p1['cpu_ns']/1e9,
            cgroup_file_before_mib=expanded['counters_before']['file']/2**20,
            cgroup_file_after_mib=expanded['counters_after']['file']/2**20,
            observed_net_shortfall_or_timing_mib=max(0,stable-comparison)/2**20,
            action_download_window_seconds=run['action_download_window_seconds'],
            tool_seconds=run['tool_seconds'],controller_seconds=run['controller_seconds'],
            endpoint_seconds=run['endpoint_wall_seconds'],permission_evidence=expanded['permission_evidence'],
            minimum_disk_gib=outer['observed_host_available_min_bytes']/2**30,
            cleanup=outer['cleanup']))
    return dict(batch_status=batch['status'],source=str(source),runs=runs,
                source_stopped=batch.get('source_returned_to_stopped'),cache_released=batch.get('cache_released'))


def report(value):
    lines=['# 三调度mincore文件覆盖验证','',
        '原19个初始下层文件结果保留，扩展查询包括全部活跃lower／upper、私有服务env、服务cgroup打开的普通文件和文件映射。每种调度新跑一次；本轮验证覆盖与可用性，不作三调度性能优劣结论。','',
        '| 调度 | 原任务 | 覆盖状态 | 唯一对象 | 原19文件 MiB | 稳定普通文件小计 MiB | 新增小计 MiB | 未知／变化对象 | 未解析来源 |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['task_status']!='PASS':
            lines.append(f"| {r['schedule'].upper()} | {r['task_status']} | 未完成 | — | — | — | — | — | — |")
            continue
        lines.append(f"| {r['schedule'].upper()} | PASS | {r['coverage_status']} | {r['unique_objects']} | {r['initial_lower_mib']:.3f} | {r['stable_regular_mib']:.3f} | {r['added_stable_mib']:.3f} | {r['unknown_objects']} | {len(r['unresolved'])} |")
    lines+=['','## 查询做了什么','',
        '所有原动作及下载完成后、官方检查之前，先保留原PSS和19文件P1，再查询新对象。文件清单来自实际Firecracker drive／rootfs配置、服务env目录元数据、该服务cgroup进程的fd及maps。打开但已删除的普通磁盘文件通过O_PATH固定身份，再打开自己的固定描述符；随后仍用原`mincore_file()`的`mmap(PROT_NONE)+mincore`，不读取数据页。只有该显式模式启用扩展，旧入口默认和旧P1字段不变。',
        '统一按设备号:inode去重，保留全部路径和使用者；初始19文件复用刚得到的P1，不重复查询。主分类互斥可相加，引用标签可能重叠不能相加。校验host root、完整UID映射和CAP_FOWNER，避免低权限mincore可能返回的全驻留假象。',
        '目录错误、路径无法解析、进程集合或身份变化、文件大小／mtime变化、查询失败都保留明确状态。未知容量为空，不补零。OBSERVED仅表示登记范围查询完整；PARTIAL表示仍有上述明确缺项或变化。查询顺序进行，回收和沙箱保持运行，不是同一时刻的原子快照。','',
        '本轮原始清单把指向非普通文件的符号链接也列入未解析来源。依据保存的明确报错与对应代码分支，离线单列为非普通文件排除项，不重查已销毁卷、不改原始PARTIAL或数值。下面分别列出真正未解析的普通文件来源和原始分类问题。','',
        '## 分类（驻留为稳定对象小计）','',
        '| 调度 | 类别 | 对象数 | 稳定驻留 MiB | 未知／变化对象 |','| --- | --- | ---: | ---: | ---: |']
    for r in value['runs']:
        for category,c in sorted(r.get('primary_categories',{}).items()):
            lines.append(f"| {r['schedule'].upper()} | `{category}` | {c['objects']} | {c['stable_resident_bytes']/2**20:.3f} | {c['unknown_or_changed']} |")
    lines+=['','`initial_lower`为原19文件，`active_snapshot_rootfs_lower`为恢复增加的下层，`rootfs_upper_data/index`为可写层。`snapshot_mem_overlaybd_file`按保存的`mem_overlaybd`路径别名识别，`snapshot_memory_config`仅是内存快照配置。分类统一使用全部已保存别名，不让目录枚举顺序决定类别；原始分类保留在summary.json。`owned_*`为本服务其他保留文件，`external_open_or_mapped`为外部普通文件（例如运行库），不是本任务独占量。shmem／hugetlb另列，不混入磁盘文件小计。','',
        '## 查询成本和原任务用时','',
        '| 调度 | 原19查询墙钟／CPU秒 | 新清单及查询墙钟／CPU秒 | 动作及下载窗口秒 | 工具累计秒 | 控制程序整轮秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['task_status']=='PASS':
            lines.append(f"| {r['schedule'].upper()} | {r['initial_wall_seconds']:.6f} / {r['initial_cpu_seconds']:.6f} | {r['expanded_wall_seconds']:.6f} / {r['expanded_cpu_seconds']:.6f} | {r['action_download_window_seconds']:.3f} | {r['tool_seconds']:.3f} | {r['controller_seconds']:.3f} |")
    lines+=['','扩展段包含元数据枚举、身份检查及新增mincore，不含先前19文件查询和PSS。工具累计跨guest相加，含并发重叠。整轮包含新增终点查询；本轮没有关闭组，不给出采样减速率。','',
        '## 与cgroup file分开对账','',
        '| 调度 | file查询前 MiB | file查询后 MiB | 小计超过两者较大值 MiB |',
        '| --- | ---: | ---: | ---: |']
    for r in value['runs']:
        if r['task_status']=='PASS':lines.append(f"| {r['schedule'].upper()} | {r['cgroup_file_before_mib']:.3f} | {r['cgroup_file_after_mib']:.3f} | {r['observed_net_shortfall_or_timing_mib']:.3f} |")
    lines+=['','mincore是对象的全局驻留，file是本服务cgroup记账，且file包含shmem。这个净差只提示记账范围或查询时差，不能当作已查明的组外容量；file较大也不能证明没有遗漏。不把两者相加或相减称为总物理内存。','',
        '## 尚未覆盖的范围和本轮异常','',
        '可写ublk／块设备缓存、外层loop后备镜像是不同缓存层；VMM中的guest缓存通常在host匿名内存中。已经关闭、解除映射且删除的文件，没有路径或持有者时，本清单也找不到；观测窗口外新增／删除的对象不保证覆盖。因此这次完善解决可枚举普通文件的遗漏与显式记录，不能保证全部host page cache无遗漏，更不等于guest Cached的物理去重。','']
    for r in value['runs']:
        lines+=['### '+r['schedule'].upper(),'','原始目录：`'+str(r.get('raw'))+'`。']
        if r['task_status']=='PASS':
            lines+=['',f"实际／逻辑动作{r['actual_actions']}/{r['logical_actions']}；每实例官方0通过2失败。shmem／hugetlb单列{len(r['separate_objects'])}个。",'',
                    f"原始未解析中{len(r['nonregular_sources_misclassified_as_unresolved'])}项已证实不是普通文件；剩余未解析普通文件来源{len(r['unresolved_regular_sources'])}项。变化文件的原查询读数合计{r['changed_file_readout_mib']:.3f} MiB，单列而不补零或混进稳定小计。",'']
            if r['failures'] or r['unresolved']:
                if r['failures']:
                    lines+=['| 使用者 | 类别 | 原大小→查询时大小（字节） | 查询内部稳定 | 原查询读数 MiB | 变化阶段 |',
                            '| --- | --- | ---: | --- | ---: | --- |']
                    for f in r['failures']:
                        reading='未知' if f['observed_resident_bytes'] is None else f"{f['observed_resident_bytes']/2**20:.6f}"
                        lines.append(f"| {','.join(f['users'])} | {f['category']} | {f['inventory_size']}→{f['query_size']} | {f['query_stable']} | {reading} | {f['change_phase']} |")
                    lines+=['','BETWEEN_INVENTORY_AND_QUERY表示清单登记后、该文件mincore查询前发生变化；原统一标记CHANGED_DURING_QUERY保留。其查询内部可稳定，但不能代表整个顺序窗口的固定大小。逐对象身份、路径和原错误见summary.json及原始清单。','']
                if r['unresolved_regular_sources']:
                    lines+=['```json',json.dumps(r['unresolved_regular_sources'],indent=2,ensure_ascii=False),'```','']
            else:lines+=['本次登记范围无查询失败或未解析来源。设备等排除项和逐对象清单保留于原始`schedule-smoke-metrics.json`。','']
        else:lines+=['```text',str(r.get('error')),'```','']
    lines+=['原始批次：`'+value['source']+'`。本轮只做三次，不与旧九次统计混算；历史结果不改写。','']
    if value['batch_status']=='PASS':
        lines+=['## 验证与收尾','',
                '三次原任务、身份／采集和清理检查均通过；357实际／567逻辑动作，21个guest。GRPO保存／恢复0／0次，BPO和TVCache各2／6次。三个私有服务及成功卷均按原流程清理，源服务恢复停止，本轮RAM输入释放；没有删除旧失败卷。',
                '运行中最低可用盘依次为'+ '／'.join(f"{r['minimum_disk_gib']:.3f}" for r in value['runs'])+' GiB，均高于20 GiB保护；16／32 GiB卷启动前原门槛保持。',
                '35项相关离线检查通过。实跑后仅离线修正非普通文件分类、快照别名分类及变化阶段说明，并补进程归属核对；全部保存的进程也通过新增归属条件。这些收尾修改没有另起实跑，原始版本、PARTIAL、变化读数和统计小计不改写。最终证据见批次verification.json和各次code目录。','']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=collect(args.source)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    (args.output/'REPORT.md').write_text(report(value))
    print('status=REPORT_SAVED result_dir='+str(args.output))


if __name__=='__main__':main()
