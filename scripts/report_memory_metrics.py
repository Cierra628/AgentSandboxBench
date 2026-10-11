"""Offline memory composition and phase report; creates a new output directory."""
import argparse
import csv
import json
from pathlib import Path

from audit_exp1_metrics import audit_run
from memory_composition import series


def report(item):
    result=audit_run(item)
    raw=Path(result['raw_dir'])
    samples=[]
    path=raw/'replay/guest-memory.tsv'
    if path.exists():
        with path.open() as stream:
            for row in csv.DictReader(stream,delimiter='\t'):
                samples.append(dict(timestamp_ns=int(row['timestamp_ns']),phase=row['phase'],step=row['step'],
                    meminfo_bytes={k.removesuffix('_kib'):int(v)*1024 for k,v in row.items() if k.endswith('_kib')}))
    has_periodic_samples=bool(samples)
    if not samples:
        samples=[dict(timestamp_ns=row['timestamp_ns'],phase=row['label'],
                      meminfo_bytes=row['meminfo_bytes']) for row in result['guest_memory']['boundary_observations']]
    samples.sort(key=lambda row:row['timestamp_ns'])
    result['guest_composition']=series(samples)
    result['guest_composition']['sampling_mode']=result['guest_memory']['sampling_mode']
    if has_periodic_samples:
        result['guest_composition']['sampling_mode']='per-step boundaries plus in-step periodic samples'
        result['guest_composition']['time_scope']='guest timestamps; after initial Git preparation; no samples during checkpoint/restore'
    collector=raw/'replay/collector-status.json'
    if collector.exists():
        receipt=json.loads(collector.read_text())
        assert receipt['status']=='PASS' and receipt.get('samples_in_file',receipt['samples'])==len(samples)
        segments=sorted((raw/'replay/collector-segments').glob('*.json'))
        if segments:
            receipts=sorted((json.loads(p.read_text()) for p in segments),key=lambda row:row['samples_before'])
            assert all(row['status']=='PASS' for row in receipts)
            assert sum(row['samples'] for row in receipts)==len(samples)
            total=0
            for row in receipts:
                assert row['samples_before']==total
                total+=row['samples']
                assert row['samples_in_file']==total
            assert receipts[-1]==receipt
            result['guest_collector_segments']=receipts
        result['guest_collector']=receipt
        result['guest_composition']['sampling_mode']='persistent asynchronous process; advisory phase markers'
        result['guest_composition']['time_scope']='includes runner preparation; short actions may have no sample; per-runner lifetime only'
    result['physical_memory_scope']='named-file results only where explicitly present; guest/cgroup/PSS are not added'
    result['monitoring_impact']='NOT_VALIDATED_BY_THIS_RUN; see separate matched controls'
    return result


def write_report(result,out):
    out.mkdir(parents=True,exist_ok=False)
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    rows=result['guest_composition']['samples']
    lines=['# 内存组成与阶段耗时', '',f"运行：`{result['run_id']}`，后端：`{result['backend']}`。",'',
        f"guest 组成状态：{result['guest_composition']['status']}；样本数：{len(rows)}。",
        '单位为 bytes；图中换算为 MiB。非空闲内存为 MemTotal−MemFree；占比来自同一采样点。Slab 已包含可回收与不可回收 slab。',
        'guest、host cgroup、VMM PSS、文件内容物理量分别报告，不能相加。峰值是观测最大值。', '',
        '| 阶段 | 秒 | 范围 |','| --- | ---: | --- |']
    for name,phase in result['phase_breakdown'].items():
        value=f"{phase['duration_ns']/1e9:.6f}" if phase['duration_ns'] is not None else phase['status']
        lines.append(f"| {name} | {value} | {phase['boundary']} |")
    lines += ['', '本报告不单独证明采集开销达标或完整任务物理页已计数；缺失字段和未归因部分保留。','']
    (out/'report.md').write_text('\n'.join(lines))
    if rows:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(2,1,figsize=(10,7),constrained_layout=True)
        good=[row for row in rows if row['status']=='PASS']
        if good:
            times=[(row['timestamp_ns']-good[0]['timestamp_ns'])/1e9 for row in good]
            plot_times=[];plot_rows=[]
            for index,(timestamp,row) in enumerate(zip(times,good)):
                if index and timestamp-times[index-1]>1:
                    plot_times.append(None);plot_rows.append(None)
                    for axis in axes:
                        axis.axvspan(times[index-1],timestamp,color='gray',alpha=.15)
                plot_times.append(timestamp);plot_rows.append(row)
            keys=list(good[0]['components_bytes'])
            for key in keys:
                axes[0].plot(plot_times,[row['components_bytes'][key]/2**20 if row else None for row in plot_rows],label=key)
                axes[1].plot(plot_times,[row['percentages'][key] if row else None for row in plot_rows],label=key)
            axes[0].plot(plot_times,[row['nonfree_bytes']/2**20 if row else None for row in plot_rows],label='nonfree',color='black')
            axes[1].set_title('Gray spans: sample gaps longer than 1 second',fontsize=9)
        axes[0].set(ylabel='Guest logical memory (MiB)',title=result['backend']+' '+result['run_id'])
        axes[1].set(xlabel='Seconds since first sample',ylabel='Same-sample nonfree share (%)')
        axes[0].legend(fontsize=8,ncol=3)
        for extension in ('svg','png'):fig.savefig(out/('guest-composition.'+extension))
        plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--backend',choices=('agentenv','trenvx'),required=True)
    parser.add_argument('--audit',type=Path)
    parser.add_argument('--mode',default='full',choices=('full','full-light','checkpoint-013',
                        'checkpoint-022','full-diagnostic','checkpoint-022-diagnostic'))
    args=parser.parse_args()
    item=dict(backend=args.backend,mode=args.mode,raw=str(args.raw.resolve()))
    if args.audit:item['audit']=str(args.audit.resolve())
    value=report(item);write_report(value,args.output)
    print(f"status={value['guest_composition']['status']} result_dir={args.output}")
