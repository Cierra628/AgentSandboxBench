"""Analyze the saved C4 1s/2s pair without querying live processes or pages."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from run_c4_parallel import validate_parallel
from run_c4_parallel_pair import TIMING_KEYS,read,write

NS=10**9


def rows(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream,delimiter='\t'))


def gaps(points):
    values=[(int(b['monotonic_ns'])-int(a['monotonic_ns']))/NS for a,b in zip(points,points[1:])]
    assert values and min(values)>0
    return dict(median_seconds=statistics.median(values),max_seconds=max(values),min_seconds=min(values))


def scan_stats(points,receipt):
    assert receipt['status']=='PASS' and len(points)==receipt['samples']
    pss=[p for p in points if p['pss_kib']!='']
    assert len(pss)==receipt['pss_samples'] and len(pss)>1
    pss_gaps=gaps(pss)
    assert pss_gaps['min_seconds']>=receipt['pss_interval_seconds']
    return dict(samples=len(points),pss_samples=len(pss),ordinary_gaps=gaps(points),pss_gaps=pss_gaps,
        cpu_seconds=receipt['cpu_user_seconds']+receipt['cpu_system_seconds'],
        collector_elapsed_seconds=receipt['elapsed_ns']/NS,
        pss_scan_wall_seconds=sum(int(p['pss_scan_duration_ns']) for p in pss)/NS,
        pss_scan_median_ms=statistics.median(int(p['pss_scan_duration_ns']) for p in pss)/10**6,
        observed_pss_peak_mib=max(int(p['pss_kib']) for p in pss)/1024)


def analyze(base):
    saved=read(base/'result.json');assert saved['status']=='PASS' and saved['source_stopped']
    assert [r['pss_interval_seconds'] for r in saved['runs']]==[1,2]
    runs=[]
    for row in saved['runs']:
        task=row['task'];interval=row['pss_interval_seconds'];validate_parallel(task,'on',pss_interval_seconds=interval)
        raw=Path(row['raw']);host=rows(Path(row['workflow'])/'host-file/memory.tsv')
        assert len(host)==row['host_collector']['samples']
        run=dict(pss_interval_seconds=interval,workflow=row['workflow'],raw=str(raw),storage=row['storage'],
            min_disk_gib=row['min_disk_gib'],timing={k:task[k]/NS for k in TIMING_KEYS},
            replay_overlap_seconds=task['replay_overlap_ns']/NS,children=[],
            host_samples=len(host),host_gaps=gaps(host),host_cpu_seconds=row['host_collector']['thread_cpu_ns']/NS,
            current_observed_peak_mib=max(int(p['current']) for p in host)/2**20,
            file_observed_peak_mib=max(int(p['file']) for p in host)/2**20,
            after_task_seconds=(task['controller_elapsed_ns']-task['schedule_elapsed_ns'])/NS,
            task_start_monotonic_ns=task['schedule_start_monotonic_ns'],task_end_monotonic_ns=task['schedule_end_monotonic_ns'])
        active=[p for p in host if task['schedule_start_monotonic_ns']<=int(p['monotonic_ns'])<=task['schedule_end_monotonic_ns']]
        assert active
        run['task_current_observed_peak_mib']=max(int(p['current']) for p in active)/2**20
        for i,c in enumerate(task['children']):
            directory=raw/f'c4-parallel-{i}';points=rows(directory/'host-vmm/external/host-vmm.tsv')
            monitor=read(directory/'runtime-monitor.json');session=monitor['sessions'][0]
            assert len(monitor['sessions'])==1 and session['collector_reaped'] and session['receipt']['status']=='PASS'
            guest=rows(directory/session['raw_directory']/'guest-memory.tsv')
            assert len(guest)==session['receipt']['samples']
            endpoint=c['endpoint_metrics'];pss=endpoint['pss'];p1=endpoint['p1']
            details=dict(guest=i+1,sandbox_id=c['sandbox_id'],scan=scan_stats(points,c['host_vmm_collector']),
                guest_cpu_seconds=session['receipt']['cpu_user_seconds']+session['receipt']['cpu_system_seconds'],
                guest_samples=len(guest),guest_gaps=gaps(guest),endpoint_pss_mib=pss['total_bytes']/2**20,
                p1_mib=p1['resident_bytes']/2**20,p1_unique_files=p1['unique_files'],
                p1_net_shortfall_mib=p1['net_shortfall_bytes']/2**20,endpoint_costs={})
            for name,query in (('pss',pss),('p1',p1)):
                details['endpoint_costs'][name]=dict(wall_seconds=(query['end_monotonic_ns']-query['start_monotonic_ns'])/NS,cpu_seconds=query['cpu_ns']/NS)
            details['tools_seconds']=c['tool_duration_sum_ns']/NS
            events=read(directory/'events.json');delete=next(e for e in events if e['stage']=='delete-own-guest')
            details['delete_seconds']=delete['command_duration_ns']/NS
            details['delete_start_after_group_task_end_seconds']=(delete['command_start_monotonic_ns']-task['schedule_end_monotonic_ns'])/NS
            run['children'].append(details)
        run['vmm_cpu_seconds']=sum(c['scan']['cpu_seconds'] for c in run['children'])
        run['guest_cpu_seconds']=sum(c['guest_cpu_seconds'] for c in run['children'])
        run['periodic_cpu_seconds']=run['vmm_cpu_seconds']+run['guest_cpu_seconds']+run['host_cpu_seconds']
        run['pss_samples']=sum(c['scan']['pss_samples'] for c in run['children'])
        run['pss_scan_wall_seconds']=sum(c['scan']['pss_scan_wall_seconds'] for c in run['children'])
        run['vmm_cpu_per_vmm_elapsed_second']=run['vmm_cpu_seconds']/sum(c['scan']['collector_elapsed_seconds'] for c in run['children'])
        runs.append(run)
    return dict(status='PASS',source_batch=str(base),runs=runs,timing_changes=saved['changes'],
        scope='One 1s then one 2s run, two concurrent guests each; no sampling-off baseline or causal slowdown estimate')


def plots(value,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,4.5),constrained_layout=True)
    labels=['Tools sum','Task window','Actions/download','Controller round']
    for i,r in enumerate(value['runs']):
        axes[0].bar([j+(i-.5)*.35 for j in range(4)],[r['timing'][k] for k in TIMING_KEYS],width=.35,label=f"PSS {r['pss_interval_seconds']}s")
        axes[1].bar([j+(i-.5)*.35 for j in range(3)],[r[k] for k in ('vmm_cpu_seconds','guest_cpu_seconds','host_cpu_seconds')],width=.35,label=f"PSS {r['pss_interval_seconds']}s")
    axes[0].set_xticks(range(4),labels,rotation=15);axes[0].set_ylabel('Elapsed / cumulative tool time, seconds')
    axes[1].set_xticks(range(3),['Two VMM collectors','Two guest collectors','Host group collector']);axes[1].set_ylabel('Periodic collector CPU time, seconds')
    for ax in axes:ax.legend();ax.grid(axis='y',alpha=.2)
    fig.savefig(out/'timing-cost.png',dpi=150);plt.close(fig)
    fig,axes=plt.subplots(3,2,figsize=(13,10),sharey='row')
    fig.subplots_adjust(left=.09,right=.98,bottom=.07,top=.95,hspace=.34,wspace=.12)
    for j,r in enumerate(value['runs']):
        points=rows(Path(r['workflow'])/'host-file/memory.tsv');zero=int(points[0]['monotonic_ns'])
        for key,label in (('current','memory.current'),('file','memory.stat file')):
            axes[0,j].plot([(int(p['monotonic_ns'])-zero)/NS for p in points],[int(p[key])/2**20 for p in points],label=label)
        axes[0,j].axvspan((r['task_start_monotonic_ns']-zero)/NS,(r['task_end_monotonic_ns']-zero)/NS,alpha=.08,color='green',label='Task window')
        axes[0,j].set(title=f"PSS interval {r['pss_interval_seconds']}s",xlabel='Host group sampling elapsed, seconds',ylabel='Cgroup accounted memory / file, MiB')
        raw=Path(r['raw'])
        for i in range(2):
            directory=raw/f'c4-parallel-{i}';points=rows(directory/'host-vmm/external/host-vmm.tsv');zero=int(points[0]['monotonic_ns'])
            pss=[p for p in points if p['pss_kib']!='']
            axes[1,j].plot([(int(p['monotonic_ns'])-zero)/NS for p in pss],[int(p['pss_kib'])/1024 for p in pss],'.-',label=f'Guest {i+1} VMM')
            monitor=read(directory/'runtime-monitor.json');points=rows(directory/monitor['sessions'][0]['raw_directory']/'guest-memory.tsv');zero=int(points[0]['monotonic_ns'])
            axes[2,j].plot([(int(p['monotonic_ns'])-zero)/NS for p in points],[int(p['Cached_kib'])/1024 for p in points],label=f'Guest {i+1}')
        axes[1,j].set(xlabel='Each VMM sampling elapsed, seconds',ylabel='VMM proportional resident size PSS, MiB')
        axes[2,j].set(xlabel='Each guest sampling elapsed, seconds',ylabel='Guest Cached (includes Shmem), MiB')
    for ax in axes[:,1]:ax.set_ylabel('')
    for ax in axes.flat:ax.legend();ax.grid(alpha=.2)
    fig.savefig(out/'memory-curves.png',dpi=150);plt.close(fig)


def report(value,out):
    out.mkdir(parents=True,exist_ok=False);write(out/'summary.json',value);plots(value,out)
    lines=['# C4双沙箱：1秒／2秒PSS实际对照','',
        '按1秒→2秒各运行一次，两组均开启采集，每组两个并发C4 guest各执行原36步，共144动作。普通计数0.1秒、默认回收、独立终点PSS及9个初始下层文件P1保持；无清缓存、额外下载、代理、压力或删除诊断。结果不混入旧三对。','',
        '## 任务和清理检查通过','',
        '两组原退出序列、9898文件内容／目录往返、约束及语法检查通过。实际VMM身份和归属逐点核对，两guest指向同一9个下层inode；两组共4个不同sandbox ID。指定间隔与线程收据一致，终点值和查询成本均可取。成功卷、私有服务、挂载及loop已清理，源服务原ID／镜像保持且恢复停止。','',
        '## 时间变化：2秒相对1秒','',
        '| 指标 | 1秒PSS，秒 | 2秒PSS，秒 | 差值，秒 | 变化 |','| --- | ---: | ---: | ---: | ---: |']
    for key,label in zip(TIMING_KEYS,('工具累计（两guest合计）','组任务窗口（含创建、子程序启动及编译）','动作及下载窗口','双guest控制程序整轮')):
        c=value['timing_changes'][key];lines.append(f"| {label} | {c['one_second']:.6f} | {c['two_seconds']:.6f} | {c['delta_seconds']:+.6f} | {c['change_percent']:+.2f}% |")
    lines+=['','任务窗口从协调程序启动子程序前到最后工具下载返回；整轮另含终点、原检查、删除及记录复制，不含此前私有服务卷和镜像准备。工具累计是两guest的各动作时长之和，不是并发墙钟；两种时钟不直接相减。','',
        '## 周期采集成本与实际间隔','',
        '| PSS间隔 | PSS次数，两VMM合计 | VMM采集CPU，秒 | guest采集CPU，秒 | host组CPU，秒 | 周期合计CPU，秒 | PSS读取经过时间合计，秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in value['runs']:lines.append(f"| {r['pss_interval_seconds']}秒 | {r['pss_samples']} | {r['vmm_cpu_seconds']:.6f} | {r['guest_cpu_seconds']:.6f} | {r['host_cpu_seconds']:.6f} | {r['periodic_cpu_seconds']:.6f} | {r['pss_scan_wall_seconds']:.6f} |")
    a,b=value['runs']
    lines+=['',f"周期合计CPU变化{(b['periodic_cpu_seconds']/a['periodic_cpu_seconds']-1)*100:+.2f}%；VMM采集CPU变化{(b['vmm_cpu_seconds']/a['vmm_cpu_seconds']-1)*100:+.2f}%。VMM线程CPU除以各自采集经过时间之和为{a['vmm_cpu_per_vmm_elapsed_second']:.6f}→{b['vmm_cpu_per_vmm_elapsed_second']:.6f} CPU秒／VMM采样秒。CPU来自线程或进程实际用量，可相加；PSS读取经过时间可重叠，只表示查询成本，不当PSS独立CPU或任务减速。",'',
        'host组计数覆盖整段执行流程，2秒组的长收尾也延长了host采样时间；host累计CPU增加不能单独解释为降频后查询更贵。','',
        '| 配置／guest | 普通点数 | 普通间隔中位／最大，秒 | PSS点数 | PSS间隔中位／最大，秒 | guest点数 | guest间隔中位／最大，秒 |',
        '| --- | ---: | --- | ---: | --- | ---: | --- |']
    for r in value['runs']:
        for c in r['children']:
            s=c['scan'];o=s['ordinary_gaps'];p=s['pss_gaps'];g=c['guest_gaps']
            lines.append(f"| {r['pss_interval_seconds']}秒／{c['guest']} | {s['samples']} | {o['median_seconds']:.6f}／{o['max_seconds']:.6f} | {s['pss_samples']} | {p['median_seconds']:.6f}／{p['max_seconds']:.6f} | {c['guest_samples']} | {g['median_seconds']:.6f}／{g['max_seconds']:.6f} |")
    lines+=['','普通计数目标仍0.1秒，实际最大间隔原值保留；耗时较长的PSS查询也在同一线程内。频率按各自采集时刻核对，终点PSS不计入周期点数。','',
        '## 峰值与终点分开记录','',
        '| 配置／guest | 过程PSS观测峰值，MiB | 终点VMM PSS，MiB | 下层P1，MiB | PSS／P1终点查询经过时间，秒 | PSS／P1终点CPU，秒 | 删除请求，秒 |',
        '| --- | ---: | ---: | ---: | --- | --- | ---: |']
    for r in value['runs']:
        for c in r['children']:
            p=c['endpoint_costs']['pss'];q=c['endpoint_costs']['p1']
            lines.append(f"| {r['pss_interval_seconds']}秒／{c['guest']} | {c['scan']['observed_pss_peak_mib']:.6f} | {c['endpoint_pss_mib']:.6f} | {c['p1_mib']:.6f} | {p['wall_seconds']:.6f}／{q['wall_seconds']:.6f} | {p['cpu_seconds']:.6f}／{q['cpu_seconds']:.6f} | {c['delete_seconds']:.6f} |")
    lines+=['','各guest终点并不同步，共享下层P1不相加；PSS仅含VMM映射，不等于整组内存。不同运行的峰值差不能当降频漏峰误差。P1只覆盖初始9个下层文件，不代表upper或整个缓存；各量不相加。','',
        '## 本轮差异和长等待单列','',
        f"2秒组整轮增加{value['timing_changes']['controller_elapsed_ns']['delta_seconds']:.6f}秒，但组任务窗口减少{-value['timing_changes']['schedule_elapsed_ns']['delta_seconds']:.6f}秒。任务结束后的收尾从{a['after_task_seconds']:.6f}秒增至{b['after_task_seconds']:.6f}秒；两个2秒组删除请求分别{b['children'][0]['delete_seconds']:.6f}／{b['children'][1]['delete_seconds']:.6f}秒，主要等待与任务尾段及彼此重叠，不能相加后当整轮增加。PSS周期采集在删除前已停止。",'',
        '删除耗时直接来自各guest的`events.json`中`delete-own-guest`命令；2秒组`service-logs/server.log`的两条sandbox deleted日志均在09:20:50.683 UTC附近，与请求返回相符。这里只定位到删除请求等待，没有本轮函数分段或BPF证据，不能追认此次也一定是RocksDB同步或PSS造成。原17～18秒慢请求及整轮值保留，不重跑。','',
        f"整组memory.current观测峰值为{a['current_observed_peak_mib']:.6f}→{b['current_observed_peak_mib']:.6f} MiB，file观测峰值为{a['file_observed_peak_mib']:.6f}→{b['file_observed_peak_mib']:.6f} MiB，两个峰值不要求同一时刻。2秒组的下层P1也比1秒组低约124 MiB；身份与文件元数据稳定，原值保留。file还含其他准备／任务对象，本轮没有覆盖全部文件或回收事件，不能把整组file增加或P1减少归因于PSS降频或指定回收机制。",'',
        '四次P1相对组file的净不足均为0，仅表示本范围未发现净不足，不证明所有目标页都记账在组内。上述内存差异原值在曲线和摘要中单列，不用这两次运行推导内存节省或峰值漏采率。','',
        '## 原始路径、资源和图','']
    for r in value['runs']:
        lines.append(f"- {r['pss_interval_seconds']}秒：`{r['raw']}`；外层记录`{r['workflow']}`；私有卷记录`{r['storage']}`。最小可用磁盘{r['min_disk_gib']:.3f} GiB；并发重放重叠{r['replay_overlap_seconds']:.6f}秒。host组普通点{r['host_samples']}，间隔中位／最大{r['host_gaps']['median_seconds']:.6f}／{r['host_gaps']['max_seconds']:.6f}秒。")
    lines+=['',f"批次及当时源码：`{value['source_batch']}`；逐guest结果及成本在[summary.json](summary.json)。",'',
        '![时间和采集CPU](timing-cost.png)','',
        '![内存过程曲线](memory-curves.png)','',
        '曲线每列是一组，最多两条线，同一行使用相同纵轴范围。host行浅绿色区间为组任务窗口，曲线包含准备、控制程序和私有服务；PSS是单VMM；guest Cached是guest逻辑缓存。各行分别从自己的采样起点计时，不用于跨host／guest时钟定位因果。纵轴明确各自物理量。','',
        '## 结论边界与下一步','',
        '2秒配置已通过本轮真实可用性验证：实际周期正确，周期采集CPU下降，终点采集与原任务检查保持。建议后续C4成本敏感的测量显式选2秒，仍保留默认1秒。2秒曲线能显示过程变化，但它是否保留短峰值，本轮跨运行对照无法保证。只有顺序各一次，没有关闭采样对照，宿主和缓存变化也会影响任务；本轮时间变化不作为稳定减速率或PSS因果效应。慢组和全部实际值保留，不追加重复来取得更小百分比。','',
        '下一步建议仅离线统一采样说明，将默认1秒与显式2秒的适用情况、成本、终点值和峰值限制写清，以用法和报告一致为完成条件。若需要稳定的性能结论，再单独确认交替重复范围；本轮不追加实跑或启动正式Prettier九次。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('batch',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();value=analyze(args.batch.resolve());report(value,args.output.resolve())
    print('status=PASS result_dir='+str(args.output.resolve()))
