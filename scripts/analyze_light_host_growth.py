"""Offline host-file growth decomposition from saved light rollout metadata."""
import argparse
import csv
import json
from pathlib import Path


def read(path):return json.loads(Path(path).read_text())


def event_family(stage):
    if stage.endswith('-snapshot-call'):return 'snapshot_request'
    if '-restore-' in stage:return 'restore_request'
    return None


def split_changes(points, events):
    """Disjoint sample intervals; concurrent requests are never added twice."""
    totals={key:0 for key in ('snapshot_request','restore_request','mixed_requests','other')}
    counts={key:0 for key in totals}
    for before,after in zip(points,points[1:]):
        assert before['monotonic_ns']<after['monotonic_ns']
        overlap={event_family(e['stage']) for e in events if event_family(e['stage'])
            and e['start_monotonic_ns']<after['monotonic_ns']
            and e['start_monotonic_ns']+e['duration_ns']>before['monotonic_ns']}
        key=next(iter(overlap)) if len(overlap)==1 else 'mixed_requests' if overlap else 'other'
        totals[key]+=after['file']-before['file'];counts[key]+=1
    assert sum(totals.values())==points[-1]['file']-points[0]['file']
    return {key:dict(file_change_bytes=value,sample_intervals=counts[key]) for key,value in totals.items()}


def file_family(path,storage):
    p=Path(path)
    if p==storage/'volume.ext4':return 'private_loop_image'
    if '/env/snapshot-store/' in path or '/env/snapshot-local-cache/' in path:return 'snapshot_files'
    if '/env/firecracker-work/' in path:return 'task_writable_backing'
    if '/image-cache/' in path or '/deps/' in path or '/memory/volume.ext4' in path:return 'prepared_inputs'
    return 'logs_state_other'


def analyze(run):
    storage=Path(run['storage']);raw=storage/'prettier6604'
    task=read(raw/'result.json');events=read(raw/'events.json')
    assert task['status']=='PASS'
    inventory=read(storage/'host-file-residency.json')
    allocation=read(raw/'terminal-storage-allocation.json')
    baseline=run['host_before_first_segment'];start=task['first_tool_start_monotonic_ns']
    end=run['rollout_timing']['end_monotonic_ns']
    with (Path(run['workflow'])/'host-file/memory.tsv').open() as stream:
        rows=[{key:int(value) if value else None for key,value in r.items()} for r in csv.DictReader(stream,delimiter='\t')]
    window=[r for r in rows if start<=r['monotonic_ns']<=end]
    points=[dict(monotonic_ns=baseline['end_monotonic_ns'],**baseline['counters']),*window]
    assert points[0]['monotonic_ns']<window[0]['monotonic_ns']
    delta=window[-1]['file']-baseline['counters']['file']
    assert delta==run['task_window_file_final_delta_bytes']
    partitions=split_changes(points,events)
    buckets={key:[] for key in ('snapshot_files','private_loop_image','task_writable_backing','prepared_inputs','logs_state_other')}
    seen=set()
    for row in inventory['files']:
        identity=(row['device'],row['inode'])
        assert identity not in seen;seen.add(identity)
        buckets[file_family(row['path'],storage)].append(row)
    grouped={key:dict(file_count=len(value),resident_bytes=sum(r['resident_bytes'] for r in value) if value else None,
        status=('INCOMPLETE_FINAL_ONLY' if any(not r['stable'] for r in value) else 'OBSERVED_FINAL_ONLY') if value else 'NOT_OBSERVED_NOT_ZERO',
        unstable_file_count=sum(not r['stable'] for r in value)) for key,value in buckets.items()}
    assert sum(v['resident_bytes'] or 0 for v in grouped.values())==inventory['resident_bytes']
    request_events=[e for e in events if event_family(e['stage'])]
    return dict(schedule=run['schedule'],workflow=run['workflow'],storage=str(storage),
        original_workflow_status=run['original_workflow_status'],task_status=task['status'],
        official_task_status=run['official_task_status'],official_per_instance=run['official_per_instance'],
        task_window_file_delta_bytes=delta,sample_associated_changes=partitions,
        snapshot_requests=sum(event_family(e['stage'])=='snapshot_request' for e in request_events),
        restore_requests=sum(event_family(e['stage'])=='restore_request' for e in request_events),
        last_sample_to_checks_end_ns=end-window[-1]['monotonic_ns'],
        final_inventory_status=inventory['status'],final_accounting=inventory['accounting'],
        final_inventory_resident_bytes=inventory['resident_bytes'],final_inventory_groups=grouped,
        final_inventory_start_ns=inventory['start_monotonic_ns'],final_inventory_end_ns=inventory['end_monotonic_ns'],
        cleanup_snapshot_delete_start_ns=min((e['start_monotonic_ns'] for e in events if e['stage'].endswith('-delete-snapshot')),default=None),
        terminal_file_allocation_bytes={key:allocation['allocated_regular_file_bytes'].get(key) for key in
            ('env/firecracker-work','env/snapshot-store','env/snapshot-local-cache')},
        source_decomposition=dict(snapshot_device_cache_bytes=None,restored_writable_cache_bytes=None,
            task_repository_cache_bytes=None,status='NOT_IDENTIFIABLE_FROM_SAVED_DATA'),
        whole_physical_savings='NOT_MEASURED')


def report(pair,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    runs=[analyze(r) for r in read(Path(pair)/'result.json')['runs']]
    assert [r['schedule'] for r in runs]==['grpo','bpo']
    result=dict(status='PASS_OFFLINE_DECOMPOSITION',runs=runs,
        scope='Sample-interval associations and later object inventories are separate; no physical source attribution inferred',
        new_sandbox_actions=0,live_cache_queries=0)
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    def values(get):return ' | '.join('未测' if get(r) is None else f'{get(r)/2**20:.6f}' for r in runs)
    lines=['# GRPO／BPO的host file增长：离线分解','',
        '| 同一原任务观察区间，MiB | GRPO | BPO |','| --- | ---: | ---: |',
        '| file总变化 | '+values(lambda r:r['task_window_file_delta_bytes'])+' |']
    labels={'snapshot_request':'与保存请求相交的采样区间','restore_request':'与恢复请求相交的采样区间',
        'mixed_requests':'同时与两类请求相交的采样区间','other':'其余采样区间'}
    for key,label in labels.items():lines.append('| '+label+' | '+values(lambda r:r['sample_associated_changes'][key]['file_change_bytes'])+' |')
    lines.extend(['','每个采样区间只分入一类，因此各行有符号变化之和等于原总变化。并发恢复请求不重复累计；这些是时间关联，不是逐文件或设备的因果归属。',
        '', '| 清理后的普通文件全局驻留，MiB | GRPO | BPO |','| --- | ---: | ---: |'])
    for key,label in {'snapshot_files':'快照文件','private_loop_image':'私有loop卷镜像','task_writable_backing':'任务可写后备文件',
        'prepared_inputs':'准备输入','logs_state_other':'日志、状态及其他'}.items():
        lines.append('| '+label+' | '+values(lambda r:r['final_inventory_groups'][key]['resident_bytes'])+' |')
    lines.extend(['','未列举的对象不是零驻留。最终清单没有覆盖运行中快照设备或任务仓库的独立缓存；卷镜像还承载其他文件系统数据，不能全算成任务文件。',
        '', 'GRPO原清单INCOMPLETE保持；两组清单在任务与沙箱／快照清理之后，不能用其容量差分解先前任务区间的增长。磁盘分配量另存JSON，不当作内存驻留。',
        '', '结论：保存／恢复请求相关的增长和最终保留文件容量可分列，三类实际物理来源的精确容量仍未测得；不重跑任务来补造历史时刻。'])
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('pair',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();value=report(args.pair,args.output)
    print('status='+value['status']+' result_dir='+str(args.output))
