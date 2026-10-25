"""Selected shared-file observations for complete Prettier GRPO/BPO paths."""
import json
import stat
import time
from pathlib import Path

from host_file_monitor import counters, mincore_file, pids
from host_prefix_compare import drives_for_vm
from host_scaling import object_key
from run_host_file_restore import device_residency, ram_backend
from host_rootfs_layers import rootfs_inventory, validate_rootfs_inventory


def select_objects(inventory, backends, layers, restored, rootfs_objects=()):
    """One table across all templates; device-node aliases are not extra caches."""
    selected={};excluded={}
    def add(row,category,role,template=None):
        key=tuple(object_key(row))
        if key not in selected:
            selected[key]=dict(row,object_key=list(key),category=category,users=[],templates=[])
        item=selected[key]
        if item['category']!=category:raise ValueError('conflicting shared-file categories')
        if row.get('size')!=item.get('size'):raise ValueError('shared-file size changed')
        if role not in item['users']:item['users'].append(role)
        if template and template not in item['templates']:item['templates'].append(template)
    for role,rows in inventory.items():
        for row in rows:
            if row['is_read_only']:add(row,'shared_readonly_backing',role)
            else:
                key=tuple(object_key(row))
                if key not in excluded:
                    excluded[key]=dict(object_key=list(key),users=[],status='NOT_REQUESTED',resident_pages=None,resident_bytes=None)
                if role not in excluded[key]['users']:excluded[key]['users'].append(role)
    for role,row in backends.items():
        add(dict(row,block_device=True),'snapshot_memory_device',role,restored[role])
    for row in layers:
        for role,template in restored.items():
            if template==row['template']:add(row,'snapshot_memory_layer',role,template)
    for row in rootfs_objects:
        if row['status']!='SELECTED':continue
        for role in row['users']:add(row,'shared_rootfs_layer',role,restored.get(role))
    if not any(v['category']=='shared_readonly_backing' for v in selected.values()):
        raise ValueError('no actual read-only backing')
    if selected.keys() & excluded.keys():raise ValueError('selected shared object is also writable')
    return list(selected.values()),list(excluded.values())


def opened_regular_files(service_group):
    opened={}
    for pid in pids(service_group):
        try:
            for fd in (Path('/proc')/str(pid)/'fd').iterdir():
                try:
                    info=fd.stat()
                    if stat.S_ISREG(info.st_mode):opened.setdefault((info.st_dev,info.st_ino),[]).append(dict(pid=pid,fd=fd.name))
                except FileNotFoundError:continue
        except FileNotFoundError:continue
    return opened


def memory_layers(env,templates,service_group,*,opened=None):
    """Read active image metadata, not layer payload; check owned open descriptors."""
    if opened is None:opened=opened_regular_files(service_group)
    layers=[]
    for template in templates:
        config=env/'snapshot-local-cache/runtime'/template/'memory/image.json'
        image=json.loads(config.read_text())
        if image.get('upper',{}).get('data') or not image['lowers']:raise ValueError('immutable memory lowers required')
        for lower in image['lowers']:
            name=lower['file']
            if not name.startswith('/workspace/env/'):raise ValueError('memory layer outside owned env')
            path=env/name.removeprefix('/workspace/env/');info=path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size!=lower['size'] or (info.st_dev,info.st_ino) not in opened:
                raise ValueError('memory layer is not an active owned regular file')
            layers.append(dict(template=template,path=str(path),device=info.st_dev,inode=info.st_ino,rdev=0,
                block_device=False,size=info.st_size,origin='CREATED_THIS_ROLLOUT',image_config=str(config),
                open_descriptors=opened[(info.st_dev,info.st_ino)]))
    return layers


class SharedRollout:
    def __init__(self,group,binding,out,schedule,*,single_reference=False):
        self.group,self.binding,self.out=group,binding,Path(out)
        self.env=self.out.parent.parent/'env'
        self.restored={};self.templates={}
        self.single_reference=single_reference
        self.state=dict(status='PENDING',profile='full-shared-files-v2',schedule=schedule,phases=[],
            pss='NOT_REQUESTED',scope='Selected read-only drives, restored memory devices, memory lowers and confirmed shared local rootfs lowers. Global cache residency; unresolved and single-user lowers separate, not exact guest Cached attribution or whole host RAM.')
        if single_reference:self.state['scope']='Explicit single-guest reference candidates, not confirmed multi-guest sharing. Global selected-file residency; no exact guest Cached attribution or whole host RAM.'

    def save(self):
        (self.out/'host-shared-files.json').write_text(json.dumps(self.state,indent=2)+'\n')

    def phase(self,label,guests,*,restored=None):
        if restored:self.restored.update(restored)
        start=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(self.group)
        bindings={r:self.binding.resolve(sid) for r,sid in guests.items()}
        inventory={r:drives_for_vm(vm) for r,vm in bindings.items()}
        backends={r:ram_backend(vm) for r,vm in bindings.items() if r in self.restored}
        snapshots=[]
        for template,step in self.templates.items():
            users=sorted(r for r in backends if self.restored[r]==template)
            config=self.env/'snapshot-local-cache/runtime'/template/'memory/image.json'
            snapshots.append(dict(template=template,step=step,users=users,
                device_status='IDENTIFIED' if users else 'NOT_CREATED_FOR_RESTORE',
                config_status='MATERIALIZED_NOT_QUERIED' if config.exists() else 'NOT_MATERIALIZED',
                resident_bytes=None))
        if any(self.binding.resolve(guests[r])!=vm for r,vm in bindings.items()):raise ValueError('VMM identity changed')
        after=counters(self.group)
        row=dict(label=label,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
            cpu_ns=time.thread_time_ns()-cpu,counters_before=before,counters_after=after,
            bindings=bindings,drive_inventory=inventory,backends=backends,snapshots=snapshots)
        if label=='paths-finished':
            begin=time.monotonic_ns();thread_cpu=time.thread_time_ns()
            self.opened=opened_regular_files(self.binding.cgroup)
            row['rootfs_inventory']=rootfs_inventory(bindings,inventory,self.opened,self.restored)
            if self.single_reference:
                if len(bindings)!=1:raise ValueError('single reference requires exactly one guest')
                roots=row['rootfs_inventory']
                for obj in roots['objects']:
                    if obj['status']=='NOT_REQUESTED_SINGLE_USER':obj.update(status='SELECTED',selection_reason='SINGLE_REFERENCE_CANDIDATE')
                roots['scope']='Explicit single-guest reference candidates; sharing is confirmed separately at N=7. Upper and unresolved files are not queried.'
            row['rootfs_inventory'].update(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-thread_cpu)
            if any(self.binding.resolve(guests[r])!=vm for r,vm in bindings.items()):raise ValueError('VMM identity changed during rootfs inventory')
            row['counters_after']=counters(self.group)
            row.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
        self.state['phases'].append(row);self.save()
        return row

    def query(self,guests,paths_end_ns):
        self.state['original_paths_end_monotonic_ns']=paths_end_ns
        point=self.phase('paths-finished',guests)
        start=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(self.group)
        try:
            layers=memory_layers(self.env,sorted(set(self.restored.values())),self.binding.cgroup,opened=self.opened) if self.restored else []
            if self.state['schedule']=='short-restore':
                layers=[dict(layer,origin='IMPORTED_PREFIX') for layer in layers]
            roots=point['rootfs_inventory']
            selected,excluded=select_objects(point['drive_inventory'],point['backends'],layers,self.restored,roots['objects'])
            devices={r['rdev']:dict(identity=(r['device'],r['inode'],r['rdev']),path=r['path'],target=r['path'],
                owner_pid=point['bindings'][r['users'][0]]['pid']) for r in sorted(selected,key=lambda v:v['category']!='snapshot_memory_device') if r['block_device']}
            first=next(((r['device'],r['inode'],r['rdev']) for r in selected if r['category']=='snapshot_memory_device'),None)
            queried,_=device_residency(devices,first,self.group,[],capture_memory_pss=False)
            by_key={tuple(object_key(r)):r for r in queried}
            for obj in selected:
                key=tuple(obj['object_key'])
                if not obj['block_device']:
                    begin=time.monotonic_ns();thread_cpu=time.thread_time_ns();value=mincore_file(Path(obj['path']))
                    value.update(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-thread_cpu);by_key[key]=value
            objects=[]
            for obj in selected:
                value=dict(by_key[tuple(obj['object_key'])],kind=obj['category'])
                if tuple(object_key(value))!=tuple(obj['object_key']) or not value['stable']:
                    raise ValueError('queried shared-file identity or size changed')
                if obj['category']=='snapshot_memory_device':
                    if value['mapping_length']!=4*2**30:raise ValueError('unexpected memory device capacity')
                    if any(r['offset']<0 or r['offset']+r['end']-r['start']>value['mapping_length'] for user in obj['users'] for r in point['backends'][user]['regions']):
                        raise ValueError('memory mapping exceeds backing')
                if obj.get('size') is not None and value['mapping_length']!=obj['size']:raise ValueError('layer size changed')
                objects.append(dict(object_key=obj['object_key'],category=obj['category'],users=sorted(obj['users']),
                    templates=sorted(obj['templates']),origin=obj.get('origin','ACTIVE_BACKING'),residency=value))
            validate_rootfs_inventory(roots)
            for role,vm in point['bindings'].items():
                if self.binding.resolve(guests[role])!=vm:raise ValueError('VMM identity changed during query')
                if role in point['backends']:
                    again=ram_backend(vm);old=point['backends'][role]
                    if any(again[k]!=old[k] for k in ('device','inode','rdev')):raise ValueError('memory backing changed')
            after=counters(self.group)
            self.state.update(status='OBSERVED',query=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
                cpu_ns=time.thread_time_ns()-cpu,counters_before=before,counters_after=after,objects=objects,excluded=excluded,layer_inventory=layers,rootfs_inventory=roots,
                categories={c:sum(o['residency']['resident_bytes'] for o in objects if o['category']==c) for c in sorted({o['category'] for o in objects})},
                scope='Each actual cache identity queried once across both templates; categories are separate, no combined physical total or linear savings reference.'))
        except BaseException as error:
            self.state.update(status='FAIL',error=repr(error));raise
        finally:self.save()
        return self.state


def report(raw,output):
    """Use saved observations only; existing rollout time retains query and checks."""
    raw,output=Path(raw),Path(output)
    value=json.loads((raw/'host-shared-files.json').read_text())
    task=json.loads((raw/'result.json').read_text())
    if value['status']!='OBSERVED' or task['status']!='PASS':raise ValueError('shared-file observation or task checks incomplete')
    query=value['query'];end=value['original_paths_end_monotonic_ns']
    if not task['rollout_start_monotonic_ns']<=end<=query['start_monotonic_ns']<=query['end_monotonic_ns']<=task['rollout_end_monotonic_ns']:
        raise ValueError('shared query is outside the declared task/check window')
    result=dict(value,original_paths_elapsed_ns=end-task['rollout_start_monotonic_ns'],
        query_elapsed_ns=query['end_monotonic_ns']-query['start_monotonic_ns'],
        metadata_elapsed_ns=sum(p['end_monotonic_ns']-p['start_monotonic_ns'] for p in value['phases']),
        metadata_thread_cpu_ns=sum(p['cpu_ns'] for p in value['phases']),
        rollout_elapsed_ns=task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns'],
        rollout_includes_query=True,actual_actions=task['actual_action_count'],
        official_results={r:{k:p[k] for k in ('official_task_status','official_passed','official_failed')} for r,p in task['paths'].items()})
    roots=query.get('rootfs_inventory')
    result['rootfs_coverage_status']=roots['status'] if roots else 'NOT_RECORDED'
    if roots:
        result['rootfs_metadata_elapsed_ns']=roots['end_monotonic_ns']-roots['start_monotonic_ns']
        result['rootfs_metadata_thread_cpu_ns']=roots['cpu_ns']
        result['rootfs_query_elapsed_ns']=sum(o['residency']['end_monotonic_ns']-o['residency']['start_monotonic_ns'] for o in query['objects'] if o['category']=='shared_rootfs_layer')
        result['rootfs_query_thread_cpu_ns']=sum(o['residency']['cpu_ns'] for o in query['objects'] if o['category']=='shared_rootfs_layer')
    output.mkdir(parents=True,exist_ok=False)
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    names=dict(shared_readonly_backing='共享只读后备',snapshot_memory_device='快照内存设备',snapshot_memory_layer='原始内存层',shared_rootfs_layer='共享系统盘下层文件')
    lines=['# 完整任务的指定共享文件驻留','',
        '| 类别 | 缓存对象身份 | 实际使用者 | 来源 | 驻留／MiB |','| --- | --- | --- | --- | ---: |']
    for obj in query['objects']:
        origin='本轮保存产物' if obj['origin']=='CREATED_THIS_ROLLOUT' else '活跃后备'
        lines.append('| '+names[obj['category']]+' | '+':'.join(map(str,obj['object_key']))+' | '+', '.join(obj['users'])+' | '+origin+' | '+f"{obj['residency']['resident_bytes']/2**20:.6f}"+' |')
    lines+=['',f"创建及原路径用时{result['original_paths_elapsed_ns']/1e9:.6f}秒，随后驻留查询{result['query_elapsed_ns']/1e9:.6f}秒；整轮{result['rollout_elapsed_ns']/1e9:.6f}秒包含该查询和官方检查。元数据观察合计{result['metadata_elapsed_ns']/1e9:.6f}秒，各阶段CPU及计数见JSON。",'',
        '各层单列，不相加为整体RAM，也不套用七份单沙箱参考。独立可写盘未查询，驻留字段为空；'+('rootfs下层覆盖见下方识别状态。' if roots else 'rootfs不可变下层文件未完整覆盖。'),
        '', '运行中guest Cached和host组内file曲线与本页末尾指定对象驻留的层次、范围和时刻不同。原官方结果保留在summary.json。']
    if roots:
        lines+=['',f"系统盘下层识别状态：{roots['status']}。元数据{result['rootfs_metadata_elapsed_ns']/1e9:.6f}秒已计入元数据合计，新增共享文件查询{result['rootfs_query_elapsed_ns']/1e9:.6f}秒已计入驻留查询，不重复相加。",'',
            '仅至少两个活实例确认引用的本地不可变文件加入查询；单实例文件和upper未查询，未解析项为未知。完整关联及保留槽身份见JSON，不由对象数推算覆盖比例。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return result
