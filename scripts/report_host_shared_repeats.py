"""Aggregate saved file-focused runs only; never starts services or cache queries."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from host_scaling import FILE_PROFILE,SIZES


def read(path):return json.loads(Path(path).read_text())


def stats(values):
    values=list(values)
    return dict(n=len(values),mean=statistics.mean(values) if values else None,
        sd=statistics.stdev(values) if len(values)>1 else None,values=values)


def collect(workflow):
    workflow=Path(workflow);work=read(workflow/'result.json')
    assert work['status']=='PASS' and work['cache_released']
    outer=read(Path(work['storage_result_dir'])/'result.json')
    assert outer['status']=='PASS' and all(outer['cleanup'].get(k) for k in (
        'owned_container_removed','mount_and_loop_removed','successful_fixture_removed',
        'original_service_unchanged','original_firecracker_identities_unchanged'))
    raw=Path(work['storage_result_dir'])/'prettier6604';task=read(raw/'result.json')
    from host_scaling import audit
    summary=audit(raw)
    assert summary['profile']==FILE_PROFILE and summary['mode']=='cold' and summary['actual_actions']==77
    assert work['collector']['status']=='PASS' and work['collector']['pss_interval_seconds'] is None
    assert not (workflow/'host-file/pss.jsonl').exists()
    manifest=read(raw/'runtime-monitor.json')
    assert manifest['profile']=='light' and not manifest['host_vmm_requested'] and len(manifest['sessions'])==7
    for item in manifest['sessions']:
        assert item['status']=='PASS' and item['collector_reaped'] and item['receipt']['profile']=='light'
    with (workflow/'host-file/memory.tsv').open() as f:
        points=[{k:int(v) if v else None for k,v in row.items()} for row in csv.DictReader(f,delimiter='\t')]
    assert len(points)==work['collector']['samples'] and len(points)>1
    begin=summary['baseline']['end_monotonic_ns'];end=task['host_scaling']['task_end_monotonic_ns']
    task_points=[p for p in points if begin<=p['monotonic_ns']<=end]
    assert task_points and points[0]['monotonic_ns']<=begin<=end<=points[-1]['monotonic_ns']
    # Retain individual traces; no zero padding after a task exits.
    return dict(workflow=str(workflow),storage=work['storage_result_dir'],summary=summary,
        host_samples=len(points),guest_samples=sum(v['receipt']['samples'] for v in manifest['sessions']),
        file_task_peak_bytes=max(p['file'] for p in task_points),
        file_task_peak_above_baseline_bytes=max(p['file'] for p in task_points)-summary['baseline']['counters']['file'],
        host_max_sample_gap_ns=max(b['monotonic_ns']-a['monotonic_ns'] for a,b in zip(points,points[1:])),
        collector_thread_cpu_ns=work['collector']['thread_cpu_ns'])


def summarize(runs,planned=5):
    assert len({r['workflow'] for r in runs})==len(runs),'duplicate experiment'
    assert all(r['summary']['profile']==FILE_PROFILE and r['summary']['mode']=='cold' for r in runs)
    categories=[r['category'] for r in runs[0]['summary']['shared_file_reference']] if runs else []
    assert all([r['category'] for r in run['summary']['shared_file_reference']]==categories for run in runs)
    refs=[]
    for category in categories:
        rows=[next(v for v in r['summary']['shared_file_reference'] if v['category']==category) for r in runs]
        refs.append(dict(category=category,**{k:stats(v[k] for v in rows) for k in
            ('single_bytes','multiple_bytes','reference_bytes','reduction_bytes')},
            reduction_fraction=stats(v['reduction_fraction'] for v in rows if v['reduction_fraction'] is not None)))
    metrics={key:stats(r['summary'][key] for r in runs) for key in
        ('tool_ns','task_elapsed_ns','file_residency_query_total_ns','intermediate_query_ns','endpoint_residency_query_ns')}
    metrics.update({key:stats(r[key] for r in runs) for key in ('file_task_peak_bytes',
        'file_task_peak_above_baseline_bytes','collector_thread_cpu_ns')})
    return dict(status='PASS_FIVE_REPEATS' if len(runs)==planned else 'PARTIAL_REPEATS',profile=FILE_PROFILE,
        planned=planned,valid=len(runs),actual_actions=sum(r['summary']['actual_actions'] for r in runs),
        shared_file_reference=refs,metrics=metrics,
        file_stage_delta=[dict(sandboxes=n,**stats(r['summary']['stages'][i]['baseline_delta']['file'] for r in runs)) for i,n in enumerate(SIZES)],
        runs=runs,whole_physical_savings='OUT_OF_SCOPE',
        scope='Default reclaim, fixed cumulative schedule; means and sample SD across runs. Object observations at N=1/N=7 only. Hypothetical linear reference, not measured no-sharing. Peak means use per-run peaks, not the peak of a mean curve.')


def report(runs,output,planned=5):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);summary=summarize(runs,planned)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if not runs:return summary
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    refs=summary['shared_file_reference']
    fig,axes=plt.subplots(1,1+len(refs),figsize=(6*(1+len(refs)),4.5),squeeze=False,constrained_layout=True)
    ax=axes[0,0]
    for run in runs:
        ax.plot(SIZES,[s['baseline_delta']['file']/2**20 for s in run['summary']['stages']],color='#93c5fd',alpha=.6)
    ax.errorbar(SIZES,[s['mean']/2**20 for s in summary['file_stage_delta']],
        yerr=[(s['sd'] or 0)/2**20 for s in summary['file_stage_delta']],fmt='o-',capsize=4,color='#1d4ed8',label='Mean +/- sample SD')
    ax.set(title='Group file accounting',xlabel='Live sandboxes',ylabel='MiB above each baseline',xticks=SIZES);ax.grid(alpha=.2);ax.legend(fontsize=8)
    for ax,row in zip(axes[0,1:],refs):
        for run in runs:
            r=next(v for v in run['summary']['shared_file_reference'] if v['category']==row['category'])
            ax.plot([1,7],[r['single_bytes']/2**20,r['multiple_bytes']/2**20],color='#99d5c9',alpha=.6)
        ax.errorbar([1,7],[row[k]['mean']/2**20 for k in ('single_bytes','multiple_bytes')],
            yerr=[(row[k]['sd'] or 0)/2**20 for k in ('single_bytes','multiple_bytes')],fmt='o-',capsize=4,color='#0f766e',label='Observed mean +/- SD')
        ax.plot([1,7],[row['single_bytes']['mean']/2**20,row['reference_bytes']['mean']/2**20],'--',color='#94a3b8',label='N x single (hypothetical)')
        ax.set(title=row['category'],xlabel='Live sandboxes',ylabel='Global resident cache / MiB',xticks=[1,7]);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle(f"Prettier same 11 actions: {len(runs)}/{planned} repeats, default reclaim; specified file cache only")
    fig.savefig(output/'shared-files.png',dpi=140);fig.savefig(output/'shared-files.svg');plt.close(fig)
    lines=['# 共享文件缓存：五轮短实验','',f"计划{planned}轮，有效{len(runs)}轮，共{summary['actual_actions']}条原工具动作。",'',
        '| 类别 | N=1平均±标准差／MiB | N=7平均±标准差／MiB | 七份单沙箱参考少占／MiB |','| --- | ---: | ---: | ---: |']
    fmt=lambda v:f"{v['mean']/2**20:.3f} ± {v['sd']/2**20:.3f}" if v['sd'] is not None else f"{v['mean']/2**20:.3f}（标准差未测）"
    for row in refs:lines.append('| '+row['category']+' | '+' | '.join(fmt(row[k]) for k in ('single_bytes','multiple_bytes','reduction_bytes'))+' |')
    lines+=['','公共准备输入不乘七；共享对象按实际缓存身份去重，快照类别与只读系统盘分开。N=2／4只有组内计数，没有补造物理驻留。',
        '', '保持默认回收，不清缓存、不预读、不锁页，不额外暂停。各轮文件冷热状态可能不同，不称为每轮冷缓存。早启动沙箱经历更长时间，重复不会消除这一固定顺序影响。',
        '', '每轮先计算7F1−F7，再汇总平均值和样本标准差。这是相对线性参考的容量差，不是实测不共享对照或整体RAM节省。',
        '', 'N=1查询成本包含在递增序列总时长；N=7查询在原任务结束后。工具累计、序列用时、各阶段查询及采样CPU分别保存在summary.json。峰值取每轮峰值再平均；未做采样关闭对照，不从这些数据推导减速。',
        '', '![五轮均值、标准差及各轮结果](shared-files.png)']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('workflows',nargs='+',type=Path)
    p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    value=report([collect(w) for w in args.workflows],args.output)
    print('status='+value['status']+' result_dir='+str(args.output))
