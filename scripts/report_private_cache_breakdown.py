"""Read saved private-guest cache, PSS and memcg data; never query a live host."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode,PHYSICAL_MASK,PRESENT,SWAPPED,MASK

PAGE=4096
MIB=2**20
HOST=struct.Struct('<4Q')
ROLES=('private-a','private-b')
KEYS=('current','file','anon','kernel')


def partition(before,after,host):
    """Classify after-snapshot guest pages; union host PFNs within each class."""
    left_info,left=decode(before);info,last=decode(after)
    known={p for p,t in left.items() if t}
    if len(host)!=len(known) or {r[0] for r in host}!=known:
        raise ValueError('host rows differ from known before-snapshot PFNs')
    entries={r[0]:r[1:] for r in host};counts=Counter();physical={'ordinary':set(),'shmem':set()}
    capacities=Counter()
    for p,tag in last.items():
        kind='untyped' if not tag else 'shmem' if tag&1 else 'ordinary'
        capacities[kind]+=1
        if not tag:reason='stale_after'
        elif p in left and not left[p]:reason='stale_before'
        elif p not in left:reason='new_pfn'
        elif left[p]!=tag:reason='reused_pfn'
        else:
            a,b,c=entries[p]
            if ((a^b)|(b^c))&PHYSICAL_MASK:reason='host_mapping_changed'
            elif a&PRESENT:
                if not a&MASK:raise ValueError('host PFN hidden')
                reason='confirmed';physical[kind].add(a&MASK)
            elif a&SWAPPED:reason='swapped'
            else:reason='nonresident'
        counts[reason]+=1
    unknown_keys=('stale_after','stale_before','new_pfn','reused_pfn','host_mapping_changed')
    unknown=sum(counts[k] for k in unknown_keys)
    union=physical['ordinary']|physical['shmem']
    assert sum(counts.values())==info['records']
    assert counts['confirmed']+counts['nonresident']+counts['swapped']+unknown==info['records']
    return dict(guest_cache_after_bytes=info['records']*PAGE,
        guest_ordinary_capacity_bytes=capacities['ordinary']*PAGE,guest_shmem_capacity_bytes=capacities['shmem']*PAGE,
        guest_untyped_capacity_bytes=capacities['untyped']*PAGE,
        host_confirmed_cache_bytes=len(union)*PAGE,
        ordinary_confirmed_host_bytes=len(physical['ordinary'])*PAGE,
        shmem_confirmed_host_bytes=len(physical['shmem'])*PAGE,
        ordinary_shmem_host_overlap_bytes=len(physical['ordinary']&physical['shmem'])*PAGE,
        stable_resident_guest_pages=counts['confirmed'],host_nonresident_pages=counts['nonresident'],
        host_swapped_pages=counts['swapped'],unconfirmed_after_pages=unknown,
        unconfirmed_guest_page_capacity_bytes=unknown*PAGE,
        **{k+'_pages':counts[k] for k in unknown_keys},
        retired_before_pages=sum(bool(tag) and last.get(p)!=tag for p,tag in left.items()),
        metadata_counters={k:info[k] for k in ('duplicate_adds','missing_removes','out_of_range','moved_pages','stale_records')}),physical


def write_csv(path,rows):
    with path.open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def report(batch,output):
    batch=batch.resolve();output=output.resolve()
    if output.exists() or output.is_relative_to(batch) or batch.is_relative_to(output):
        raise ValueError('output must be a new directory separate from input')
    inventory={}
    def load(path,binary=False):
        st=path.stat();inventory[str(path)]=dict(size_bytes=st.st_size,mtime_ns=st.st_mtime_ns)
        raw=path.read_bytes();return raw if binary else json.loads(raw)
    outer=load(batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
    if output.is_relative_to(storage) or storage.is_relative_to(output):raise ValueError('output overlaps raw data')
    receipt=load(storage/'result.json');run=load(raw/'result.json');accepted=load(raw/'target-check.json')
    assert outer['status']==run['status']==accepted['status']=='PASS'
    points=[];stages=[];group_rows=[];group_stages=[];identity={};unavailable={}
    for role in ROLES:
        live=raw/'live-cache'/role;vm=run['guests'][role]['host_identity'];sid=run['guests'][role]['sandbox_id']
        marker=load(live/'marker.json');binding=load(live/'vmm-child-binding.json');collector=load(live/'result.json')
        assert collector['status']==binding['status']=='PASS'
        assert collector['sandbox_id']==marker['sandbox_id']==sid and collector['host_identity']==vm
        assert collector['guest_boot_id']==marker['boot_id'] and binding['actual_cgroup']==vm['cgroup']
        identity[role]=dict(sandbox_id=sid,guest_boot_id=marker['boot_id'],host_identity=vm,
            binding=binding,locked_anonymous_bytes=collector['locked_anonymous_bytes'])
        saved=[json.loads(l) for l in load(live/'samples.jsonl',True).splitlines()]
        dynamic=load(live/'dynamic-file-cache.json');files={r['point']:r for r in dynamic['observations']}
        assert dynamic['sandbox_id']==sid and dynamic['boot_id']==marker['boot_id']
        unavailable[role]=[dict(point=r['point'],reason=r['reason']) for r in files.values() if r['status']=='UNAVAILABLE']
        role_rows=[]
        for sample in saved:
            point=sample['point'];prefix=live/f'pair-{point:04}'
            rows=list(HOST.iter_unpack(load(Path(str(prefix)+'-host.bin'),True)))
            value,physical=partition(load(Path(str(prefix)+'-before.bin'),True),load(Path(str(prefix)+'-after.bin'),True),rows)
            for k in ('guest_cache_after_bytes','host_confirmed_cache_bytes','stable_resident_guest_pages',
                      'host_nonresident_pages','host_swapped_pages','unconfirmed_after_pages','unconfirmed_guest_page_capacity_bytes'):
                assert value[k]==sample[k],(role,point,k,value[k],sample[k])
            file=files[point]
            if file['status']!='UNAVAILABLE':
                target=set(file['confirmed_host_pfns'])
                assert target<=physical['ordinary'],(role,point,'named file not a subset of whole-guest ordinary confirmed pages')
                assert len(target)*PAGE==file['confirmed_host_union_bytes']
                remainder=len(physical['ordinary']-target)*PAGE
            else:remainder=None
            begin=sample['host_sample_start_ns'];end=begin+sample['host_elapsed_ns']
            phase=next((p['name'] for p in run['phases'] if p['start_monotonic_ns']<=begin and end<=p['end_monotonic_ns']),None)
            row=dict(role=role,point=point,phase=phase,sample_start_monotonic_ns=begin,sample_end_monotonic_ns=end,
                **value,file_status=file['status'],file_cache_bytes=file.get('current_file_cache_after_bytes'),
                file_confirmed_host_bytes=file.get('confirmed_host_union_bytes'),file_unconfirmed_pages=file.get('unconfirmed_after_pages'),
                other_ordinary_confirmed_host_bytes=remainder,
                sources=dict(sample=str(live/'samples.jsonl'),binary_prefix=str(prefix),named_file=str(live/'dynamic-file-cache.json')))
            points.append(row);role_rows.append(row)
        for phase in run['phases']:
            selected=[r for r in role_rows if r['phase']==phase['name']];assert selected
            last=selected[-1];em=phase['groups']['experiment_memory'];cg='/sys/fs/cgroup'+binding['actual_cgroup']
            vmm=phase['vmms'][role];child=em['direct_children'][cg]
            assert int(vmm['start_ticks'])==int(vm['start_time_ticks'])
            stages.append(dict(role=role,phase=phase['name'],complete_pairs=len(selected),last_complete_point=last['point'],
                last_guest_cache_bytes=last['guest_cache_after_bytes'],last_host_confirmed_bytes=last['host_confirmed_cache_bytes'],
                last_ordinary_confirmed_bytes=last['ordinary_confirmed_host_bytes'],last_shmem_confirmed_bytes=last['shmem_confirmed_host_bytes'],
                last_file_confirmed_bytes=last['file_confirmed_host_bytes'],last_other_ordinary_confirmed_bytes=last['other_ordinary_confirmed_host_bytes'],
                last_unconfirmed_pages=last['unconfirmed_after_pages'],
                host_confirmed_min_bytes=min(r['host_confirmed_cache_bytes'] for r in selected),
                host_confirmed_max_bytes=max(r['host_confirmed_cache_bytes'] for r in selected),
                unconfirmed_min_pages=min(r['unconfirmed_after_pages'] for r in selected),
                unconfirmed_max_pages=max(r['unconfirmed_after_pages'] for r in selected),
                pss_bytes=vmm['pss_kib']*1024,pss_anon_bytes=vmm['pss_anon_kib']*1024,
                pss_file_bytes=vmm['pss_file_kib']*1024,pss_shmem_bytes=vmm['pss_shmem_kib']*1024,
                vmm_rss_bytes=vmm['rss_kib']*1024,child_cgroup=cg,child_counters=child,
                cache_to_group_read_start_seconds=(phase['groups']['monotonic_ns']-last['sample_end_monotonic_ns'])/1e9,
                pss_read_bracket_monotonic_ns=[phase['end_monotonic_ns'],phase['groups']['monotonic_ns']],
                source=str(raw/'result.json')+'#phases/'+phase['name']))
    assert len({v['sandbox_id'] for v in identity.values()})==len(ROLES)
    for phase in run['phases']:
        group=phase['groups'];em=group['experiment_memory'];assert em['status']=='OBSERVED' and em['parent']==outer['parent']
        vmmpaths={'/sys/fs/cgroup'+identity[r]['binding']['actual_cgroup']:r for r in ROLES}
        classified={};items=[('parent',em['parent'],em['parent_counters'])]
        for path,c in em['direct_children'].items():
            label=vmmpaths.get(path,'controller' if path.endswith('control.service') else 'private_service' if Path(path).name.startswith('docker-') else 'other_child')
            classified[path]=label;items.append((label,path,c))
        for label,path,c in items:
            full=group['groups'][path]
            group_rows.append(dict(phase=phase['name'],owner=label,cgroup=path,**{k+'_bytes':c[k] for k in KEYS},
                shmem_bytes=full['shmem'],file_minus_shmem_bytes=full['file']-full['shmem'],
                file_dirty_bytes=full['file_dirty'],file_writeback_bytes=full['file_writeback'],
                source=str(raw/'result.json')+'#phases/'+phase['name']+'/groups'))
        children=em['direct_children'];vmm_sum={k:sum(c[k] for p,c in children.items() if p in vmmpaths) for k in KEYS}
        other_sum={k:sum(c[k] for p,c in children.items() if p not in vmmpaths) for k in KEYS}
        residual={k:em['parent_counters'][k]-vmm_sum[k]-other_sum[k] for k in KEYS}
        assert residual['file']==em['file_closure']['parent_minus_children_bytes']
        group_stages.append(dict(phase=phase['name'],vmm_children_sum=vmm_sum,non_vmm_children_sum=other_sum,
            parent_minus_children=residual,child_labels=classified))
    result=dict(status='PASS_OFFLINE_BREAKDOWN',batch=str(batch),storage=str(storage),raw=str(raw),
        saved_acceptance_status=accepted['status'],business_runs_started=0,page_size_bytes=PAGE,bytes_per_mib=MIB,
        identities=identity,points=points,stages=stages,cgroups=group_rows,cgroup_stage_sums=group_stages,
        file_unavailable=unavailable,pre_guest=receipt['vmm_child_cgroups'],
        source_service_cgroup=receipt['original_service_cgroup'].strip(),lower_file_references=receipt['child_cache_offline']['local_files'],
        uncovered=dict(host_lower_files='NOT_COLLECTED: 19 paths saved, no per-file identity/residency in this run',
            host_private_upper_and_loop='NOT_COLLECTED: logs/loop identity do not measure object residency or per-guest charges',
            guest_memory_snapshot='NOT_APPLICABLE: fresh private guests, no save/restore',
            provider_service_pss='NOT_COLLECTED: service group charged totals only, not per-process PSS',
            unregistered_guest_cache='OUT_OF_SCOPE: block-device-specific and swap-only caches; no size evidence',
            shared_cost_allocation='UNAVAILABLE: no basis for an exclusive per-sandbox allocation',
            whole_vmm_ram_physical_union='NOT_SAVED: full RAM pagemap vectors were read live, only cache-selected rows persisted'),
        sources=inventory)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    write_csv(output/'points.csv',[{k:(json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v) for k,v in r.items()} for r in points])
    write_csv(output/'stages.csv',[{k:(json.dumps(v) if isinstance(v,dict) else v) for k,v in r.items()} for r in stages])
    write_csv(output/'cgroups.csv',group_rows)
    return result


def markdown(v):
    lines=['# 双沙箱host内存与缓存：离线分解','',
        '**指定文件已分别对应；本次进一步分出了全guest已确认缓存、未知原因，以及VMM之外的记账。完整逐沙箱host总量仍缺后备对象与共享成本归属。**','',
        '只读上次双沙箱数据，不启动业务、不重新采集、不改历史判定。1 MiB = 1,048,576字节，页大小4096字节。所有表保留六位小数，计算使用原始整数；不跨阶段或沙箱求平均。','',
        '## 1. 每沙箱的guest缓存对应host驻留','',
        '下表取每阶段最后一对完整观察，不挑最大值。普通文件与shmem（共享内存/tmpfs）由guest生命周期标签区分；“其他普通文件”通过同一采样对的host物理页集合排除指定16 MiB文件，不用PSS相减。','',
        '| 阶段 | 沙箱 | point | guest缓存容量 MiB | host确认并集 MiB | 普通文件对应host MiB | shmem对应host MiB | 其中指定文件host MiB | 其他普通文件host MiB | 待确认guest页 |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in v['stages']:
        f=lambda k:'UNAVAILABLE' if r[k] is None else f'{r[k]/MIB:.6f}'
        lines.append(f"| {r['phase']} | {r['role']} | {r['last_complete_point']} | "+' | '.join(f(k) for k in ('last_guest_cache_bytes','last_host_confirmed_bytes','last_ordinary_confirmed_bytes','last_shmem_confirmed_bytes','last_file_confirmed_bytes','last_other_ordinary_confirmed_bytes'))+f" | {r['last_unconfirmed_pages']} |")
    lines+=['','guest缓存容量是guest页集合的容量；host确认并集才是这些稳定缓存页对应的host实驻物理页下界。前后guest身份/生命周期及三次host页表一致才确认。普通文件与shmem若映到同一host页，合计按并集，交集另存；不直接相加两个小计。指定文件列是普通文件列的子集，不额外加到并集中。',
        '其他普通文件未按文件名细分，包含guest系统、工具和工作负载；shmem包含采集器附加文件，不能全部算作任务自身缓存。跨沙箱查询不同步，不把两份页集合相加或并成同一时刻的全局唯一物理量。','',
        '## 2. 未确认页：新进入，还是host映射变化','',
        '| 沙箱 | 全部点数 | 待确认页范围 | 对应guest容量范围 MiB | 最大点及原因 | host非驻留/换出页最大数 |','| --- | ---: | ---: | ---: | --- | ---: |']
    unknown=('stale_after','stale_before','new_pfn','reused_pfn','host_mapping_changed')
    for role in ROLES:
        rows=[r for r in v['points'] if r['role']==role];worst=max(rows,key=lambda r:r['unconfirmed_after_pages'])
        lo=min(r['unconfirmed_after_pages'] for r in rows);hi=worst['unconfirmed_after_pages']
        reasons={k:worst[k+'_pages'] for k in unknown}
        lines.append(f"| {role} | {len(rows)} | {lo}—{hi} | {lo*PAGE/MIB:.6f}—{hi*PAGE/MIB:.6f} | point {worst['point']}，`{json.dumps(reasons)}` | {max(r['host_nonresident_pages'] for r in rows)}/{max(r['host_swapped_pages'] for r in rows)} |")
    lines+=['','分类互斥：后快照未知标签→前快照未知标签→前快照无此PFN→同PFN生命周期已换→host三次映射变化。旧生命周期退出单列，不再算作后快照未知页。每点对照原samples.jsonl的容量、物理并集及未知数量，全部一致；36点明细见points.csv。',
        '这些待确认容量不是已测得的host物理缺口，不能补进驻留量；后快照新增的页要等后续稳定采样才能确认。point编号仅在各自沙箱内有效，不用两边相同编号表示同时刻。','',
        '## 3. VMM进程内存与VMM子组记账','',
        '| 阶段 | 沙箱 | PSS MiB | PSS匿名 MiB | PSS文件 MiB | PSS shmem MiB | 子current MiB | 子file MiB | 子anon MiB | 子kernel MiB |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in v['stages']:
        values=[r[k]/MIB for k in ('pss_bytes','pss_anon_bytes','pss_file_bytes','pss_shmem_bytes')]+[r['child_counters'][k]/MIB for k in KEYS]
        lines.append('| '+r['phase']+' | '+r['role']+' | '+' | '.join(f'{x:.6f}' for x in values)+' |')
    lines+=['','PSS从各自VMM的smaps_rollup取得，按映射次数分摊共享页；范围是整个VMM映射，不只是guest缓存，也不覆盖服务和全部内核开销。子组current/file/anon/kernel是记在该子组的内核统计，不是按用途分类的逐沙箱完整物理总量。',
        '**guest文件缓存可处在host的匿名guest RAM中。host子组file不是guest page cache，PSS_File也不是guest page cache。** 上面的guest缓存物理并集与VMM内存有重叠，不能相加；也不能用PSS减缓存下界，声称剩余就是完整非缓存内存。',
        'PSS分项由内核分别取整，保留原值；不强求小计与PSS逐字节相等。子组current也不强求等于file+anon+kernel。','',
        '## 4. 大块host file记在哪里','',
        '| 阶段 | 组 | current MiB | file MiB | anon MiB | kernel MiB | file内shmem MiB | file减shmem MiB |','| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in v['cgroups']:
        if r['owner'] in ROLES:continue
        values=[r[k+'_bytes']/MIB for k in KEYS]+[r[k]/MIB for k in ('shmem_bytes','file_minus_shmem_bytes')]
        lines.append('| '+r['phase']+' | '+r['owner']+' | '+' | '.join(f'{x:.6f}' for x in values)+' |')
    pre=v['pre_guest']['parent_counters']
    lines+=['',f"创建VMM前，父组已有current {pre['current']/MIB:.6f} MiB、file {pre['file']/MIB:.6f} MiB，其中shmem {pre['shmem']/MIB:.6f} MiB。其时间戳是monotonic_ns={v['pre_guest']['before_guest_creation_monotonic_ns']}。这部分已存在于合成文件操作前，不能全归因为两个16 MiB文件，也不能除以2分配给沙箱。",'',
        'controller是准备、控制和采集所在service的记账；private_service是私有AgentENV/ublk服务Docker组。shmem已在file中，file减shmem只是同组非shmem文件记账，不是某个镜像层的实测驻留。file_dirty/file_writeback是file的状态子项，原值保存在cgroups.csv，不再相加。','',
        '| 阶段 | ΣVMM子file MiB | 非VMM子file MiB | 父减全部子file MiB | 父减全部子current MiB |','| --- | ---: | ---: | ---: | ---: |']
    for r in v['cgroup_stage_sums']:
        values=(r['vmm_children_sum']['file'],r['non_vmm_children_sum']['file'],r['parent_minus_children']['file'],r['parent_minus_children']['current'])
        lines.append('| '+r['phase']+' | '+' | '.join(f'{x/MIB:.6f}' for x in values)+' |')
    lines+=['','这是直接子组记账的分解，父包含子，不将父与子再相加。各阶段file闭合只是记账闭合，不证明文件对象覆盖完整；current的原始差额保留，不解释成遗漏物理页。',
        f"源服务原先在`{v['source_service_cgroup']}`，不在本次slice内；本批没有它的周期内存或逐文件归属，不能填零，也不能把同机其他组历史数值移入本批。",'',
        '## 5. host后备层和共享成本：哪些还缺','',
        '| 项目 | 本批证据 | 能否归属到单个沙箱 |','| --- | --- | --- |',
        f"| 镜像下层文件 | 保存{len(v['lower_file_references'])}条本地路径，未保存本批对应设备号/inode及mincore驻留 | 未测到逐文件驻留，不能用历史5308 MiB或长度补入 |",
        '| 每guest可写层、rootfs设备、loop后备卷 | 服务日志有设备创建，卷有loop身份；无完整对象驻留/页归属查询 | 设备引用不等于物理使用，当前无法补足 |',
        '| 恢复内存快照 | 本次独立新建，无保存或恢复 | 本负载不适用；不能借此验证恢复路径 |',
        '| 服务/控制程序内存 | 直接子组四分项与shmem已保存 | 能定位记账组，缺逐对象与逐沙箱归属；不均分 |',
        '| 全部VMM RAM物理并集 | 采样时读全RAM页表，但只持久化缓存页对应行 | PSS可用；完整RAM逐页物理并集无法离线恢复 |',
        '| guest缓存覆盖之外 | 普通文件/shmem表不包括块设备专用与swap-only缓存 | 未保存其数量，不能宣称完整guest缓存 |',
        '| 初始覆盖与采集成本 | 首点在启动/上传后；每guest锁定16.007812 MiB匿名导出缓冲区，附加文件在guest tmpfs | 不能把首点叫启动瞬间；没有采集开关对照，附加文件长度不当物理量 |','',
        '现有日志不能把controller中大块file精确映射到某个卷、某个19层文件或某个沙箱；也不能由“谁记账”推断“谁独占使用”。不把非VMM总量均分、不给历史倍率换算。','',
        '## 6. 时间、来源与本阶段结论','',
        '页级结果只取完整包含在阶段内的采样对；PSS随后在阶段末读取，cgroup再顺序读取，并非同时快照。下面列出最后完整缓存采样结束到cgroup查询开始的间隔；PSS读数只可定位在阶段结束至cgroup开始的括号内，没有逐VMM绝对查询时间戳。','',
        '| 阶段 | 沙箱 | 缓存观察结束至cgroup开始 秒 | PSS查询括号宽度 毫秒 |','| --- | --- | ---: | ---: |']
    for r in v['stages']:
        a,b=r['pss_read_bracket_monotonic_ns']
        lines.append(f"| {r['phase']} | {r['role']} | {r['cache_to_group_read_start_seconds']:.9f} | {(b-a)/1e6:.6f} |")
    lines+=['',f"- 外层来源：`{v['batch']}`；原始：`{v['raw']}`。",'- `summary.json:sources`记录全部输入路径、文件长度与mtime；各点保存二进制前缀，各阶段保存原字段路径。没有读取目标文件内容，没有新增/proc、cgroup或服务查询。',
        '- 新输出：points.csv（36点原始字节及未知原因）、stages.csv（10个沙箱×阶段读数）、cgroups.csv（父与直接子组原值）。既有结果、判定和原始文件不改。','',
        '**已完成：每个VMM的PSS和独立子组记账、指定文件的动态host物理对应、普通文件/shmem对应物理量与未知原因分解，以及大块file记账所在组。**',
        '**仍缺：逐沙箱完整host总内存、完整host后备文件缓存，以及共享成本的物理去重与分摊。本批不能用PSS、cgroup或指定文件下界中的任一个冒充这些完整总量。**','',
        '## 下一步建议','',
        '先离线接入最小补采：在现有显式双沙箱入口保存每个VMM已读取的全RAM页表结果（区别RAM与整个进程），并把已有19层、每guest可写层/设备、loop对象清单与有效驻留查询接到同一运行目录，公共对象单列、不均分。完成条件是能区分未映射后备页、已映射页和共享引用，缺项明确UNAVAILABLE；之后最多一次双沙箱验证，另经确认，不先跑七实例完整业务。','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();v=report(a.batch,a.output)
    (a.output/'REPORT.md').write_text(markdown(v))
    print('status='+v['status']+' result_dir='+str(a.output))


if __name__=='__main__':main()
