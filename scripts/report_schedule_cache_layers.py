"""Report one saved seven-role cache-layer run; never open live cache objects."""
import argparse
from collections import Counter
import json
import re
from pathlib import Path

from run_schedule_smoke import summarize, expanded_summary

MIB=2**20


def seconds(row):return (row['end_monotonic_ns']-row['start_monotonic_ns'])/1e9

def mib(value):return '未知' if value is None else f'{value/MIB:.6f}'


def ram_log_matches(obj, row):
    paths={a['region']['mapping_path'] for a in obj['aliases'] if a['source']=='vmm_ram_mapping'}
    # ublk dev_id is a name/id, not Linux's dynamically assigned block minor.
    return row['path'] in paths and row['path']=='/dev/ublkb'+str(row['dev_id'])


def method_costs(devices):
    values={}
    for method in devices.get('method_totals',{}):
        queries=[r['queries'][method] for r in devices['objects'] if method in r['queries']]
        if not queries:continue
        stages={}
        for name in ('identity_before','core_query','identity_after'):
            selected=[q['stages'][name] for q in queries if name in q['stages']]
            stages[name]=dict(objects=len(selected),wall_seconds=sum(map(seconds,selected)),
                cpu_seconds=sum(s['cpu_ns'] for s in selected)/1e9)
        good=[q for q in queries if q['status']=='OBSERVED']
        values[method]=dict(attempted_objects=len(queries),observed_objects=len(good),
            wall_seconds=sum(map(seconds,queries)),cpu_seconds=sum(q['cpu_ns'] for q in queries)/1e9,
            observed_wall_seconds=sum(map(seconds,good)),observed_cpu_seconds=sum(q['cpu_ns'] for q in good)/1e9,
            stages=stages)
    return values


def collect(source):
    batch=json.loads(source.read_text());assert len(batch['runs'])==1
    run=batch['runs'][0];assert run['schedule'] in ('grpo','bpo','tvcache') and run['status']=='PASS'
    storage=Path(run['storage']);base=summarize(storage);regular=expanded_summary(storage)
    assert base['schedule']==run['schedule']
    raw=storage/'prettier6604';metrics=json.loads((raw/'schedule-smoke-metrics.json').read_text())
    expanded=metrics['expanded_files'];layers=metrics['cache_layers'];devices=layers['block_devices']
    keys=[tuple(r['object_key']) for r in devices['objects']];assert len(keys)==len(set(keys))
    drive_rdevs={d['rdev'] for entries in metrics['drives'].values() for d in entries if d['block_device']}
    captured={r['rdev'] for r in devices['objects']}
    assert drive_rdevs<=captured
    ram=[r for r in devices['objects'] if any(a['source']=='vmm_ram_mapping' for a in r['aliases'])]
    ram_count=0 if run['schedule']=='grpo' else 2
    assert len(drive_rdevs)==8 and len(devices['objects'])==8+ram_count and len(ram)==ram_count
    assert sorted(len({a['role'] for a in r['aliases'] if a['source']=='vmm_ram_mapping'}) for r in ram)==[3]*ram_count
    assert all(a['role']!='backbone' for r in ram for a in r['aliases'] if a['source']=='vmm_ram_mapping')
    log_path=storage/'service-logs/server.log';ram_logs=[]
    for number,line in enumerate(log_path.read_text().splitlines(),1):
        text=re.sub(r'\x1b\[[0-9;]*m','',line)
        if 'created or acquired shared read-only ublk device' in text and '/memory/image.json' in text:
            path=re.search(r'path=(/dev/ublkb(\d+))',text)
            if path:ram_logs.append(dict(source=str(log_path),line_number=number,path=path[1],dev_id=int(path[2]),text=text))
    for obj in ram:
        assert any(ram_log_matches(obj,row) for row in ram_logs)
    tmpfs=layers['deleted_tmpfs'];tmp_keys=[(r['device'],r['inode']) for r in tmpfs['objects']]
    assert len(tmp_keys)==len(set(tmp_keys))
    expected={(r['device'],r['inode']) for r in expanded['files'] if r['status']=='SEPARATE_SHMEM_OR_HUGETLB'}
    missing=tmpfs['missing_sources']
    assert set(tmp_keys)|{tuple(r['object_key'][1:]) for r in missing}==expected
    assert expanded['owned_roots']==[str(storage/'mount'/n) for n in ('env','run','tmp')]
    for row in devices['objects']:
        # Saved old runs have no method map; their mincore contract remains.
        if 'queries' not in row:
            if row['status']=='OBSERVED':
                assert row['residency']['stable'] and row['resident_bytes']==row['residency']['resident_bytes']
                assert row['rdev']==row['residency']['rdev'] and row['mapping_length']==row['residency']['mapping_length']
            else:assert row['resident_bytes'] is None
            continue
        for method,q in row['queries'].items():
            field='resident_bytes' if method=='mincore' else 'cache_index_bytes'
            if q['status']=='OBSERVED':
                assert q['range']==dict(offset=0,length=row['mapping_length'])
                if method=='mincore':
                    assert q['residency']['stable'] and q[field]==q['residency'][field]
                    assert row['rdev']==q['residency']['rdev'] and row['mapping_length']==q['residency']['mapping_length']
                else:assert q[field]==q['cache_counters']['nr_cache']*expanded['page_size']
            else:assert q[field] is None
        assert row['resident_bytes']==row['queries'].get('mincore',{}).get('resident_bytes')
        assert row['cache_index_bytes']==row['queries'].get('cachestat',{}).get('cache_index_bytes')
    if 'method_totals' in devices:
        for method,total in devices['method_totals'].items():
            if total['status']=='NOT_REQUESTED':assert total['observed_subtotal_bytes'] is None;continue
            field='resident_bytes' if method=='mincore' else 'cache_index_bytes'
            values=[r['queries'][method][field] for r in devices['objects'] if r['queries'][method]['status']=='OBSERVED']
            assert total['observed_objects']==len(values) and total['observed_subtotal_bytes']==sum(values)
            assert total['complete_bytes']==(sum(values) if total['status']=='OBSERVED' else None)
    if 'fixed_ram_totals' in devices:
        from host_schedule_cache_layers import ram_anchor_plan
        assert devices['method']=='cachestat'
        probes=[o['fixed_ram_device'] for o in devices['objects'] if 'fixed_ram_device' in o]
        good=[p for p in probes if p['query']['status']=='OBSERVED'];total=devices['fixed_ram_totals']
        assert len(probes)==ram_count
        for original in devices['objects']:
            is_ram=any(a['source']=='vmm_ram_mapping' for a in original['aliases'])
            assert ('fixed_ram_device' in original)==is_ram
        for p in probes:
            q=p['query'];relation=p['source_relationships']
            assert relation['status'] in ('OBSERVED','CHANGED','UNKNOWN')
            original=next(o for o in devices['objects'] if o['rdev']==p['rdev'])
            planned=ram_anchor_plan(original['aliases'],{int(k):v for k,v in expanded['process_identities'].items()})
            assert p['anchor']==planned[p['rdev']]
            if relation['status']=='CHANGED':
                assert 'mapping_diagnostic' in relation or any('mapping_diagnostic' in e for e in relation.get('inventory_errors',[]))
            if relation['status']=='OBSERVED':
                assert relation['before_complete'] and relation['after_complete']
                assert original['queries']['cachestat']['status']=='OBSERVED'
            if q['status']=='OBSERVED':
                assert p['anchor']['source']=='open_block_fd' and p['anchor']['region'] is None
                assert p['anchor']['rdev']==p['rdev'] and p['anchor']['process'] in map(int,expanded['process_identities'])
                assert q['range']==dict(offset=0,length=original['mapping_length'])
                assert {'identity_before','core_query','identity_after'}<=q['stages'].keys()
                assert q['cache_index_bytes']==q['cache_counters']['nr_cache']*expanded['page_size']
            else:assert q['cache_index_bytes'] is None
        assert total['observed_objects']==len(good) and total['unknown_objects']==len(probes)-len(good)
        subtotal=sum(p['query']['cache_index_bytes'] for p in good) if probes else None
        assert total['observed_subtotal_bytes']==subtotal
        assert total['complete_bytes']==(subtotal if probes and len(good)==len(probes) else None)
        assert total['status']==('NOT_APPLICABLE' if not probes else 'OBSERVED' if len(good)==len(probes) else 'PARTIAL')
    outer=json.loads((storage/'result.json').read_text())
    loop=layers['outer_loop']
    if loop['status']=='OBSERVED':
        assert loop['loop_before']==loop['loop_after']
        assert (loop['residency']['device'],loop['residency']['inode'])==(loop['loop_before']['backing_device'],loop['loop_before']['backing_inode'])
    if 'query_window' in loop:
        window=loop['query_window']
        if window['status']=='OBSERVED':
            residency=loop['residency'];before=residency['metadata_before'];after=residency['metadata_after']
            assert loop['loop_before']==loop['loop_after'] and residency['identity_stable'] and residency['size_stable']
            assert all(before[k]==after[k] for k in ('device','inode','rdev','size'))
            assert all(residency[k]==before[k] for k in ('device','inode','rdev','size'))
            assert residency['mapping_length']==before['size']
            assert (before['device'],before['inode'])==(loop['loop_before']['backing_device'],loop['loop_before']['backing_inode'])
            assert window['range']==dict(offset=0,length=residency['mapping_length'])
            assert window['resident_bytes']==residency['resident_bytes'] and not window['rejected_checks']
            if loop['status']=='UNKNOWN':
                assert window['strict_rejection_reason']=='MTIME_CHANGED' and not residency['stable']
                assert set(residency['metadata_changes'])=={'mtime_ns'}
        else:assert window['status']=='UNKNOWN' and window['resident_bytes'] is None
    assert batch['status']=='PASS' and batch['source_returned_to_stopped'] and batch['cache_released']
    value=dict(source=str(source),raw=str(raw),run=base,regular=regular,expanded=expanded,layers=layers,
        original_p1_cpu_seconds=metrics['p1']['cpu_ns']/1e9,
        ordinary_statuses=dict(Counter(r['status'] for r in expanded['files'])),
        minimum_disk_gib=outer['observed_host_available_min_bytes']/2**30,cleanup=outer['cleanup'],
        source_stopped=True,cache_released=True,ram_log_bindings=ram_logs,
        checks=dict(task_and_official='PASS',seven_vmm_pss='PASS',drive_and_ram_identity='PASS',
                    per_layer_dedup='PASS',private_write_roots='PASS',queries_and_unknowns_recorded='PASS',cleanup='PASS'))
    if 'method_totals' in devices:value['block_method_costs']=method_costs(devices)
    return value


def report(v):
    e=v['expanded'];l=v['layers'];d=l['block_devices'];t=l['deleted_tmpfs'];loop=l['outer_loop'];r=v['run']
    schedule=r['schedule'].upper()
    ram_count=sum(any(a['source']=='vmm_ram_mapping' for a in obj['aliases']) for obj in d['objects'])
    mincore_status=d.get('method_totals',{}).get('mincore',{}).get('status',d['status'])
    lines=['# '+schedule+'缓存分层完整运行验证','',
        f"本轮只跑一次完整Prettier #6604七沙箱{schedule}：{r['actual_actions']}个实际动作／189个逻辑动作，{len(r['save_calls'])}次保存、{len(r['restore_calls'])}次恢复；每实例官方结果仍为0通过2失败。原任务、身份及收尾通过，新增缓存查询的覆盖状态另列。历史三组及九次结果不改写。",'',
        '| 查询范围 | 状态 | 唯一对象数 | 稳定观测小计 MiB | 墙钟秒 | 调用线程CPU秒 |',
        '| --- | --- | ---: | ---: | ---: | ---: |',
        f"| 原19个初始lower（旧字段） | OBSERVED | 19 | {mib(r['mincore_p1_mib']*MIB)} | {r['mincore_wall_seconds']:.6f} | {v['original_p1_cpu_seconds']:.6f} |",
        f"| 普通文件（含原19、env/run/tmp、FD/maps） | {e['status']} | {e['unique_objects']} | {mib(e['stable_regular_resident_bytes'])} | {seconds(e):.6f} | {e['cpu_ns']/1e9:.6f} |",
        f"| ublk块设备mincore（drive及RAM） | {mincore_status} | {len(d['objects'])} | {mib(d['stable_resident_bytes'])} | {seconds(d):.6f} | {d['cpu_ns']/1e9:.6f} |",
        f"| 已删除TMPFS整后备 | {t['status']} | {len(t['objects'])} | {mib(t['whole_backing_cache_bytes'])} | {seconds(t):.6f} | {t['cpu_ns']/1e9:.6f} |",
        f"| 外层loop后备volume.ext4 | {loop['status']} | 1 | {mib(loop['resident_bytes'])} | {seconds(loop):.6f} | {loop['cpu_ns']/1e9:.6f} |",'',
        '各行属于不同范围和查询窗口，不相加成为总物理内存。普通文件已经包含原19文件；不能再把第一行加进去。块设备缓存与其普通后备文件也不是相同查询对象。块设备行的时间是该层全部方法、登记和检查的总成本，不单指mincore。',
        f"新增三层查询合计墙钟{seconds(l):.6f}秒，调用线程CPU{l['cpu_ns']/1e9:.6f}秒；TMPFS工作进程内查询函数CPU为"+('未知' if l['worker_cpu_ns'] is None else f"{l['worker_cpu_ns']/1e9:.6f}秒")+'。后者不含解释器启动、结果序列化和退出的CPU；这些步骤的墙钟已包括在TMPFS行，完整进程CPU未采到。不把所列线程CPU与函数CPU称为整进程总成本。', '',
        '## '+str(len(d['objects']))+'个块设备的身份与去重','',
        ('14个块设备drive引用对应8个块设备；另2个恢复RAM后备，各被3个恢复分支映射。冷起backbone不要求有恢复RAM后备。' if ram_count else
         '14个块设备drive引用对应8个块设备；本次七个guest均冷起，没有恢复RAM后备，不强求该对象存在。')+
        '另28个普通文件备用drive属于普通文件清单，不重复计入块设备。'+
        '按活跃期rdev合并并持有已核实的打开描述符，设备节点别名、使用者和映射段数不重复计数；所有别名保留节点设备号:inode:rdev及完整映射字段。','',
        '| rdev | 用途 | 使用角色 | 容量 MiB | mincore驻留 MiB | mincore状态 | 全方法墙钟秒 |',
        '| --- | --- | --- | ---: | ---: | --- | ---: |']
    for obj in d['objects']:
        ram=any(a['source']=='vmm_ram_mapping' for a in obj['aliases'])
        roles=sorted({a['role'] for a in obj['aliases'] if a.get('role')})
        status=obj['queries'].get('mincore',{}).get('status','NOT_REQUESTED') if 'queries' in obj else obj['status']
        lines.append(f"| {obj['rdev']} | {'恢复RAM' if ram else 'drive／其他打开块设备'} | {','.join(roles)} | {mib(obj.get('mapping_length'))} | {mib(obj['resident_bytes'])} | {status} | {seconds(obj):.6f} |")
    lines+=['',('两个RAM设备另与保存服务日志中的memory/image.json及实际ublkb设备名对应；日志行号和对应字段保存于summary.json的ram_log_bindings。' if ram_count else
               '本次没有恢复RAM后备，相关日志绑定表为空；这不表示恢复RAM驻留为0。'), '',
        '## 普通文件与未知项','',
        '本次只增加私有卷的run和tmp两处目录，不遍历宿主全局目录；它们与env、FD、maps共用设备号:inode去重表。',
        'run／tmp与env统一去重；逐对象路径、大小及查询值保留在summary.json，不以目录为空推定宿主其他旧缓存为零。',
        '普通文件状态：`'+json.dumps(v['ordinary_statuses'],ensure_ascii=False)+'`。未解析来源'+str(len(e['unresolved']))+'项。变化对象保留实际查询值，但不进入稳定小计；未知不是零。','']
    for obj in e['files']:
        if obj['status'] not in ('OBSERVED','SEPARATE_SHMEM_OR_HUGETLB'):
            lines.append(f"- `{obj['primary_category']}`，{obj['object_key']}，{obj['status']}，原查询驻留{mib(obj['resident_bytes'])} MiB，阶段{obj.get('change_phase')}。路径及错误见summary.json。")
    lines+=['','## TMPFS与loop','',
        'TMPFS从实际map_files固定后备，复用整缓存索引cachestat；并不把映射长度或逻辑文件大小当驻留。按设备号:inode去重，nr_evicted非零、身份变化和超时保留未知。',
        f"工作进程退出码{t['worker_returncode']}，已回收{t['worker_reaped']}。查询对象状态：`"+json.dumps(dict(Counter(o['status'] for o in t['objects'])),ensure_ascii=False)+'`。',
        'loop实际状态（LOOP_GET_STATUS64）：`'+json.dumps(loop.get('loop_before'),ensure_ascii=False)+'`。dio表示有效direct I/O；offset和sizelimit为字节。以准确volume.ext4设备号:inode匹配，查询前后状态保留，不根据loop设备名称追补旧运行。','',
        '## 查询成本、任务和清理','',
        f"终点全部查询墙钟{r['endpoint_wall_seconds']:.6f}秒，调用线程CPU{r['endpoint_cpu_seconds']:.6f}秒；其中含七VMM PSS、原19文件、扩展普通文件及新增分层查询。动作及下载窗口{r['action_download_window_seconds']:.3f}秒、工具累计{r['tool_seconds']:.3f}秒、控制程序整轮{r['controller_seconds']:.3f}秒。没有关闭组，不从单次数据推断减速率。",
        f"普通host/guest计数0.1秒、周期PSS1秒和默认回收保持。运行中最低可用磁盘{v['minimum_disk_gib']:.3f} GiB；成功私有服务及卷已清理，源服务恢复停止，RAM输入已释放。没有删除旧卷。",'',
        '## 覆盖边界和后续','',
        '本轮补上已确认的四类缺项，并为每个登记对象提供读数或具体未知状态。未被本服务路径、打开描述符或映射引用的旧缓存，已关闭／解除映射又删除的对象，以及窗口外新增删除的对象仍不保证可枚举。guest Cached归属和跨文件系统别名物理并集也不由这些接口证明。',
        f"分层查询前／后cgroup file为{l['counters_before']['file']/MIB:.3f}／{l['counters_after']['file']/MIB:.3f} MiB，稳定普通文件小计{e['stable_regular_resident_bytes']/MIB:.3f} MiB。这个差额尚无页记账归属证据；新查到的loop或RAM层也不能自动用来填平它。",
        'file仍是本服务cgroup记账，mincore/cachestat是目标对象全局缓存观测；相差只提示范围或时间不同，不能当成已确认组外容量，也不加回current。',
        '后续是否采用新查询方法，须按其实际成本、数值含义和覆盖状态决定；不为获得更小耗时追加运行。','',
        '原始记录：`'+v['raw']+'`。批次：`'+v['source']+'`。','']
    if 'method_totals' in d:
        lines+=['## 块设备查询方法（独立指标）','',
            '本次method=`'+d['method']+'`。cachestat的cache_index_bytes是现有缓存索引页数乘页大小，含尚未uptodate的页；mincore的resident_bytes只计uptodate页。未请求字段为null，不是0。compare为每个固定对象先mincore再cachestat，不能解释为同时快照。', '',
            '| 方法 | 状态 | 成功／未知对象 | 观测小计 MiB | 完整查询量 MiB |',
            '| --- | --- | --- | ---: | ---: |']
        for method,total in d['method_totals'].items():
            lines.append(f"| {method} | {total['status']} | {total['observed_objects']}/{total['unknown_objects']} | {mib(total['observed_subtotal_bytes'])} | {mib(total['complete_bytes'])} |")
        lines+=['', '| 方法 | 尝试／成功对象 | 实际核心查询对象 | 方法窗口合计秒 | 核心查询合计秒 | 前后身份检查合计秒 | 方法线程CPU合计秒 |',
            '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
        for method,cost in method_costs(d).items():
            s=cost['stages'];checks=s['identity_before']['wall_seconds']+s['identity_after']['wall_seconds']
            lines.append(f"| {method} | {cost['attempted_objects']}/{cost['observed_objects']} | {s['core_query']['objects']} | {cost['wall_seconds']:.6f} | {s['core_query']['wall_seconds']:.6f} | {checks:.6f} | {cost['cpu_seconds']:.6f} |")
        lines+=['','上表包含失败前已执行的检查，未执行核心查询的对象不能计为成功或驻留0。登记成本两方法共用，不重复加到每方法；方法合计不是整轮减速率。','']
        lines+=['', '| rdev | 方法 | 状态 | 该方法观测 MiB | 查询范围（字节） | 该方法窗口秒 | 核心查询秒 | 线程CPU秒 |',
            '| --- | --- | --- | ---: | --- | ---: | ---: | ---: |']
        for obj in d['objects']:
            for method,q in obj['queries'].items():
                core=q['stages'].get('core_query')
                field='resident_bytes' if method=='mincore' else 'cache_index_bytes'
                core_text=f'{seconds(core):.6f}' if core else '未执行'
                lines.append(f"| {obj['rdev']} | {method} | {q['status']} | {mib(q[field])} | {q['range']['offset']}+{q['range']['length']} | {seconds(q):.6f} | {core_text} | {q['cpu_ns']/1e9:.6f} |")
        for obj in d['objects']:
            for method,q in obj['queries'].items():
                if q.get('error'):lines+=['',f"- rdev={obj['rdev']}，{method}：{q['error']}"]
                details={k:q[k] for k in ('failed_reference','mapping_diagnostic') if k in q}
                if details:lines+=['','```json',json.dumps(details,indent=2,ensure_ascii=False),'```','']
        lines+=['','每次方法的核心查询、前后身份检查及每阶段墙钟／线程CPU保存在queries[method].stages；核心计时包含接口包装，不是单独的内核耗时。','']
    if 'query_window_regular' in e:
        q=e['query_window_regular']
        lines+=['## 普通文件各自查询窗口小计','',
            f"状态{q['status']}，有效{q['observed_objects']}个、未知{q['unknown_objects']}个；观测小计{mib(q['observed_subtotal_bytes'])} MiB，其中登记后变化但查询自身稳定{q['changed_before_query_objects']}个、{mib(q['changed_before_query_bytes'])} MiB。",
            '原严格小计和PARTIAL不变；新值包含复用的原19文件P1，逐对象一次，不是同一时刻快照。','']
    for label,value in (('块设备别名错误',d['errors']),('TMPFS缺项',t['missing_sources']),('进程复查错误',l['process_errors']),('普通文件未解析来源',e['unresolved'])):
        if value:lines+=['### '+label,'','```json',json.dumps(value,indent=2,ensure_ascii=False),'```','']
    if loop.get('error'):lines+=['### loop错误','',loop['error'],'']
    if 'query_window' in loop:
        w=loop['query_window'];residency=loop.get('residency',{})
        lines+=['## loop查询窗口观测（独立字段）','',
            f"原严格状态{loop['status']}，原严格驻留{mib(loop['resident_bytes'])} MiB；新窗口状态{w['status']}，窗口驻留{mib(w['resident_bytes'])} MiB。",'',
            '这两个字段来自同一次完整mincore，不是两次查询，不相加。新窗口允许mtime变化，但要求固定文件身份／大小、准确loop配置和挂载归属前后符合；它不是缓存不变的原子快照。原严格状态和分层PARTIAL条件保持，未知不补0。', '',
            '```json',json.dumps(dict(query_window=w,metadata_before=residency.get('metadata_before'),
                metadata_after=residency.get('metadata_after'),metadata_changes=residency.get('metadata_changes')),
                ensure_ascii=False,indent=2),'```','',
            '成本已包含在原outer_loop和全部终点计时中，没有额外mincore或fstat；新增字段构造的CPU影响尚未实测。','']
    if 'fixed_ram_totals' in d:
        total=d['fixed_ram_totals']
        lines+=['## 固定RAM设备查询与原来源关系（显式独立查询）','',
            f"新RAM设备口径状态{total['status']}，成功／未知{total['observed_objects']}/{total['unknown_objects']}，"
            f"观测小计{mib(total['observed_subtotal_bytes'])} MiB，完整量{mib(total['complete_bytes'])} MiB。",'',
            '每个RAM设备预先选定组内open FD，优先服务进程；只在显式split模式额外计划一次cachestat，先于原严格查询执行，不是失败重试或换别名补数。两个读数是不同顺序窗口，不相加。原严格状态／完整量和分层PARTIAL不变；新值只表示组内仍持有的设备全局缓存，不表示原VMM映射保持或guest缓存物理归属。', '',
            '| rdev | 固定设备查询 | 缓存索引量 MiB | 原来源关系 | 新查询含准备墙钟／CPU秒 |',
            '| --- | --- | ---: | --- | --- |']
        for obj in d['objects']:
            if 'fixed_ram_device' not in obj:continue
            p=obj['fixed_ram_device'];q=p['query']
            lines.append(f"| {p['rdev']} | {q['status']} | {mib(q['cache_index_bytes'])} | {p['source_relationships']['status']} | {seconds(p):.6f}／{p['cpu_ns']/1e9:.6f} |")
            lines+=['','```json',json.dumps(dict(anchor=p['anchor'],source_relationships=p['source_relationships'],
                prepare_errors=p['prepare_errors'],query=q),indent=2,ensure_ascii=False),'```','']
        lines+=['','CHANGED只在保存了映射变化诊断时使用；查询中断导致末端来源未复查则为UNKNOWN，不把缓存查询失败直接说成映射改变。无恢复RAM时为NOT_APPLICABLE且容量null。额外查询的成本已在块设备整层和终点计时中包含，不能逐列相加。','']
    checker=Path(v['source']).parent.parent/'report-ram-checker-error.txt'
    if checker.exists():
        lines+=['## 离线报告检查器修正','',checker.read_text().strip(),
                '最小回归检查通过；仅重算派生报告，原任务与原始采集未重跑。错误记录：`'+str(checker)+'`。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();assert not args.output.exists();v=collect(args.source)
    args.output.mkdir(parents=True);(args.output/'summary.json').write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
    (args.output/'REPORT.md').write_text(report(v));print('status=PASS result_dir='+str(args.output))


if __name__=='__main__':main()
