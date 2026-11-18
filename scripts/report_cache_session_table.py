"""Saved session tables only; no live services, proc reads or new measurements."""
import argparse,csv,json
from pathlib import Path

MIB=2**20


def build(raw,out):
    run=json.loads((raw/'result.json').read_text());assert run['status']=='PASS'
    origin=json.loads((raw/'rollout-cache-sessions.json').read_text())['shared_clock']['origin_ns']
    rows=[]
    for session,receipt in run['sessions'].items():
        source=raw/'live-cache'/session/'samples.jsonl'
        points=[json.loads(l) for l in source.read_text().splitlines()]
        assert len(points)==receipt['samples']
        for p in points:
            lower='host_three_current_union_bytes' in p
            direct='host_current_union_bytes' in p
            vm=p.get('host_vmm_confirmed_bytes',p['host_confirmed_cache_bytes'])
            dev=p.get('host_snapshot_current_bytes');layer=p.get('host_lower_current_bytes')
            shared_vd=p.get('host_vmm_snapshot_intersection_bytes')
            shared_l=p.get('host_lower_intersection_with_vmm_device_bytes')
            union=p['host_three_current_union_bytes'] if lower else p['host_current_union_bytes'] if direct else vm
            assert union==vm+(dev or 0)+(layer or 0)-(shared_vd or 0)-(shared_l or 0)
            unknown=p['host_three_unconfirmed_guest_capacity_bytes'] if lower else p['host_current_unconfirmed_guest_capacity_bytes'] if direct else (p['unconfirmed_after_pages']+p['host_nonresident_pages']+p['host_swapped_pages'])*4096
            t=p['shared_round']
            r=dict(session=session,sandbox_id=receipt['sandbox_id'],host_pid=receipt['host_identity']['pid'],point=p['point'],round=t['round'],scope='VMM+direct+lower' if lower else 'VMM+direct' if direct else 'VMM only',guest_cache_mib=p['guest_cache_after_bytes']/MIB,vmm_current_mib=vm/MIB,direct_current_mib=dev/MIB if dev is not None else None,lower_current_mib=layer/MIB if layer is not None else None,vmm_direct_overlap_mib=shared_vd/MIB if shared_vd is not None else None,lower_overlap_with_vmm_direct_mib=shared_l/MIB if shared_l is not None else None,physical_union_mib=union/MIB,unknown_guest_capacity_mib=unknown/MIB,old_direct_mib=p['snapshot_old_version_bytes']/MIB if direct else None,old_lower_mib=p['host_lower_old_snapshot_bytes']/MIB if lower else None,read_start_ns=t['read_start_ns'],read_end_ns=t['read_end_ns'],relative_start_seconds=(t['read_start_ns']-origin)/1e9,relative_end_seconds=(t['read_end_ns']-origin)/1e9,source=str(source),fields='guest_cache_after_bytes; host_vmm_confirmed_bytes or host_confirmed_cache_bytes; host_snapshot_current_bytes; host_lower_current_bytes; host_vmm_snapshot_intersection_bytes; host_lower_intersection_with_vmm_device_bytes; host_three_current_union_bytes or host_current_union_bytes or host_confirmed_cache_bytes; unknown per scope; shared_round.read_start_ns/read_end_ns')
            rows.append(r)
    out.mkdir(parents=True,exist_ok=False)
    with (out/'all-points.csv').open('x') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'table.json').write_text(json.dumps(dict(raw=str(raw),origin_ns=origin,points=rows,atomic_snapshot=False,business_runs=0),indent=2)+'\n')
    def fmt(v):return '不适用' if v is None else f'{v:.6f}'
    lines=['# 逐实例缓存容量与host驻留：可用数据表','',
        '只读已保存22点；采样与原始二进制的一致性沿用 `results/cache-lower-fixed-calibration-20261117/recount.log`，本次另逐点检查层并集算式。没有新业务、查询、历史数据修改或新物理量。完整字段及纳秒窗口见 [all-points.csv](all-points.csv)，机器读取见 [table.json](table.json)。','',
        '## 怎样使用这些列','',
        '- guest缓存容量：元数据表中的普通文件＋shmem页容量，属于guest视角，不是host物理量。',
        '- VMM、直接后备、lower：分别是该层当前缓存内容对应的确认host驻留页集合大小；lower为不可变快照文件。',
        '- 重叠：不同层集合中相同host物理页，只扣除一次。物理并集 = VMM + 直接后备 + lower − VMM/直接重叠 − lower/前两层重叠。',
        '- 同一内容的不同物理副本仍各占内存，不应因内容相同而扣掉；这是三层物理并集可能大于guest容量的原因。本表“重叠”不等于内容重复量。',
        '- 未确认量：尚不能确认host驻留的guest容量，不是已分配物理字节，也不能补进物理并集。',
        '- 旧直接／lower副本单列在CSV，不加入当前并集；两类旧副本之间也未给跨层去重总数，不直接相加。',
        '- 源实例为原生RAM映射，无恢复文件后备会话；相应列“不适用”，不是测得0。恢复实例为三层范围，两者范围不同，不把并集直接比较成“哪种调度更省”。','',
        '单位MiB，1 MiB=1048576字节；展示保留6位小数，CSV保留计算值。原始数据未舍入。','',
        '## 全部22个观察点','',
        '| 会话／点／轮 | guest容量 | VMM | 直接后备 | lower | VMM/直接重叠 | lower/前两层重叠 | 物理并集 | 未确认容量 |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    keys=['guest_cache_mib','vmm_current_mib','direct_current_mib','lower_current_mib','vmm_direct_overlap_mib','lower_overlap_with_vmm_direct_mib','physical_union_mib','unknown_guest_capacity_mib']
    for r in rows:lines.append('| '+f"{r['session']} / {r['point']} / {r['round']}"+' | '+' | '.join(fmt(r[k]) for k in keys)+' |')
    lines+=['','## 同轮也必须保留读取窗口','',
        '以下起止秒数相对于共同host单调时钟起点，不是墙钟。只并列已测值，不计算跨实例物理总量。第19—22轮在预定双实例观察期内；第23轮源实例已经开始收尾，恢复实例的额外点保留为不完整。','',
        '| 轮 | 源实例读取开始—结束秒 | 恢复实例读取开始—结束秒 | 双实例状态 |','|---|---|---|---|']
    for i in sorted({r['round'] for r in rows if r['session']=='restored-g1'}):
        selected={r['session']:r for r in rows if r['round']==i}
        def window(name):
            r=selected.get(name)
            return '缺失（已停采）' if r is None else f"{r['relative_start_seconds']:.9f}—{r['relative_end_seconds']:.9f}"
        lines.append(f"| {i} | {window('backbone-g2')} | {window('restored-g1')} | "+('完整轮次观测，非原子同刻' if 'backbone-g2' in selected else 'INCOMPLETE')+' |')
    lines+=['','## 覆盖边界与当前完成情况','',
        '已实跑验证：两个实例的身份、保存恢复、共同轮次、guest缓存到VMM／直接后备／lower物理页的对应，以及采集收尾。恢复5点中3点未知容量为0；它只说明当次已声明范围的查询，没有证明整个沙箱的所有host成本都已覆盖。',
        '未覆盖：provider临时缓冲区、其他共享服务成本与全部组外缓存。整VMM PSS、父组current/file和本表物理并集范围重叠，不能相加。未取得严格原子同刻的跨实例去重总量；实际读取窗口最长约0.506秒。单次合成校准不代表完整rollout或三调度性能结论。','',
        '## 来源','',f'原始目录：`{raw}`。每行source列指向对应samples.jsonl，point定位原始条目；身份来自同目录result.json；shared_round给出真实读取边界。来源均为同一批，没有混入旧Docker范围或其他失败运行。','',
        '## 下一步建议','',
        '本阶段表格整理完成，不再追加同类校准。若下一阶段目标仍是完整逐沙箱host缓存，先离线评估provider槽位接口所需的进程归属、内容有效期和采集成本，明确是否值得补采；没有这些证据前，继续使用本表的“三层确认驻留＋未知项”口径，不称完整总量。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return len(rows)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print('saved points:',build(a.raw,a.output))
