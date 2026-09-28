"""Generate an offline report from opt-in TrEnv-X timing and memory records."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil


def report(run):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    chinese='Noto Sans CJK JP' in {font.name for font in font_manager.fontManager.ttflist}
    if chinese:
        plt.rcParams['font.family']='Noto Sans CJK JP'

    result=json.loads((run/'result.json').read_text())
    if result['status']!='PASS':
        raise ValueError('completed successful run required')
    config=json.loads((run/'config.json').read_text())
    if not config.get('diagnostic_metrics'):
        raise ValueError('diagnostic metrics were not enabled')
    events=[json.loads(line) for line in (run/'diagnostic-events.jsonl').read_text().splitlines()]
    if not events or any(row['status']!='PASS' for row in events):
        raise ValueError('missing or failed timing events')
    snapshots=[dict(json.loads(path.read_text()),label=path.stem.removeprefix('vmm-'))
               for path in sorted(run.glob('vmm-*.json'))]
    labels={row['label'] for row in snapshots}
    required={'before-replay','after-replay'} | ({'before-checkpoint','restored'} if config['checkpoint_after'] else set())
    if not required.issubset(labels) or not all(row['cgroup_membership_verified'] for row in snapshots):
        raise ValueError('missing VMM observations or membership checks')
    by_label={row['label']:row for row in snapshots}
    parent=json.loads((run/'parent.json').read_text())['id']
    expected={label:parent for label in required}
    if config['checkpoint_after']:
        checkpoint=json.loads((run/'checkpoint.json').read_text())
        if checkpoint['parent']!=parent or checkpoint['restored']==parent:
            raise ValueError('file restore must identify distinct parent and child sandboxes')
        expected.update(restored=checkpoint['restored'])
        expected['after-replay']=checkpoint['restored']
    for label,ident in expected.items():
        if by_label[label]['sandbox_id']!=ident:
            raise ValueError(f'wrong VMM observation for {label}')
    with (run/'host/memory-samples.tsv').open() as stream:
        samples=list(csv.DictReader(stream,delimiter='\t'))
    if len(samples)<2:
        raise ValueError('host memory curve needs at least two samples')
    phases={row['phase']:row['elapsed_ns'] for row in events}
    selected=['private-template-prepare','template-build','create-to-first-tool-success']
    selected+=['tool:replay-prefix','checkpoint','parent-delete','restore-to-first-tool-success','tool:replay-continuation'] if config['checkpoint_after'] else ['tool:replay-all']
    if not all(name in phases for name in selected):
        raise ValueError('missing required timing phase')
    summary=dict(status='PASS',run_id=run.name,
        report_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256={str(path.relative_to(run)):hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in [run/'diagnostic-events.jsonl',run/'host/memory-samples.tsv',
                                   run/'config.json',*sorted(run.glob('vmm-*.json'))]},
        selected_phase_seconds={name:phases[name]/1e9 for name in selected},
        sdk_create_seconds=phases['sdk-create']/1e9,
        restore_interface_seconds=phases.get('restore-interface',0)/1e9 if config['checkpoint_after'] else None,
        host_observed_peak_bytes=max(int(row['cgroup_current_bytes']) for row in samples),
        vmm_observations=[{key:row[key] for key in ('label','sandbox_id','pid','pid_scope','cgroup','memory_bytes','scan_elapsed_ns')} for row in snapshots],
        limitations=['selected timings do not cover full experiment duration',
                     'create-to-first-tool-success includes local bookkeeping and first existing tool',
                     'VMM boundary scans are extra diagnostics outside frozen runner calls',
                     'host cgroup, VMM PSS, guest memory and DAX residency must not be added',
                     'sampling maximum is an observed peak; scan overhead is not total monitoring overhead'])
    (run/'diagnostic-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if Path(__file__).resolve()!= (run/'report_trenvx_diagnostics.py').resolve():
        shutil.copy2(Path(__file__),run/'report_trenvx_diagnostics.py')
    zero=int(samples[0]['timestamp_ns'])
    fig,axes=plt.subplots(3,1,figsize=(10,10),constrained_layout=True)
    seconds=[(int(row['timestamp_ns'])-zero)/1e9 for row in samples]
    for field,label in [('cgroup_current_bytes','总量' if chinese else 'current'),
                        ('cgroup_anon_bytes','匿名内存' if chinese else 'anon'),
                        ('cgroup_file_bytes','文件缓存' if chinese else 'file'),
                        ('cgroup_kernel_bytes','内核内存' if chinese else 'kernel')]:
        axes[0].plot(seconds,[int(row[field])/1024**2 for row in samples],label=label)
    axes[0].set(title='宿主机：本次沙箱资源组的内存采样' if chinese else 'Host sandbox parent cgroup (observed samples)',ylabel='MiB',xlabel='距第一次采样的秒数' if chinese else 'Seconds since first host sample')
    axes[0].legend()
    snapshots.sort(key=lambda row:row['timestamp_ns'])
    positions=list(range(len(snapshots)))
    for key,marker in [('Pss','o'),('Rss','x')]:
        axes[1].scatter(positions,[row['memory_bytes'][key]/1024**2 for row in snapshots],label=key,marker=marker)
    boundary_names={'before-replay':'重放前','before-checkpoint':'保存前','restored':'恢复后','after-replay':'重放后'}
    axes[1].set_xticks(positions,[boundary_names[row['label']] if chinese else row['label'] for row in snapshots])
    axes[1].set(title='虚拟机进程内存：PSS 分摊共享页，RSS 不分摊（不能与资源组相加）' if chinese else 'Host VMM process memory at selected boundaries (not additive with cgroup)',ylabel='MiB')
    axes[1].legend()
    axes[2].barh(selected,[phases[name]/1e9 for name in selected])
    axes[2].invert_yaxis()
    phase_names={'private-template-prepare':'准备私有模板','template-build':'构建模板',
                 'create-to-first-tool-success':'创建至首个工具成功','tool:replay-all':'完整重放调用',
                 'tool:replay-prefix':'保存前重放','checkpoint':'保存文件状态','parent-delete':'删除父虚拟机',
                 'restore-to-first-tool-success':'恢复至首个工具成功','tool:replay-continuation':'恢复后续跑'}
    if chinese:
        axes[2].set_yticks(range(len(selected)),[phase_names[name] for name in selected])
    axes[2].set(title='控制器记录的部分阶段耗时（不代表整个实验的全部耗时）' if chinese else 'Selected controller durations (not a complete end-to-end breakdown)',xlabel='秒' if chinese else 'Seconds')
    fig.savefig(run/'diagnostic-metrics.svg')
    fig.savefig(run/'diagnostic-metrics.png',dpi=130)
    plt.close(fig)
    lines=['# 诊断结果','',f'运行：`{run.name}`。','',
        '| 阶段 | 秒 |','| --- | ---: |']
    names={'private-template-prepare':'准备私有模板','template-build':'构建模板',
           'create-to-first-tool-success':'开始创建至第一个工具调用成功',
           'tool:replay-all':'完整重放的控制器调用','tool:replay-prefix':'保存前重放',
           'checkpoint':'保存文件状态','parent-delete':'删除父虚拟机',
           'restore-to-first-tool-success':'开始恢复至第一个工具调用成功',
           'tool:replay-continuation':'恢复后续跑'}
    lines += [f'| {names[name]} | {phases[name]/1e9:.4f} |' for name in selected]
    lines += ['',f'创建接口本身用时 {phases["sdk-create"]/1e9:.4f} 秒；它包含在创建至首个工具成功的时间内，不能重复相加。',
        '首个工具成功表示已经观察到通道可用，不是精确检测通道最早可用的时刻；包含本地记录和首个已有工具调用。',
        '图中进程内存来自宿主机虚拟机进程，PSS 将共享页按使用进程分摊。它与资源组内存分别报告，不能相加，也不能代替 guest 内存或 DAX 文件实际驻留量。',
        '精细读取发生在重放前后；其耗时单独记录，不能据此推算整体监控开销。这里只列部分阶段，不代表完整端到端耗时。','',
        '![内存曲线和阶段耗时](diagnostic-metrics.svg)','']
    (run/'diagnostic-report.md').write_text('\n'.join(lines))
    print(f'status=PASS result_dir={run}')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    report(parser.parse_args().run)
