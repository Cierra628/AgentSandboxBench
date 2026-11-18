"""Offline report of the single running child-VMM calibration, including unknowns."""
import argparse
import json
from pathlib import Path
import statistics

import numpy as np
from fast_cache_metadata import decode_array,matching_tags
from kernel_cache_metadata import MASK,PRESENT,SWAPPED,PHYSICAL_MASK
from report_experiment_schedule import periodic_closure,checked_peak

PAGE=4096
MIB=2**20


def target_observation(before,after,host,tags):
    """Do not confuse a reused guest PFN with the old file's cache page."""
    pfns=np.array(sorted(map(int,tags)),dtype='<u8')
    expected=np.array([tags[str(int(p))] for p in pfns],dtype='<u8')
    a=matching_tags(before[1],pfns)==expected;b=matching_tags(after[1],pfns)==expected
    indices=np.searchsorted(host[:,0],pfns);found=indices<len(host)
    pos=np.flatnonzero(found);found[pos]=host[indices[pos],0]==pfns[pos]
    stable=np.zeros(len(pfns),dtype=bool);present=stable.copy();absent=stable.copy();swapped=stable.copy()
    physical=[]
    for i in np.flatnonzero(a&b&found):
        first,middle,last=map(int,host[indices[i],1:])
        if ((first^middle)|(middle^last))&PHYSICAL_MASK:continue
        stable[i]=True
        if first&PRESENT:
            assert first&MASK,'host PFN hidden';present[i]=True;physical.append(first&MASK)
        elif first&SWAPPED:swapped[i]=True
        else:absent[i]=True
    return dict(target_cache_before_bytes=int(a.sum())*PAGE,target_cache_after_bytes=int(b.sum())*PAGE,
        target_confirmed_host_union_bytes=len(set(physical))*PAGE,
        target_stable_resident_guest_pages=int(present.sum()),target_nonresident_guest_pages=int(absent.sum()),
        target_swapped_guest_pages=int(swapped.sum()),target_unconfirmed_after_pages=int((b&~stable).sum()))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();batch=args.batch.resolve();out=args.output.resolve();assert not out.exists();out.mkdir(parents=True)
    read=lambda p:json.loads(p.read_text())
    outer=read(batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
    run=read(raw/'result.json');live=raw/'live-cache';cache=read(live/'result.json')
    samples=[json.loads(l) for l in (live/'samples.jsonl').read_text().splitlines()]
    written=read(raw/'written.json');tags=written['lifetime_tags'];rows=[]
    for s in samples:
        prefix=live/f"pair-{s['point']:04}"
        before=decode_array(Path(str(prefix)+'-before.bin').read_bytes());after=decode_array(Path(str(prefix)+'-after.bin').read_bytes())
        host=np.fromfile(Path(str(prefix)+'-host.bin'),dtype='<u8').reshape(-1,4)
        assert not len(host) or np.all(host[1:,0]>host[:-1,0])
        target=target_observation(before,after,host,tags)
        # Keep a sample only when its entire observation interval is in a phase.
        phase=next((p['name'] for p in run['phases'] if p['start_ns']<=s['timestamp_ns'] and
            s['timestamp_ns']+s['host_elapsed_ns']<=p['end_ns']),None)
        rows.append(dict(point=s['point'],phase=phase,**target,whole_guest_cache_after_bytes=s['guest_cache_after_bytes'],
            whole_host_confirmed_bytes=s['host_confirmed_cache_bytes'],whole_unconfirmed_pages=s['unconfirmed_after_pages'],
            host_elapsed_ns=s['host_elapsed_ns'],host_cpu_ns=s['host_thread_cpu_ns'],
            duplicate_adds=after[0]['duplicate_adds'],missing_removes=after[0]['missing_removes'],
            out_of_range=after[0]['out_of_range'],moved_pages=after[0]['moved_pages'],stale_records=after[0]['stale_records']))
    phases={p['name']:[r for r in rows if r['phase']==p['name']] for p in run['phases']}
    assert all(phases.values()), 'phase has no complete paired observation'
    phase_values={p['name']:p for p in run['phases']}
    series=[json.loads(l) for l in (batch/'full-memory/memory.jsonl').read_text().splitlines()]
    closure=periodic_closure(series)
    peaks={key:checked_peak(series,list(range(len(series))),key,closure) for key in ('current','file')}
    lifecycle=read(storage/'result.json')
    result=dict(status='PASS' if outer['status']=='PASS' and closure['status']=='PASS' else 'PARTIAL',
        page_calibration_status=outer['status'],full_series_closure=closure,full_series_peaks=peaks,
        lifecycle_cleanup=lifecycle['cleanup'],available_after_bytes=outer['available_after_bytes'],
        batch=str(batch),storage=str(storage),raw=str(raw),
        scope='single guest diagnostic calibration; no original business task, no multi-guest physical union',
        run=run,collector=cache,target=written,evicted=read(raw/'evicted.json'),observations=rows,
        binding=read(live/'vmm-child-binding.json'),
        host_sample_wall_median_ns=statistics.median(r['host_elapsed_ns'] for r in rows),
        host_sample_wall_max_ns=max(r['host_elapsed_ns'] for r in rows))
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    def span(items,key):
        values=[r[key]/MIB for r in items];return f'{min(values):.6f}—{max(values):.6f}'
    lines=['# 单沙箱运行中缓存页对应验证','',f"页级校准及生命周期：**{outer['status']}**；完整父子记账序列 segments_v3：**{closure['status']}**，两项分开判定。只运行一次单VMM校准；使用Prettier #6604固定镜像，没有运行原29动作业务，没有暂停、保存或恢复。",'',
        '1 MiB = 1,048,576 字节。下表为各阶段内完整双快照观察的最小—最大值，不跨时间累加。', '',
        '| 阶段 | 完整观察数 | 目标文件guest缓存 MiB | 目标文件对应host确认驻留 MiB | 全guest缓存 MiB | 全guest对应host确认驻留 MiB | 全guest未确认页数 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for phase,items in phases.items():
        lines.append(f"| {phase} | {len(items)} | {span(items,'target_cache_after_bytes')} | {span(items,'target_confirmed_host_union_bytes')} | {span(items,'whole_guest_cache_after_bytes')} | {span(items,'whole_host_confirmed_bytes')} | {min(r['whole_unconfirmed_pages'] for r in items)}—{max(r['whole_unconfirmed_pages'] for r in items)} |")
    lines+=['','## 同一阶段的记账与PSS','',
        '| 阶段 | 父组current MiB | 父组file MiB | VMM子组current MiB | VMM子组file MiB | VMM PSS MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        em=phase['groups']['experiment_memory'];parent=em['parent_counters'];children=em['direct_children']
        child=children[result['binding']['actual_cgroup'] if result['binding']['actual_cgroup'].startswith('/sys/fs/cgroup') else '/sys/fs/cgroup'+result['binding']['actual_cgroup']]
        lines.append(f"| {phase['name']} | {parent['current']/MIB:.6f} | {parent['file']/MIB:.6f} | {child['current']/MIB:.6f} | {child['file']/MIB:.6f} | {phase['vmm']['pss_kib']/1024:.6f} |")
    lines+=['', f"目标文件登记{len(tags)}个生命周期；写读两阶段的目标未确认页合计{sum(r['target_unconfirmed_after_pages'] for name in ('written','read-done') for r in phases[name])}页。定向回收后旧生命周期移除{result['evicted']['removed_or_reused_pages']}页，剩余{result['evicted']['same_lifetime_pages_remaining']}页。", '',
        '**缓存身份移除不等于host释放16 MiB RAM。** guest释放页后，VMM对应匿名RAM仍可能驻留：'+
        f"本次VMM PSS从written的{phase_values['written']['vmm']['pss_kib']/1024:.6f}变为evicted的{phase_values['evicted']['vmm']['pss_kib']/1024:.6f} MiB，并未下降16 MiB。"+
        '页级结果回答的是“哪些host驻留页当前属于guest文件缓存”，不是虚拟机把多少RAM还给宿主。', '',
        '## 父子记账检查单列','',
        '三个观察终点file差额均为0字节。完整序列包含准备和收尾，不用三个终点替代全序列判定。原始外层`status=PASS`只表示本次专用校准、资源生命周期检查通过；本报告另按不变的segments_v3检查完整记账序列。', '',
        f"- 周期点{closure['total_points']}，偏离{closure['deviation_count']}点，占{closure['deviation_fraction']*100:.6f}%（上限5%）；最长段{closure['longest_segment_seconds']:.9f}秒（上限1秒）。",
        f"- 全部恢复：{closure['all_segments_recovered']}；完整序列判定 **{closure['status']}**。1 MiB阈值不变，缺恢复证据仍FAIL；没有重试或把最后一点丢掉。"]
    for window in closure['unrecovered_windows']:
        last=series[window['end_index']]['experiment_memory']['file_closure']
        lines.append(f"- 未恢复段：索引{window['start_index']}—{window['end_index']}；末点父减子file差{last['parent_minus_children_bytes']/MIB:.9f} MiB。持续时间{window['duration_seconds']:.9f}秒为标称0.1秒/点换算，原因：{window['duration_fallback_reason']}。")
    final=outer['after_cleanup']
    lines.append(f"- 另存的after_cleanup查询在最后周期点之后{(final['monotonic_ns']-series[-1]['monotonic_ns'])/1e9:.9f}秒开始，其file差为{final['experiment_memory']['file_closure']['parent_minus_children_bytes']/MIB:.6f} MiB。它提供了稍后闭合的独立观察，但不追加到周期序列，也不覆盖原周期FAIL；现有证据不足以证明具体内核刷新原因。")
    for key,peak in peaks.items():
        lines.append(f"- 父组{key}峰值：原始{peak['raw_bytes']/MIB:.6f} MiB，状态`{peak['status']}`，偏离点受影响：{peak['affected']}。最终值"+
            (f"{peak['value_bytes']/MIB:.6f} MiB。" if peak['value_bytes'] is not None else '不可用；不补值。'))
    lines+=['','## 方法、成本和边界','',
        '- 元数据从诊断guest内核的`/proc/asb_pagecache`导出，涵盖该内核登记的普通文件及shmem缓存；按guest PFN和生命周期编号核对加入、移出、复用。匿名导出缓冲区不计入文件缓存。',
        '- KVM注册在创建前捕获；按沙箱ID、MMDS、启动时间、clone3出生收据及注册时cgroup核对。实际成员是一个独立VMM子组，不从Docker组迁移。',
        '- host pagemap连续读三次；只有前后guest生命周期相同且三次host映射一致的页，才计入host PFN去重后的确认驻留。变化页、未确认页、非驻留和swap另列。没有读取目标文件来查询host驻留。',
        '- 16 MiB目标文件的写读是显式校准负载，不锁页。回收仅用该文件的`POSIX_FADV_DONTNEED`，没有清空全机或全guest缓存。导出器锁定的是自己的匿名缓冲区，开销单列。',
        f"- 页级采样间隔1秒，父子cgroup采样间隔0.1秒。页级采样{cache['samples']}点，原始数据{cache['raw_bytes']}字节；导出缓冲区{cache['locked_anonymous_bytes']/MIB:.6f} MiB。host采集线程CPU {cache['host_thread_total_cpu_ns']/1e9:.6f}秒；每点墙钟中位数{result['host_sample_wall_median_ns']/1e9:.6f}秒，最大{result['host_sample_wall_max_ns']/1e9:.6f}秒。开销未做开关对照。",
        f"- 全部{len(rows)}点的guest内核duplicate_adds、missing_removes、out_of_range最大值分别为"+
        '/'.join(str(max(r[k] for r in rows)) for k in ('duplicate_adds','missing_removes','out_of_range'))+
        '；KVM捕获stderr及注册记录保存在原始目录，PID/子组ID/启动时间核对证据在vmm-child-binding.json。没有用内存容量除以沙箱数代替测量。',
        f"- 初始观察point=0已包含guest启动和上传工具后的缓存，不是启动瞬间：全guest未确认{rows[0]['whole_unconfirmed_pages']}页；read-done阶段逐点未确认页数为"+
        '/'.join(str(r['whole_unconfirmed_pages']) for r in phases['read-done'])+
        '。确认驻留是稳定页的下界，不把未确认页补成已测得。',
        '- `current`是cgroup记账；PSS是该VMM映射页按共享映射比例分摊；页级结果是guest缓存对应host确认驻留。三项不相加，VMM子组file不等于guest page cache。',
        '- 本次仅一个诊断内核guest，未验证七实例并发、恢复后备层、跨沙箱物理去重及全部共享服务成本；它不构成逐沙箱完整host内存的验收。',
        '- 内核4 KiB页、THP关闭；普通文件/shmem缓存范围不包含块设备专用缓存及swap-only缓存，采集器缓冲区和工具也会影响guest/host占用。原内核业务的性能和覆盖不能直接套用本次校准。',
        f"- 隔离容器、VMM、挂载和loop已由原入口清理，成功卷按原行为释放；已有失败卷未动。收尾可用盘{outer['available_after_bytes']/2**30:.6f} GiB。",
        '', '## 原始来源','',f'- 外层：`{batch}`',f'- 隔离卷记录：`{storage}`',f'- 业务校准及逐页原始文件：`{raw}`',
        '- 所有观察、未知项、各阶段父子组原值、PSS分项及身份链见本目录`summary.json`。没有修改旧数据。','',
        '## 代码和离线回归','',
        '- 新入口：`run_live_child_calibration.py`建立专属slice并准备本地输入；`validate_isolated_agentenv.py`仅增加显式`--guest-cache-child-calibration`，普通默认和七实例GRPO限制保留。',
        '- `run_guest_cache_child.py`在创建前启动既有Live采集；`guest_cache_child_fixture.py`只做一个文件的写读和定向回收；`report_live_child_calibration.py`只读原始二进制与JSON生成本报告。',
        '- `tests/test_live_child_calibration.py`七项模拟回归通过：创建前捕获、身份失败不重试、原磁盘门槛、目标不锁页、物理页去重、guest PFN复用排除、host映射变化保留未知。测试不使用真实服务/设备。日志：`results/live-child-calibration-preflight-20261115/tests-with-offline-report.log`。', '',
        '## 下一步建议','',
        '先离线核对页级与cgroup收尾时间线，保留最后一点FAIL；再验证两个沙箱并发时的身份、缓存生命周期和host驻留。普通独立创建验证逐沙箱归属；跨沙箱共享PFN去重另需有实际共享页的对照，不用两个私有guest冒充共享验证。不先扩展到七实例完整业务。','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    print('status='+result['status']+' result_dir='+str(out))


if __name__=='__main__':main()
