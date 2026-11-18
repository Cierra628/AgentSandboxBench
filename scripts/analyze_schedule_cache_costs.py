"""Analyze saved cache-layer timings only; no process/device or cache queries."""
import argparse
import json
from pathlib import Path

MIB=2**20
CHUNK=256*MIB  # Reviewed saved host_file_monitor.py:119.


def duration(row):return row['end_monotonic_ns']-row['start_monotonic_ns']


def query_observed(row):
    value=row.get('residency',{})
    return (row['status'] in ('OBSERVED','CHANGED_DURING_QUERY') and value.get('stable') is True
            and (value.get('device'),value.get('inode'))==(row['device'],row['inode'])
            and row['resident_bytes']==value.get('resident_bytes'))


def cost_group(rows):
    return dict(objects=len(rows),aliases=sum(len(r['aliases']) for r in rows),
        capacity_bytes=sum(r['mapping_length'] for r in rows),
        pages=sum(r['residency']['pages'] for r in rows),
        resident_bytes=sum(r['resident_bytes'] for r in rows),
        chunks=sum((r['mapping_length']+CHUNK-1)//CHUNK for r in rows),
        object_window_ns=sum(duration(r) for r in rows),cpu_ns=sum(r['cpu_ns'] for r in rows))


def collect(source):
    v=json.loads(source.read_text());d=v['cache_layers']['block_devices'];e=v['expanded_files']
    rows=d['objects'];assert d['status']=='OBSERVED' and len(rows)==10
    assert len({r['rdev'] for r in rows})==10
    groups={'drive':[],'ram':[],'zero_resident_drive':[]}
    device_rows=[]
    for r in rows:
        ram=any(a['source']=='vmm_ram_mapping' for a in r['aliases'])
        groups['ram' if ram else 'drive'].append(r)
        if not ram and r['resident_bytes']==0:groups['zero_resident_drive'].append(r)
        assert r['residency']['stable'] and r['residency']['pages']*e['page_size']==r['mapping_length']
        device_rows.append(dict(rdev=r['rdev'],kind='RAM' if ram else 'drive',aliases=len(r['aliases']),
            capacity_bytes=r['mapping_length'],resident_bytes=r['resident_bytes'],pages=r['residency']['pages'],
            object_window_ns=duration(r),cpu_ns=r['cpu_ns']))
    costs={key:cost_group(entries) for key,entries in groups.items()};costs['all']=cost_group(rows)
    wall=duration(d);row_wall=costs['all']['object_window_ns'];assert row_wall<=wall
    stages=dict(block_device_wall_ns=wall,block_device_cpu_ns=d['cpu_ns'],
        object_windows_ns=row_wall,outside_object_windows_ns=wall-row_wall,
        outside_object_windows_cpu_ns=d['cpu_ns']-costs['all']['cpu_ns'],
        alias_setup_before_first_object_ns=rows[0]['start_monotonic_ns']-d['start_monotonic_ns'],
        extra_layers_wall_ns=duration(v['cache_layers']),endpoint_wall_ns=duration(v),
        block_start_after_paths_end_ns=d['start_monotonic_ns']-v['original_paths_end_monotonic_ns'],
        zero_drive_share_percent=100*costs['zero_resident_drive']['object_window_ns']/wall,
        block_share_extra_layers_percent=100*wall/duration(v['cache_layers']))
    changes=[]
    for row in e['files']:
        if row['status']!='CHANGED_DURING_QUERY':continue
        q=row['residency'];changes.append(dict(object_key=row['object_key'],users=row['users'],
            category=row['primary_category'],path=row['queried_alias']['path'],
            inventory_size=row['size'],query_size=q['size'],growth_bytes=q['size']-row['size'],
            inventory_mtime_ns=row['mtime_ns'],query_mtime_ns=q['mtime_ns'],
            query_stable=q['stable'],identity_stable=q.get('identity_stable'),size_stable=q.get('size_stable'),
            phase=row['change_phase'],query_observed=query_observed(row),resident_bytes=row['resident_bytes'],
            query_offset_from_inventory_start_ns=row['start_monotonic_ns']-e['start_monotonic_ns'],
            query_wall_ns=duration(row)))
    assert len(changes)==4 and all(c['query_observed'] for c in changes)
    assert all(c['phase']=='BETWEEN_INVENTORY_AND_QUERY' for c in changes)
    observed=[r for r in e['files'] if query_observed(r)]
    stable=sum(r['resident_bytes'] for r in e['files'] if r['status']=='OBSERVED')
    changed=sum(c['resident_bytes'] for c in changes)
    assert stable==e['stable_regular_resident_bytes']
    assert sum(r['resident_bytes'] for r in observed)==stable+changed
    return dict(source=str(source),source_code=str(source.parent/'code'),page_size=e['page_size'],chunk_bytes=CHUNK,
        stages=stages,cost_groups=costs,devices=device_rows,changed_files=changes,
        original_regular_coverage_status=e['status'],original_stable_regular_bytes=stable,
        changed_query_observed_bytes=changed,
        sequential_query_window_regular_observed_bytes=stable+changed,
        changed_size_growth_bytes=sum(c['growth_bytes'] for c in changes),
        interpretation='New sequential query-window subtotal, not a replacement for the original stable-inventory subtotal; no atomic snapshot, physical-union or error bound inferred.',
        not_measured=['mincore syscall-only CPU/time','per-alias/proc parsing timing','kernel/user CPU split',
                      'individual initial inventory times','specific writer/writeback events',
                      'cachestat runtime on these block devices','future speedup','full TMPFS helper process CPU'])


def report(v):
    s=v['stages'];g=v['cost_groups'];c=g['all'];m=lambda n:f'{n/MIB:.6f}'
    lines=['# 块设备查询成本与4个变化文件：离线分析','',
        '本次只读取上一轮保存的JSON、实际执行源码副本和同版本内核源码；不启动服务／沙箱，不打开目标文件或设备，不执行mincore／cachestat，不改变采集设置。旧报告、原始数据和历史数值保留。','',
        '## 1. 成本主要落在全容量逐页查询，不在对象遗漏','',
        '这些查询在原动作及下载结束后、官方检查前执行一次，不是每0.1秒执行。它们计入控制程序整轮，但位于已记录的动作及下载窗口之后；不能把1.32秒直接说成工具执行减速。','',
        '| 范围 | 对象数 | 逻辑容量 GiB | 查询页数 | 驻留 MiB | 对象查询窗口秒 | 调用线程CPU秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for key,label in [('drive','8个drive设备'),('zero_resident_drive','其中：驻留为0的7个drive'),('ram','2个RAM后备'),('all','10个设备合计')]:
        r=g[key];lines.append(f"| {label} | {r['objects']} | {r['capacity_bytes']/2**30:.0f} | {r['pages']:,} | {m(r['resident_bytes'])} | {r['object_window_ns']/1e9:.6f} | {r['cpu_ns']/1e9:.6f} |")
    lines+=['',f"块设备段共{s['block_device_wall_ns']/1e9:.6f}秒，占新增三层查询的{s['block_share_extra_layers_percent']:.2f}%。对象窗口合计{s['object_windows_ns']/1e9:.6f}秒；窗口外登记、打开／容量验证、间隔和收尾合计{s['outside_object_windows_ns']/1e9:.6f}秒，CPU{s['outside_object_windows_cpu_ns']/1e9:.6f}秒。其中首个对象查询前为{s['alias_setup_before_first_object_ns']/1e9:.6f}秒。",
        f"7个实际驻留为0的设备仍查询448 GiB逻辑地址，合计{g['zero_resident_drive']['object_window_ns']/1e9:.6f}秒，占整个块设备段{s['zero_drive_share_percent']:.2f}%。0是本轮实测结果，不能据此跳过未来查询或固定填0。",
        f"实际源码每256 MiB调用一组mmap(PROT_NONE)、mincore、munmap。520 GiB对应{c['pages']:,}个4 KiB位置、{c['chunks']:,}组调用、累计{c['pages']/MIB:.0f} MiB的一字节／页位图。这里没有读取520 GiB文件内容；成本来自映射、缓存索引查询、位图传输及统计等操作。",
        '保存的对象窗口包含mincore及前后别名／进程／映射复核，不能把1.259秒全部写成mincore系统调用耗时。没有分段计时，无法精确拆出内核扫描、用户态统计和身份检查各占多少。RAM的66个别名也会重复解析maps，但不能凭别名数推算其秒数。',
        '同版本mm/mincore.c:104–153明确按逻辑页号逐个查f_mapping，并检查folio是否uptodate（内容已准备好）；因此大片空区也要逐页经过。现有count_resident()已经用C实现的bytes.translate/count，不应再把问题归咎于Python逐页求和。单纯增大256 MiB分块只能减少部分调用，不能消除逐页扫描。','',
        '## 2. 四个文件有有效查询值，变化在登记后、查询前','',
        '| 角色 | 文件 | 登记大小→查询大小，字节 | 增长，字节 | 查询驻留 MiB | 查询内身份／大小稳定 | 距枚举开始，毫秒 |',
        '| --- | --- | ---: | ---: | ---: | --- | ---: |']
    for r in v['changed_files']:
        lines.append(f"| {','.join(r['users'])} | {Path(r['path']).name} | {r['inventory_size']}→{r['query_size']} | {r['growth_bytes']} | {m(r['resident_bytes'])} | {r['identity_stable']}／{r['size_stable']} | {r['query_offset_from_inventory_start_ns']/1e6:.3f} |")
    lines+=['',f"两套文件属于early_1和late_2。两个data各增长4096字节，两个index各增长16字节，总增长{v['changed_size_growth_bytes']}字节；mtime也变化。每次mincore查询自身均stable=true，身份未换、查询内大小未变，读取范围是查询时的新大小。",
        f"四项原始驻留合计{m(v['changed_query_observed_bytes'])} MiB。它们不是查询失败，也不是已证明的遗漏容量上界；排除它们来自旧策略要求登记大小／mtime也不变。单文件登记时刻和具体写入事件未保存，不能确定哪个操作触发变化，也不能归因DAMON或异步写回。",
        f"原稳定小计{m(v['original_stable_regular_bytes'])} MiB和PARTIAL保留。可另给“各文件查询窗口内观测小计”{m(v['sequential_query_window_regular_observed_bytes'])} MiB：只包含身份相符且查询自身稳定的磁盘普通文件，每个inode一次，四项变化另带标记。这个新增口径不是同一时刻快照，不证明整个枚举窗口大小固定，也不把旧未知改成零。",
        '不重查到文件不变、不等待写完、不延迟沙箱请求，不靠暂停消除变化。查询内部变化或身份变化仍不纳入新小计，失败容量保持未知。','',
        '## 3. 推荐的最小改进：新增显式缓存索引查询，不悄悄替换mincore','',
        '首选评估现有cachestat接口，而不是缩小设备范围或删除身份检查。它直接遍历文件缓存索引中的现有条目，可跳过空区。内核filemap.c:4563–4656的xas_for_each遍历和nr_cache计数、4735–4739的f_mapping选择，以及block/bdev.c:1006的bd_mapping绑定，支持对同一个已固定的块设备缓存查询；内核没有限定只能查询普通文件。权限失败会返回EPERM，不能当0。',
        '**两个接口的数值含义有差别。** mincore只把uptodate的缓存页记为1；cachestat的nr_cache统计缓存索引中已存在的普通页条目，不检查uptodate。因此在I/O尚未完成时，cachestat可能大于mincore，不能把新数直接写进旧resident_bytes或宣称方法完全等价。它更直接回答“缓存中已经分配多少页”，但仍是顺序观测，不是物理页并集。',
        '最小实现范围建议：','',
        '1. 仅给显式缓存分层模式增加块设备查询方法选项，原默认mincore、旧输出和普通入口保持。复用已有cachestat函数并增加可选范围，原TMPFS的off=0、len=0行为不变。',
        '2. 仍登记相同10个对象和全部别名，固定rdev／节点身份，查询前后核对容量与来源；新方法使用off=0、len=BLKGETSIZE64容量，与原mincore逻辑范围一致，不能只查以前有驻留的位置。',
        '3. 单列method、range、nr_cache、nr_dirty、nr_writeback、nr_evicted、nr_recently_evicted，以及cache_index_bytes=nr_cache×页大小。块设备的nr_evicted是历史回收条目，不是当前驻留，也不能套用TMPFS交换条目判断。错误、容量或身份变化继续单列。',
        '4. 普通文件只新增查询窗口小计和前后变化标记，不改原严格小计和PARTIAL。把登记与查询分开计时；块设备新增核心查询／身份复核墙钟与线程CPU，完整TMPFS工作进程CPU若仍未采到继续注明，不伪补。',
        '5. 这一步不改loop路径、PSS、0.1秒普通计数或默认回收；不加入周期mincore、目录大扫描、预读、锁页或清缓存。','',
        'cachestat会遍历已有缓存和回收条目，还会触发有节流的memcg统计刷新；存在可能睡眠的代码。只能从源码判断有望减少空区开销，当前没有这些块设备的实测耗时或数值对照，不给出预期加速百分比。','',
        '## 4. 验证条件与停止条件','',
        '下一步先做离线实现与回归：同一rdev的节点别名只计一次；范围覆盖完整容量；保留原默认／旧字段；四个变化文件只进入新口径，查询中变化不进入；权限、范围、容量、进程／映射变化及系统调用失败均保留未知。纯临时对象／模拟系统调用，不触碰真实服务或设备。',
        '只有后续获准真实验证后，再以一次完整BPO七沙箱作为新路径验证，不额外加玩具沙箱。仍为84实际／189逻辑动作、2保存6恢复、每实例官方0通过2失败，同样10个设备、14个TMPFS、准确loop后备及私有env/run/tmp清单。',
        '验证时仅在终点比较同一固定对象和完整范围的mincore与cachestat；不暂停、不预热、不换回收策略。两种方法记录各自查询时刻、耗时、CPU和身份检查。数值不同须区分接口语义及查询时差；没有页状态事件时保留原因未知，不要求它们强行相等，也不把后者折算成旧mincore值。',
        '对象完整、旧行为保留、原任务及清理通过、数值口径和实际成本明确即可结束；没有预设加速门槛，不为更好看的值追加运行。本轮只交付分析，尚未实施这些采集修改或启动后续验证。','',
        '## 输入和复算','',
        '原始指标：`'+v['source']+'`。实际执行源码：`'+v['source_code']+'`。逐对象数值与未测字段见同目录summary.json。同版本源码摘录见`.artifacts/schedule-cache-costs-offline-20261107/source-excerpts.txt`。','',
        '```bash','python3 -B scripts/analyze_schedule_cache_costs.py \\',
        '  --metrics '+v['source']+' \\',
        '  --output results/另一个尚不存在的目录','```','']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--metrics',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();assert not args.output.exists();v=collect(args.metrics)
    args.output.mkdir(parents=True);(args.output/'summary.json').write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
    (args.output/'REPORT.md').write_text(report(v));print('status=PASS result_dir='+str(args.output))


if __name__=='__main__':main()
