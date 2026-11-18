"""Explain three observed variations from saved records only; no live queries."""
import argparse
import csv
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIB = 2**20


def read(path):
    return json.loads(Path(path).read_text())


def table(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def vector(row):
    return {key: int(row[key])/MIB for key in
            ('current', 'file', 'anon', 'kernel', 'shmem', 'file_dirty', 'file_writeback')}


def c4(row, index):
    raw = ROOT/row['raw']; result = read(raw/'result.json')
    paths = list((raw/'host-vmm').rglob('host-vmm.tsv')); assert len(paths) == 1
    samples = [r for r in table(paths[0]) if r['pss_kib']]
    assert all(r['pid'] == samples[0]['pid'] and r['start_ticks'] == samples[0]['start_ticks'] for r in samples)
    peak = max(samples, key=lambda r: int(r['pss_kib']))
    change, before, after = min(
        ((int(b['pss_kib'])-int(a['pss_kib']), a, b) for a, b in zip(samples, samples[1:])),
        key=lambda value: value[0])
    endpoint = result['endpoint_metrics']['pss']; assert len(endpoint['processes']) == 1
    process = endpoint['processes'][0]
    assert str(process['identity']['pid']) == samples[0]['pid']
    assert process['identity']['start_ticks'] == samples[0]['start_ticks']
    assert endpoint['total_bytes']/MIB == row['metrics']['endpoint_pss_per_guest_mib']
    session = read(raw/'runtime-monitor.json')['sessions'][0]
    memory_path = raw/session['raw_directory']/'guest-memory.tsv'; memory = table(memory_path)
    return dict(index=index, raw=row['raw'], host_table=str(paths[0].relative_to(ROOT)),
                guest_table=str(memory_path.relative_to(ROOT)),
                replay_start_ns=result['replay_start_monotonic_ns'],
                replay_end_ns=result['replay_end_monotonic_ns'],
                endpoint_start_ns=endpoint['start_monotonic_ns'],
                endpoint_mib={key: process[key]/1024 for key in
                              ('pss_kib', 'pss_anon_kib', 'pss_file_kib', 'pss_shmem_kib', 'rss_kib')},
                endpoint_duration_seconds=(endpoint['end_monotonic_ns']-endpoint['start_monotonic_ns'])/1e9,
                periodic_peak_mib=int(peak['pss_kib'])/1024,
                periodic_peak_replay_seconds=(int(peak['monotonic_ns'])-result['replay_start_monotonic_ns'])/1e9,
                periodic_last_mib=int(samples[-1]['pss_kib'])/1024,
                last_to_endpoint_seconds=(endpoint['start_monotonic_ns']-int(samples[-1]['monotonic_ns']))/1e9,
                largest_adjacent_drop_mib=-change/1024,
                drop_interval_seconds=(int(after['monotonic_ns'])-int(before['monotonic_ns']))/1e9,
                drop_replay_seconds=(int(after['monotonic_ns'])-result['replay_start_monotonic_ns'])/1e9,
                guest_cached_peak_mib=max(int(r['Cached_kib']) for r in memory)/1024,
                guest_cached_last_mib=int(memory[-1]['Cached_kib'])/1024,
                guest_anon_peak_mib=max(int(r['AnonPages_kib']) for r in memory)/1024,
                guest_anon_last_mib=int(memory[-1]['AnonPages_kib'])/1024,
                lower_p1_mib=row['metrics']['lower_p1_mib'])


def matplotlib_run(row, index):
    raw = ROOT/row['raw']; result = read(raw/'result.json'); outer = read(raw.parent/'result.json')
    samples = table(ROOT/row['host_table']); peak = max(samples, key=lambda r: int(r['current']))
    assert int(peak['current'])/MIB == row['metrics']['current_collector_peak_mib']
    start, end = result['replay_start_monotonic_ns'], result['replay_end_monotonic_ns']
    assert start <= int(peak['monotonic_ns']) <= end
    phases = []
    bounds = [int(samples[0]['monotonic_ns']), result['schedule_start_monotonic_ns'], start,
              end, result['schedule_end_monotonic_ns'], int(samples[-1]['monotonic_ns'])+1]
    for name, a, b in zip(('服务准备', '创建及采集准备', '动作及下载', '动作后检查', '终点查询及清理'), bounds, bounds[1:]):
        selected = [r for r in samples if a <= int(r['monotonic_ns']) < b]
        assert selected
        highest = max(selected, key=lambda r: int(r['current']))
        phases.append(dict(phase=name, duration_seconds=(b-a)/1e9, samples=len(selected),
                           current_peak_mib=int(highest['current'])/MIB))
    nearest = min(samples, key=lambda r: abs(int(r['monotonic_ns'])-start))
    return dict(index=index, raw=row['raw'], host_table=row['host_table'],
                replay_start_ns=start, replay_end_ns=end, phases=phases,
                peak=vector(peak), peak_replay_seconds=(int(peak['monotonic_ns'])-start)/1e9,
                before_guest=vector(outer['host_file_creation']['before_guest_counters']),
                near_replay_start=vector(nearest),
                near_replay_offset_seconds=(int(nearest['monotonic_ns'])-start)/1e9,
                preparation_sync=outer['external_workload_preparation_sync'],
                endpoint_pss_mib=row['metrics']['endpoint_pss_per_guest_mib'],
                lower_p1_mib=row['metrics']['lower_p1_mib'])


def prettier(row, index):
    raw = ROOT/row['raw']; result = read(raw/'result.json'); events = read(raw/'events.json')
    begin = result['rollout_start_monotonic_ns']; first = result['first_tool_start_monotonic_ns']
    paths_end = result['original_paths_end_monotonic_ns']; rollout_end = result['rollout_end_monotonic_ns']
    endpoint = result['schedule_smoke_metrics']; endpoint_end = endpoint['end_monotonic_ns']
    task = (paths_end-begin)/1e9; assert task == row['timing_seconds']['task']
    phases = dict(before_first_action=(first-begin)/1e9,
                  actions_download=(paths_end-first)/1e9,
                  endpoint_query=(endpoint_end-paths_end)/1e9,
                  verification=(rollout_end-endpoint_end)/1e9,
                  outside_rollout=result['controller_elapsed_ns']/1e9-(rollout_end-begin)/1e9)
    assert all(value >= 0 for value in phases.values())
    assert abs(sum(phases.values())-row['timing_seconds']['controller']) < 1e-8
    official = []
    for event in events:
        match = re.fullmatch(r'\d+-official-(.+)', event['stage'])
        if not match: continue
        path = raw/(event['stage']+'.stdout')
        suite_times = re.findall(r'^Time:\s+(\d+(?:\.\d+)?)s', path.read_text(), re.M)
        assert len(suite_times) == 1
        official.append(dict(role=match.group(1), seconds=event['duration_ns']/1e9,
                             jest_reported_seconds=float(suite_times[0]), stdout=str(path.relative_to(ROOT)),
                             start_ns=event['start_monotonic_ns'],
                             end_ns=event['start_monotonic_ns']+event['duration_ns']))
    assert len(official) == 7
    return dict(index=index, raw=row['raw'], phases=phases, task_seconds=task,
                controller_seconds=row['timing_seconds']['controller'],
                tool_seconds=row['timing_seconds']['tool'], official=official,
                official_envelope_seconds=(max(r['end_ns'] for r in official)-min(r['start_ns'] for r in official))/1e9)


def plots(value, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), layout='constrained')
    for run in value['c4']:
        samples = [r for r in table(ROOT/run['host_table']) if r['pss_kib']]
        t = [(int(r['monotonic_ns'])-run['replay_start_ns'])/1e9 for r in samples]
        axes[0].plot(t, [int(r['pss_kib'])/1024 for r in samples], label=f"Run {run['index']}")
        axes[0].scatter([(run['endpoint_start_ns']-run['replay_start_ns'])/1e9],
                        [run['endpoint_mib']['pss_kib']], marker='x')
        memory = table(ROOT/run['guest_table']); a = int(memory[0]['elapsed_ns'])
        axes[1].plot([(int(r['elapsed_ns'])-a)/1e9 for r in memory],
                     [int(r['Cached_kib'])/1024 for r in memory], label=f"Run {run['index']}")
    axes[0].set(xlabel='Seconds from host replay request; x = independent endpoint PSS',
                ylabel='VMM proportional resident memory PSS, MiB')
    axes[1].set(xlabel='Seconds from first guest sample (separate clock; not aligned with host)',
                ylabel='Guest logical file cache Cached, MiB')
    for ax in axes: ax.grid(alpha=.2); ax.legend()
    fig.suptitle('C4: host VMM PSS and guest Cached are different quantities')
    fig.savefig(out/'c4-variation.png', dpi=140); plt.close(fig)
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), layout='constrained', sharex=True)
    for run in value['matplotlib']:
        samples = table(ROOT/run['host_table'])
        t = [(int(r['monotonic_ns'])-run['replay_start_ns'])/1e9 for r in samples]
        for ax, key in zip(axes, ('current', 'file', 'file_dirty')):
            ax.plot(t, [int(r[key])/MIB for r in samples], label=f"Run {run['index']}")
    for ax, label in zip(axes, ('Group charge memory.current\nMiB',
                               'Group file charge (memory.stat file)\nMiB', 'Group dirty file pages file_dirty\nMiB')):
        ax.set_ylabel(label); ax.axvline(0, color='black', linestyle='--', linewidth=.8); ax.grid(alpha=.2); ax.legend()
    axes[-1].set_xlabel('Seconds from replay request; negative time = preparation')
    fig.suptitle('Matplotlib: file-charge difference already exists before replay')
    fig.savefig(out/'matplotlib-variation.png', dpi=140); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 4), layout='constrained')
    bottom = [0.] * 3
    for key, label in [('before_first_action', 'Creation/setup'), ('actions_download', 'Actions/download'),
                       ('endpoint_query', 'Endpoint query'), ('verification', 'Final checks'),
                       ('outside_rollout', 'Outside rollout (mostly cleanup)')]:
        heights = [r['phases'][key] for r in value['prettier']]
        ax.bar([1, 2, 3], heights, bottom=bottom, label=label)
        bottom = [a+b for a, b in zip(bottom, heights)]
    ax.set(xlabel='Prettier run', ylabel='Controller elapsed wall time, seconds', xticks=[1, 2, 3])
    ax.legend(bbox_to_anchor=(1.02, 1), loc='upper left'); ax.grid(axis='y', alpha=.2)
    fig.savefig(out/'prettier-phases.png', dpi=140); plt.close(fig)


def write_report(value, out):
    c = value['c4']; m = value['matplotlib']; p = value['prettier']
    span = max(r['endpoint_mib']['pss_kib'] for r in c)-min(r['endpoint_mib']['pss_kib'] for r in c)
    delta = value['matplotlib_peak_comparison']['delta_mib']
    pd = value['prettier_run2_minus_run1']
    lines = ['# 八任务重复结果中的三项波动：离线分解', '',
             '本次只读已有数据，未启动服务、沙箱、驻留查询或新采样。分析C4四次、Matplotlib三次、Prettier三次，共10份记录，不改变八任务原统计、验收或历史数值。轮号按原汇总内的逐任务记录顺序。', '',
             '## 结论', '',
             '- Prettier第二次整轮较慢，主要落在189个动作结束后的官方检查；原工具累计反而更短。已定位慢调用，但缺少调用内部计时，具体阻塞原因仍未知。',
             '- Matplotlib第二次current较高，主要对应file记账差；较大的差距在创建及准备阶段已经形成，不能解释成任务进程自身多用同等RAM。缺少逐文件驻留及归属，不能再分配给某个具体文件。',
             '- C4的终点PSS差主要在匿名映射驻留，不是共享比例变化或指定lower驻留变化。运行中已有大幅下降；没有回收事件证据，不能据此认定DAMON或某个回收机制。', '',
             '## 1. C4：PSS的差异来自哪一类映射', '',
             'PSS是进程映射到的驻留页按共享比例分摊后的容量，取自已绑定VMM的smaps_rollup；Pss_Anon、Pss_File、Pss_Shmem分别是匿名、文件、共享内存映射部分。N=1，所以PSS/N就是单VMM PSS。', '',
             '| 次数 | 终点PSS，MiB | Pss_Anon，MiB | Pss_File，MiB | Pss_Shmem，MiB | 周期PSS峰值，MiB | 最后周期点与终点的容量差绝对值，MiB |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in c:
        e = r['endpoint_mib']
        lines.append(f"| {r['index']} | {e['pss_kib']:.3f} | {e['pss_anon_kib']:.3f} | {e['pss_file_kib']:.3f} | {e['pss_shmem_kib']:.3f} | {r['periodic_peak_mib']:.3f} | {abs(r['periodic_last_mib']-e['pss_kib']):.3f} |")
    lines += ['', f'终点最大与最小相差{span:.3f} MiB，匿名部分差额相同；文件映射与共享内存映射各次一致。RSS每次仅比PSS多0.054688 MiB，说明这次差异不是共享分摊比例变动造成。四次指定9个lower文件的P1均为3270.273438 MiB，它与VMM匿名驻留不是同一个量。', '',
              '终点PSS查询耗时0.028～0.043秒，周期PSS每1秒一次。最后一个周期PSS点与独立终点查询相隔不超过0.818秒，容量差不超过0.356 MiB；790.953 MiB的跨轮差异不是只出现在独立终点查询中。周期峰值也有明显差异，不能用终点代表任务峰值。', '',
              '| 次数 | 最大相邻PSS下降，MiB | 两点间隔，秒 | 下降后点距host动作请求，秒 | guest Cached峰值／末点，MiB | guest AnonPages峰值／末点，MiB |',
              '| --- | ---: | ---: | ---: | --- | --- |']
    for r in c:
        lines.append(f"| {r['index']} | {r['largest_adjacent_drop_mib']:.3f} | {r['drop_interval_seconds']:.6f} | {r['drop_replay_seconds']:.3f} | {r['guest_cached_peak_mib']:.3f} / {r['guest_cached_last_mib']:.3f} | {r['guest_anon_peak_mib']:.3f} / {r['guest_anon_last_mib']:.3f} |")
    lines += ['', '这些是相邻采样之间的净下降，不是回收事件大小或精确发生时刻。guest的Cached与AnonPages在运行中也有明显变化，说明终点与负载过程中不同。guest Cached只是guest逻辑文件缓存；guest RAM在host常表现为VMM匿名映射，host Pss_Anon不能等同guest AnonPages，也不能逐MiB和guest Cached对账。', '',
              '现有guest轻量文件只有meminfo计数，没有DAMON回收计数、balloon变化、逐页释放或host回收事件。因此已确认的是映射类别和动态变化，具体回收机制、各机制贡献仍未知。图的host上图按host动作请求对齐，guest下图按自身首个采样点对齐，不混减两端单调时钟，也不据图推断精确同步因果。', '',
              '[C4过程图](c4-variation.png)', '',
              '## 2. Matplotlib：较高的current在什么时候形成', '',
              'current覆盖准备、控制程序、私有服务及guest所在的外层实验组。下表的file、anon、kernel、dirty取自current峰值所在的同一行，避免将各列不同时刻的峰值拼在一起。dirty是file的子集，不再相加。', '',
              '| 次数 | current峰值，MiB | 同点file，MiB | 同点anon，MiB | 同点kernel，MiB | 同点file_dirty，MiB | 峰值距动作请求，秒 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in m:
        e = r['peak']
        lines.append(f"| {r['index']} | {e['current']:.3f} | {e['file']:.3f} | {e['anon']:.3f} | {e['kernel']:.3f} | {e['file_dirty']:.3f} | {r['peak_replay_seconds']:.3f} |")
    lines += ['', f"第二次减第三次：current多{delta['current']:.3f} MiB，file多{delta['file']:.3f} MiB，anon少{-delta['anon']:.3f} MiB，kernel少{-delta['kernel']:.3f} MiB。相减后仍有{delta['unreconciled_counter_delta']:.3f} MiB未由这三项差额对齐；current与stat是不同读取及更新口径，不要求严格恒等，也不把余差命名为某类实际内存。此处只定位记账类别，不等同逐页归属。", '',
              '| 次数 | 创建前current／file，MiB | 动作开始附近current／file，MiB | 该点dirty，MiB | 该采样点相对动作请求，秒 | 初始lower P1，MiB |',
              '| --- | --- | --- | ---: | ---: | ---: |']
    for r in m:
        a, b = r['before_guest'], r['near_replay_start']
        lines.append(f"| {r['index']} | {a['current']:.3f} / {a['file']:.3f} | {b['current']:.3f} / {b['file']:.3f} | {b['file_dirty']:.3f} | {r['near_replay_offset_seconds']:+.6f} | {r['lower_p1_mib']:.3f} |")
    start_gap = m[1]['near_replay_start']['current']-m[2]['near_replay_start']['current']
    lines += ['', f'第二、三次创建前current仅差{m[1]["before_guest"]["current"]-m[2]["before_guest"]["current"]:.3f} MiB，动作开始附近已相差{start_gap:.3f} MiB。所选点距动作请求均小于0.033秒；差距在动作开始附近就已存在，而非只在动作后期出现。三次整体current最高点均在动作窗口内，但高低差异并非全由任务动作新增。', '',
              '三次都保存了输入准备结束后的文件系统同步成功记录，创建前dirty分别为'+ '、'.join(f"{r['before_guest']['file_dirty']:.3f}" for r in m)+' MiB；创建及准备之后，动作开始时dirty又到约6 GiB。这些是同步之后的新活动，不能说此前同步未执行，也不能把它当整个任务期间保持干净的保证。第二次峰值仍有2023.645 MiB脏页，另外两次为16.016／6.012 MiB；三次file_writeback在各自峰值点均为0。单点0不代表期间没有写回，脏页较多也不足以证明峰值差全由写回节奏造成。', '',
              '指定10个lower文件的P1各次相同，终点VMM PSS只有641.398／651.277／678.672 MiB。它们没有覆盖所有upper、loop后备或转换期间缓存；现有组计数不能把额外file容量准确分配给这些对象。确定逐文件来源还需要相应时刻的对象及驻留／归属记录，本轮未保存，故不估算。', '',
              '| 阶段 | 第一次current峰值，MiB | 第二次，MiB | 第三次，MiB |',
              '| --- | ---: | ---: | ---: |']
    for i, phase in enumerate(m[0]['phases']):
        lines.append('| '+phase['phase']+' | '+' | '.join(f"{r['phases'][i]['current_peak_mib']:.3f}" for r in m)+' |')
    lines += ['', '阶段边界来自保存的schedule_start、replay_start、replay_end、schedule_end；“创建及采集准备”包含guest创建、脚本准备与启动采集，未冒充纯VMM创建开销。不同阶段的峰值不能相加。', '',
              '[Matplotlib阶段曲线](matplotlib-variation.png)', '',
              '## 3. Prettier：第二次整轮为什么较慢', '',
              '任务窗口到189个原动作及下载结束，不含随后的独立检查和官方测试。下面按保存的host时钟拆分互不重叠区间；五列相加严格回到控制程序整轮。整轮中“其余时间”包括rollout开始前及结束后的工作，主要是采集停止、删除guest及控制程序收尾，不当纯删除耗时。', '',
              '| 次数 | 首动作前，秒 | 动作及下载，秒 | 终点查询，秒 | 最终检查，秒 | 其余时间，秒 | 整轮，秒 | 工具累计，秒 |',
              '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in p:
        lines.append('| '+str(r['index'])+' | '+' | '.join(f'{v:.3f}' for v in r['phases'].values())+f" | {r['controller_seconds']:.3f} | {r['tool_seconds']:.3f} |")
    lines += ['', f"第二次比第一次整轮多{pd['controller_seconds']:.3f}秒，其中最终检查多{pd['phases']['verification']:.3f}秒，占整轮增量{pd['phases']['verification']/pd['controller_seconds']*100:.2f}%；终点查询只多{pd['phases']['endpoint_query']:.3f}秒，动作及下载反而少{-pd['phases']['actions_download']:.3f}秒，工具累计少{p[0]['tool_seconds']-p[1]['tool_seconds']:.3f}秒。因此不能用整轮变慢推断原任务动作变慢，或把全部增量算作采样减速。", '',
              '| 角色 | 第一次官方调用，秒 | 第二次，秒 | 第三次，秒 | 三次Jest自报时间，秒 |',
              '| --- | ---: | ---: | ---: | --- |']
    for role in sorted(r['role'] for r in p[0]['official']):
        records = [next(x for x in r['official'] if x['role'] == role) for r in p]
        lines.append('| '+role+' | '+' | '.join(f"{r['seconds']:.3f}" for r in records)+' | '+ ' / '.join(f"{r['jest_reported_seconds']:.3f}" for r in records)+' |')
    lines += ['', '七个官方调用并发，不能将调用耗时求和当墙钟；其最早开始至最晚返回的跨度分别为'+ '、'.join(f"{r['official_envelope_seconds']:.3f}" for r in p)+'秒。第二次early_1与backbone分别到9.910／9.232秒，是该阶段的长调用。', '',
              '这两个调用的Jest日志自报Time仍为0.506／0.540秒；第一次对应0.540／0.459秒。可确认长等待未体现为Jest自报测试时间大幅增加。调用还包括CLI、通信、测试补丁检查与应用、测试启动、输出获取和日志处理；Jest自报Time不是整个guest命令时间，二者相减不能直接命名为网络或采样开销。缺少这些内部阶段的时间戳，具体阻塞点未知。', '',
              '官方0通过2失败结果与原动作验收均保持，慢组完整保留。原单轮PSS查询和P1查询都在终点查询列内；没有新的采样开关对照，本分析不产生新的减速率。', '',
              '[Prettier整轮阶段图](prettier-phases.png)', '',
              '## 证据与复现', '',
              '全部计算值、同点记账分量、阶段边界、逐guest官方调用原文路径见[summary.json](summary.json)。本轮派生文件另存，不覆盖原始记录或八任务统计。', '',
              '```bash', 'python3 -B scripts/report_eight_task_variation.py \\',
              '  --source results/eight-task-repeats-20261106/summary.json \\',
              '  --output results/eight-task-variation-new', '```', '',
              '输出目录必须不存在。脚本只读保存的JSON／TSV／stdout，生成派生统计、本文和三张图，不查询/proc、不调用Docker、不联网。', '',
              '原始目录：', '']
    for task, records in [('C4', c), ('Matplotlib', m), ('Prettier', p)]:
        lines += [f"- {task}第{r['index']}次：`{r['raw']}`。" for r in records]
    lines += ['', '直接源码依据：`scripts/host_sandbox_monitor.py:read`从smaps_rollup读取Pss及分类字段；`scripts/run_prettier_sampling_pair.py:inspect`定义任务／动作／整轮口径；`scripts/run_prettier6604_grpo.py:final_check`及`exp1_official_task.py:guest_command`说明最终检查包含的操作。对应运行的源码快照保存在各原始目录上一级的`code/`，动态阶段以原始result及events为准。', '',
              '## 完成状态与下一步建议', '',
              '本次离线分析完成：三项均已定位到可核查的类别或阶段，未知根因列在相应段落。不追加实跑、不剔除慢值，当前八任务数据可继续按既定口径使用。', '',
              '下一步建议结束当前重复采集阶段，使用已有25份记录，不为缩小波动继续加跑。若后续要做正式调度比较，再确认此前Prettier七沙箱GRPO／BPO／TVCache各3次、共9次的范围；问题是相同负载下三种调度的内存和耗时有何差异，以原验收、PSS/N、文件驻留、任务窗口及工具累计统计完整为完成条件。该计划仍需确认，不自动启动。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')


def report(source, out):
    saved = read(source); assert saved['status'] == 'PASS'
    groups = {task: [r for r in saved['runs'] if r['task'] == task] for task in
              ('c4', 'matplotlib__matplotlib-24177', 'prettier6604')}
    assert [len(r) for r in groups.values()] == [4, 3, 3]
    value = dict(status='PASS', scope='Offline analysis of ten saved runs; no new collection',
                 source=str(source.relative_to(ROOT)),
                 c4=[c4(r, i) for i, r in enumerate(groups['c4'], 1)],
                 matplotlib=[matplotlib_run(r, i) for i, r in enumerate(groups['matplotlib__matplotlib-24177'], 1)],
                 prettier=[prettier(r, i) for i, r in enumerate(groups['prettier6604'], 1)])
    high = max(value['matplotlib'], key=lambda r: r['peak']['current'])
    low = min(value['matplotlib'], key=lambda r: r['peak']['current'])
    delta = {k: high['peak'][k]-low['peak'][k] for k in high['peak']}
    delta['unreconciled_counter_delta'] = delta['current']-delta['file']-delta['anon']-delta['kernel']
    value['matplotlib_peak_comparison'] = dict(high=high['index'], low=low['index'], delta_mib=delta)
    a, b = value['prettier'][:2]
    value['prettier_run2_minus_run1'] = dict(controller_seconds=b['controller_seconds']-a['controller_seconds'],
                                           phases={k: b['phases'][k]-a['phases'][k] for k in a['phases']})
    out.mkdir(parents=True, exist_ok=False)
    plots(value, out)
    (out/'summary.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    write_report(value, out)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'results/eight-task-repeats-20261106/summary.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.source.resolve(), args.output.resolve())
    print('status='+result['status']+' result_dir='+str(args.output))
