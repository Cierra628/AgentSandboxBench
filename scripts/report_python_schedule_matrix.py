"""Read saved Python seven-role results only; do not query live resources."""
import csv
import json
import re
from datetime import datetime
from pathlib import Path
import statistics

MIB = 2**20
REPEAT_METRICS = (
    ('current_peak_mib', 'current全采集窗口峰值', 'MiB'),
    ('current_schedule_peak_mib', 'current调度窗口峰值', 'MiB'),
    ('pss_per_guest_mib', '终点PSS/N', 'MiB'),
    ('lower_p1_mib', '原镜像活跃文件P1', 'MiB'),
    ('ordinary_window_mib', '已枚举普通文件窗口合计', 'MiB'),
    ('schedule_seconds', '调度窗口', '秒'),
    ('tools_seconds', '工具累计', '秒'),
    ('controller_seconds', '控制程序整轮', '秒'),
)


def read(path):return json.loads(Path(path).read_text())
def rows(path):
    with Path(path).open() as stream:return list(csv.DictReader(stream, delimiter='\t'))


def schedule_current(samples, task):
    start = task.get('schedule_start_monotonic_ns')
    end = task.get('original_paths_end_monotonic_ns')
    window = [r for r in samples if start <= int(r['monotonic_ns']) <= end] if start is not None and end is not None else []
    peak = max(window, key=lambda r: int(r['current'])) if window else None
    return dict(current_schedule_peak_mib=int(peak['current'])/MIB if peak else None,
        current_schedule_window=dict(start_monotonic_ns=start, end_monotonic_ns=end,
            sample_count=len(window), peak_monotonic_ns=int(peak['monotonic_ns']) if peak else None))


def report_fields(row):
    """Derive new report fields from saved samples, including older successful rows."""
    row = dict(row)
    if 'current_schedule_peak_mib' not in row:
        raw = Path(row['raw'])
        row.update(schedule_current(rows(raw / 'host-file/memory.tsv'), read(raw / 'result.json')))
    ordinary = row.get('ordinary_window')
    value = ordinary.get('observed_subtotal_bytes') if ordinary else None
    row['ordinary_window_mib'] = value/MIB if value is not None else None
    return row


def repeat_statistics(runs):
    result = []
    for task in ('sphinx7590', 'xarray6992','django16819','c4','matplotlib24177','text_editing','cython'):
        for schedule in ('grpo', 'bpo', 'tvcache'):
            selected = [r for r in runs if r['task'] == task and r['schedule'] == schedule and r['status'] == 'PASS']
            if not selected:continue
            metrics = {}
            for key, _, unit in REPEAT_METRICS:
                values = [r[key] for r in selected if r.get(key) is not None]
                if not values:
                    metrics[key] = dict(unit=unit, count=0, median=None, mean=None,
                        minimum=None, maximum=None, sample_stddev=None)
                    continue
                metrics[key] = dict(unit=unit, count=len(values), median=statistics.median(values),
                    mean=statistics.mean(values), minimum=min(values), maximum=max(values),
                    sample_stddev=statistics.stdev(values) if len(values) > 1 else None)
            result.append(dict(task=task, schedule=schedule, count=len(selected), metrics=metrics))
    return result


def lower_scope(metrics, logical_layers=13):
    p1 = metrics['p1']
    mode = p1.get('reference_mode', 'shared_initial_inodes')
    if mode == 'shared_initial_inodes':
        assert p1['unique_files'] == logical_layers
    else:
        assert mode == 'all_active_image_layer_copies' and p1['logical_layer_count'] == logical_layers
        selection = metrics['initial_layer_selection']
        assert selection['mode'] == mode and selection['logical_layer_count'] == logical_layers
        keys = {(r['device'], r['inode']) for r in p1['files']}
        objects = {(r['device'], r['inode']) for r in selection['objects']}
        assert len(keys) == len(p1['files']) == p1['unique_files'] == selection['physical_file_count'] == len(selection['objects'])
        assert keys == objects and len(keys) >= logical_layers
    return dict(lower_reference_mode=mode, lower_logical_layers=logical_layers, lower_physical_files=p1['unique_files'])


def c4_cleanup_timeout_evidence(storage, task, metrics):
    """Validate the user-approved measurement exception without changing raw FAIL."""
    raw = storage/'external-workload'
    assert task['status']=='FAIL' and task['functional_validation']==task['strict_replay_status']=='PASS'
    assert not task.get('error') and all(v=='PASS' for v in task['checks'].values())
    assert len(task['cleanup_errors'])==1 and task['cleanup_errors'][0].startswith('TimeoutExpired(')
    assert task['cleanup_errors'][0].endswith(', 60)')
    assert task['cleanup']==dict(owned_guests_removed=False, snapshots_removed=False,
        service_sandbox_list_empty=True, samplers_stopped=True)
    ids={r['role']:r['sandbox_id'] for r in rows(raw/'sandbox-ids.tsv')}
    assert len(ids)==len(set(ids.values()))==7 and set(ids)==set(task['paths'])
    assert {p['role']:p['sandbox_id'] for p in metrics['pss']['processes']}==ids
    for role, path in task['paths'].items():
        assert path['action_count']==36 and not path['exit_differences']
        assert path['syntax_validation']=='PASS' and path['roundtrip_validation']['status']=='PASS'
        assert read(raw/'evaluation'/role/'receipt.json')['status']=='PASS'
    calls=read(raw/'calls.json'); deletes=[c for c in calls if c['stage']=='delete-guest']
    assert len(deletes)==7 and sum(c['returncode'] is None for c in deletes)==1
    assert all(c['returncode'] in (None,0) for c in deletes)
    first=min(c['start_monotonic_ns'] for c in deletes)
    assert task['original_paths_end_monotonic_ns']<=metrics['start_monotonic_ns']<=metrics['end_monotonic_ns']<first
    syntax=[c for c in calls if c['stage'].startswith('syntax-')]
    assert len(syntax)==7 and all(c['returncode']==0 and c['end_monotonic_ns']<first for c in syntax)
    listed=[c for c in calls if c['stage']=='list-after']; assert len(listed)==1 and listed[0]['returncode']==0
    assert read(raw/f"{listed[0]['index']:04}-list-after.stdout")==[]
    log=re.sub(r'\x1b\[[0-9;]*m','',(storage/'service-logs/server.log').read_text())
    durations={}
    for role,sid in ids.items():
        start=[l for l in log.splitlines() if 'deleting sandbox' in l and 'sandbox_id='+sid in l]
        end=[l for l in log.splitlines() if 'sandbox deleted' in l and 'sandbox_id='+sid in l]
        assert len(start)==len(end)==1
        durations[role]=(datetime.fromisoformat(end[0].split()[0])-datetime.fromisoformat(start[0].split()[0])).total_seconds()
        assert durations[role]>=0
    assert sum(60<=v<90 for v in durations.values())==1
    return dict(original_status='FAIL',cleanup_exception='user_approved_timeout_measurement_reuse',
        cleanup_errors=task['cleanup_errors'],server_delete_seconds=durations,
        controller_statistics_excluded=True)


def collect(storage, item, *, accept_c4_cleanup_timeout=False, experiment_revalidation=None):
    django = item['task']=='django16819'
    c4 = item['task']=='c4'
    matplotlib = item['task']=='matplotlib24177'
    text_editing = item['task']=='text_editing'
    cython = item['task']=='cython'
    external = django or c4 or matplotlib or text_editing or cython
    storage = Path(storage); raw = storage / ('external-workload' if external else 'python-backbone')
    outer = read(storage / 'result.json'); task = read(raw / 'result.json')
    metrics = read(raw / 'schedule-smoke-metrics.json'); monitor = read(raw / 'runtime-monitor.json')
    reuse={}
    if accept_c4_cleanup_timeout:
        assert c4 and item['round']==1 and item['schedule']=='grpo' and outer['status']=='FAIL'
        reuse=c4_cleanup_timeout_evidence(storage,task,metrics)
        assert monitor['status']=='PASS' and task['host_collector']['status']=='PASS'
    elif experiment_revalidation is not None:
        # Explicit reuse of the approved first Matplotlib run only. Raw FAIL remains.
        assert matplotlib and item['round']==1 and item['schedule']=='grpo'
        assert outer['status']=='FAIL' and outer['failed_stage']=='experiment-memory-acceptance'
        assert task['status']==monitor['status']=='PASS' and all(v=='PASS' for v in task['checks'].values())
        evidence=read(experiment_revalidation)
        matches=[r for r in evidence['runs'] if Path(r['source_storage']).resolve()==storage.resolve()]
        assert evidence['policy']=='segments_v3' and len(matches)==1
        checked=matches[0]['recomputed']
        assert checked['experiment_scope_validation']=='PASS' and checked['closure_policy']=='segments_v3'
        assert checked['experiment_parent']==outer['host_file_creation']['cgroup']
        assert checked['historical_periodic_file_closure_v2']==outer['experiment_memory_acceptance']['periodic_file_closure']
        receipt=read(Path(experiment_revalidation).parent/'cleanup-receipt.json')
        assert receipt['status']=='PASS' and receipt['volume_absent'] and receipt['other_files_preserved']
        assert Path(receipt['deleted_path']).resolve()==(storage/'volume.ext4').resolve()
        reuse=dict(original_status=outer['status'],original_failed_stage=outer['failed_stage'],
            original_error=outer['error'],revalidation_summary=str(experiment_revalidation),
            measurement_acceptance='segments_v3_offline_revalidation')
    else:
        assert outer['status'] == task['status'] == monitor['status'] == 'PASS'
    logical,restored=(581,176) if cython else (350,113) if text_editing else (665,185) if matplotlib else (252,93) if c4 else (196,64) if django else (230,104)
    assert task['schedule'] == item['schedule'] and task['actual_action_count'] == (logical if item['schedule'] == 'grpo' else restored)
    assert task['logical_action_count'] == logical and len(task['paths']) == 7
    assert metrics['status'] == 'OBSERVED'
    lower = lower_scope(metrics,7 if cython else 8 if text_editing else 9 if c4 else 10 if django or matplotlib else 13)
    assert len(metrics['pss']['processes']) == 7 and all(p['uid'] == 0 for p in metrics['pss']['processes'])
    assert monitor['interval_seconds'] == .1 and monitor['host_pss_interval_seconds'] == 1
    assert all(s['status'] == 'PASS' and s['collector_reaped'] and s['host_receipt']['pss_samples'] > 0 for s in monitor['sessions'])
    if not accept_c4_cleanup_timeout:assert all(task['cleanup'].values())
    assert all(outer['cleanup'][k] for k in ('owned_container_removed', 'mount_and_loop_removed',
        'failed_fixture_retained' if accept_c4_cleanup_timeout or experiment_revalidation is not None else 'successful_fixture_removed', 'original_service_unchanged'))
    assert not (storage / 'volume.ext4').exists()
    samples = rows(raw / 'host-file/memory.tsv'); assert samples
    assert task['host_collector']['cgroup'] == metrics['cgroup']
    c = metrics['p1']['counters_before']; layers = metrics['cache_layers']; e = metrics['expanded_files']
    pss = metrics['pss']; save = task['save_restore']
    assert (len(save['saves']), len(save['restores'])) == ((0, 0) if item['schedule'] == 'grpo' else (2, 6))
    return report_fields(dict(**item, **lower, **reuse, **schedule_current(samples, task),
        status='ACCEPTED_SEGMENTS_V3' if experiment_revalidation is not None else 'ACCEPTED_CLEANUP_TIMEOUT' if accept_c4_cleanup_timeout else 'PASS', raw=str(raw), storage=str(storage), cgroup=metrics['cgroup'],
        branch_pipeline_policy=task.get('branch_pipeline_policy','original'),
        find_exit_policy=task.get('find_exit_policy','original'),strict_replay_status=task['strict_replay_status'],
        exit_differences_by_role={role:p['exit_differences'] for role,p in task['paths'].items()},
        host_scope='private_service_cgroup', actual_actions=task['actual_action_count'], logical_actions=logical,
        current_peak_mib=max(int(r['current']) for r in samples)/MIB,
        file_p1_mib=c['file']/MIB, anon_p1_mib=c['anon']/MIB, kernel_p1_mib=c['kernel']/MIB,
        pss_total_mib=pss['total_bytes']/MIB, pss_per_guest_mib=pss['per_sandbox_bytes']/MIB,
        lower_p1_mib=metrics['p1']['resident_bytes']/MIB,
        ordinary_window=e.get('query_window_regular'), ordinary_strict_status=e['status'],
        block_cachestat=layers['block_devices']['method_totals']['cachestat'],
        fixed_ram=layers['block_devices'].get('fixed_ram_totals'), loop=layers['outer_loop'],
        tmpfs=layers['deleted_tmpfs'], saves=save['saves'], restores=save['restores'],
        schedule_seconds=task['schedule_elapsed_ns']/1e9, actions_download_seconds=task['actions_download_elapsed_ns']/1e9,
        tools_seconds=sum(p['tool_ns'] for p in task['paths'].values())/1e9,
        controller_seconds=task['controller_elapsed_ns']/1e9,
        endpoint_wall_seconds=(metrics['end_monotonic_ns']-metrics['start_monotonic_ns'])/1e9,
        endpoint_cpu_seconds=metrics['cpu_ns']/1e9, official_by_role={role:p['official'] for role,p in task['paths'].items()},
        volume_gib=outer['cython_schedule_storage_budget' if cython else 'text_editing_schedule_storage_budget' if text_editing else 'matplotlib_schedule_storage_budget' if matplotlib else 'c4_schedule_storage_budget' if c4 else 'django_schedule_storage_budget' if django else 'python_schedule_storage_budget']['capacity_bytes']/2**30,
        min_available_disk_gib=outer['observed_host_available_min_bytes']/2**30))


def plot_run(row, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    raw = Path(row['raw']); host = rows(raw / 'host-file/memory.tsv')
    task = read(raw / 'result.json')
    colors = {role: f'C{i}' for i, role in enumerate(sorted(task['paths']))}
    zero = int(host[0]['monotonic_ns']); monitor = read(raw / 'runtime-monitor.json')
    fig, axes = plt.subplots(3, 1, figsize=(11, 10), constrained_layout=True)
    for key, title in (('current', 'memory.current'), ('file', 'memory.stat:file')):
        axes[0].plot([(int(r['monotonic_ns'])-zero)/1e9 for r in host], [int(r[key])/MIB for r in host], label=title)
    axes[0].set(xlabel='Host elapsed seconds', ylabel='Private service cgroup accounting, MiB')
    for session in monitor['sessions']:
        directory = raw / session['host_directory']; table = rows(directory / 'host-vmm.tsv')
        points = [r for r in table if r['pss_kib']]
        axes[1].plot([(int(r['monotonic_ns'])-zero)/1e9 for r in points], [int(r['pss_kib'])/1024 for r in points], label=session['role'],color=colors[session['role']])
    axes[1].set(xlabel='Host elapsed seconds (snapshot gaps retained)', ylabel='VMM proportional resident memory PSS, MiB')
    for role, value in task['paths'].items():
        tools = [r for segment in value['segments'] for r in rows(raw / 'rollouts' / role / segment / 'steps.tsv')]
        axes[2].plot(range(1,len(tools)+1), [int(r['duration_ns'])/1e9 for r in tools], label=role,color=colors[role],marker='.',markersize=3)
    axes[2].set(xlabel='Actual tool index within each role', ylabel='Actual tool duration, seconds')
    for ax in axes:
        handles, labels = ax.get_legend_handles_labels(); unique = dict(zip(labels, handles))
        ax.legend(unique.values(), unique.keys(), fontsize=8, ncol=3); ax.grid(alpha=.2)
    fig.suptitle(row['task']+' / '+row['schedule']+' / round '+str(row['round']))
    output.mkdir(parents=True, exist_ok=False)
    fig.savefig(output / 'curves.png', dpi=140); plt.close(fig)
    return str(output / 'curves.png')


def report(state, output):
    output = Path(output); output.mkdir(exist_ok=False, parents=True)
    state = dict(state, runs=[report_fields(r) if r['status'] == 'PASS' else r for r in state['runs']])
    repeated = repeat_statistics(state['runs'])
    lines = ['# Sphinx／Xarray原七分支三调度重复', '',
        '只统计这里列出的有效七角色运行；旧单guest重复和Prettier结果不混入。current是私有服务cgroup，缓存层分别报告，不相加。普通计数0.1秒／PSS1秒，默认回收；曲线保留完整采集窗口。', '',
        '批次状态：'+state['status']+'；计划18次，已通过'+str(sum(r['status']=='PASS' for r in state['runs']))+'次。', '',
        '| 任务／轮次／调度 | 状态 | 实际／逻辑动作 | current全采集窗口峰值 MiB | current调度窗口峰值 MiB | PSS/N MiB | 原镜像13层活跃文件P1 MiB | 物理文件数 |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    if state.get('resume_from'):
        lines[4:4]=['复用已完成'+str(state['reused_completed_slots'])+'次，本批计划新执行'+str(state['expected_new_runs'])+'次；来源`'+state['resume_from']+'`。失败尝试另列，不进入重复统计。', '']
    for r in state['runs']:
        label=f"{r['task']}／{r['round']}／{r['schedule']}"
        if r['status']!='PASS':
            lines += ['| '+label+' | '+r['status']+' | — | — | — | — | — | — |'];continue
        vals=[f"{r[k]:.3f}" if r[k] is not None else '未知' for k in ('current_peak_mib','current_schedule_peak_mib','pss_per_guest_mib','lower_p1_mib')]
        vals.append(str(r.get('lower_physical_files',13)))
        lines += [f"| {label} | PASS | {r['actual_actions']}/{r['logical_actions']} | "+' | '.join(vals)+' |']
    lines += ['', '全采集窗口峰值保留原值，包含原动作结束后的终点采集、独立最终测试和清理。调度窗口峰值只取schedule_start_monotonic_ns至original_paths_end_monotonic_ns（含两端）的已有样本，覆盖创建／准备、原动作、保存恢复及下载，不含后续独立最终测试。边界、样本数及峰值时刻见summary.json的current_schedule_window；没有窗口或样本时记为未知。', '',
        '| 任务／轮次／调度 | 已枚举普通文件窗口合计 MiB | 窗口有效对象数 | 原普通文件严格状态 | 调度窗口秒 | 工具累计秒 | 整轮秒 |',
        '| --- | ---: | ---: | --- | ---: | ---: | ---: |']
    for r in state['runs']:
        if r['status'] != 'PASS':continue
        ordinary = r.get('ordinary_window') or {}
        value = f"{r['ordinary_window_mib']:.3f}" if r['ordinary_window_mib'] is not None else '未知'
        times = ' | '.join(f"{r[k]:.3f}" for k in ('schedule_seconds','tools_seconds','controller_seconds'))
        lines += [f"| {r['task']}／{r['round']}／{r['schedule']} | {value} | {ordinary.get('observed_objects', '未知')} | {r['ordinary_strict_status']} | {times} |"]
    lines += ['', '已枚举普通文件窗口合计包含复用的原镜像P1、活跃lower／upper及其他已枚举普通文件；每个设备号:inode只计一次，各对象并非同时查询。它不是原P1以外的增量、全部host page cache或同一时刻的物理并集，不能再与P1相加；不含另列的RAM、块设备和外层loop查询。窗口读数不替换原严格状态。', '']
    lines += ['', 'P1按原镜像13层的有序digest与大小确认范围，再按设备号:inode查询所有活跃物理文件副本；相同内容的不同inode不会合并。恢复时新增的快照层另列在扩展查询中。旧GRPO复用值经过七实例共同指向原13文件检查，保留原值与来源，不补算失败组。', '', '逐次计数、普通文件窗口／严格状态、块设备、RAM、TMPFS、loop、查询成本及逐角色官方结果见summary.json。未知与PARTIAL保持；只有主干与固定完整补丁参考比较，其他分支执行原命令并保留归档补丁及独立测试，不套用主干通过／失败数。', '']
    if state.get('sphinx_find_exit_diagnostic'):
        lines += ['采用trace-find-exit-v1的Sphinx记录，仅对trace_cpp_parser/005在原命令、完整输出与补丁不变时允许0／1；实际退出差异、被抑制错误未知和严格FAIL保留，最终任务检查仍须通过。其他记录沿用各自原条件，是否复用不改变实际规则；逐角色差异见summary.json的exit_differences_by_role。', '']
    if state.get('xarray_find_exit_diagnostic'):
        lines += ['本批新增Xarray显式启用trace-index-find-exit-v1：仅trace_index_state/001在原命令、128字节完整输出、空stderr及空补丁不变时允许0／1；实际退出及严格FAIL保持，被抑制错误原因未知，最终任务验收不变。其余动作仍按原条件检查，复用组保留原条件。', '']
    for task in ('sphinx7590','xarray6992'):
        selected = [r for r in repeated if r['task'] == task]
        if not selected:continue
        lines += ['## '+task+'：同任务重复统计', '', '| 调度 | 指标（单位） | 次数 | 中位数 | 最小～最大 | 均值 | 样本标准差 |', '| --- | --- | ---: | ---: | --- | ---: | ---: |']
        for group in selected:
            for key, label, unit in REPEAT_METRICS:
                v = group['metrics'][key]
                if not v['count']:
                    lines += [f"| {group['schedule']} | {label}（{unit}） | 0 | 未知 | 未知 | 未知 | 未知 |"]
                    continue
                sd = '—（仅1次）' if v['sample_stddev'] is None else f"{v['sample_stddev']:.3f}"
                lines += [f"| {group['schedule']} | {label}（{unit}） | {v['count']} | {v['median']:.3f} | {v['minimum']:.3f}～{v['maximum']:.3f} | {v['mean']:.3f} | {sd} |"]
    lines += ['', '## 原始路径与异常', '']
    for r in state['runs']:
        lines += ['- '+r['task']+'／'+str(r['round'])+'／'+r['schedule']+'：`'+str(r.get('raw',r.get('storage',r.get('log'))))+'`。']
        if r.get('error'):lines += ['  错误：`'+r['error']+'`。阶段：`'+str(r.get('failed_stage'))+'`。']
        if r.get('curve'):lines += ['  [逐次曲线]('+str(Path(r['curve']).relative_to(output))+')。']
    if state.get('error'):lines += ['', '批次错误：`'+state['error']+'`。失败现场保留，不自动重跑。']
    if state.get('previous_failures'):
        lines+=['','## 此前失败尝试（不计入统计）','']
        for r in state['previous_failures']:lines+=['- `'+str(r.get('raw',r.get('log')))+'`：'+str(r.get('child_error',r.get('error')))]
    (output / 'REPORT.md').write_text('\n'.join(lines)+'\n')
    (output / 'summary.json').write_text(json.dumps(dict(state,repeat_statistics=repeated),ensure_ascii=False,indent=2)+'\n')
