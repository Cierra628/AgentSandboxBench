"""Audit saved eight-task records and report same-profile repeats; no live queries."""
import argparse
import csv
import json
from pathlib import Path
import re
import statistics

from report_new_sampling_summary import ROOT, build
from external_task_metrics import validate_result
from run_prettier_sampling_pair import inspect as inspect_prettier
from run_light_monitor_controls import SOURCE_ID


def read(path):
    return json.loads(Path(path).read_text())


def table(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def external_activity_audit(storage):
    """Reconcile this saved cleanup-only failure, retaining the original FAIL."""
    storage = Path(storage); outer = read(storage/'result.json')
    task = read(storage/'external-workload/result.json')
    before = read(storage/'firecracker-before.json'); after = read(storage/'firecracker-after.json')
    assert outer['status']=='FAIL' and task['status']=='PASS'
    assert not outer.get('error') and all(v=='PASS' for v in outer['checks'].values())
    expected = dict(owned_container_removed=True, mount_and_loop_removed=True, successful_fixture_removed=True,
                    original_firecracker_identities_unchanged=False, original_service_unchanged=True)
    assert outer['cleanup']==expected and before!=after
    # This saved run used the verified fixed source. Both lists contain only
    # foreign docker scopes; do not infer this from the current /proc state.
    assert outer['source_service_name']=='asb-source-rebuilt-20261018T160925Z-2726362'
    suffix='/docker-'+SOURCE_ID+'.scope'
    assert all(suffix not in row['cgroup'] for row in before+after)
    assert all('/docker-' in row['cgroup'] for row in before+after)
    validate_result(task, pss_interval_seconds=1)
    assert not (storage/'volume.ext4').exists() and not (storage/'mount').is_mount()
    return dict(status='PASS', original_outer_status='FAIL', source_container_id=SOURCE_ID,
                source_vmm_before=[], source_vmm_after=[], original_service_unchanged=True,
                before=before, after=after, raw=str((storage/'external-workload').relative_to(ROOT)),
                basis='Owned cleanup and source service unchanged; only foreign VMMs changed; global exclusivity is not required',
                original_files_modified=False)


def collect(task, raw, report):
    raw = (ROOT / raw).resolve(); report = (ROOT / report).resolve()
    storage = raw.parent; t = read(raw / 'result.json'); outer = read(storage / 'result.json')
    recovered = None
    if outer['status']!='PASS' and (report/'external-activity-audit.json').exists():
        recovered = external_activity_audit(storage)
        assert read(report/'external-activity-audit.json')==recovered
    assert t['status']=='PASS' and (outer['status']=='PASS' or recovered)
    source_check = 'original_source_firecracker_identities_unchanged' if 'original_source_firecracker_identities_unchanged' in outer['cleanup'] else 'original_firecracker_identities_unchanged'
    for key in ('owned_container_removed', 'mount_and_loop_removed', 'successful_fixture_removed',
                'original_service_unchanged', source_check):
        assert outer['cleanup'][key] or recovered and key=='original_firecracker_identities_unchanged', key
    assert not (storage / 'volume.ext4').exists() and not (storage / 'mount').is_mount()
    prettier = task == 'prettier6604'
    if prettier:
        value = inspect_prettier(storage, 'on')
        e = value['endpoint']; actions = value['actual_actions']; guests = 7
        timing = {k: value[k + '_seconds'] for k in ('task', 'actions_download', 'tool', 'controller')}
        host = raw / 'host/memory-samples.tsv'; current_key = 'cgroup_current_bytes'
        file_key = 'cgroup_file_bytes'
    else:
        validate_result(t, pss_interval_seconds=1)
        tools = table(report / 'tools.tsv')
        actions = len(tools); guests = 1; e = t['endpoint_metrics']
        assert actions == {'django16819': 28, 'c4': 36, 'sphinx7590': 35, 'xarray6992': 35,
                           'cython': 83, 'matplotlib__matplotlib-24177': 95,
                           'large-scale-text-editing': 50}[task]
        timing = dict(task=t['schedule_elapsed_ns']/1e9,
                      actions_download=t['actions_download_elapsed_ns']/1e9,
                      tool=sum(int(r['duration_ns']) for r in tools)/1e9,
                      controller=t['controller_elapsed_ns']/1e9)
        host = report / ('host-file/memory.tsv' if task in ('sphinx7590', 'xarray6992') else 'host/memory.tsv')
        current_key = 'current'; file_key = 'file'
        if task in ('sphinx7590', 'xarray6992'):
            assert t['diagnostic_status'] == 'PASS' and t['strict_replay_status'] == 'FAIL'
            assert (t['official']['passed'], t['official']['failed']) == ((24, 1) if task == 'sphinx7590' else (945, 12))
    monitor = read(raw / 'runtime-monitor.json')
    assert monitor['status'] == 'PASS' and monitor['interval_seconds'] == .1
    assert len(monitor['sessions']) == guests
    guest_samples = 0; guest_cpu = 0
    for session in monitor['sessions']:
        receipt = session['receipt']
        memory = table(raw / session['raw_directory'] / 'guest-memory.tsv')
        assert session['collector_reaped'] and receipt['status'] == 'PASS'
        assert receipt['interval_seconds'] == .1 and len(memory) == receipt['samples'] > 2
        assert all(r.get('Cached_kib') and r.get('MemAvailable_kib') for r in memory)
        guest_samples += len(memory)
        guest_cpu += receipt['cpu_user_seconds'] + receipt['cpu_system_seconds']
    receipts = [read(p) for p in (raw / 'host-vmm').rglob('collector-status.json')]
    assert len(receipts) == guests
    for receipt in receipts:
        assert receipt['status'] == 'PASS' and receipt['interval_seconds'] == .1
        assert receipt['pss_interval_seconds'] == 1 and receipt['pss_samples'] > 0
    periodic = [r for p in (raw / 'host-vmm').rglob('host-vmm.tsv') for r in table(p)]
    assert sum(bool(r['pss_kib']) for r in periodic) == sum(r['pss_samples'] for r in receipts)
    h = table(host); assert len(h) > 2 and all(r[current_key] and r[file_key] for r in h)
    p1 = e['p1']; assert all(r['stable'] for r in p1['files'])
    assert len({(r['device'], r['inode']) for r in p1['files']}) == p1['unique_files']
    assert p1['resident_bytes'] == sum(r['resident_bytes'] for r in p1['files'])
    assert p1['unique_files'] == {'prettier6604': 19, 'django16819': 10, 'c4': 9,
                                'sphinx7590': 13, 'xarray6992': 13, 'cython': 7,
                                'matplotlib__matplotlib-24177': 10, 'large-scale-text-editing': 8}[task]
    dependency_commit = None
    if task == 'cython':
        dependency_commit = re.search(r'^source_repo_commit: (\S+)$', (raw/'extension-validation.txt').read_text(), re.M).group(1)
    return dict(task=task, raw=str(raw.relative_to(ROOT)), report=str(report.relative_to(ROOT)),
                guests=guests, actions=actions, status='PASS', timing_seconds=timing,
                dependency_commit=dependency_commit,
                original_outer_status=outer['status'], external_activity_audit=recovered,
                metrics=dict(current_collector_peak_mib=max(int(r[current_key]) for r in h)/2**20,
                             file_collector_peak_mib=max(int(r[file_key]) for r in h)/2**20,
                             endpoint_current_mib=p1['counters_before']['current']/2**20,
                             endpoint_file_mib=p1['counters_before']['file']/2**20,
                             endpoint_pss_per_guest_mib=e['pss']['per_sandbox_bytes']/2**20,
                             lower_p1_mib=p1['resident_bytes']/2**20),
                samples=dict(host=len(h), guest=guest_samples, periodic_pss=sum(r['pss_samples'] for r in receipts)),
                costs=dict(guest_cpu_seconds=guest_cpu,
                           vmm_cpu_seconds=sum(r['cpu_user_seconds']+r['cpu_system_seconds'] for r in receipts),
                           endpoint_wall_seconds=(e['end_monotonic_ns']-e['start_monotonic_ns'])/1e9,
                           endpoint_cpu_seconds=e['cpu_ns']/1e9,
                           pss_query_wall_seconds=(e['pss']['end_monotonic_ns']-e['pss']['start_monotonic_ns'])/1e9,
                           pss_query_cpu_seconds=e['pss']['cpu_ns']/1e9,
                           mincore_query_wall_seconds=(p1['end_monotonic_ns']-p1['start_monotonic_ns'])/1e9,
                           mincore_query_cpu_seconds=p1['cpu_ns']/1e9),
                host_table=str(host.relative_to(ROOT)), current_key=current_key,
                lower_files=p1['unique_files'],
                min_available_disk_gib=outer['observed_host_available_min_bytes']/2**30)


def inventory():
    runs = []
    for row in build()['runs']:
        run = collect(row['task'], row['raw'], Path(row['report']).parent)
        run.update(origin='previous_single', outcome=row['outcome']); runs.append(run)
    for row in read(ROOT / 'results/c4-new-sampling-repeats-20261105/summary.json')['runs']:
        if row['mode'] == 'on':
            run = collect('c4', row['raw'], row['published_report'])
            run.update(origin='previous_repeat', outcome='原36步、9898文件往返及约束通过'); runs.append(run)
    assert len({r['raw'] for r in runs}) == len(runs) == 11
    return runs


def stats(values):
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                stdev=statistics.stdev(values) if len(values)>1 else None,
                minimum=min(values), maximum=max(values))


def plot_runs(runs, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    images = {}
    for task in dict.fromkeys(r['task'] for r in runs):
        selected = [r for r in runs if r['task'] == task]
        fig, axes = plt.subplots(3, 1, figsize=(10, 9), constrained_layout=True)
        labels = []
        for index, row in enumerate(selected, 1):
            h = table(ROOT / row['host_table'])
            key = 'timestamp_ns' if row['task'] == 'prettier6604' else 'monotonic_ns'
            zero = int(h[0][key]); label = str(index)+': '+row['origin']; labels.append(str(index))
            axes[0].plot([(int(r[key])-zero)/1e9 for r in h],
                         [int(r[row['current_key']])/2**20 for r in h], label=label)
        axes[0].set(xlabel='Seconds from first host sample in each run', ylabel='Cgroup memory.current, MiB')
        axes[0].legend(fontsize=8)
        x = list(range(len(selected)))
        for offset, key, title in ((-.18, 'task', 'Task window'), (.18, 'tool', 'Cumulative tools')):
            axes[1].bar([i+offset for i in x], [r['timing_seconds'][key] for r in selected], width=.36, label=title)
        axes[1].set(xticks=x, xticklabels=labels, xlabel='Run', ylabel='Task / cumulative tool duration, seconds')
        axes[1].legend()
        axes[2].bar([i-.18 for i in x], [r['metrics']['endpoint_pss_per_guest_mib'] for r in selected], width=.36, label='Endpoint VMM PSS/N')
        axes[2].bar([i+.18 for i in x], [r['metrics']['lower_p1_mib'] for r in selected], width=.36, label='Initial-lower mincore P1')
        axes[2].set(xticks=x, xticklabels=labels, xlabel='Run (separate quantities, not summed)', ylabel='VMM PSS/N / lower-file residency, MiB')
        axes[2].legend()
        for ax in axes: ax.grid(axis='y', alpha=.2)
        fig.suptitle(task+' — saved and new sampled runs')
        path = output / (task+'.png'); fig.savefig(path, dpi=130); plt.close(fig)
        images[task] = path.name
    return images


def report(batches, output):
    output.mkdir(parents=True, exist_ok=False)
    runs = inventory(); states = [read(batch / 'result.json') for batch in batches]
    state = states[-1]; batch = batches[-1]
    for saved in states:
        for row in saved['runs']:
            if row['status'] == 'PASS':
                run = collect(row['task'], row['raw'], row['report'])
                run.update(origin='offline_recovered' if run['external_activity_audit'] else 'new_repeat', round=row['round']); runs.append(run)
    assert len({r['raw'] for r in runs}) == len(runs), 'same saved run must not count twice'
    summaries = {}
    for task in dict.fromkeys(r['task'] for r in runs):
        selected = [r for r in runs if r['task'] == task]
        summaries[task] = {section: {k: stats([r[section][k] for r in selected]) for k in selected[0][section]}
                           for section in ('timing_seconds', 'metrics', 'costs')}
    images = plot_runs(runs, output)
    counts = {task: sum(r['task']==task for r in runs) for task in summaries}
    complete = state['status']=='PASS' and all(n>=3 for n in counts.values())
    prior = batch.parent / 'live/result.json'
    cleanup_path = batch.parent / 'cleanup-approved/result.json'
    cleanup_receipt = read(cleanup_path) if cleanup_path.exists() else None
    failed_volumes_deleted = bool(cleanup_receipt and cleanup_receipt['status']=='PASS' and len(cleanup_receipt['deleted'])==2)
    preparation_failure = None
    if prior.exists() and prior.parent != batch:
        failure = read(prior)
        preparation_failure = dict(record=str(prior.relative_to(ROOT)), status=failure['status'],
                                   error=failure.get('error'), raw='.artifacts/storage-isolated-service/20261106T101653Z-3471660',
                                   reason='Default network pools occupied by another workspace; stopped before guest creation; no task actions')
    value = dict(status='PASS' if complete else 'PARTIAL', run_counts=counts, runs=runs, statistics=summaries,
                 batch_status=state['status'], batch=str(batch.relative_to(ROOT)),
                 source_batches=[str(b.relative_to(ROOT)) for b in batches],
                 preparation_failure=preparation_failure,
                 approved_cleanup=cleanup_receipt,
                 failures=[r for saved in states for r in saved['runs'] if r['status'] != 'PASS'],
                 scope='Same-task same-profile descriptive repeats; previous runs reused, no cache reset, not a schedule matrix')
    (output / 'summary.json').write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
    with (output/'runs.tsv').open('w') as stream:
        columns = ['task','origin','round','guests','actions','raw',*runs[0]['timing_seconds'],*runs[0]['metrics']]
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter='\t'); writer.writeheader()
        for row in runs:
            writer.writerow({**{k: row.get(k,'') for k in columns[:6]}, **row['timing_seconds'], **row['metrics']})
    lines = ['# 八种任务采集完整性与重复统计', '',
             '旧8种任务均有完整记录，已重新读取原结果、工具表、guest曲线、host计数、周期PSS及终点PSS／下层P1核对。成功只指原验收与采集通过，不将原测试失败改为通过。', '',
             '## 重复次数与耗时', '',
             '下表为均值±样本标准差；括号内为最小～最大值。中位数和全部原值见summary.json。跨日已有记录与新增轮分列来源，不视为同一日的随机对照。', '',
             '| 任务 | 沙箱数 | 完整次数 | 任务窗口，秒 | 工具累计，秒 | 整轮，秒 |',
             '| --- | ---: | ---: | --- | --- | --- |']
    def cell(s):
        sd = '未计算' if s['stdev'] is None else f"{s['stdev']:.3f}"
        return f"{s['mean']:.3f} ± {sd}（{s['minimum']:.3f}～{s['maximum']:.3f}）"
    for task, v in summaries.items():
        selected = [r for r in runs if r['task'] == task]; t = v['timing_seconds']
        lines.append(f"| {task} | {selected[0]['guests']} | {len(selected)} | {cell(t['task'])} | {cell(t['tool'])} | {cell(t['controller'])} |")
    lines += ['', '## 内存统计', '',
              '同一任务内对每次观测量取统计，不把不同任务、不同沙箱数量或不同采集配置混算。', '',
              '| 任务 | 采集窗口current峰值，MiB | 终点PSS/N，MiB | 下层文件P1，MiB |',
              '| --- | --- | --- | --- |']
    for task, v in summaries.items():
        m = v['metrics']; lines.append(f"| {task} | {cell(m['current_collector_peak_mib'])} | {cell(m['endpoint_pss_per_guest_mib'])} | {cell(m['lower_p1_mib'])} |")
    lines += ['', '普通计数0.1秒，周期PSS1秒，默认回收开启，不清缓存、不强制驻留。current峰值是各自完整host采集窗口的峰值，不冒充纯任务阶段；Prettier为七角色完整监控、服务组计数，其余为单guest轻量、外层实验组计数，绝对current不能直接横向比较。', '',
              'PSS/N仅为VMM映射驻留的共享分摊；P1只覆盖初始下层文件，没有P0，未覆盖upper及全部guest缓存对应页。guest Cached与host file不是同一层。上述量互不相加构造总物理内存。', '',
              'C4已有原单次及三次相同1秒PSS开启记录，四次全部保留，不混入关闭组、双沙箱或2秒PSS组。其余每项目标累计3次；这不是三调度完整矩阵，也不是采样开关减速对照。每轮重新建服务和沙箱，不剔除慢组。', '',
              '工具累计是各个实际动作时长之和，包含并发重叠，不是墙钟或占比。各轮全部原值见[runs.tsv](runs.tsv)，终点各项查询墙钟／CPU及周期VMM／guest CPU的原值和统计见summary.json。', '',
              'Sphinx保留24通过1失败、Xarray保留945通过12失败，二者严格重放FAIL／诊断PASS。其他原结果见旧汇总。Cython原轮与本次重复均走各自获准的临时Git／pip代理，联网波动属于该原任务路径。', '',
              '## 逐次来源与曲线', '']
    lines += ['每项图最多4条current曲线，按各轮首个host采样点对齐，保留准备／任务／收尾的原长度，不将这一对齐称为工具开始对齐，不平均曲线或填补缺失点。图还列各轮任务／工具耗时及两种独立终点内存量；数值统计是每轮峰值取平均，不是平均曲线取峰值。', '']
    for task, name in images.items(): lines.append('- ['+task+'重复曲线与原值]('+name+')')
    lines += ['']
    for r in runs:
        entry = 'overview.png' if r['task'] in ('sphinx7590', 'xarray6992') else 'REPORT.md'
        lines.append(f"- {r['task']}，{r['origin']}：原始`{r['raw']}`；[逐次结果与曲线](../../{r['report']}/{entry})；host {r['samples']['host']}点、guest {r['samples']['guest']}点、PSS {r['samples']['periodic_pss']}点。")
    lines += ['', '## 异常与完成状态', '', f"汇总状态：{value['status']}；最后批次状态：{state['status']}。过程目录："+ '、'.join('`'+str(b.relative_to(ROOT))+'`' for b in batches)+'。']
    if preparation_failure:
        lines += ['', '首个准备在默认网段已被另一工作区使用时被原检查拦住，尚未创建guest、0任务动作，不计完整重复。原始错误记录于`'+preparation_failure['raw']+'`；过程`'+preparation_failure['record']+'`。16 GiB失败卷'+('已按本轮授权删除，日志和失败记录保留。' if failed_volumes_deleted else '仍保留。')+'定位后本批显式复用已有空闲网段选择器，普通入口默认不变。两类组入口均转传选项，Cython转发仅使用本服务实际选定网段并按保存的归属精确清理，没有改动另一工作区任务。此环境差异与跨日波动保留，数据不视为严格同期随机实验。']
    for r in value['failures']:
        lines.append('- '+json.dumps(r, ensure_ascii=False))
    if any(r['task']=='cython' for r in value['failures']):
        lines += ['', 'Cython首次新增准备创建了guest，但原83步尚未执行，联网HEAD报`Connection reset by peer`。原始`.artifacts/storage-isolated-service/20261106T103835Z-3487805/external-workload/`；转发及规则已清理，32 GiB失败卷'+('已按本轮授权删除，日志和失败记录保留。' if failed_volumes_deleted else '保留。')+'保存计数显示精确规则已有3个允许包，宿主原代理HEAD成功；源码核对发现上游转发程序的允许来源仍固定10.211／10.212，拒绝新选10.213来源。只对本次拥有的转发副本替换为实际选择网段，原上游文件不改；原生服务再次核对选择一致，归属与清理规则保持。修正后仅执行未完成项，六项已成功记录不重跑；失败不计完整样本，也不补零。']
    for row in runs:
        if row['external_activity_audit']:
            lines += ['', 'Cython第三份记录的业务83步及采集均通过，但原外层保留FAIL，原因是另一实验组自然更换VMM。已保存的前后清单不含本源服务VMM，源服务状态与本次资源清理不变；[离线核对及原清单](../../'+row['report']+'/REPORT.md)给出依据。该次完整数据纳入统计，不重跑业务，不把原外层状态改成PASS。并发模式之后核对本次／源服务资源，另外保存全宿主VMM变化，不以其他实验不能创建／删除VM为验收条件；普通入口默认未改。']
    if 'resource budget insufficient' in state.get('error',''):
        lines += ['', '最后批次在已成功的文本编辑和Matplotlib之后、下一个Xarray创建之前被磁盘预算检查拦住。可用盘约70 GiB，低于本批保守>82 GiB门槛；这不是Matplotlib业务失败。源服务已停止、本轮RAM输入已释放，另外四项最后一次尚未执行。已请求定向删除本轮16／32 GiB准备失败卷，未擅自删除其他文件。']
    if failed_volumes_deleted:
        lines += ['', '此前批次在完成文本编辑／Matplotlib后因约70 GiB可用盘不足而停止，未执行另外四项最后一次。用户随后授权删除上述两个准备失败卷；复查时可用盘已降到约'+f"{cleanup_receipt['before_available_bytes']/2**30:.3f}"+' GiB。确认无挂载、loop及打开使用者后定向删除48 GiB，保留全部日志及失败记录，可用盘恢复约'+f"{cleanup_receipt['after_available_bytes']/2**30:.3f}"+' GiB。只续余下四次，无新Cython联网；清理依据`'+str(cleanup_path.relative_to(ROOT))+'`。']
    commits = sorted({r['dependency_commit'] for r in runs if r['task']=='cython'})
    lines += ['', 'Cython各完整轮保存的pyknotid源码提交：'+ '、'.join('`'+c+'`' for c in commits)+'。'+ ('各轮一致；Git／pip传输耗时仍可能波动。' if len(commits)==1 else '存在不同源码提交，不可将这项波动仅归为宿主或采样，应分别查看来源。')]
    if state.get('error'): lines.append('批次错误：`'+state['error']+'`。')
    lines += ['', '## 下一步建议', '', '各项已达到至少3次，本轮收尾，不再追加实跑。下一步仅建议离线检查C4终点PSS、Matplotlib的current峰值和Prettier整轮耗时波动，使用已有记录定位准备、任务或收尾时段；证据不足的原因明确保留未知，不为缩小波动继续追加轮次。']
    (output / 'REPORT.md').write_text('\n'.join(lines)+'\n')
    return value


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch', type=Path, action='append'); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.batch:
        v = report([b.resolve() for b in a.batch], a.output.resolve())
        print('status='+v['status']+' result_dir='+str(a.output))
    else:
        v = dict(status='PASS', runs=inventory())
        with a.output.open('x') as stream: json.dump(v, stream, ensure_ascii=False, indent=2)
        print('status=PASS audited_records='+str(len(v['runs'])))
