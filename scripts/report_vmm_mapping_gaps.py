"""Audit saved VMM mapping coverage; never follow recorded proc/device paths."""
import argparse
import csv
import json
from pathlib import Path
import re

from host_backing_evidence import summarize
from ram_page_evidence import manifest as ram_manifest

MIB = 2**20


def union_bytes(intervals):
    end = total = 0
    for a, b in sorted(intervals):
        if a < 0 or b <= a:
            raise ValueError('invalid interval')
        total += max(0, b-max(a, end))
        end = max(end, b)
    return total


def geometry(start, end, slots):
    union_bytes([(start, end)])  # validate; sizes below are virtual, not resident
    overlap = sum(max(0, min(end, s['host']+s['size'])-max(start, s['host'])) for s in slots)
    return dict(virtual_bytes=end-start, captured_ram_overlap_virtual_bytes=overlap,
                outside_captured_ram_virtual_bytes=end-start-overlap,
                resident_physical_bytes=None, resident_physical_status='NOT_SAVED')


def audit_vmm(raw, evidence, role, meta):
    vm = meta['host_identity']; binding = raw['bindings'][role]
    if (meta != ram_manifest(meta['sandbox_id'], meta['guest_boot_id'], vm, meta['slots']) or
            meta['sandbox_id'] != raw['guests'][role] or vm['sandbox_id'] != raw['guests'][role] or
            vm['pid'] != binding['pid'] or str(vm['start_time_ticks']) != str(binding['start_ticks'])):
        raise ValueError('saved RAM/VMM identity conflict')
    expanded = raw['expanded_files']
    identity = expanded['process_identities'][str(vm['pid'])]
    if identity != dict(start_ticks=str(vm['start_time_ticks']), cgroup='0::'+vm['cgroup']+'\n'):
        raise ValueError('saved VMM birth cgroup/start identity conflict')
    objects = {tuple(o['object_key']): o for o in evidence['objects'] if o['kind'] != 'outer_loop'}
    rows = {}; linked = {}

    def add(row):
        key = (row['start'], row['end'])
        if key in rows:
            previous = rows[key]
            if any(previous.get(k) != row.get(k) for k in ('object_key', 'region', 'source_kind')):
                raise ValueError('same virtual range has conflicting saved identity')
            previous['sources'].extend(row['sources'])
        else:
            rows[key] = row

    for fi, f in enumerate(expanded['files']):
        for ai, alias in enumerate(f['aliases']):
            r = alias.get('region')
            if alias.get('process') != vm['pid'] or not r:
                continue
            if (r['pid'] != vm['pid'] or str(r['start_ticks']) != str(vm['start_time_ticks']) or
                    (r['device'], r['inode']) != (f['device'], f['inode'])):
                raise ValueError('saved mapping alias identity conflict')
            key = ('regular', f['device'], f['inode']); obj = objects[key]
            add(dict(role=role, start=r['start'], end=r['end'], region=r,
                     object_key=list(key), source_kind='file_mapping', backing_kind=obj['kind'],
                     sources=[f'/expanded_files/files/{fi}/aliases/{ai}'],
                     **geometry(r['start'], r['end'], meta['slots'])))
            linked[key] = obj
    pattern = re.compile(r'^/proc/'+str(vm['pid'])+r'/map_files/([0-9a-f]+)-([0-9a-f]+)$')
    for i, row in enumerate(expanded['excluded_entries']):
        match = pattern.fullmatch(row.get('path', ''))
        if not match:
            continue
        start, end = (int(v, 16) for v in match.groups())
        add(dict(role=role, start=start, end=end, source_kind='excluded_nonregular_mapping',
                 object_key=None, region=None, backing_kind='UNKNOWN', saved_exclusion=row,
                 sources=[f'/expanded_files/excluded_entries/{i}'],
                 **geometry(start, end, meta['slots'])))
    values = sorted(rows.values(), key=lambda r: r['start'])
    if any(a['end'] > b['start'] for a, b in zip(values, values[1:])):
        raise ValueError('saved mapping intervals overlap')
    object_rows = []
    for key, o in linked.items():
        selected = [r for r in values if r['object_key'] == list(key)]
        offset_ranges = [(r['region']['offset'], r['region']['offset']+r['virtual_bytes']) for r in selected]
        object_rows.append(dict(object_key=list(key), kind=o['kind'], mappings=len(selected),
            mapped_virtual_bytes=sum(r['virtual_bytes'] for r in selected),
            mapped_file_offset_union_bytes=union_bytes(offset_ranges),
            whole_object_cache_query=o['query'], mapped_resident_physical_bytes=None,
            physical_pfn_evidence_status='NOT_SAVED',
            interpretation='file-offset union is not physical residency; private CoW and unmapped cache remain separate'))
    return dict(role=role, sandbox_id=meta['sandbox_id'], host_identity=vm,
        captured_ram_capacity_bytes=sum(s['size'] for s in meta['slots']),
        saved_file_mapping_count=sum(r['source_kind'] == 'file_mapping' for r in values),
        saved_excluded_mapping_count=sum(r['source_kind'] == 'excluded_nonregular_mapping' for r in values),
        saved_mapping_virtual_bytes=sum(r['virtual_bytes'] for r in values),
        saved_mapping_captured_ram_overlap_virtual_bytes=sum(r['captured_ram_overlap_virtual_bytes'] for r in values),
        mappings=values, linked_objects=object_rows,
        complete_maps_status='NOT_SAVED', per_mapping_smaps_status='NOT_SAVED',
        non_ram_pagemap_status='NOT_SAVED', complete_non_ram_host_physical_bytes=None,
        scope='Partial saved mapping references compared to captured RAM addresses; no full maps snapshot or residency inference')


def markdown(v):
    mib = lambda n: f'{n/MIB:.6f}'
    lines = ['# VMM非RAM映射与host后备页身份：离线核对', '',
        '只读已保存的双沙箱合成校准数据，未启动业务、BPF或真实查询。没有新增物理占用读数。', '',
        '## 已保存映射的覆盖范围', '',
        '| 沙箱 | 已证明RAM容量 MiB | 文件映射段数 | 排除的非普通映射段数 | 已保存映射虚拟长度 MiB | 与已捕获RAM地址重叠 MiB | 完整非RAM物理驻留 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for g in v['guests']:
        lines.append(f"| {g['role']} | {mib(g['captured_ram_capacity_bytes'])} | {g['saved_file_mapping_count']} | {g['saved_excluded_mapping_count']} | {mib(g['saved_mapping_virtual_bytes'])} | {mib(g['saved_mapping_captured_ram_overlap_virtual_bytes'])} | 未采集 |")
    lines += ['', 'RAM容量和映射虚拟长度是地址空间大小，不是实际物理驻留，不能加到上轮RAM确认驻留量。重叠检查只比较已存地址，不证明查询全程映射未变。', '',
        '每沙箱的文件映射及排除项见mappings.tsv和summary.json，原始字段由JSON pointer定位。相同地址别名只计一次；同一文件映射两次仍保留两段，不因此计算两份物理内存。', '',
        '## 文件映射与整个后备对象不能等同', '',
        '| 沙箱 | 后备类型 | 对象身份 | 映射段数 | 映射虚拟长度 MiB | 文件偏移并集 MiB | 整对象有效缓存 MiB |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: |']
    for g in v['guests']:
        for o in g['linked_objects']:
            q=o['whole_object_cache_query'];size='未知' if q['bytes'] is None else mib(q['bytes'])
            lines.append(f"| {g['role']} | {o['kind']} | {o['object_key']} | {o['mappings']} | {mib(o['mapped_virtual_bytes'])} | {mib(o['mapped_file_offset_union_bytes'])} | {size} |")
    lines += ['', 'Firecracker程序文件由两个VMM引用，整文件缓存包含未映射范围；私有映射写入后也可能产生匿名复制页。memfd同一偏移的两个映射指向同一对象，但只有取得物理页号才能核对当前映射是否共享物理页。文件偏移去重不能替代物理去重。', '',
        '## 缺失字段和原因', '',
        '- 全量maps原文未保存：现有host_schedule_files.py跳过inode=0的映射，因此匿名堆、线程栈等无法离线枚举。缺失范围不能填0。',
        '- 非普通映射排除项只保存map_files地址、设备/inode、mode和原因，未保留完整映射名字、权限、偏移、逐映射smaps或页表。不能仅凭匿名inode相同或旧批次的名字，把这些段认定为KVM或io_uring。',
        '- smaps_rollup已保存VMM总PSS及分项，但不含逐段对应。上述映射可能部分已被总PSS统计，缺逐段证据不表示全部未记账；PFNMAP等特殊映射还可能被普通PSS跳过。PSS减去异时的RAM物理并集不能得到非RAM量。',
        f"- 本批后备对象共{v['backing_object_count']}条（各层分列），已存查询是mincore容量或cachestat计数，没有对象页偏移→host物理PFN记录；与RAM的物理交集为NOT_MEASURED。对象身份/引用不等于页身份或记账归属。",
        '- RAM与后备查询在不同时段，单有相同PFN也不足以排除跨时段回收复用，不能离线拼出同一瞬间的跨层物理并集。', '',
        '## 最小补采方案（本轮未实施）', '',
        '1. 先补完整VMM映射：仅对已绑定VMM保存前后maps、逐映射smaps与VmFlags、相同窗口的RAM和非RAM三次pagemap读数。按实际KVM注册区间切分，RAM外的普通驻留PFN先去重，再与RAM求并集；零PFN、换出、映射变化及PFNMAP特殊映射单列，不静默丢弃。身份含host启动、PID/启动ticks、沙箱及guest启动身份。',
        '2. host后备页优先复用snapshot_cache_residency.py已有的mincore内核查询跟踪，不新装host内核或另建全机页打标系统。它曾用于快照/指定偏移，不是本批全对象已验证能力。先离线适配逐对象有界分块、页偏移及查询token、缺失结果和前后身份，并沿用host-root/CAP_FOWNER权限前置检查。现有BPF表最多131072条，整文件必须分块，缺返回不能当冷页；mincore驻留与cachestat缓存索引计数也不强求相等。随后只做一次指定文件验证；不读取内容、不用缺页装入、不暂停。TMPFS、块设备的语义与特殊映射不能直接照搬普通文件。',
        '3. 第一轮只补映射采集，不同时扩展全部后备对象。完成条件是全量映射均有分类和状态、RAM投影一致、普通非RAM页可与RAM按PFN去重，特殊/变化项保留未知，新增CPU/墙钟/数据长度单列。随后再验证一个已列普通文件的物理身份，不扩到七沙箱业务。',
        '4. 不给PFN跨时段增加虚假的永久身份。任何并集均注明非原子观察窗口；前后检查只能减少不确定性，未覆盖的回收复用和共享成本归属仍单列。即使普通VMM映射完整，未映射的内核分配、服务和控制程序仍需另列；完整沙箱总成本继续留空。', '',
        '已有可复用实现：measure_file_physical.py的进程身份/RAM地址转换，ram_page_evidence.py的三读物理集合，host_special_metadata.py的maps解析与smaps关联，snapshot_cache_residency.py的指定页内核查询跟踪。本轮只新增离线核对器，没有改采集入口或默认行为。', '',
        '## 来源与验证', '',
        '源文件清单见summary.json:sources；mappings.tsv给出原始JSON pointer。只重放已保存的host后备汇总，复用上轮RAM证据验证，不重复读取888 MiB RAM向量。本轮9项回归通过，见verification.json；测试仅用内存字典，不访问真实proc或设备。', '',
        '下一步建议：离线实现默认关闭的完整VMM映射证据保存，先用模拟进程文件验证区间拆分、重复映射、变化/特殊页和预算；通过后再确认一次双沙箱合成校准，不自动启动。', '']
    return '\n'.join(lines)


def report(raw_directory, output):
    raw_directory=raw_directory.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(raw_directory) or raw_directory.is_relative_to(output):
        raise ValueError('output must be a new separate directory')
    sources={}
    def load(path):
        sources[str(path)]=dict(size_bytes=path.stat().st_size)
        return json.loads(path.read_text())
    raw=load(raw_directory/'host-backing-raw.json')
    saved=load(raw_directory/'host-backing-evidence.json')
    if raw['status']!='COLLECTED' or summarize(raw)!=saved:
        raise ValueError('saved backing evidence does not match raw replay')
    guests=[audit_vmm(raw,saved,r,load(raw_directory/'live-cache'/r/'ram-page-manifest.json')) for r in raw['guests']]
    v=dict(status='PASS_OFFLINE_AUDIT',sources=sources,guests=guests,
        backing_object_count=len(saved['objects']),backing_coverage=saved['status'],
        backing_object_host_pfns_status='NOT_SAVED',physical_union_with_ram_bytes=None,
        business_runs_started=0,live_queries_started=0,
        observation_window=dict(start_monotonic_ns=raw['start_monotonic_ns'],end_monotonic_ns=raw['end_monotonic_ns']))
    output.mkdir(parents=True)
    with (output/'summary.json').open('x') as f:
        json.dump(v,f,indent=2);f.write('\n')
    with (output/'mappings.tsv').open('x',newline='') as f:
        names=['role','source_kind','start','end','virtual_bytes','captured_ram_overlap_virtual_bytes',
               'outside_captured_ram_virtual_bytes','resident_physical_bytes','object_key','sources']
        writer=csv.DictWriter(f,fieldnames=names,delimiter='\t');writer.writeheader()
        for g in guests:
            for row in g['mappings']:
                writer.writerow({k:json.dumps(row[k]) if isinstance(row[k],list) else row[k] for k in names})
    with (output/'REPORT.md').open('x') as f:
        f.write(markdown(v))
    return v


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw-directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();v=report(args.raw_directory,args.output)
    print('status='+v['status']+' result_dir='+str(args.output))


if __name__=='__main__':
    main()
