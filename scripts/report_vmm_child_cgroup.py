"""Recover only saved counters from the one VMM-cgroup run; never query live pages."""
import argparse
import csv
import json
from pathlib import Path

MIB=2**20
KEYS=('current','file','anon','kernel')


def report(batch,out):
    read=lambda p:json.loads(p.read_text())
    run=read(batch/'run-result.json')
    storage=Path(run.get('storage') or run['reported_paths'][0]);raw=storage/'prettier6604'
    task=read(raw/'result.json');metrics=read(raw/'schedule-smoke-metrics.json')
    setup=read(storage/'vmm-cgroup-setup.json');sampler=read(storage/'vmm-cgroup-samples/result.json')
    assert task['actual_action_count']==189 and len(task['paths'])==7
    assert all(p['official_passed']==0 and p['official_failed']==2 for p in task['paths'].values())
    assert metrics['pss']['status']=='OBSERVED' and len(metrics['pss']['processes'])==7
    p1=metrics['p1'];assert len(p1['files'])==19
    assert len({(r['device'],r['inode']) for r in p1['files']})==19
    assert all(r['stable'] and r['mincore_permission']['basis']=='HOST_CAP_FOWNER' for r in p1['files'])
    rows=[json.loads(l) for l in (storage/'vmm-cgroup-samples/memory.jsonl').read_text().splitlines()]
    assert sampler['status']=='PASS' and len(rows)==sampler['samples']
    row=min(rows,key=lambda r:abs(r['monotonic_ns']-p1['end_monotonic_ns']))
    groups=row['groups'];parent=groups[setup['parent']]
    children={p:v for p,v in groups.items() if Path(p).name.startswith('vmm-')}
    other={p:v for p,v in groups.items() if p!=setup['parent'] and p not in children}
    assert len(children)==7
    birth={p.stem:read(p) for p in (storage/'service-logs/vmm-cgroups').glob('*.json')}
    assert set(birth)=={Path(p).name for p in children}
    assert all(r['creation']=='clone3_CLONE_INTO_CGROUP' and
               r['birth_cgroup']=='0::'+p.removeprefix('/sys/fs/cgroup') for p in children for r in [birth[Path(p).name]])
    sums={k:sum(v[k] for v in children.values()) for k in KEYS}
    others={k:sum(v[k] for v in other.values()) for k in KEYS}
    pss=metrics['pss'];pss_anon=sum(r['pss_anon_kib'] for r in pss['processes'])*1024
    data=dict(status='PARTIAL_OFFLINE_RECOVERY',run_status=run['status'],task_status=task['status'],
        failed_stage=task.get('failed_stage'),error=task.get('error'),storage=str(storage),raw=str(raw),
        actual_actions=189,official_results='Each of seven: 0 passed, 2 failed',
        snapshot=row,parent=parent,child_sum=sums,other_children=other,other_sum=others,birth=birth,
        parent_minus_vmm={k:parent[k]-sums[k] for k in KEYS},
        parent_minus_all_children={k:parent[k]-sums[k]-others[k] for k in KEYS},
        pss_bytes=pss['total_bytes'],pss_anon_bytes=pss_anon,
        mincore_bytes=p1['resident_bytes'],file_bytes=sum(r['size'] for r in p1['files']),
        sampler_to_p1_end_ns=row['monotonic_ns']-p1['end_monotonic_ns'],
        p1_counters_before=p1['counters_before'],p1_counters_after=p1['counters_after'],
        exact_lower_memcg_ownership='NOT_QUERIED: endpoint checker failed before attaching the page-owner probe',
        post_exec_host_pid_to_child_group='Only first live check reached before error; seven birth groups and seven parent-bound VMM identities saved separately',
        original_19_requery='UNAVAILABLE: old inodes removed',history_modified=False)
    with (out/'summary.json').open('x') as f:json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n')
    with (out/'children.tsv').open('x') as f:
        writer=csv.DictWriter(f,fieldnames=['group','namespace_pid','birth_cgroup',*KEYS],delimiter='\t');writer.writeheader()
        for p,v in children.items():
            b=birth[Path(p).name];writer.writerow(dict(group=p,namespace_pid=b['namespace_pid'],birth_cgroup=b['birth_cgroup'],**{k:v[k] for k in KEYS}))
    lines=['# 一次GRPO父子cgroup验证：数据部分恢复，逐页归属未取得','',
        '**189个原动作及七份官方结果完成，七个VMM子组已创建；终点检查器因KeyError失败，整轮仍是FAIL。** 本报告只离线整理保存数据，不修改原状态、不重跑。第一步的真实19文件权限对照见[单独结论](PHASE1.md)。','',
        '## 对照表：父组总量不是子组之和再加父组','',
        f'父子组取自同一次0.1秒周期采样，扫描耗时{row["duration_ns"]/1e6:.3f}毫秒；该点在mincore结束后{data["sampler_to_p1_end_ns"]/1e6:.3f}毫秒。PSS为紧邻mincore之前的七进程顺序窗口，三项不是原子同步快照。父组是新建独立实验slice，含控制程序、私有服务和七VMM；旧报告的82.465 MiB是旧私有服务组，不能与本次实验父组混用。','',
        '第一步恢复的只读RAM输入在实验slice外准备，收尾已释放；父组包含其后私有服务的输入复制，不代表整台宿主机或全部准备输入的物理占用。旧host/memory-samples.tsv在本显式模式中仅覆盖服务，权威父子曲线是下文的新JSONL。','',
        '| 口径 | 值（MiB） |','| --- | ---: |']
    for label,values in [('父组',parent),('Σ 七VMM子组',sums)]:
        for k in KEYS:lines.append(f'| {label} {k} | {values[k]/MIB:.6f} |')
    lines += [f'| Σ 七VMM PSS | {pss["total_bytes"]/MIB:.6f} |',
        f'| 其中 PSS_Anon | {pss_anon/MIB:.6f} |',
        f'| 19文件 mincore P1（逐文件权限检查通过） | {p1["resident_bytes"]/MIB:.6f} |',
        f'| 19文件总字节换算 | {data["file_bytes"]/MIB:.6f} |','',
        '## 七个子组的记账','', '| 子组 | file（MiB） | anon（MiB） | 出生时容器PID |','| --- | ---: | ---: | ---: |']
    for p,v in children.items():lines.append(f'| {Path(p).name} | {v["file"]/MIB:.6f} | {v["anon"]/MIB:.6f} | {birth[Path(p).name]["namespace_pid"]} |')
    lines+=['', '七份出生记录都在exec原Firecracker之前写出自身cgroup，创建方式为clone3(CLONE_INTO_CGROUP)，不是启动后迁移。原命令、七角色、十九层引用与0.1秒普通计数／1秒周期PSS保持。服务和控制程序自开始就在各自子组，也未迁移。','',
        '本次错误发生在第一个VMM的子组／出生身份检查之后、匹配PSS行时；其余六个的逐一host PID到子组的终点检查没有执行完。七份出生记录与七份父组内VMM身份分别保留，不伪造缺少的逐一终点绑定。','',
        '## 四个问题的回答','',
        f'1. **Σ VMM子组file不等于父组file。** 子组之和为{sums["file"]/MIB:.6f} MiB，父组为{parent["file"]/MIB:.6f} MiB，父组多{(parent["file"]-sums["file"])/MIB:.6f} MiB。其他直接子组如下。','',
        '| 其他直接子组 | file（MiB） | anon（MiB） |','| --- | ---: | ---: |']
    for p,v in other.items():lines.append(f'| {Path(p).name} | {v["file"]/MIB:.6f} | {v["anon"]/MIB:.6f} |')
    lines += ['', f'同一扫描中，父组file减去全部直接子组file为{data["parent_minus_all_children"]["file"]/MIB:.6f} MiB。控制程序所在service包含输入复制、验收和日志／结果下载；Docker scope是私有服务及提供块设备的进程。它们不是七个VMM子组，不能漏掉，也不能再与父组相加。','',
        f'2. **mincore比Σ VMM子组file大。** 差{(p1["resident_bytes"]-sums["file"])/MIB:.6f} MiB。mincore统计指定inode的全局文件缓存驻留，子组file只统计记在该组的页，两者不同范围。','',
        '3. **不是已确认的mincore权限假阳性；十九文件逐页记账归属仍未取得。** 本次私有服务file仍很小，但扩大到正确的实验父组后，file明显大于指定文件mincore，且大量file明确记在控制程序所在组。这个分解解释了“为什么不能用服务／VMM记账代替全局驻留”，但没有证明这19个inode的每一页都记在该控制程序组。仅比较其他cgroup总量不构成逐文件归属证据。原计划在终点复用不触页的memcg查询；本次在探针启动前失败，不能补值或把组总量接近写成归属已确认。文件已随挂载收尾而不可在线访问，事后重挂不能重建旧时刻归属。','',
        f'4. **VMM子组anon与七VMM PSS接近。** anon={sums["anon"]/MIB:.6f} MiB，PSS={pss["total_bytes"]/MIB:.6f} MiB，相差{(sums["anon"]-pss["total_bytes"])/MIB:.6f} MiB；PSS_Anon={pss_anon/MIB:.6f} MiB。PSS主要来自guest RAM的匿名映射，与该解释一致；记账分项和按映射分摊的PSS仍不是同一个统计量。','',
        '## 谁承担共享页的记账','',
        '文件缓存页通常在首次创建／记账时归入当时分配或I/O上下文对应的内存cgroup；后续另一个VMM命中同一页，不会把file均分，也不会因为读者变多而重复记账。loop后备等I/O路径要以实际memcg证据为准，不能简单把发起读请求的VMM当作记账者。PSS则按映射共享比例分摊，这是另一套机制。父组memory.stat含后代记账；不能用父组加子组算总量。','',
        '## 原始失败与本次边界','',
        '```text', (raw/'schedule-smoke-failure.txt').read_text().strip(), '```','',
        'PSS行的PID实际在identity.pid，而新增检查器错误读取了顶层pid。源码已离线修正，并增加直接覆盖真实PSS结构的回归，未再运行任务。旧metrics／task／outer的FAIL原文保留。原任务顺序／退出码、最终源码属性和七份完整官方结果检查为PASS；整体验收仍缺逐页归属和六份逐一终点绑定，不记为整轮PASS。','',
        f'原始业务目录：`{raw}`；父子曲线：`{storage}/vmm-cgroup-samples/memory.jsonl`；出生记录：`{storage}/service-logs/vmm-cgroups/`；完整错误：`{raw}/schedule-smoke-failure.txt`。','',
        '源服务已恢复停止，七VMM和新私有服务已清理，挂载与loop已解除，临时RAM输入已释放，七个空VMM子组已移除。16 GiB失败卷保留，不自动删除。','',
        '运行前24项相关离线检查通过，clone3出生位置用非沙箱进程验证。修补后的1项回归直接使用identity.pid结构覆盖完整七子组汇总，未再启动业务。日志：`../../.artifacts/vmm-child-cgroup-20261110T072553Z/offline-final-tests.log`、`../../.artifacts/vmm-child-cgroup-20261110T072553Z/endpoint-fix-test.log`。独立实验slice确认无进程后也已停止，见同目录slice-cleanup.json。','',
        '## 下一步建议（等待确认，不自动运行）','',
        '若仍需确定十九文件的实际记账组，建议仅新建一次准备环境、不运行189个动作，在文件仍在线时查询十九文件的逐页memcg并与有效mincore对齐。完成条件是所有驻留页均分到可核查的组或明确未知；它只能回答新的准备时刻，不能补回本次或历史P1。不要为检查器失败重跑已完成的业务。']
    with (out/'REPORT.md').open('x') as f:f.write('\n'.join(lines)+'\n')
    print('status='+data['status']+' result_dir='+str(out))
    return data


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report(a.batch.resolve(),a.output.resolve())
