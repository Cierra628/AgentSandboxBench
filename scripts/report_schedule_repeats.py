"""Report nine new schedule runs using saved data, retaining the established scope."""
import argparse
import csv
import json
from pathlib import Path
import statistics

from platform_tasks import ROOT
from report_schedule_metrics_offline import normalize, rows
from report_sandbox_runtime import samples, curve_chunks
from run_schedule_repeats import inspect_run, run_plan
from run_schedule_smoke import SCHEDULES, read, write


METRICS = dict(vmm_pss_endpoint_per_guest_mib='终点PSS/N，MiB/guest',
               initial_lower_mincore_p1_mib='初始19文件P1，MiB',
               schedule_run_seconds='调度执行窗口，秒',
               actual_tool_sum_seconds='实际工具累计，秒',
               action_download_window_seconds='动作及下载窗口，秒',
               controller_seconds='控制程序整轮，秒',
               service_cgroup_current_peak_mib='服务组current采集窗口峰值，MiB',
               service_cgroup_file_before_mib='终点服务组file，MiB',
               endpoint_observation_seconds='终点观测，秒')


def stats(values):
    return dict(count=len(values), mean=statistics.mean(values), median=statistics.median(values),
                stdev=statistics.stdev(values) if len(values)>1 else None,
                minimum=min(values), maximum=max(values))


def draw_run(raw, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    host = rows(raw/'host/memory-samples.tsv'); origin = int(host[0]['timestamp_ns'])
    vmm_paths = list((raw/'host-vmm').rglob('host-vmm.tsv'))
    vmm = [rows(path) for path in vmm_paths]
    assert len({r['sandbox_id'] for data in vmm for r in data})==7
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), layout='constrained')
    for key in ('current', 'file'):
        axes[0].plot([(int(r['timestamp_ns'])-origin)/1e9 for r in host],
                     [int(r['cgroup_'+key+'_bytes'])/2**20 for r in host], label=key)
    roles = sorted({r['role'] for data in vmm for r in data})
    colors = {role: 'C'+str(i) for i, role in enumerate(roles)}
    shown = set()
    for data in vmm:
        for chunk in curve_chunks([r for r in data if r['pss_kib']], max_gap_ns=2_500_000_000):
            role = chunk[0]['role']
            axes[1].plot([(int(r['timestamp_ns'])-origin)/1e9 for r in chunk],
                         [int(r['pss_kib'])/1024 for r in chunk], color=colors[role],
                         label=role if role not in shown else None)
            shown.add(role)
    task = read(raw/'result.json')
    for ax in axes:
        ax.set_xlabel('Recorded host wall-clock seconds from first host sample')
        ax.legend(ncol=4, fontsize=8); ax.grid(alpha=.2)
    axes[0].set_ylabel('memory.current / memory.stat file\nMiB; overlapping measures')
    axes[1].set_ylabel('Per-VMM PSS, MiB\n(asynchronous samples)')
    fig.suptitle(task['schedule'].upper()+' — full collection window including final checks and cleanup')
    fig.savefig(out/'host.png', dpi=130); plt.close(fig)
    groups, scope = samples(raw)
    fig, axes = plt.subplots(4, 2, figsize=(12, 11), layout='constrained')
    for ax, (sid, group) in zip(axes.flat, sorted(groups.items(), key=lambda item: item[1]['role'])):
        origin_guest = min(int(r['timestamp_ns']) for data in group['chunks'] for r in data)
        for data in group['chunks']:
            for chunk in curve_chunks(data):
                ax.plot([(int(r['timestamp_ns'])-origin_guest)/1e9 for r in chunk],
                        [int(r['Cached_kib'])/1024 for r in chunk], color=colors[group['role']])
        ax.set(title=group['role']+' / '+sid[-8:], xlabel='Recorded guest seconds since its first sample',
               ylabel='Guest logical Cached, MiB'); ax.grid(alpha=.2)
    axes.flat[-1].axis('off')
    fig.suptitle('Guest file cache by role; independent origins, snapshot gaps remain disconnected')
    fig.savefig(out/'guest-cached.png', dpi=130); plt.close(fig)
    return dict(guest_count=len(groups), guest_scope=scope, pss_sessions=len(vmm_paths),
                guest_sessions=sum(len(g['sessions']) for g in groups.values()),
                host_samples=len(host), guest_samples=sum(len(c) for g in groups.values() for c in g['chunks']),
                pss_samples=sum(bool(r['pss_kib']) for data in vmm for r in data))


def report(batch, out):
    saved = read(batch/'result.json')
    assert saved['status']=='PASS' and saved['source_stopped'] and saved['cache_released']
    assert [(r['round'], r['schedule']) for r in saved['runs']] == [(r['round'], r['schedule']) for r in run_plan()]
    assert all(r['status']=='PASS' for r in saved['runs'])
    assert not out.exists(); out.mkdir(parents=True)
    normalized = []; reference = None
    for number in (1, 2, 3):
        source = out/f'round-{number}-source.json'
        selected = [r for r in saved['runs'] if r['round']==number]
        write(source, dict(status='PASS', runs=selected))
        normal = normalize(source)
        for original, row in zip(selected, normal['runs']):
            verified = inspect_run(Path(original['storage']))
            for key, val in verified.items(): assert val == original[key], key
            raw = Path(original['raw']); endpoint = read(raw/'schedule-smoke-metrics.json')
            initial = {(v['device'], v['inode']) for v in endpoint['p1']['files']}
            one = next(iter(endpoint['rootfs_inventory']['guests'].values()))
            identity = sorted((f['reference']['digest'], f['reference']['size']) for f in one['lowers']
                              if (f.get('device'), f.get('inode')) in initial)
            if reference is None: reference = identity
            assert identity==reference
            row.update(round=number, service_cgroup_current_peak_mib=verified['current_peak_mib'],
                       save_calls=original['save_calls'], restore_calls=original['restore_calls'],
                       minimum_disk_gib=original['minimum_disk_gib'],
                       outer_command_seconds=original['outer_command_seconds'],
                       endpoint_cpu_seconds=original['endpoint_cpu_seconds'],
                       endpoint_pss_cpu_seconds=endpoint['pss']['cpu_ns']/1e9,
                       endpoint_p1_cpu_seconds=endpoint['p1']['cpu_ns']/1e9)
            name = f"r{number}-{row['schedule']}"; directory = out/name; directory.mkdir()
            row['curves'] = dict(directory=name, **draw_run(raw, directory))
            (directory/'REPORT.md').write_text('# '+name+'\n\n原始目录：`'+str(raw.relative_to(ROOT))+'`。\n\n'
                '[服务组current／file及七VMM PSS](host.png) · [逐角色guest Cached](guest-cached.png)\n\n'
                '过程图包含最终检查与清理；主表任务窗口在终点查询和官方检查之前截止。guest图按各自首点对齐，恢复空缺断线；不能与host时钟相减。\n')
            normalized.append(row)
    assert len(normalized)==9 and sum(r['actual_actions'] for r in normalized)==1071
    summaries = {s: {key: stats([r[key] for r in normalized if r['schedule']==s]) for key in METRICS}
                 for s in SCHEDULES}
    result = dict(status='PASS', batch=str(batch.relative_to(ROOT)), runs=normalized, statistics=summaries,
                  metric_labels=METRICS, initial_reference=reference, old_smoke_included=False,
                  actual_actions=1071, logical_actions=1701, guest_instances=63)
    write(out/'summary.json', result)
    scalar_keys = [key for key, val in normalized[0].items() if not isinstance(val, (dict, list))]
    with (out/'runs.tsv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=scalar_keys, delimiter='\t', extrasaction='ignore')
        writer.writeheader(); writer.writerows(normalized)
    lines = ['# Prettier #6604三调度各三次对照', '',
             '9次新运行、63个guest、1071个实际动作／1701个逻辑动作全部通过原验收和采集检查，每实例官方结果仍为0通过2失败。旧冒烟和已有GRPO重复不混入本次统计。', '',
             '三轮顺序为GRPO→BPO→TVCache、BPO→TVCache→GRPO、TVCache→GRPO→BPO，每次创建新私有服务及cgroup，复用同一固定本地镜像和七角色任务。普通计数0.1秒，逐VMM周期PSS1秒，默认回收，不加压、不清缓存、不强制驻留、不联网及使用代理。', '',
             '## 四项主指标', '',
             '下表为中位数（最小～最大）；均值、样本标准差与全部原值见后表和summary.json。PSS/N是全部路径及下载结束、官方检查与删除前的七VMM共同短窗口PSS之和除7，不是峰值或整个沙箱部署成本。', '',
             '| 调度 | 实际／逻辑动作数，每次 | save／restore，每次 | 终点PSS/N，MiB/guest | 初始19文件P1，MiB | 调度执行窗口，秒 | 实际工具累计，秒 |',
             '| --- | --- | --- | --- | --- | --- | --- |']
    def cell(v): return f"{v['median']:.3f}（{v['minimum']:.3f}～{v['maximum']:.3f}）"
    main_keys = ('vmm_pss_endpoint_per_guest_mib', 'initial_lower_mincore_p1_mib', 'schedule_run_seconds', 'actual_tool_sum_seconds')
    for s in SCHEDULES:
        lines.append('| '+s.upper()+f" | {189 if s=='grpo' else 84}/189 | {'0/0' if s=='grpo' else '2/6'} | "+' | '.join(cell(summaries[s][k]) for k in main_keys)+' |')
    lines += ['', 'BPO复用两次保存的前缀，六分支后缀并发；本项目TVCache复用确定性前缀树、按子树顺序推进，没有动态容量预算或淘汰。两者实际84步、逻辑189步。它们不是同样次数实际执行的加速对照，不将工具累计折算到189步，也不把不同分支路径改成同一29步。', '',
              '## 均值及波动', '', '| 指标 | GRPO均值±标准差 | BPO均值±标准差 | TVCache均值±标准差 |', '| --- | --- | --- | --- |']
    for key, label in METRICS.items():
        lines.append('| '+label+' | '+' | '.join(f"{summaries[s][key]['mean']:.3f} ± {summaries[s][key]['stdev']:.3f}" for s in SCHEDULES)+' |')
    lines += ['', '## 每次运行与曲线', '',
              '| 顺序 | 调度 | PSS/N，MiB | P1，MiB | 调度窗口，秒 | 工具累计，秒 | 整轮，秒 | 最小可用盘，GiB |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    by_pair = {(r['round'], r['schedule']): r for r in normalized}
    for order, planned in enumerate(run_plan(), 1):
        r = by_pair[planned['round'], planned['schedule']]; name = r['curves']['directory']
        lines.append(f"| {order} | [{name}]({name}/REPORT.md) | {r['vmm_pss_endpoint_per_guest_mib']:.3f} | {r['initial_lower_mincore_p1_mib']:.3f} | {r['schedule_run_seconds']:.3f} | {r['actual_tool_sum_seconds']:.3f} | {r['controller_seconds']:.3f} | {r['minimum_disk_gib']:.3f} |")
    lines += ['', '每次子报告包含服务组current／file和七VMM PSS曲线，以及逐角色Cached小图。host采用保存的host墙钟，guest各自按首点对齐；恢复间隙断线，不把各VMM异步峰值相加。曲线包含官方检查和收尾；表中调度窗口在这些操作之前截止。慢组不剔除，3次只给描述性波动，不输出置信区间或通用加速倍数。', '',
              '## 文件记账覆盖与额外对象', '',
              '| 运行 | 19文件P1，MiB | P1前／后file，MiB | P1减较大file的净不足，MiB | 额外活跃下层数量／长度，MiB |',
              '| --- | ---: | --- | ---: | --- |']
    for r in normalized:
        lines.append(f"| {r['curves']['directory']} | {r['initial_lower_mincore_p1_mib']:.3f} | {r['service_cgroup_file_before_mib']:.3f} / {r['service_cgroup_file_after_mib']:.3f} | {r['residency_minus_max_file_mib']:.3f} | {r['additional_active_lower_count']} / {r['additional_active_lower_logical_size_mib']:.6f} |")
    lines += ['', '19个初始不可变下层对象由实际VMM drive及rootfs解析确认，逐运行按设备号:inode去重，7个guest均引用该集合；9次的内容标识及大小一致。P1是指定文件的全局驻留，没有P0，不能当任务新读入量或整体guest缓存。额外快照下层只记文件长度，驻留未知；upper、VM内存后备及其他未覆盖对象容量保持未知，不补零。', '',
              '本入口只计私有服务cgroup，不含其创建前的输入复制与外部控制程序。mincore大于file时，净不足显示覆盖不全；它不是已查明的组外容量或current修正量。没有不足也不证明完整，其他文件记账可能掩盖遗漏。不把current、PSS、mincore、file或guest Cached相加构造总物理内存，不据此给部署密度或共享物理节省结论。', '',
              '## 时间、成本及完整性', '',
              '调度执行窗口从调用调度器前、创建guest前，到所有路径及下载返回，包含创建准备、保存恢复与等待。动作及下载窗口从第一次段调用前开始；工具累计仅实际guest动作计时之和，存在并发重叠。控制程序整轮再含终点查询、官方检查和删除；外层调用至验收另含分卷、输入复制、服务启停及卷清理。原始纳秒字段保持，不混用这些时间层。', '',
              '| 运行 | 保存各次调用，秒 | 恢复各次调用，秒 | 终点观测墙钟／CPU，秒 | PSS墙钟／CPU，秒 | P1墙钟／CPU，秒 |',
              '| --- | --- | --- | --- | --- | --- |']
    for r in normalized:
        save = '、'.join(f"{v['seconds']:.3f}" for v in r['save_calls']) or '无'
        restore = '、'.join(f"{v['seconds']:.3f}" for v in r['restore_calls']) or '无'
        lines.append(f"| {r['curves']['directory']} | {save} | {restore} | {r['endpoint_observation_seconds']:.3f} / {r['endpoint_cpu_seconds']:.3f} | {r['pss_query_seconds']:.3f} / {r['endpoint_pss_cpu_seconds']:.3f} | {r['p1_query_seconds']:.3f} / {r['endpoint_p1_cpu_seconds']:.3f} |")
    lines += ['', '保存／恢复为原调用方计时，保存不含之前停止采集器，恢复不含随后准备与状态验证；并发恢复耗时不能求和当恢复窗口。终点观测在任务窗口外，周期采集仍可能竞争任务资源；本次没有关闭采样组，不从这些成本推导采样减速率。', '',
              '每次原入口检查动作顺序、预期退出、关键输出、补丁／恢复状态、官方结果及清理；离线再次核对实际steps计数和工具累计、七root VMM读权限／身份／PSS分母、19文件去重及内容标识、0.1秒／1秒采集配置。源服务已回到停止状态，本轮RAM输入释放，九个成功卷及私有服务已清理，其他工作区不动。', '',
              '### 原始路径', '']
    for r in normalized:
        lines.append('- '+r['curves']['directory']+'：`'+str(Path(r['raw']).relative_to(ROOT))+'`。')
    lines += ['', '批次日志和逐次错误记录位于`'+str(batch.relative_to(ROOT))+'`。所有慢值保留，异常原因不通过静默重跑消除。若需核对某次长耗时，应先按保存的events分段，不用整轮差直接归因调度或采样。', '',
              '## 下一步建议', '',
              '先检查本表和曲线是否满足三调度四项指标的使用需求。若要推广到第二个任务，先离线确认该任务的分支路径和可复用前缀；最小范围只适配一个任务，以三种调度保持任务语义和各指标可取为完成条件，不直接把单路径多开七次当作BPO或TVCache。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    out.chmod(0o755)
    for path in out.rglob('*'): path.chmod(0o755 if path.is_dir() else 0o644)
    write_timing_notes(batch, out, result)
    return result


def write_timing_notes(batch, out, result):
    details = []
    for row in result['runs']:
        raw = Path(row['raw']); task = read(raw/'result.json'); events = read(raw/'events.json')
        end = task['schedule_smoke_metrics']['original_paths_end_monotonic_ns']
        deletes = [e for e in events if e['stage'].endswith('-delete-owned')]
        assert len(deletes)==7
        details.append(dict(round=row['round'], schedule=row['schedule'], raw=row['raw'],
                            paths_to_checks_end_seconds=(task['rollout_end_monotonic_ns']-end)/1e9,
                            outside_rollout_seconds=task['controller_elapsed_ns']/1e9-
                            (task['rollout_end_monotonic_ns']-task['rollout_start_monotonic_ns'])/1e9,
                            longest_delete_call_seconds=max(e['duration_ns'] for e in deletes)/1e9,
                            save_seconds=[r['seconds'] for r in row['save_calls']]))
    result['timing_observations'] = details
    write(out/'summary.json', result)
    grpo = [r for r in details if r['schedule']=='grpo']
    tvcache = [r for r in details if r['schedule']=='tvcache']
    text = ['## 单列的较慢记录与资源变化', '',
            '| 运行 | 路径完成至最终检查结束，秒 | rollout以外其余时间，秒 | 最长删除调用，秒 |',
            '| --- | ---: | ---: | ---: |']
    for row in details:
        text.append(f"| r{row['round']}-{row['schedule']} | {row['paths_to_checks_end_seconds']:.3f} | {row['outside_rollout_seconds']:.3f} | {row['longest_delete_call_seconds']:.3f} |")
    text += ['', f"GRPO第三次的整轮较长，但路径完成至检查结束仍为{grpo[2]['paths_to_checks_end_seconds']:.3f}秒，与前两次接近。其余时间增至{grpo[2]['outside_rollout_seconds']:.3f}秒；其中一个删除调用为{grpo[2]['longest_delete_call_seconds']:.3f}秒，前两次最长删除为{grpo[0]['longest_delete_call_seconds']:.3f}／{grpo[1]['longest_delete_call_seconds']:.3f}秒。已定位长等待在删除调用，内部阻塞原因没有分段证据，不归因任务动作或采样。", '',
             f"TVCache第三次第一次保存调用为{tvcache[2]['save_seconds'][0]:.3f}秒，前两次为{tvcache[0]['save_seconds'][0]:.3f}／{tvcache[1]['save_seconds'][0]:.3f}秒。其调度窗口和工具累计也较长，保留原值；保存调用等待是已观察到的一部分，不认定是全部变慢的唯一原因。", '',
             '运行中共享盘可用空间变化较大，每组资源前检查及运行中保护均通过，最小值见逐次表。磁盘变化与慢调用在同一批次出现，只作背景，不推断他人写盘或I/O争用的因果。原始事件位于各raw目录的events.json／events.tsv，批次资源读数在`'+str(batch.relative_to(ROOT))+'/r*-resources.json`，不为解释波动追加监控或重跑。', '']
    report_path = out/'REPORT.md'; content = report_path.read_text()
    marker = '### 原始路径'
    assert marker in content and '## 单列的较慢记录与资源变化' not in content
    report_path.write_text(content.replace(marker, '\n'.join(text)+'\n'+marker))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(); result = report(args.batch.resolve(), args.output.resolve())
    print('status='+result['status']+' result_dir='+str(args.output))
