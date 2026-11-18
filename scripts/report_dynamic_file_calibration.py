"""Check one declared file through write/eviction/reread, not immutable PFNs."""
import argparse
import json
from pathlib import Path
import statistics

from file_cache_metadata import decode, IDENTITY
from report_experiment_schedule import periodic_closure, checked_peak
from report_dynamic_file_cache import damon_delta
from kernel_cache_metadata import decode as decode_guest

SIZE=16*2**20
PAGE=4096
MIB=2**20


def unconfirmed_details(live,observations,samples):
    """Explain saved unknowns without changing the original sample or verdict."""
    details=[]
    for row in observations:
        if not row.get('unconfirmed_after_pages'):continue
        point=row['point'];left,right=row['publications']
        side=live/'file-query-download/file-query'
        before=decode((side/f'file-{left:04}.bin').read_bytes())[1]
        after=decode((side/f'file-{right:04}.bin').read_bytes())[1]
        whole=[decode_guest((live/f'pair-{point:04}-{suffix}.bin').read_bytes())[1] for suffix in ('before','after')]
        reasons=dict(unknown_file_tag=0,file_pair_changed=0,whole_guest_before_changed=0,whole_guest_after_changed=0)
        for index,(pfn,tag) in after.items():
            if not tag:reasons['unknown_file_tag']+=1
            elif before.get(index)!=(pfn,tag):reasons['file_pair_changed']+=1
            elif whole[0].get(pfn)!=tag:reasons['whole_guest_before_changed']+=1
            elif whole[1].get(pfn)!=tag:reasons['whole_guest_after_changed']+=1
        reasons['other_host_unconfirmed']=row['unconfirmed_after_pages']-sum(reasons.values())
        assert reasons['other_host_unconfirmed']>=0
        sample=samples[point]
        details.append(dict(point=point,publications=[left,right],unconfirmed_after_pages=row['unconfirmed_after_pages'],
            reasons=reasons,file_query_before_records=len(before),file_query_after_records=len(after),
            file_query_after_to_publication_end_ns=sample['guest_end_ns']-row['file_query_after']['file_cache']['header']['end_ns'],
            whole_guest_unconfirmed_after_pages=sample['unconfirmed_after_pages'],
            damon_delta=row['damon_delta'],source_pair_prefix=str(live/f'pair-{point:04}')))
    return details


def check(raw,run,*,live=None,prefix='',sandbox_id=None,positive_phases=('written','reread')):
    read=lambda p:json.loads(p.read_text())
    live=live if live is not None else raw/'live-cache'
    dynamic=read(live/'dynamic-file-cache.json')
    samples={r['point']:r for r in map(json.loads,(live/'samples.jsonl').read_text().splitlines())}
    marker=read(live/'marker.json')
    expected_sid=sandbox_id if sandbox_id is not None else (raw/'sandbox-id.txt').read_text().strip()
    if dynamic['sandbox_id']!=expected_sid or marker['sandbox_id']!=expected_sid or dynamic['boot_id']!=marker['boot_id']:
        raise ValueError('dynamic calibration guest identity mismatch')
    errors=[];endpoints={};identities=[]
    for phase in ('written','evicted','reread'):
        receipt=read(raw/(prefix+phase+'.json'))['file_cache']
        header,pages=decode((raw/(prefix+phase+'-file.bin')).read_bytes())
        if header!=receipt['header']:raise ValueError('fixture query receipt differs from binary')
        endpoints[phase]=dict(header=header,pages=len(pages),file_identity=receipt['identity'])
        identities.append(tuple(header[k] for k in IDENTITY))
        expected=0 if phase=='evicted' else SIZE//PAGE
        if (len(pages)!=expected or header['unknown_records'] or
                header['mapping_pages_before']!=expected or header['mapping_pages_after']!=expected):
            errors.append(phase+': endpoint cache enumeration is not complete at prescribed workload boundary')
        if phase!='evicted' and (set(pages)!=set(range(SIZE//PAGE)) or any(not tag or tag&1 for pfn,tag in pages.values())):
            errors.append(phase+': file offsets or ordinary-file lifetime evidence missing')
        if header['size_before']!=SIZE or header['size_after']!=SIZE:
            errors.append(phase+': file size differs')
        endpoints[phase]['offset_records']=pages
    if len(set(identities))!=1 or any(endpoints[p]['file_identity']!=endpoints['written']['file_identity'] for p in endpoints):
        errors.append('file or backing mapping identity changed')
    initial=endpoints['written']['offset_records'];last=endpoints['reread']['offset_records']
    new_lifetimes=sum(i in initial and initial[i][1]!=tag for i,(pfn,tag) in last.items())
    if new_lifetimes!=SIZE//PAGE:errors.append('reread did not establish new lifetimes at all file offsets')
    phases={}
    for phase in run['phases']:
        rows=[]
        for row in dynamic['observations']:
            s=samples[row['point']]
            if phase['start_ns']<=s['timestamp_ns'] and s['timestamp_ns']+s['host_elapsed_ns']<=phase['end_ns']:
                rows.append(row)
        phases[phase['name']]=rows
        if not rows:errors.append(phase['name']+': no complete paired observation in phase')
        if phase['name'] in positive_phases and not any(
                r.get('confirmed_host_union_bytes',0) and r.get('current_file_cache_after_bytes',0) for r in rows):
            errors.append(phase['name']+': no positive file-to-host physical correspondence confirmed')
    # Do not require all subsequent samples to retain the first 4096 PFNs.
    return dict(version='dynamic-file-calibration-v1',status='FAIL' if errors else 'PASS',errors=errors,
        endpoints=endpoints,new_lifetime_offsets_after_reread=new_lifetimes,phases=phases,
        preserved_initial_pfn_retention_check='NOT_APPLIED_NEW_PROTOCOL; historical checker unchanged',
        scope='one 16 MiB ordinary file; stable host-resident lower bound; no whole-sandbox claim')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    assert not (out/'REPORT.md').exists() and not (out/'summary.json').exists()
    read=lambda p:json.loads(p.read_text())
    outer=read(args.batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
    run=read(raw/'result.json');checked=check(raw,run)
    live=raw/'live-cache';collector=read(live/'result.json');binding=read(live/'vmm-child-binding.json')
    samples=list(map(json.loads,(live/'samples.jsonl').read_text().splitlines()))
    series=list(map(json.loads,(args.batch/'full-memory/memory.jsonl').read_text().splitlines()))
    closure=periodic_closure(series)
    peaks={k:checked_peak(series,list(range(len(series))),k,closure) for k in ('current','file')}
    dynamic=read(live/'dynamic-file-cache.json');rows=dynamic['observations']
    sidecars=live/'file-query-download/file-query'
    receipts=[read(f) for f in sorted(sidecars.glob('*.json'))]
    total_damon=damon_delta(receipts[0]['damon'],receipts[-1]['damon'])
    result=dict(status='PASS' if outer['status']==checked['status']==closure['status']=='PASS' else 'FAIL',
        batch=str(args.batch),storage=str(storage),raw=str(raw),outer=outer,run=run,calibration=checked,
        full_series_closure=closure,peaks=peaks,collector=collector,binding=binding,damon_delta=total_damon,
        costs=dict(sidecar_logical_bytes=sum(f.stat().st_size for f in sidecars.iterdir() if f.is_file()),
            sidecar_files=sum(f.is_file() for f in sidecars.iterdir()),
            guest_file_query_kernel_median_ns=statistics.median(r['file_cache']['header']['end_ns']-r['file_cache']['header']['start_ns'] for r in receipts if r['file_cache']['status']=='OBSERVED'),
            host_sample_median_ns=statistics.median(s['host_elapsed_ns'] for s in samples),
            host_sample_cpu_ns=sum(s['host_thread_cpu_ns'] for s in samples)),
        scope='one running private guest, synthetic file; no pause, restore or original task trajectory')
    result['file_unconfirmed_details']=unconfirmed_details(live,rows,{s['point']:s for s in samples})
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    lines=['# 动态文件缓存：单沙箱校准','',
        f"新文件对应检查 **{checked['status']}**；原入口 **{outer['status']}**；完整周期 segments_v3 **{closure['status']}**。",'',
        '只运行一次单沙箱合成校准：Prettier #6604固定镜像、4 GiB诊断guest、一个16 MiB文件。没有运行原29动作，没有暂停、保存、恢复、锁文件页或清全局缓存。1 MiB = 1,048,576字节。','',
        '## 写入、回收和重新装入','',
        '| 边界 | 文件当前缓存 MiB | 未知条目 | 说明 |','| --- | ---: | ---: | --- |']
    for phase,v in checked['endpoints'].items():
        lines.append(f"| {phase} | {v['pages']*PAGE/MIB:.6f} | {v['header']['unknown_records']} | 直接查询现有文件映射，二进制与receipt一致 |")
    lines+=['',f"重读后有{checked['new_lifetime_offsets_after_reread']}个文件偏移获得新生命周期；物理页号可以相同，但不会当作旧生命周期。",'',
        '| 观察阶段 | 完整采样对 | 当前文件缓存 MiB范围 | 文件对应host确认驻留 MiB范围 | 文件未确认页数 |','| --- | ---: | ---: | ---: | ---: |']
    def span(rows,key,scale):
        values=[r[key]/scale for r in rows if r.get(key) is not None]
        return f'{min(values):.6f}—{max(values):.6f}' if values else 'UNAVAILABLE'
    for name,values in checked['phases'].items():
        lines.append(f"| {name} | {len(values)} | {span(values,'current_file_cache_after_bytes',MIB)} | {span(values,'confirmed_host_union_bytes',MIB)} | {span(values,'unconfirmed_after_pages',1)} |")
    for detail in result['file_unconfirmed_details']:
        lines+=['',f"- point {detail['point']}：文件前查询{detail['file_query_before_records']}条、后查询{detail['file_query_after_records']}条；未确认{detail['unconfirmed_after_pages']}页，分类为`{json.dumps(detail['reasons'],ensure_ascii=False)}`。文件后查询结束至该次全guest发布结束相隔{detail['file_query_after_to_publication_end_ns']/1e6:.6f}毫秒；不能沿用先前文件清单补足物理量。",
            f"  同点全guest的未确认{detail['whole_guest_unconfirmed_after_pages']}页以更晚的全guest后快照为基准，已移出的生命周期另列；因此不必等于文件查询口径的未确认页数。同对DAMON统计增量为`{json.dumps(detail['damon_delta']['values'])}`，没有逐页原因，不能指定回收者。"]
    lines+=['','缓存可以在观察期被后台回收，不要求一直保持16 MiB。确认物理量只计前后文件身份、偏移、生命周期及host三次映射一致的物理页并集；变化和缺失单列。端点和采样对不是原子快照，不能把下界当完整瞬时物理总量。','',
        '## 同一阶段的记账和PSS','',
        '| 阶段 | 父current MiB | 父file MiB | VMM子组current MiB | VMM子组file MiB | VMM PSS MiB |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        em=phase['groups']['experiment_memory'];parent=em['parent_counters']
        group=binding['actual_cgroup'];group=group if group.startswith('/sys/fs/cgroup') else '/sys/fs/cgroup'+group
        child=em['direct_children'][group]
        lines.append(f"| {phase['name']} | {parent['current']/MIB:.6f} | {parent['file']/MIB:.6f} | {child['current']/MIB:.6f} | {child['file']/MIB:.6f} | {phase['vmm']['pss_kib']/1024:.6f} |")
    lines+=['','PSS是VMM已映射页按共享比例分摊，cgroup是内存记账，文件对应host驻留是缓存身份的物理对应。三者不相加；guest移除文件缓存不等于host释放等量RAM。','',
        '## 覆盖、回收状态和成本','',
        f"- 全guest采样{len(samples)}点，未确认页数{min(s['unconfirmed_after_pages'] for s in samples)}—{max(s['unconfirmed_after_pages'] for s in samples)}。新文件查询之外仍保存全guest普通文件/shmem元数据，不含块设备专用缓存与swap-only缓存。",
        '- DAMON增量：`'+json.dumps(total_damon,ensure_ascii=False)+'`。这是全guest区域操作统计，不能确定某个文件页由它回收，也不是host实际释放字节。',
        f"- 匿名导出缓冲区锁定{collector['locked_anonymous_bytes']/MIB:.6f} MiB；只锁采集器，不锁目标文件。附加文件{result['costs']['sidecar_files']}个，逻辑长度合计{result['costs']['sidecar_logical_bytes']}字节；不是其物理驻留量。它们在guest tmpfs占用并进入全guest缓存统计，不算任务自身缓存。",
        f"- guest内核文件查询耗时中位数{result['costs']['guest_file_query_kernel_median_ns']/1e6:.6f}毫秒；host单点墙钟中位数{result['costs']['host_sample_median_ns']/1e9:.6f}秒，采样CPU合计{result['costs']['host_sample_cpu_ns']/1e9:.6f}秒。没有做采集开关对照。",
        f"- 父组`{outer['parent']}`；全周期{closure['total_points']}点，偏离{closure['deviation_count']}点、比例{closure['deviation_fraction']*100:.6f}%，最长{closure['longest_segment_seconds']:.9f}秒，全部恢复{closure['all_segments_recovered']}。终点阈值1 MiB、每段≤1秒、偏离≤5%，未调整。新路径固定收尾1.2秒，不根据结果延长。",'',
        '## 来源、异常和下一步','',f'- 批次：`{args.batch}`',f'- 原始：`{raw}`',
        '- 身份、逐页文件证据、全guest未知项、父子四分项、阶段时间和闭合检查保存在summary.json及原始目录。',
        '- 失败或缺项：'+('; '.join(checked['errors']) if checked['errors'] else '新文件对应检查无错误；变化未知范围见上表。'),
        '- 本次是新版本检查，没有修改历史双沙箱七条失败，也没有给旧数据补字段。','']
    lines+=['## 构建、身份与终点闭合','',
        f"- VMM PID {binding['vmm_binding']['pid']}，出生方式`{binding['cgroup_birth']['creation']}`，实际子组`{binding['actual_cgroup']}`，注册时启动ticks {binding['registration_identity']['start_ticks']}、子组ID {binding['registration_identity']['cgroup_id']}。沙箱ID、MMDS和RAM捕获绑定检查通过。",
        '- 实际guest版本`'+read(live/'marker.json')['kernel']+'`。内核查询按文件可见身份与底层映射身份分别保存；本次两者相同，没有验证overlay上下层身份不同的情况。',
        f"- 末周期点父子file差{series[-1]['experiment_memory']['file_closure']['parent_minus_children_bytes']/MIB:.6f} MiB；三个阶段终点分别为"+' / '.join(str(phase['groups']['experiment_memory']['file_closure']['parent_minus_children_bytes']/MIB) for phase in run['phases'])+' MiB。']
    for name,peak in peaks.items():
        final='UNAVAILABLE' if peak['value_bytes'] is None else f"{peak['value_bytes']/MIB:.6f} MiB"
        lines.append(f"- 父组{name}采集峰值：原始{peak['raw_bytes']/MIB:.6f} MiB，最终{final}，检查状态`{peak['status']}`；完整原始及替代信息见summary.json。")
    lines+=['','## 下一步建议','',
        '仅在本次通过后做一次双沙箱同类验证，确认两份动态文件页与各自VMM对应，回收A时不误改B的文件身份；不假定B的缓存必须一直保留。仍不先跑七实例完整业务或宣称全部共享成本已归属。','']
    with (out/'REPORT.md').open('x') as f:f.write('\n'.join(lines))
    print('status='+result['status']+' result_dir='+str(out))


if __name__=='__main__':main()
