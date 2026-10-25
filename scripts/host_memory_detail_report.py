"""Summarize saved host detail without querying processes, devices or payloads."""

LABELS={'io_uring':'io_uring','kvm':'KVM vCPU','memfd':'已删除memfd','dev_zero':'已删除/dev/zero','other':'其他未解析映射'}


def family(name):
    if name=='anon_inode:[io_uring]':return 'io_uring'
    if name.startswith('anon_inode:kvm-vcpu:'):return 'kvm'
    if name.startswith('/memfd:'):return 'memfd'
    if name=='/dev/zero (deleted)':return 'dev_zero'
    return 'other'


def duration(row,start='start_monotonic_ns',end='end_monotonic_ns'):
    if row.get(start) is None or row.get(end) is None:return None
    if row[end]<row[start]:raise ValueError('saved query end precedes start')
    return row[end]-row[start]


def existing_owner_detail(value):
    observation=(value.get('observation') or {}) if value is not None else {}
    status=value['status'] if value is not None else 'NOT_MEASURED'
    fields=('inside_bytes','outside_bytes','unknown_bytes','resident_bytes')
    capacities={k:value.get(k) if value is not None else None for k in fields}
    if status in ('OBSERVED','INCOMPLETE'):
        if any(not isinstance(v,int) or v<0 for v in capacities.values()):
            raise ValueError('saved owner query lacks valid capacities')
        if sum(capacities[k] for k in fields[:3])!=capacities['resident_bytes']:
            raise ValueError('saved owner query capacities differ')
        if (status=='OBSERVED')!=(capacities['unknown_bytes']==0):
            raise ValueError('saved owner status differs from unknown capacity')
    elif any(v is not None for v in capacities.values()):
        raise ValueError('unmeasured owner query must not expose valid capacities')
    return dict(status=status,**capacities,
        path=value.get('path') if value is not None else None,
        reference_group=value.get('reference_group') if value is not None else None,
        reference_group_ids=value.get('reference_group_ids') if value is not None else None,
        start_monotonic_ns=value.get('start_monotonic_ns') if value is not None else None,
        end_monotonic_ns=value.get('end_monotonic_ns') if value is not None else None,
        query_start_monotonic_ns=observation.get('query_start_monotonic_ns'),
        query_end_monotonic_ns=observation.get('query_end_monotonic_ns'),
        query_ns=observation.get('query_ns'),worker_lifecycle_ns=duration(value) if value is not None else None,
        coverage=observation.get('probe_counts'),owners=observation.get('owners'),
        worker_reaped=value.get('worker_reaped') if value is not None else None,
        worker_group_released=value.get('worker_group_released') if value is not None else None)


def memory_detail(residency,*,existing_cache_owner=None):
    c=residency.get('compact_inventory')
    if c is None:return None  # Old modes did not request this observation.
    m=c['mapped_backing'];families=[]
    def missing_descriptors(entries):
        if not entries or m.get('errors') or any(e.get('stage')=='deleted-descriptors' for e in m.get('special_metadata_errors',[])):
            return None
        return sum(not e['deleted_descriptors'] for e in entries) if all('deleted_descriptors' in e for e in entries) else None
    for key in LABELS:
        entries=[e for e in m['unresolved'] if family(e['mapping_path'])==key]
        if not entries:continue
        regions=[r for e in entries for r in e.get('regions',[])]
        count=sum(e['mappings'] for e in entries)
        requested=any('regions' in e for e in entries)
        valid=requested and len(regions)==count and all(r.get('smaps_status')=='OBSERVED' for r in regions)
        values={field:sum(r['smaps_bytes'][field] for r in regions)
                if valid and all(r.get('smaps_bytes',{}).get(field) is not None for r in regions) else None
                for field in ('Pss','Rss','Anonymous','Shared_Hugetlb','Private_Hugetlb')}
        deleted=[e for e in entries if e['mapping_path'].endswith(' (deleted)')]
        families.append(dict(family=key,metadata_keys=len(entries),mapping_lines=count,
            smaps_status='OBSERVED' if valid else 'INCOMPLETE' if requested else 'NOT_MEASURED',
            mapping_pss_rss_bytes=values,deleted_objects_without_descriptor=missing_descriptors(deleted),
            backing_types=sorted({d['backing_type'] for e in entries for d in e.get('deleted_descriptors',[])}),
            backing_resident_bytes=None,backing_residency_status='NOT_MEASURED'))
    commands=[e['command_buffer'] for e in m['special_objects'] if 'command_buffer' in e]
    calculated=bool(commands) and all(c['status']=='SOURCE_CALCULATED' and c.get('allocation_bytes') is not None for c in commands)
    allocation=sum(c['allocation_bytes'] for c in commands) if calculated else None
    if allocation!=m.get('ublkc_command_allocation_bytes'):raise ValueError('saved command allocation summary differs')
    query_times=[duration(c) for c in commands]
    deleted=[e for e in m['unresolved'] if e['mapping_path'].endswith(' (deleted)')]
    cache=m.get('deleted_backing_cache')
    result=dict(status=c['status'],window_ns=duration(c),
        additional_mapped_files=len(m['additional_files']),additional_global_cache_bytes=m['additional_global_resident_bytes'],
        unresolved_mappings=len(m['unresolved']),mapped_coverage=m['coverage'],
        all_process_anon_pss_before_bytes=c['before_pss'].get('pss_anon'),all_process_anon_pss_after_bytes=c['after_pss'].get('pss_anon'),
        special_metadata_status=m.get('special_metadata_status','NOT_MEASURED'),
        special_mapping_families=families,deleted_objects_without_descriptor=missing_descriptors(deleted),
        special_metadata_errors=m.get('special_metadata_errors'),
        deleted_backing_cache=dict(status=cache['status'] if cache else 'NOT_MEASURED',
            whole_backing_cache_bytes=cache.get('whole_backing_cache_bytes') if cache else None,
            object_count=len(cache['objects']) if cache and cache.get('objects') else None,
            query_ns=duration(cache) if cache else None,worker_reaped=cache.get('worker_reaped') if cache else None),
        ublkc_command_status='SOURCE_CALCULATED' if calculated else 'INCOMPLETE' if commands else 'NOT_MEASURED',
        ublkc_command_allocation_bytes=allocation,ublkc_query_count=len(commands) if commands else None,
        ublkc_query_success_count=sum(c['status']=='SOURCE_CALCULATED' for c in commands) if commands else None,
        timing_ns=dict(mapped_metadata_and_file_query=duration(m),
            ublkc_queries=sum(query_times) if commands and all(t is not None for t in query_times) else None,
            all_post_task_queries=duration(dict(start_monotonic_ns=c['start_monotonic_ns'],end_monotonic_ns=residency.get('end_monotonic_ns')))),
        scope='Mapping PSS, whole backing-file cache and source-calculated command allocation are separate observations. Pss_File can overlap kernel accounting; PFN zero PSS is not zero allocation. No physical sum inferred.')
    result['existing_cache_owner']=existing_owner_detail(existing_cache_owner)
    if existing_cache_owner is not None:
        if existing_cache_owner['start_monotonic_ns']<residency['end_monotonic_ns']:
            raise ValueError('old cache ownership must follow original device observations')
        result['timing_ns']['including_existing_cache_owner']=duration(dict(
            start_monotonic_ns=c['start_monotonic_ns'],end_monotonic_ns=existing_cache_owner['end_monotonic_ns']))
    return result


def write_memory_report(path,summary):
    groups=summary['groups']
    def table(title,rows):
        lines.extend(['', '## '+title, '', '| 指标 | '+' | '.join('N='+str(g['sandboxes']) for g in groups)+' |',
                      '| --- | '+' | '.join('---:' for g in groups)+' |'])
        lines.extend('| '+label+' | '+' | '.join(values)+' |' for label,values in rows)
    def fmt(value,scale=1):return '未测' if value is None else f'{value/scale:.3f}'
    details=[g.get('memory_detail',{}) for g in groups]
    lines=['# host内存分项与查询耗时','',
        f"本次报告对应{summary['actual_actions']}个已核实动作，其中新增{summary['new_actions']}个。平台动作及原结果保持检查：{summary['status']}。",
        '', '映射PSS按共享比例分摊；文件／设备mincore是对象全局驻留；命令缓冲是按实际配置和源码计算的分配量。三者分别报告，不相加。',
        '', 'Pss_File还可包含映射内核页，不是纯page cache，也可能与kernel记账重叠。PFN映射的零PSS不代表零分配。']
    def official(g):
        entries=g.get('official_results')
        if not entries or any(e.get('passed') is None or e.get('failed') is None for e in entries):return '未记录'
        return '；'.join(f"{e['role']}：{e['passed']}通过／{e['failed']}失败（{e['status']}）" for e in entries)
    table('缓存与内存上下文',[
        ('原任务官方检查',[official(g) for g in groups]),
        ('指定内存设备驻留／MiB',[fmt(g['memory_device_bytes'],2**20) for g in groups]),
        ('额外普通文件全局驻留／MiB',[fmt(d.get('additional_global_cache_bytes'),2**20) for d in details]),
        ('全组匿名PSS，观察段前／MiB',[fmt(d.get('all_process_anon_pss_before_bytes'),2**20) for d in details]),
        ('全组匿名PSS，观察段后／MiB',[fmt(d.get('all_process_anon_pss_after_bytes'),2**20) for d in details]),
        ('最终普通文件净不足／MiB',[fmt(g['final_net_shortfall_bytes'],2**20) for g in groups]),
        ('原文件记账状态',[g['final_accounting'] for g in groups])])
    family_values=[{f['family']:f for f in d.get('special_mapping_families',[])} for d in details]
    family_rows=[
        (label+'映射PSS／KiB',[fmt(f.get(key,{}).get('mapping_pss_rss_bytes',{}).get('Pss'),1024) for f in family_values])
        for key,label in LABELS.items() if any(key in f for f in family_values)]
    if family_rows:table('特殊映射PSS：不是完整后备对象容量',family_rows)
    else:lines.extend(['','## 特殊映射PSS','','本轮未采逐映射PSS。'])
    table('命令缓冲及缺项',[
        ('ublkc命令缓冲分配／KiB（源码计算）',[fmt(d.get('ublkc_command_allocation_bytes'),1024) for d in details]),
        ('命令缓冲状态',[d.get('ublkc_command_status','NOT_MEASURED') for d in details]),
        ('ublkc查询成功／请求',[str(d['ublkc_query_success_count'])+'/'+str(d['ublkc_query_count']) if d.get('ublkc_query_count') is not None else '未测' for d in details]),
        ('已删除对象无匹配描述符／个',[str(d['deleted_objects_without_descriptor']) if d.get('deleted_objects_without_descriptor') is not None else '未测' for d in details]),
        ('特殊元数据状态',[d.get('special_metadata_status','NOT_MEASURED') for d in details]),
        ('映射清单覆盖',[d.get('mapped_coverage','NOT_MEASURED') for d in details])])
    backing=[d.get('deleted_backing_cache',{}) for d in details]
    table('已删除后备缓存：单列，不与前面相加',[
        ('查询状态',[v.get('status','NOT_MEASURED') for v in backing]),
        ('整后备缓存观察合计／KiB',[fmt(v.get('whole_backing_cache_bytes'),1024) for v in backing]),
        ('返回对象数',[str(v['object_count']) if v.get('object_count') is not None else '未测' for v in backing]),
        ('查询子进程及回收／毫秒',[fmt(v.get('query_ns'),1e6) for v in backing])])
    lines.extend(['', '这项只覆盖所列后备缓存，不提供页记账归属；映射PSS、描述符完整状态分别保留。未查询、交换条目或失败项继续未知，旧数据不补零。'])
    owners=[d.get('existing_cache_owner',{}) for d in details]
    table('旧归档缓存归属：独立时段，不作为物理总量修正',[
        ('查询状态',[v.get('status','NOT_MEASURED') for v in owners]),
        ('文件',[v['path'].rsplit('/',1)[-1] if v.get('path') else '未测' for v in owners]),
        ('参考统计组',[v.get('reference_group') or '未测' for v in owners]),
        ('已查询驻留／MiB',[fmt(v.get('resident_bytes'),2**20) for v in owners]),
        ('组内缓存／MiB',[fmt(v.get('inside_bytes'),2**20) for v in owners]),
        ('组外缓存／MiB',[fmt(v.get('outside_bytes'),2**20) for v in owners]),
        ('未知缓存／MiB',[fmt(v.get('unknown_bytes'),2**20) for v in owners]),
        ('查询开始／host单调时钟ns',[str(v['query_start_monotonic_ns']) if v.get('query_start_monotonic_ns') is not None else '未测' for v in owners]),
        ('查询结束／host单调时钟ns',[str(v['query_end_monotonic_ns']) if v.get('query_end_monotonic_ns') is not None else '未测' for v in owners])])
    lines.extend(['', '本项位于原文件／设备查询之后；只解释所查归档当前页的记账归属，不从file扣除或向current相加。未测、失败及超时容量保持未知，旧任务不追补。完整索引覆盖、页所有者及进程回收状态见summary.json。'])
    table('直接计时：不等于任务减速率',[
        ('工具累计／秒',[fmt(g['tool_ns'],1e9) for g in groups]),
        ('后缀墙钟／秒',[fmt(g['task_wall_ns'],1e9) for g in groups]),
        ('ublkc查询合计／毫秒',[fmt(d.get('timing_ns',{}).get('ublkc_queries'),1e6) for d in details]),
        ('映射元数据及额外文件查询／毫秒',[fmt(d.get('timing_ns',{}).get('mapped_metadata_and_file_query'),1e6) for d in details]),
        ('主要观察段／秒',[fmt(d.get('window_ns'),1e9) for d in details]),
        ('原文件／设备驻留查询段／秒',[fmt(d.get('timing_ns',{}).get('all_post_task_queries'),1e9) for d in details]),
        ('旧缓存归属探针查询／毫秒',[fmt(v.get('query_ns'),1e6) for v in owners]),
        ('旧缓存归属含准备／回收／秒',[fmt(v.get('worker_lifecycle_ns'),1e9) for v in owners]),
        ('含旧缓存归属的全部观察段／秒',[fmt(d.get('timing_ns',{}).get('including_existing_cache_owner'),1e9) for d in details])])
    build=summary.get('metadata_query_build')
    build_ns=build.get('duration_ns') if build else None
    build_text='未测' if build_ns is None else fmt(build_ns,1e6)+'毫秒'
    lines.extend(['', '查询程序编译准备：'+build_text+'。编译和任务后明细查询不计入工具用时；双沙箱工具累计为两实例相加。',
        '', '整体host物理节省：'+summary['whole_host_physical_savings']+'；guest缓存对应的host物理集合：'+summary['guest_cache_physical_attribution']+'。',
        '', '## 原曲线',''])
    lines.extend(f"- [N={g['sandboxes']}的host和guest曲线](report-N{g['sandboxes']}/matched.png)" for g in groups)
    path.write_text('\n'.join(lines)+'\n')
