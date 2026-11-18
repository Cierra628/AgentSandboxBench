"""Current-file evidence for two private guests; old fixed-PFN checks stay intact."""
import argparse
import json
from pathlib import Path
import statistics

from report_dynamic_file_cache import analyze,damon_delta
from report_dynamic_file_calibration import check,unconfirmed_details,MIB,PAGE
from report_experiment_schedule import periodic_closure,checked_peak

ROLES=('private-a','private-b')
PHASES=('both_written','a_evicted','a_reread','b_evicted','b_reread')


def read(path):return json.loads(path.read_text())


def check_targets(raw,run):
    errors=[];guests={};bindings={};collectors={};boots=[]
    if [p['name'] for p in run['phases']]!=list(PHASES):errors.append('dynamic phase order differs')
    for role,letter in zip(ROLES,('a','b')):
        live=raw/'live-cache'/role;sid=run['guests'][role]['sandbox_id']
        marker=read(live/'marker.json');binding=read(live/'vmm-child-binding.json')
        collector=read(live/'result.json');vm=run['guests'][role]['host_identity']
        bindings[role]=binding;collectors[role]=collector;boots.append(marker['boot_id'])
        if (collector['status']!='PASS' or binding['status']!='PASS' or
                collector['sandbox_id']!=sid or marker['sandbox_id']!=sid or
                collector['guest_boot_id']!=marker['boot_id'] or collector['host_identity']!=vm or
                binding['actual_cgroup']!=vm['cgroup'] or binding['vmm_binding']['pid']!=vm['pid'] or
                int(binding['registration_identity']['start_ticks'])!=int(vm['start_time_ticks'])):
            raise ValueError(role+': collector/VMM identity mismatch')
        # Rejoin binary evidence under this guest's marker, never another guest's PFNs.
        actual=analyze(live,live/'file-query-download/file-query')
        if actual!=read(live/'dynamic-file-cache.json'):
            raise ValueError(role+': saved file analysis differs from original binary evidence')
        value=check(raw,run,live=live,prefix=role+'-',sandbox_id=sid,
                    positive_phases=('both_written',letter+'_reread'))
        errors.extend(role+': '+e for e in value['errors']);guests[role]=value
    for name,values in (
            ('sandbox ID',[run['guests'][r]['sandbox_id'] for r in ROLES]),('guest boot ID',boots),
            ('PID',[run['guests'][r]['host_identity']['pid'] for r in ROLES]),
            ('child cgroup',[bindings[r]['actual_cgroup'] for r in ROLES]),
            ('cgroup ID',[bindings[r]['registration_identity']['cgroup_id'] for r in ROLES])):
        if len(set(values))!=2:errors.append('two guests share '+name)
    return dict(version='dynamic-two-child-v1',status='FAIL' if errors else 'PASS',errors=errors,
        guests=guests,bindings=bindings,collectors=collectors,
        identity_scope='sandbox ID + guest boot ID + VMM PID/start ticks + birth cgroup; guest inode/PFN may coincide',
        peer_cache_retention='NOT_REQUIRED; current values and unknowns reported',
        shared_page_deduplication='NOT_VALIDATED_PRIVATE_GUESTS')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=False)
    outer=read(args.batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
    run=read(raw/'result.json');checked=read(raw/'target-check.json')
    series=list(map(json.loads,(args.batch/'full-memory/memory.jsonl').read_text().splitlines()))
    closure=periodic_closure(series)
    peaks={k:checked_peak(series,list(range(len(series))),k,closure) for k in ('current','file')}
    details={};costs={};damon={};unavailable={}
    for role in ROLES:
        live=raw/'live-cache'/role
        samples=list(map(json.loads,(live/'samples.jsonl').read_text().splitlines()))
        dynamic=read(live/'dynamic-file-cache.json')
        unavailable[role]=[dict(point=r['point'],reason=r['reason']) for r in dynamic['observations'] if r['status']=='UNAVAILABLE']
        details[role]=unconfirmed_details(live,dynamic['observations'],{s['point']:s for s in samples})
        side=live/'file-query-download/file-query';receipts=[read(f) for f in sorted(side.glob('*.json'))]
        damon[role]=damon_delta(receipts[0]['damon'],receipts[-1]['damon'])
        costs[role]=dict(samples=len(samples),whole_unconfirmed_min=min(s['unconfirmed_after_pages'] for s in samples),
            whole_unconfirmed_max=max(s['unconfirmed_after_pages'] for s in samples),
            sidecar_logical_bytes=sum(f.stat().st_size for f in side.iterdir() if f.is_file()),
            file_query_kernel_median_ns=statistics.median(r['file_cache']['header']['end_ns']-r['file_cache']['header']['start_ns'] for r in receipts if r['file_cache']['status']=='OBSERVED'),
            host_sample_median_ns=statistics.median(s['host_elapsed_ns'] for s in samples),
            host_sample_cpu_ns=sum(s['host_thread_cpu_ns'] for s in samples))
    endpoint=series[-1]['experiment_memory']['file_closure']
    stages_closed=all(p['groups']['experiment_memory']['file_closure']['status']=='WITHIN_THRESHOLD' for p in run['phases'])
    passed=(outer['status']==run['status']==checked['status']==closure['status']=='PASS' and
            endpoint['status']=='WITHIN_THRESHOLD' and stages_closed and all(v['status']!='UNAVAILABLE' for v in peaks.values()))
    value=dict(status='PASS' if passed else 'FAIL',batch=str(args.batch),storage=str(storage),raw=str(raw),
        outer=outer,run=run,calibration=checked,closure=closure,endpoint=endpoint,stages_closed=stages_closed,
        peaks=peaks,file_unconfirmed_details=details,file_unavailable=unavailable,costs=costs,damon_delta=damon,
        lifecycle=read(storage/'result.json')['cleanup'])
    (out/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    lines=['# 双沙箱动态文件缓存验证','',
        f"总判定 **{value['status']}**；动态文件检查 **{checked['status']}**；原入口 **{outer['status']}**；完整周期 segments_v3 **{closure['status']}**。",'',
        '只运行一次双沙箱合成校准，使用本地Prettier固定镜像、两个4 GiB诊断guest，各有一个16 MiB文件。不是原29动作；无暂停、保存恢复、锁目标页、关闭DAMON或清全局缓存。1 MiB = 1,048,576字节。','',
        '## 文件当前页与各自host驻留','',
        '两边先写入；依次回收A、重读A、回收B、重读B。阶段名只表示派发操作，不要求另一边缓存保持不变。每段固定观察3秒。','',
        '| 阶段 | 沙箱 | 完整采样对 | 当前文件缓存 MiB | 对应host确认驻留 MiB | 未确认页数 |','| --- | --- | ---: | ---: | ---: | ---: |']
    def span(rows,key,scale):
        values=[r[key]/scale for r in rows if r.get(key) is not None]
        return f'{min(values):.6f}—{max(values):.6f}' if values else 'UNAVAILABLE'
    for name in PHASES:
        for role in ROLES:
            rows=checked['guests'][role]['phases'][name]
            lines.append(f"| {name} | {role} | {len(rows)} | {span(rows,'current_file_cache_after_bytes',MIB)} | {span(rows,'confirmed_host_union_bytes',MIB)} | {span(rows,'unconfirmed_after_pages',1)} |")
    lines+=['','## 边界与身份','',
        '| 沙箱 | written页数 | evicted页数 | reread页数 | 重读新生命周期偏移数 |','| --- | ---: | ---: | ---: | ---: |']
    for role in ROLES:
        c=checked['guests'][role]
        lines.append('| '+role+' | '+' | '.join(str(c['endpoints'][s]['pages']) for s in ('written','evicted','reread'))+f" | {c['new_lifetime_offsets_after_reread']} |")
    lines.append('')
    for role in ROLES:
        b=checked['bindings'][role];vm=run['guests'][role]['host_identity']
        lines.append(f"- {role}：sandbox `{run['guests'][role]['sandbox_id']}`，guest boot `{checked['collectors'][role]['guest_boot_id']}`，PID {vm['pid']}，start ticks {vm['start_time_ticks']}，出生组 `{b['actual_cgroup']}`，注册cgroup ID {b['registration_identity']['cgroup_id']}。")
    lines+=['','文件设备号、inode、guest PFN可以在两个guest中相同；它们只在各自身份范围内使用，不把数字相同当成同一文件或物理共享。host物理页由各自VMM pagemap确认。未验证跨沙箱实际共享页去重。','',
        '## 父子记账与PSS','',
        '| 阶段 | 沙箱 | 子current MiB | 子file MiB | 子anon MiB | 子kernel MiB | VMM PSS MiB |','| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        for role in ROLES:
            group=checked['bindings'][role]['actual_cgroup'];group=group if group.startswith('/sys/fs/cgroup') else '/sys/fs/cgroup'+group
            c=phase['groups']['experiment_memory']['direct_children'][group]
            lines.append('| '+phase['name']+' | '+role+' | '+' | '.join(f'{c[k]/MIB:.6f}' for k in ('current','file','anon','kernel'))+f" | {phase['vmms'][role]['pss_kib']/1024:.6f} |")
    lines+=['','| 阶段 | 父current MiB | 父file MiB | Σ子file MiB | file差 MiB |','| --- | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        em=phase['groups']['experiment_memory']
        lines.append(f"| {phase['name']} | {em['parent_counters']['current']/MIB:.6f} | {em['parent_counters']['file']/MIB:.6f} | {em['direct_children_sum']['file']/MIB:.6f} | {em['file_closure']['parent_minus_children_bytes']/MIB:.6f} |")
    lines+=['',f"父组 `{outer['parent']}`。完整周期{closure['total_points']}点，偏离{closure['deviation_count']}点、{closure['deviation_fraction']*100:.6f}%，最长{closure['longest_segment_seconds']:.9f}秒，全部恢复{closure['all_segments_recovered']}。末点file差{endpoint['parent_minus_children_bytes']/MIB:.6f} MiB。",'',
        '规则不变：终点file差≤1 MiB；周期偏离段必须恢复、每段≤1秒、偏离点≤5%；峰值在偏离段时用前后闭合边界实测值替代并保留原值。固定收尾1.2秒，不根据结果延长。']
    for key,peak in peaks.items():
        final='UNAVAILABLE' if peak['value_bytes'] is None else f"{peak['value_bytes']/MIB:.6f} MiB"
        lines.append(f"- 父{key}采集峰值：原始{peak['raw_bytes']/MIB:.6f} MiB，最终{final}，状态`{peak['status']}`，受偏离影响{peak['affected']}。")
    lines+=['','## 未知项、覆盖与成本','']
    for role in ROLES:
        c=costs[role];collector=checked['collectors'][role]
        lines.append(f"- {role}：全guest {c['samples']}点，未确认{c['whole_unconfirmed_min']}—{c['whole_unconfirmed_max']}页；文件未知逐点解释见summary.json。查询内核耗时中位数{c['file_query_kernel_median_ns']/1e6:.6f}毫秒；host采样墙钟中位数{c['host_sample_median_ns']/1e9:.6f}秒，CPU合计{c['host_sample_cpu_ns']/1e9:.6f}秒；匿名导出缓冲区{collector['locked_anonymous_bytes']/MIB:.6f} MiB，附加文件逻辑长度{c['sidecar_logical_bytes']}字节（不是物理驻留量）。")
        lines.append('- '+role+' DAMON增量：`'+json.dumps(damon[role],ensure_ascii=False)+'`。')
        lines.append('- '+role+' 文件不可用点：`'+json.dumps(unavailable[role],ensure_ascii=False)+'`；不补零。')
    lines+=['','阶段中的变化应直接按上表解释：只有本沙箱收到的回收、重读命令才是本沙箱的显式操作。另一边的缓存也可以在后台变化；即使DAMON计数增加，也没有逐页证据可把具体文件页的变化归因于它。']
    lines+=['','host确认驻留是前后文件身份/偏移/生命周期、全guest元数据及三次host映射一致的稳定页下界。端点不是原子快照，未知不补值；文件未创建时UNAVAILABLE，不记零。采集器和附加文件也有成本，尚未做开关对照。',
        '全guest元数据覆盖普通文件/shmem缓存，不覆盖块设备专用缓存或swap-only缓存。DAMON只记录全guest区域操作，不能指定某一文件页的回收原因。目标文件回收不等于host释放等量RAM。',
        '父组与子组不相加；cgroup记账、VMM PSS、guest缓存对应host驻留不相加。共享服务、控制程序、host后备层及组外早已记账缓存仍未逐沙箱完整归属，本次不宣称完整host内存或完整page cache测量完成。','',
        '## 来源、清理与下一步','',f'- 外层：`{args.batch}`',f'- 原始：`{raw}`',
        '- 本次代码、身份、阶段时间、父子四项、PSS分项、逐页证据和清理记录均保留。旧固定初始页检查及历史FAIL不改。',
        '- 下一步建议：先离线汇总仍未归属的host后备层与共享成本，明确每沙箱已测范围及缺口；不直接扩展七沙箱完整业务，也不把指定文件校准当成全覆盖。','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    print('status='+value['status']+' result_dir='+str(out))


if __name__=='__main__':main()
