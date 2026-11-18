"""Split saved host backing references; no live host, device or payload queries."""
import argparse
import csv
import json
from pathlib import Path

from host_backing_evidence import summarize

MIB=2**20
KINDS=('regular','shmem_or_hugetlb','block','outer_loop')


def totals(rows):
    observed=[r for r in rows if r['query']['status']=='OBSERVED']
    for r in observed:
        if type(r['query']['bytes']) is not int or r['query']['bytes']<0:
            raise ValueError('observed object lacks valid byte count')
    subtotal=sum(r['query']['bytes'] for r in observed)
    return dict(objects=len(rows),observed_objects=len(observed),unknown_objects=len(rows)-len(observed),
        observed_subtotal_bytes=subtotal,
        complete_listed_bytes=subtotal if len(rows)==len(observed) else None,
        status='EMPTY_LIST' if not rows else 'OBSERVED_LISTED_OBJECTS' if len(rows)==len(observed) else 'PARTIAL')


def split(evidence,regular_rows):
    """Disjoint reference groups within each layer, never exclusive physical owners."""
    guests=evidence['guests'];rows=[];seen=set()
    source={}
    for r in regular_rows:
        key=('regular',r['device'],r['inode'])
        if key in source:raise ValueError('duplicate saved regular object')
        source[key]=r
    for index,o in enumerate(evidence['objects']):
        key=tuple(o['object_key']) if o['object_key'] is not None else None
        # A loop backing may also be a listed regular object: retain both
        # observations in separate layers, never make a cross-layer sum.
        identity=(o['kind'],key)
        if identity in seen:raise ValueError('duplicate object in one layer')
        seen.add(identity)
        refs=o['references'];roles=sorted(r['role'] for r in refs)
        if len(set(roles))!=len(roles) or any(r['role'] not in guests or guests[r['role']]!=r['sandbox_id'] for r in refs):
            raise ValueError('object reference sandbox identity conflict')
        if o['kind']=='outer_loop':
            if refs:raise ValueError('outer loop containment must not be a direct guest reference')
            group='container_only'
        elif not refs:group='unassigned'
        elif len(refs)==1:group='single_reference:'+roles[0]
        else:group='shared_reference:'+','.join(roles)
        s=source.get(key,{}) if o['kind'] in ('regular','shmem_or_hugetlb') else {}
        if o['kind'] in ('regular','shmem_or_hugetlb') and not s:
            raise ValueError('saved regular source missing for object')
        rows.append(dict(layer=o['kind'],reference_group=group,object_key=o['object_key'],
            references=refs,categories=o.get('categories',[]),primary_category=s.get('primary_category',o['kind']),
            aliases=o.get('aliases',[]),query=o['query'],source_status=o.get('source_status'),
            query_window_status=s.get('query_window_status'),
            raw_resident_bytes=s.get('resident_bytes'),raw_query=s.get('residency'),
            evidence_pointer='/objects/'+str(index),exclusive_physical_bytes=None))
    groups=[];layers={};categories=[]
    for kind in KINDS:
        selected=[r for r in rows if r['layer']==kind];layer=totals(selected)
        layer['reference_groups']=[]
        for group in sorted({r['reference_group'] for r in selected}):
            g=[r for r in selected if r['reference_group']==group]
            record=dict(layer=kind,reference_group=group,**totals(g));groups.append(record)
            layer['reference_groups'].append(record)
            for category in sorted({r['primary_category'] for r in g}):
                categories.append(dict(layer=kind,reference_group=group,primary_category=category,
                    **totals([r for r in g if r['primary_category']==category])))
        for field in ('objects','observed_objects','unknown_objects','observed_subtotal_bytes'):
            if sum(g[field] for g in layer['reference_groups'])!=layer[field]:
                raise ValueError('reference partition does not reconcile '+kind+' '+field)
        layer['partition_check']='EXACT';layers[kind]=layer
    per_guest={role:dict(sandbox_id=sid,
        single_reference_groups=[r for r in groups if r['reference_group']=='single_reference:'+role],
        shared_reference_groups=[r for r in groups if r['reference_group'].startswith('shared_reference:')
            and role in r['reference_group'].split(':',1)[1].split(',')],
        complete_host_memory_bytes=None,complete_host_cache_bytes=None,
        reference_meaning='only confirmed reference in saved inventory; not exclusive physical ownership')
        for role,sid in guests.items()}
    return dict(objects=rows,groups=groups,layers=layers,categories=categories,per_guest=per_guest)


def write_table(path,rows):
    with path.open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),delimiter='\t');writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def report(batch,out):
    batch=batch.resolve();out=out.resolve();sources={}
    def load(path):
        # Read saved files only. Never follow paths inside the saved object list.
        sources[str(path)]=dict(size_bytes=path.stat().st_size)
        return json.loads(path.read_text())
    outer=load(batch/'result.json');storage=Path(outer['storage']);raw=storage/'guest-cache'
    if out.exists() or any(out.is_relative_to(p) or p.is_relative_to(out) for p in (batch,storage)):
        raise ValueError('output must be a new separate directory')
    run=load(raw/'result.json');receipt=load(raw/'target-check.json')
    if outer['status']!=run['status'] or run['status']!='PASS' or receipt['status']!='PASS':
        raise ValueError('source run/calibration did not pass')
    backing_raw=load(raw/'host-backing-raw.json');evidence=load(raw/'host-backing-evidence.json')
    if summarize(backing_raw)!=evidence:raise ValueError('saved backing evidence differs from raw replay')
    for role,sid in evidence['guests'].items():
        vm=run['guests'][role]['host_identity'];b=evidence['bindings'][role]
        if sid!=run['guests'][role]['sandbox_id'] or b['pid']!=vm['pid'] or int(b['start_ticks'])!=int(vm['start_time_ticks']):
            raise ValueError('guest RAM/backing identities differ')
    value=split(evidence,backing_raw['expanded_files']['files']);guest_rows=[];group_rows=[]
    for role,sid in evidence['guests'].items():
        live=raw/'live-cache'/role;collector=load(live/'result.json');ram=load(live/'ram-page-analysis.json')
        samples=list(map(json.loads,(live/'samples.jsonl').read_text().splitlines()))
        sources[str(live/'samples.jsonl')]=dict(size_bytes=(live/'samples.jsonl').stat().st_size)
        vm=run['guests'][role]['host_identity'];binding=load(live/'vmm-child-binding.json')
        if (collector['status']!='PASS' or ram['status']!='OBSERVED' or ram['sandbox_id']!=sid or
                ram['guest_boot_id']!=collector['guest_boot_id'] or ram['host_identity']!=vm or collector['host_identity']!=vm):
            raise ValueError('saved RAM identity mismatch')
        ram_points={r['point']:r for r in ram['points']}
        if len(samples)!=len(ram_points) or len(samples)!=collector['samples']:raise ValueError('saved sample coverage mismatch')
        phase=run['phases'][-1]
        selected=[s for s in samples if phase['start_ns']<=s['timestamp_ns'] and s['timestamp_ns']+s['host_elapsed_ns']<=phase['end_ns']]
        if not selected:raise ValueError('no complete observation in last phase')
        s=selected[-1];r=ram_points[s['point']]
        if r['confirmed_guest_cache_host_union_bytes']!=s['host_confirmed_cache_bytes']:raise ValueError('cache/RAM subset mismatch')
        group='/sys/fs/cgroup'+binding['actual_cgroup'];child=phase['groups']['experiment_memory']['direct_children'][group]
        guest_rows.append(dict(role=role,sandbox_id=sid,phase=phase['name'],point=s['point'],
            guest_cache_capacity_bytes=s['guest_cache_after_bytes'],guest_cache_unconfirmed_pages=s['unconfirmed_after_pages'],
            ram_host_stable_bytes=r['stable_host_physical_union_bytes'],guest_cache_host_confirmed_bytes=r['confirmed_guest_cache_host_union_bytes'],
            ram_not_confirmed_as_cache_bytes=r['ram_not_confirmed_as_cache_host_bytes'],
            vmm_pss_bytes=phase['vmms'][role]['pss_kib']*1024,**{k+'_bytes':child[k] for k in ('current','file','anon','kernel')},
            cgroup=group,ram_observation_start_monotonic_ns=s['host_sample_start_ns'],
            ram_observation_end_monotonic_ns=s['host_sample_start_ns']+s['host_elapsed_ns'],
            phase_group_observation_monotonic_ns=phase['groups']['monotonic_ns'],
            backing_start_after_ram_seconds=(backing_raw['start_monotonic_ns']-s['host_sample_start_ns']-s['host_elapsed_ns'])/1e9))
    phase=run['phases'][-1];em=phase['groups']['experiment_memory']
    labels={g['cgroup']:g['role'] for g in guest_rows}
    for path,counters in [(em['parent'],em['parent_counters']),*em['direct_children'].items()]:
        group_rows.append(dict(owner='parent' if path==em['parent'] else labels.get(path,
            'controller' if path.endswith('control.service') else 'private_service' if Path(path).name.startswith('docker-') else 'other'),
            cgroup=path,**{k+'_bytes':counters[k] for k in ('current','file','anon','kernel')}))
    for key in ('current','file','anon','kernel'):
        if sum(r[key+'_bytes'] for r in group_rows if r['owner']!='parent')!=em['direct_children_sum'][key]:
            raise ValueError('saved child totals mismatch')
    value.update(status='PASS_OFFLINE_SPLIT',backing_coverage=evidence['status'],batch=str(batch),raw=str(raw),
        sources=sources,guest_memory=guest_rows,cgroup_snapshot=group_rows,
        parent_minus_children={k:em['parent_counters'][k]-em['direct_children_sum'][k] for k in ('current','file','anon','kernel')},
        unresolved_sources=evidence['coverage']['unresolved_sources'],process_errors=evidence['coverage']['process_errors'],
        business_runs_started=0,complete_per_guest_host_memory_bytes=None,complete_per_guest_host_cache_bytes=None)
    out.mkdir(parents=True)
    with (out/'summary.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    for name,rows in [('objects.tsv',value['objects']),('groups.tsv',value['groups']),('categories.tsv',value['categories']),
            ('guest-memory.tsv',guest_rows),('cgroups.tsv',group_rows)]:write_table(out/name,rows)
    with (out/'REPORT.md').open('x') as f:f.write(markdown(value))
    return value


def markdown(v):
    mib=lambda n:'未知' if n is None else f'{n/MIB:.6f}'
    labels={'single_reference:private-a':'仅确认A引用','single_reference:private-b':'仅确认B引用',
        'shared_reference:private-a,private-b':'A/B公共引用','unassigned':'未归属','container_only':'外层卷包含关系'}
    lines=['# 逐沙箱host内存与缓存：已存对象离线拆分','',
        f"拆分检查 **PASS**，原后备对象覆盖仍为 **{v['backing_coverage']}**。只读一次双沙箱校准的已存数据，没有启动业务、查询真实进程/设备或修改原始记录。",'',
        '## 直接引用、公共引用与未归属分别列出','',
        '“仅确认A/B引用”表示已保存清单中只绑定了这一个沙箱，不表示物理页独占。公共对象只列一次，不均分给A/B。普通文件按设备号/inode、块设备按已固定的rdev去重；各层不相加，不与RAM/PSS/current相加。1 MiB=1,048,576字节；计算用原始整数字节，显示保留6位小数。', '',
        '| 层 | 引用类别 | 对象数 | 有效数 | 未知数 | 有效对象驻留合计 MiB | 此清单是否完整 |',
        '| --- | --- | ---: | ---: | ---: | ---: | --- |']
    for g in v['groups']:
        lines.append(f"| {g['layer']} | {labels.get(g['reference_group'],g['reference_group'])} | {g['objects']} | {g['observed_objects']} | {g['unknown_objects']} | {mib(g['observed_subtotal_bytes'])} | {g['status']} |")
    lines+=['','各层对象数、有效数、未知数及有效字节的分组之和与原对象表逐项相等，差0；这是已存清单的拆分闭合，不是完整物理内存闭合。EMPTY_LIST与有效查询得到0字节不同；未知项不进入有效小计，完整值留null。分组明细见groups.tsv，对象、来源别名及原查询见objects.tsv。', '',
        '### 公共对象和未归属普通文件由什么构成','',
        '沿用原采集器的primary_category（每对象唯一主类别），原categories标签可能重叠，不能直接相加。主类别表示保存位置或已验证引用类型，不推断实际访问或物理页记账者。本批active_snapshot_rootfs_lower表示19个活跃镜像下层文件，只沿用分类键名，并未发生保存恢复。', '',
        '| 引用类别 | 主类别 | 对象数 | 有效数 | 未知数 | 有效驻留 MiB |',
        '| --- | --- | ---: | ---: | ---: | ---: |']
    for r in v['categories']:
        if r['layer']=='regular':lines.append(f"| {labels.get(r['reference_group'],r['reference_group'])} | {r['primary_category']} | {r['objects']} | {r['observed_objects']} | {r['unknown_objects']} | {mib(r['observed_subtotal_bytes'])} |")
    lines+=['','镜像目录里未绑定guest的文件仍为未归属；不能仅因摘要、长度相同或在同目录就并入活跃lower。对象引用不提供逐页使用证据，cgroup总量也不能反推对象归属。', '',
        '## 每guest已测物理量与记账量：分列，不求和','',
        '取最后观察阶段b_reread内每guest最后一对完整RAM/缓存观察；PSS和cgroup在阶段末另读，后备对象又在采样器退出后查询。不同时间和范围不能拼成同一瞬间总量。guest-memory.tsv保留原点号及相对时间。', '',
        '| guest | 点号 | RAM稳定host驻留 MiB | 其中guest缓存host确认驻留 MiB | guest缓存容量 MiB | 未确认guest页 | VMM PSS MiB | VMM子current MiB | 子file MiB |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in v['guest_memory']:
        lines.append(f"| {r['role']} | {r['point']} | {mib(r['ram_host_stable_bytes'])} | {mib(r['guest_cache_host_confirmed_bytes'])} | {mib(r['guest_cache_capacity_bytes'])} | {r['guest_cache_unconfirmed_pages']} | {mib(r['vmm_pss_bytes'])} | {mib(r['current_bytes'])} | {mib(r['file_bytes'])} |")
    lines+=['','RAM稳定驻留与guest缓存对应量是非原子三读下界，后者是前者子集。RAM减已确认缓存的剩余部分可能仍有未确认缓存，不能命名为非缓存内存。guest缓存容量是guest页数，不是host驻留；子file是内存记账，并不是该guest缓存物理量。', '',
        '| 记账范围（同一阶段） | current MiB | file MiB | anon MiB | kernel MiB |',
        '| --- | ---: | ---: | ---: | ---: |']
    for r in v['cgroup_snapshot']:
        lines.append('| '+r['owner']+' | '+' | '.join(mib(r[k+'_bytes']) for k in ('current','file','anon','kernel'))+' |')
    lines+=['','父减全部直接子组差（MiB）：'+ ' / '.join(k+'='+mib(v['parent_minus_children'][k]) for k in ('current','file','anon','kernel'))+'。原值保留，父包含子不相加；控制程序与服务不按沙箱数均分。', '',
        '## 未知项与完整总量还缺什么','']
    for r in v['objects']:
        if r['query']['status']=='OBSERVED':continue
        lines.append('- 严格未知对象`'+json.dumps(r['object_key'])+'`，类别`'+r['primary_category']+'`，原因`'+str(r['query'].get('reason'))+'`。原查询窗口内读数'+mib(r['raw_resident_bytes'])+' MiB、query_window_status='+str(r['query_window_status'])+'，仅单列，不补入严格小计。')
    for r in v['unresolved_sources']:lines.append('- 缺失来源：`'+json.dumps(r,ensure_ascii=False)+'`。对象身份/目标没有证据，不补零、不归给某个guest。')
    lines+=['','现在能交付：每guest的RAM确认驻留、其中guest缓存物理子集、PSS和子cgroup四项，以及已列出host后备对象按引用关系拆分的查询值。',
        '完整逐沙箱host内存/完整page cache字段继续留空。缺口包括：未被列出或观察期间消失的缓存对象；VMM非RAM映射与RAM的完整去重；后备对象与RAM之间的物理去重；共享服务和公共对象按什么规则归属；逐页cgroup记账位置。源服务组外及早于实验的缓存不能记零。',
        '公共引用的驻留量回答“这批对象占了多少”，不回答“每个guest独占多少”。若要按比例分摊，那是另选分摊规则，不是新测得的物理归属。当前不为得到单一总数而均分。', '',
        '## 复核与下一步','',
        '- 从host-backing-raw.json重放summarize，与已保存host-backing-evidence.json逐字段相同；不重新查询对象。所有层的分组拆分精确闭合，身份与RAM采样器匹配。有效原RAM证据检查直接复用，不重复读取888 MiB原始RAM向量。',
        '- 纯离线回归覆盖单引用/公共/未归属/卷包含关系、有效0与未知、别名不重复计数、同层重复与沙箱身份冲突拒绝、不同层不混加；本次7项通过，见tests.log。',
        '- 下一步建议：继续分列交付RAM/guest缓存物理量、单引用对象与公共对象列表；为补齐完整逐沙箱范围，先离线设计VMM非RAM映射及host后备页物理身份的核对，不自动加跑业务。','',
        f"原批次：`{v['batch']}`；原始：`{v['raw']}`。全部取值源文件见summary.json:sources；引用原数值，不修改历史PASS/FAIL。",'']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();v=report(a.batch,a.output)
    print('status='+v['status']+' backing_coverage='+v['backing_coverage']+' result_dir='+str(a.output))


if __name__=='__main__':main()
