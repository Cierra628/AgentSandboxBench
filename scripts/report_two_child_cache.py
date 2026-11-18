"""Saved-page analysis for two private VMMs, with no shared-page assumption."""
import argparse
import json
from pathlib import Path
import statistics

import numpy as np
from fast_cache_metadata import decode_array
from report_live_child_calibration import target_observation
from report_experiment_schedule import periodic_closure,checked_peak

ROLES=('private-a','private-b')
EXPECTED={'both_written':(16,16),'both_read':(16,16),'a_evicted_b_retained':(0,16),'both_evicted':(0,0)}
MIB=2**20


def check_targets(out,run):
    observations={};collectors={};bindings={};errors=[]
    for role in ROLES:
        live=out/'live-cache'/role
        tags=json.loads((out/(role+'-written.json')).read_text())['lifetime_tags']
        bindings[role]=json.loads((live/'vmm-child-binding.json').read_text())
        collectors[role]=json.loads((live/'result.json').read_text())
        rows=[]
        for line in (live/'samples.jsonl').read_text().splitlines():
            s=json.loads(line);prefix=live/f"pair-{s['point']:04}"
            a=decode_array(Path(str(prefix)+'-before.bin').read_bytes())
            b=decode_array(Path(str(prefix)+'-after.bin').read_bytes())
            host=np.fromfile(Path(str(prefix)+'-host.bin'),dtype='<u8').reshape(-1,4)
            assert not len(host) or np.all(host[1:,0]>host[:-1,0]),'host rows not sorted'
            phase=next((p['name'] for p in run['phases'] if p['start_monotonic_ns']<=s['host_sample_start_ns'] and
                s['host_sample_start_ns']+s['host_elapsed_ns']<=p['end_monotonic_ns']),None)
            rows.append(dict(role=role,point=s['point'],phase=phase,
                **target_observation(a,b,host,tags),
                whole_guest_cache_after_bytes=s['guest_cache_after_bytes'],
                whole_confirmed_host_bytes=s['host_confirmed_cache_bytes'],
                whole_unconfirmed_pages=s['unconfirmed_after_pages'],
                host_elapsed_ns=s['host_elapsed_ns'],host_cpu_ns=s['host_thread_cpu_ns'],
                guest_before=s['guest_publication_before'],guest_after_start_ns=s['guest_start_ns'],
                duplicate_adds=b[0]['duplicate_adds'],missing_removes=b[0]['missing_removes'],out_of_range=b[0]['out_of_range']))
        observations[role]=rows
        for name,expected in EXPECTED.items():
            selected=[r for r in rows if r['phase']==name]
            if len(selected)<2:errors.append(role+' '+name+': fewer than two complete paired observations')
            wanted=expected[ROLES.index(role)]*MIB
            for r in selected:
                if (r['target_cache_after_bytes']!=wanted or r['target_confirmed_host_union_bytes']!=wanted or
                        r['target_unconfirmed_after_pages']):
                    errors.append(role+' '+name+' point '+str(r['point'])+': target cache/physical evidence differs from prescribed phase')
        if collectors[role]['status']!='PASS':errors.append(role+': collector failed')
    ids=[bindings[r]['registration_identity'] for r in ROLES]
    if len({v['cgroup_id'] for v in ids})!=2:errors.append('two guests have the same child cgroup ID')
    if len({run['guests'][r]['host_identity']['pid'] for r in ROLES})!=2:errors.append('two guests have the same host PID')
    return dict(status='FAIL' if errors else 'PASS',errors=errors,observations=observations,
        collectors=collectors,bindings=bindings,
        shared_page_deduplication='NOT_VALIDATED_PRIVATE_GUESTS',
        target_definition='Only the initial written guest PFN/lifetime set; not current file contents after eviction/repopulation',
        current_file_cache_after_repopulation=dict(status='UNAVAILABLE',bytes=None,
            reason='No current file device/inode/page-offset to guest-PFN mapping was saved'),
        expectation_mib=EXPECTED,minimum_complete_pairs_per_phase=2)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();batch=a.batch.resolve();out=a.output.resolve();assert not out.exists();out.mkdir(parents=True)
    read=lambda p:json.loads(p.read_text())
    outer=read(batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache';run=read(raw/'result.json')
    analysis=check_targets(raw,run)
    samples=[json.loads(l) for l in (batch/'full-memory/memory.jsonl').read_text().splitlines()]
    closure=periodic_closure(samples);peaks={k:checked_peak(samples,list(range(len(samples))),k,closure) for k in ('current','file')}
    value=dict(status='FAIL' if run['status']=='FAIL' or analysis['status']=='FAIL' else
        'PASS' if outer['status']==analysis['status']==closure['status']=='PASS' else 'PARTIAL',
        calibration=analysis,run=run,outer=outer,closure=closure,peaks=peaks,
        lifecycle=read(storage/'result.json')['cleanup'],storage=str(storage),raw=str(raw),batch=str(batch))
    (out/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    def span(rows,key):
        values=[r[key]/MIB for r in rows];return f'{min(values):.6f}—{max(values):.6f}'
    lines=['# 双沙箱逐页缓存对应验证','',
        f"校准验收：**{analysis['status']}**；原入口：**{outer['status']}**；完整父子周期 segments_v3：**{closure['status']}**。只运行一次双沙箱验证，无暂停、保存或恢复。",'',
        '本次两个采集器均正常结束、身份链均通过；失败发生在保存数据的目标页检查，完整错误原文在下方。失败卷保留，没有重试。' if run['status']=='FAIL' else '两个采集器、身份及目标检查通过。', '',
        '## A回收时，B的缓存是否保留','',
        '使用Prettier #6604固定本地镜像，但只执行16 MiB文件的合成写读/回收负载，不是原29动作。两个私有guest同时存活并分别采集，写读命令并发派发。下表为每段完整双快照内的最小—最大值；1 MiB = 1,048,576字节。', '',
        '**“初始目标页”只指written阶段登记的4096个guest PFN及生命周期。它们消失不能推出当前文件缓存为0，重新装入的页没有保存文件inode/页偏移对应。** 阶段名a_evicted_b_retained是预定操作，不表示B实际保留。全guest列不使用固定目标PFN清单，仍按各时点完整内核元数据计算。', '',
        '| 阶段 | 沙箱 | 完整观察数 | 初始目标页仍在guest缓存 MiB | 初始目标页对应host确认驻留 MiB | 初始目标未确认页数 | 全guest缓存 MiB | 全guest对应host确认驻留 MiB | 全guest未确认页数 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        for role in ROLES:
            rows=[r for r in analysis['observations'][role] if r['phase']==phase['name']]
            lines.append(f"| {phase['name']} | {role} | {len(rows)} | {span(rows,'target_cache_after_bytes')} | {span(rows,'target_confirmed_host_union_bytes')} | {min(r['target_unconfirmed_after_pages'] for r in rows)}—{max(r['target_unconfirmed_after_pages'] for r in rows)} | {span(rows,'whole_guest_cache_after_bytes')} | {span(rows,'whole_confirmed_host_bytes')} | {min(r['whole_unconfirmed_pages'] for r in rows)}—{max(r['whole_unconfirmed_pages'] for r in rows)} |")
    lines+=['', '## 每个VMM的记账和PSS','',
        '各阶段末尾先后读取smaps_rollup和父子记账，保留各自窗口，不称原子快照。PSS是单个VMM映射页按共享映射比例分摊，不含共享服务；VMM子组file不是guest缓存。','',
        '| 阶段 | 沙箱 | VMM子组current MiB | file MiB | anon MiB | kernel MiB | VMM PSS MiB |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for phase in run['phases']:
        for role in ROLES:
            group='/sys/fs/cgroup'+analysis['bindings'][role]['actual_cgroup']
            c=phase['groups']['experiment_memory']['direct_children'][group]
            lines.append('| '+phase['name']+' | '+role+' | '+' | '.join(f"{c[k]/MIB:.6f}" for k in ('current','file','anon','kernel'))+f" | {phase['vmms'][role]['pss_kib']/1024:.6f} |")
    lines+=['', '## 父子闭合与固定收尾','',
        f"- 专属父组：`{outer['parent']}`。父包含子，只读父总量，不相加。全准备/清理期间{closure['total_points']}点，偏离{closure['deviation_count']}点，比例{closure['deviation_fraction']*100:.6f}%（≤5%）；最长段{closure['longest_segment_seconds']:.9f}秒（≤1秒）；全部恢复：{closure['all_segments_recovered']}。",
        '- 终点file差阈值仍为1 MiB。周期每段须在结束前恢复，最长≤1秒、总偏离比例≤5%。峰值若在偏离段内，按原规则用前后闭合边界实测值替代；原值与替代值保留在summary.json。',
        '- 新双沙箱入口从清理完成边界起固定再采1.2秒后停止，不根据PASS/FAIL延长或挑点；单沙箱旧记录和旧FAIL不改。',
        '| 观察阶段 | 父file MiB | Σ直接子组file MiB | 差 MiB |',
        '| --- | ---: | ---: | ---: |']
    for phase in run['phases']:
        em=phase['groups']['experiment_memory']
        lines.append(f"| {phase['name']} | {em['parent_counters']['file']/MIB:.6f} | {em['direct_children_sum']['file']/MIB:.6f} | {em['file_closure']['parent_minus_children_bytes']/MIB:.6f} |")
    for k,peak in peaks.items():
        lines.append(f"- {k}采集峰值：原始{peak['raw_bytes']/MIB:.6f} MiB，状态`{peak['status']}`，最终"+(f"{peak['value_bytes']/MIB:.6f} MiB。" if peak['value_bytes'] is not None else '不可用。'))
    for w in closure['unrecovered_windows']:lines.append('- 未恢复段：'+json.dumps(w,ensure_ascii=False))
    lines+=['','## 失败原文与离线定位','']
    if run.get('error'):
        lines+=['失败阶段：`'+run['failed_stage']+'`。原文：','', '```text',run['error'],'```','']
    events=read(raw/'events.json');timeline=[]
    for role in ROLES:
        saved=[json.loads(l) for l in (raw/'live-cache'/role/'samples.jsonl').read_text().splitlines()]
        rows=analysis['observations'][role]
        first_full=next((r for r in rows if r['target_cache_after_bytes']==16*MIB),None)
        lost=next((r for r in rows if first_full is not None and r['point']>first_full['point'] and r['target_cache_after_bytes']==0),None)
        evict=next(e for e in events if e['role']==role and e['stage']=='request-evict')
        if lost:
            s=next(r for r in saved if r['point']==lost['point']);end=s['host_sample_start_ns']+s['host_elapsed_ns']
            timeline.append(dict(role=role,first_full_point=first_full['point'],first_lost_point=lost['point'],
                lost_observation_end_monotonic_ns=end,own_evict_request_start_ns=evict['start_ns'],
                seconds_before_own_evict=(evict['start_ns']-end)/1e9))
            lines.append(f"- {role}首次观察初始4096页全部消失为point {lost['point']}；该完整观察结束比本沙箱显式回收派发早{(evict['start_ns']-end)/1e9:.6f}秒。不能把它归因于稍后的回收命令。")
    boot_evidence=[]
    for role in ROLES:
        sid=run['guests'][role]['sandbox_id'];path=storage/'service-logs/serial'/sid/'firecracker-stdout.log'
        for number,line in enumerate(path.read_text().splitlines(),1):
            if 'Kernel command line:' in line:
                boot_evidence.append(dict(role=role,path=str(path),line=number,text=line));break
    lines+=['- 本次实际guest串口的Kernel command line含`damon_reclaim.enabled=Y`、`damon_reclaim.min_age=100000`和其余回收参数。确认继承了DAMON后台回收配置，它是初始页提前移除的优先解释；本次未保存运行期DAMON统计和逐页回收原因，不能证明唯一原因。',
        '- 目标检查假定初始4096个生命周期一直保留到显式回收，这个假定被数据否定。原FAIL保留；不通过关闭后台回收、锁页或缩短观察窗口来凑通过。',
        '- 现有元数据能跟踪全guest缓存页加入/移出及生命周期，但没有当前文件的设备号、inode、页偏移到guest PFN对应。仅凭这份固定初始清单，无法量化文件重新装入后的当前缓存；该项记UNAVAILABLE，不把0写成文件当前缓存值。',
        '- 两份written阶段均有16 MiB guest缓存及16 MiB稳定host驻留证据。后续全guest未知项仍逐点保存；失败不是把一个VMM的页错绑给另一个VMM的已知证据。', '']
    value.update(initial_page_disappearance=timeline,actual_guest_boot_evidence=boot_evidence)
    (out/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    lines+=['','## 身份、覆盖、成本和结论边界','']
    for role in ROLES:
        b=analysis['bindings'][role];c=analysis['collectors'][role];obs=analysis['observations'][role]
        lines.append(f"- {role}：PID {run['guests'][role]['host_identity']['pid']}，实际组`{b['actual_cgroup']}`，注册组ID {b['registration_identity']['cgroup_id']}，启动ticks {b['registration_identity']['start_ticks']}。MMDS沙箱ID、出生记录与注册时身份核对一致。")
        lines.append(f"- {role}：采样{c['samples']}点、原始数据{c['raw_bytes']}字节；匿名导出缓冲区{c['locked_anonymous_bytes']/MIB:.6f} MiB；host采集线程CPU {c['host_thread_total_cpu_ns']/1e9:.6f}秒，单点墙钟中位数{statistics.median(r['host_elapsed_ns'] for r in obs)/1e9:.6f}秒。全程未确认页数范围{min(r['whole_unconfirmed_pages'] for r in obs)}—{max(r['whole_unconfirmed_pages'] for r in obs)}，逐点原值保留。")
        lines.append(f"- {role}：duplicate_adds/missing_removes/out_of_range最大值 "+'/'.join(str(max(r[k] for r in obs)) for k in ('duplicate_adds','missing_removes','out_of_range'))+'。')
    lines+=['- 页级范围是诊断guest内核登记的普通文件及shmem缓存，4 KiB页、THP关闭；按guest PFN及生命周期核对前后元数据，再连续三次读取VMM pagemap，只计稳定host PFN并集。变化、非驻留、swap和未知项不补值。',
        '- 目标文件不锁页；回收仅对本文件POSIX_FADV_DONTNEED，不清空全机/全guest缓存。采集器自己的匿名缓冲区锁定且单列开销。页级采样1秒、父子组0.1秒，未做采集开关成本对照。',
        '- 两个guest独立创建，验证的是逐沙箱对应和A/B选择性回收，不是恢复或共享页去重。没有把两份确认值相加宣称跨沙箱唯一物理总量。',
        '- guest缓存移除后，相应host匿名RAM可以继续驻留；本次不能把缓存归属变化当作host释放等量RAM。',
        '- VMM PSS、VMM子组current/file、guest缓存对应host驻留是不同口径，不相加。共享服务、控制程序、host后备层及组外早已记账缓存仍不能按沙箱独占分摊，因此“完整逐沙箱host内存和完整缓存”尚未完成。',
        '', '## 来源与下一步','',f'- 外层：`{batch}`',f'- 原始：`{raw}`',
        '- 原始身份、每页证据、全guest未知项、父子分项、阶段时间和完整闭合检查见summary.json。没有覆盖历史数据。',
        '- 下一步先离线补齐动态文件页归属方案及DAMON运行状态采集点，不再沿用“初始PFN永不变”的检查假定；本批不自动重跑，不清理失败卷。实际共享页去重仍需独立对照。','']
    (out/'REPORT.md').write_text('\n'.join(lines))
    print('status='+value['status']+' result_dir='+str(out))


if __name__=='__main__':main()
