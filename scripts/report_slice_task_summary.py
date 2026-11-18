"""Summarize only saved dedicated-slice records; never contact a live service."""
import argparse
import csv
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
SCHEDULES = ('grpo', 'bpo', 'tvcache')
TASKS = (
    ('django16819', 'Django #16819', 'django-slice-v3-third-round-20261113T062231Z', 3, 28, (18, 26), 196, 64, 10),
    ('c4', 'C4', 'c4-slice-segments-20261112T034356Z-runs', 3, 36, (24, 29), 252, 93, 9),
    ('matplotlib24177', 'Matplotlib #24177', 'matplotlib-slice-v3-20261112T052721Z', 3, 95, (73, 87), 665, 185, 10),
    ('text_editing', '大规模文本编辑', 'text-editing-slice-v3-20261112T060603Z', 3, 50, (33, 46), 350, 113, 8),
    ('cython', 'Cython', 'cython-slice-v3-retry-20261112T074641Z', 3, 83, (60, 75), 581, 176, 7),
)
V3 = ROOT/'results/closure-segments-v3-20261112T051632Z/summary.json'
MEMORY = (
    ('slice_current_peak_mib', '父组current采集峰值'),
    ('slice_current_schedule_peak_mib', '父组current调度峰值'),
    ('slice_file_p1_mib', '父组file终点'), ('slice_anon_p1_mib', '父组anon终点'),
    ('slice_kernel_p1_mib', '父组kernel终点'),
    ('direct_children_current_p1_mib', 'Σ直接子组current终点'),
    ('pss_total_mib', 'Σ七VMM PSS'), ('pss_per_guest_mib', 'PSS/N，N=7'),
    ('lower_p1_mib', '目标mincore P1'),
)
TIME = (
    ('schedule_seconds', '调度窗口秒'), ('actions_download_seconds', '动作及下载窗口秒'),
    ('tools_seconds', '工具累计秒'), ('controller_seconds', '任务控制程序整轮秒'),
    ('save_count', '保存次数'), ('restore_count', '恢复次数'),
    ('save_total_seconds', '保存调用累计秒'), ('restore_total_seconds', '恢复调用累计秒'),
)
FIELDS = ('mean', 'median', 'range', 'sample_stddev')


def describe(values):
    """Keep one observation separately; do not label it a repeat statistic."""
    assert values and all(isinstance(v, (int, float)) for v in values)
    if len(values) == 1:
        return dict(n=1, observed=values[0], **{key: None for key in FIELDS})
    return dict(n=len(values), observed=None, mean=statistics.mean(values),
        median=statistics.median(values), range=max(values)-min(values),
        sample_stddev=statistics.stdev(values))


def coverage(metrics):
    """Count saved objects per run, without combining different cache layers."""
    expanded = metrics['expanded_files']
    blocks = metrics['cache_layers']['block_devices']
    files = expanded['files']
    assert len(files) == len({(f['device'], f['inode']) for f in files})
    assert all('query_window_status' in f for f in files
               if f['status'] != 'SEPARATE_SHMEM_OR_HUGETLB')
    changed = [f for f in files if f['status'] == 'CHANGED_DURING_QUERY']
    before = [f for f in changed if f['change_phase'] == 'BETWEEN_INVENTORY_AND_QUERY']
    during = [f for f in changed if f['change_phase'] == 'DURING_QUERY']
    assert len(changed) == len(before)+len(during)
    unknown_blocks = [o for o in blocks['objects'] if o['queries']['cachestat']['status'] == 'UNKNOWN']
    loop = metrics['cache_layers']['outer_loop']
    regular_unknown = [f for f in files if f.get('query_window_status') == 'UNKNOWN']
    assert len(regular_unknown) == expanded['query_window_regular']['unknown_objects']
    assert len(before) == expanded['query_window_regular']['changed_before_query_objects']
    assert len(unknown_blocks) == blocks['method_totals']['cachestat']['unknown_objects']
    return dict(ordinary_strict_status=expanded['status'],
        ordinary_window_status=expanded['query_window_regular']['status'],
        ordinary_unknown_objects=len(regular_unknown),
        ordinary_changed_before_query_objects=len(before), ordinary_changed_during_query_objects=len(during),
        block_status=blocks['method_totals']['cachestat']['status'],
        block_unknown_objects=len(unknown_blocks), loop_status=loop['status'],
        loop_window_status=loop.get('query_window', {}).get('status'),
        loop_changed_during_query=bool((loop.get('residency') or {}).get('metadata_changed')),
        changed_regular_objects=[dict(device=f['device'], inode=f['inode'], status=f['status'],
            change_phase=f['change_phase'], path=f['aliases'][0]['path']) for f in changed],
        regular_unknown_details=[dict(device=f['device'], inode=f['inode'], status=f['status'],
            change_phase=f.get('change_phase'), path=f['aliases'][0]['path'],
            error=f.get('error'), errors=f.get('errors')) for f in regular_unknown],
        block_unknown_details=[dict(object_key=o['object_key'],
            error=o['queries']['cachestat'].get('error'), aliases=o['aliases']) for o in unknown_blocks],
        loop_error=loop.get('error'))


def load_runs():
    inputs = {}

    def read(path):
        path = Path(path)
        st = path.stat()
        inputs[str(path)] = dict(size=st.st_size, mtime_ns=st.st_mtime_ns)
        return json.loads(path.read_text())

    revalidated = {r['source_storage']: r for r in read(V3)['runs']}
    runs = []
    for task, label, source, repeats, steps, saves_at, grpo, restoring, layers in TASKS:
        source_path = ROOT/'results'/source/'summary.json'
        saved = read(source_path)
        assert len(saved['runs']) == repeats*3
        assert {(r['round'], r['schedule']) for r in saved['runs']} == {
            (n, schedule) for n in range(1, repeats+1) for schedule in SCHEDULES}
        for original in saved['runs']:
            r = dict(original)
            historical_status = r.get('original_status', r['status'])
            assert r['status'] in ('PASS', 'ACCEPTED_SEGMENTS_V3')
            assert not r.get('stop_timeout_events')
            check_source = None
            if r.get('closure_policy') != 'segments_v3' and r['storage'] in revalidated:
                item = revalidated[r['storage']]
                assert (item['task'], item['round'], item['schedule']) == (task, r['round'], r['schedule'])
                r.update(item['recomputed'])
                check_source = str(V3)
            assert r['host_scope'] == 'experiment_slice_cgroup'
            assert r['experiment_scope_validation'] == 'PASS' and r['closure_policy'] == 'segments_v3'
            assert r['experiment_memory_context']['status'] == 'AVAILABLE'
            assert r['periodic_file_closure']['status'] == 'PASS'
            assert abs(r['endpoint_file_closure']['parent_minus_children_bytes']) <= 2**20
            assert r['pss_denominator'] == 7 and len(r['pss_processes']) == 7
            assert len({p['identity']['pid'] for p in r['pss_processes']}) == 7
            assert all(p['uid'] == 0 for p in r['pss_processes'])
            assert abs(r['pss_total_mib']/7-r['pss_per_guest_mib']) < 1e-9
            metrics_path = Path(r['raw'])/'schedule-smoke-metrics.json'
            metrics = read(metrics_path)
            p1 = metrics['p1']['files']
            assert len(p1) == r['lower_physical_files'] == len({(f['device'], f['inode']) for f in p1})
            assert r['lower_logical_layers'] == layers and all(f['mincore_permission'] for f in p1)
            assert r['lower_p1_mib'] == sum(f['resident_bytes'] for f in p1)/2**20
            parent = metrics['experiment_memory_p1']['experiment_memory']
            assert parent['parent'] == r['experiment_parent']
            for key in ('current', 'file', 'anon', 'kernel'):
                assert r[f'slice_{key}_p1_mib'] == parent['parent_counters'][key]/2**20
                assert r[f'direct_children_{key}_p1_mib'] == sum(
                    c[key] for c in parent['direct_children'].values())/2**20
            pss = sum(p['pss_kib'] for p in metrics['pss']['processes'])*1024
            assert pss == metrics['pss']['total_bytes']
            assert r['pss_total_mib'] == pss/2**20
            actual = grpo if r['schedule'] == 'grpo' else restoring
            assert r['actual_actions'] == actual and r['logical_actions'] == grpo
            expected_saves, expected_restores = (0, 0) if r['schedule'] == 'grpo' else (2, 6)
            assert len(r['saves']) == expected_saves and len(r['restores']) == expected_restores
            for name, events in (('save', r['saves']), ('restore', r['restores'])):
                assert abs(r[f'{name}_total_seconds']-sum(int(e['latency_ns']) for e in events)/1e9) < 1e-9
            r.update(task=task, label=label, source_summary=str(source_path), source_run_status=historical_status,
                revalidation_summary=check_source or r.get('revalidation_summary'),
                metrics_path=str(metrics_path), save_count=len(r['saves']), restore_count=len(r['restores']),
                coverage=coverage(metrics))
            runs.append(r)
    assert len(runs) == 45 and len({r['storage'] for r in runs}) == 45
    old_path = ROOT/'results/eight-task-schedules-20261110/summary.json'
    historical = [r for r in read(old_path)['runs_detail'] if r['task'] == 'django16819']
    assert len(historical) == 9
    return runs, historical, inputs


def aggregate(runs):
    result = []
    for task, label, _, repeats, *_ in TASKS:
        for schedule in SCHEDULES:
            chosen = [r for r in runs if (r['task'], r['schedule']) == (task, schedule)]
            assert len(chosen) == repeats
            result.append(dict(task=task, label=label, schedule=schedule, n=len(chosen),
                metrics={key: describe([r[key] for r in chosen]) for key, _ in (*MEMORY, *TIME)},
                logical_layers=sorted({r['lower_logical_layers'] for r in chosen}),
                active_physical_files=sorted({r['lower_physical_files'] for r in chosen})))
    return result


def fmt(value):
    return '—' if value is None else f'{value:.3f}'


def table(header, rows):
    return ['| '+' | '.join(header)+' |', '| '+' | '.join('---' for _ in header)+' |',
        *['| '+' | '.join(map(str, r))+' |' for r in rows]]


def metric_report(groups, metrics, title):
    assert all(g['n'] == 3 for g in groups)
    return [title, '', '每格为**均值／中位数／极差／样本标准差**；全部n=3，三轮等权，不跨任务或跨调度合并，不剔除慢组。', '',
        *table(['任务', '调度', 'n', *[label for _, label in metrics]], [
            [g['label'], g['schedule'], g['n'], *[
                '／'.join(fmt(g['metrics'][key][field]) for field in FIELDS) for key, _ in metrics]] for g in groups])]


def coverage_totals(runs):
    result = []
    for task, label, *_ in TASKS:
        rows = [r for r in runs if r['task'] == task]
        c = [r['coverage'] for r in rows]
        result.append(dict(task=task, label=label, runs=len(rows),
            ordinary_partial_runs=sum(x['ordinary_strict_status'] == 'PARTIAL' for x in c),
            ordinary_window_partial_runs=sum(x['ordinary_window_status'] == 'PARTIAL' for x in c),
            ordinary_unknown_objects=sum(x['ordinary_unknown_objects'] for x in c),
            block_unknown_runs=sum(x['block_unknown_objects'] > 0 for x in c),
            block_unknown_objects=sum(x['block_unknown_objects'] for x in c),
            loop_unknown_runs=sum(x['loop_status'] == 'UNKNOWN' for x in c),
            loop_changed_during_query_runs=sum(x['loop_changed_during_query'] for x in c),
            regular_changed_before_query_objects=sum(x['ordinary_changed_before_query_objects'] for x in c),
            regular_changed_during_query_objects=sum(x['ordinary_changed_during_query_objects'] for x in c)))
    return result


COMPARE_METRICS = ('pss_per_guest_mib', 'schedule_seconds',
                   'slice_current_peak_mib', 'slice_current_schedule_peak_mib')
CURRENT_METRICS = COMPARE_METRICS[2:]


def order(values):
    """Return descending groups, preserving exact ties rather than inventing an order."""
    return [sorted(s for s, value in values.items() if value == v)
            for v in sorted(set(values.values()), reverse=True)]


def inverted_pairs(reference, actual):
    return [f'{a.upper()} > {b.upper()}（PSS相反）' for a in SCHEDULES for b in SCHEDULES
            if actual[a] > actual[b] and reference[a] < reference[b]]


def compare_schedules(runs, groups):
    index = {(g['task'], g['schedule']): g for g in groups}
    task_rankings, round_rankings, pss_exceptions, time_exceptions = [], [], [], []
    for task, label, *_ in TASKS:
        for field in ('mean', 'median'):
            values = {key: {s: index[task, s]['metrics'][key][field] for s in SCHEDULES}
                      for key in COMPARE_METRICS}
            task_rankings.append(dict(task=task, label=label, statistic=field, values=values,
                order={k: order(v) for k, v in values.items()},
                current_matches_pss={key: order(values[key]) == order(values['pss_per_guest_mib']) for key in CURRENT_METRICS},
                inverted_pairs={key: inverted_pairs(values['pss_per_guest_mib'], values[key]) for key in CURRENT_METRICS}))
        for n in (1, 2, 3):
            subset = {r['schedule']: r for r in runs if (r['task'], r['round']) == (task, n)}
            assert set(subset) == set(SCHEDULES)
            values = {key: {s: subset[s][key] for s in SCHEDULES} for key in COMPARE_METRICS}
            round_rankings.append(dict(task=task, label=label, round=n, values=values,
                order={k: order(v) for k, v in values.items()},
                current_matches_pss={key: order(values[key]) == order(values['pss_per_guest_mib']) for key in CURRENT_METRICS},
                inverted_pairs={key: inverted_pairs(values['pss_per_guest_mib'], values[key]) for key in CURRENT_METRICS}))
            for key, target, alternatives, exceptions in (
                    ('pss_per_guest_mib', 'grpo', ('bpo', 'tvcache'), pss_exceptions),
                    ('schedule_seconds', 'tvcache', ('grpo', 'bpo'), time_exceptions)):
                for schedule in alternatives:
                    diff = values[key][schedule]-values[key][target]
                    if diff > 0:
                        exceptions.append(dict(task=task, label=label, round=n, schedule=schedule,
                            reference=target, value=values[key][schedule], reference_value=values[key][target], difference=diff))
    mismatch_counts = {field: {key: sum(not r['current_matches_pss'][key] for r in task_rankings if r['statistic']==field)
                               for key in CURRENT_METRICS} for field in ('mean', 'median')}
    return dict(task_rankings=task_rankings, round_rankings=round_rankings,
                pss_round_exceptions=pss_exceptions, schedule_round_exceptions=time_exceptions,
                current_mismatch_task_counts=mismatch_counts)


def comparison_report(runs, groups, totals, historical, findings):
    def ranking(row, key):
        return ' > '.join(' = '.join(s.upper()+' '+fmt(row['values'][key][s]) for s in tied)
                          for tied in row['order'][key])
    def rule_table(key):
        return table(['任务', '统计量', '由大到小（含数值）'], [
            [r['label'], '均值' if r['statistic']=='mean' else '中位数', ranking(r, key)]
            for r in findings['task_rankings']])
    def task_exceptions(key, target):
        rows=[]
        for r in findings['task_rankings']:
            v=r['values'][key]
            for schedule in SCHEDULES:
                if v[schedule] > v[target]:
                    rows.append([r['label'], '均值' if r['statistic']=='mean' else '中位数', schedule.upper(), fmt(v[schedule]), fmt(v[target]), fmt(v[schedule]-v[target])])
        return table(['任务', '统计量', '超过目标的调度', '该调度', target.upper(), '高出'], rows) if rows else ['任务级反例：无（均值和中位数均无其他调度超过目标调度）。']
    def round_exceptions(exceptions, unit):
        return table(['任务', '轮次', '超过目标的调度', '该调度'+unit, '目标调度'+unit, '高出'+unit],
            [[r['label'], r['round'], r['schedule'].upper(), fmt(r['value']), fmt(r['reference_value']), fmt(r['difference'])] for r in exceptions])
    lines=['# 五任务正式对照：45份新父组范围数据','',
        '**五任务三调度各3份，合计45份。Django已从各n=1更新为各n=3；本次仅离线读取，没有业务运行或重新采集。**','',
        '[内存总表](MEMORY.md) · [时间总表](TIME.md) · [逐次数据](runs.csv) · [逐次覆盖](coverage.csv) · [未舍入统计与排序过程](summary.json)','',
        '## 方法与纳入范围','',
        '每个任务、每种调度独立统计3次，三轮等权，包括Django已获确认的首轮与新增第2、3轮。算术均值、中位数、极差=max−min、样本标准差（分母n−1）均完整给出。不跨任务求平均、不混入旧Docker范围、不剔除慢组。排除Prettier／Sphinx／Xarray、五个外部单沙箱任务及所有未获接受的失败记录。','',
        '主值host_scope=experiment_slice_cgroup，读本次独占slice父组memory.current，file／anon／kernel读父组memory.stat；Σ直接子组只作分解，不与父组相加。两种current峰值分别为任务内采集窗口和调度窗口；不含更早卷及输入准备，也不自动包含slice外的旧缓存或其他工作负载。','',
        'PSS来自七root VMM的smaps_rollup，将共享映射页按共享进程数分摊，PSS/N分母7。它只覆盖映射且驻留的页，不含全部未映射文件缓存或独立服务。mincore只查列出的目标文件，按设备号:inode去重并保存CAP_FOWNER权限证据；覆盖层数／活跃文件数见内存总表。父子、PSS和mincore是各自的查询窗口；current、PSS、mincore不相加。','',
        '内存统一MiB，1 MiB=1,048,576字节；时间为秒，纳秒除以1,000,000,000。工具累计是实际调用耗时之和，可能大于墙钟，复用前缀不补算。保存／恢复次数和调用累计时间分列。用未舍入数统计、排序和比较；展示三位小数，采用Python固定小数格式（最近偶数舍入）。排序由大到小，精确相等用等号，不以展示值判断相等。','',
        '全部采用已保存的segments_v3验收：终点file绝对差≤1 MiB；周期每段必须恢复、持续≤1秒、偏离点占比≤5%；峰值在偏离段时采用前后闭合边界实测替代并保留原值。已有周期重放证据适用于同一数据，本次不重复采集。原FAIL、MISMATCH和历史规则判定保留。','',
        '## Django九份来源，三轮等权','']
    django=[r for r in runs if r['task']=='django16819']
    lines+=table(['轮次','调度','本次采用状态','原入口状态','来源类别','原始目录'],[
        [r['round'],r['schedule'],r['status'],r['source_run_status'],r['measurement_origin'],'`'+r['raw']+'`'] for r in django])
    lines+=['','第1轮GRPO为ACCEPTED_SEGMENTS_V3，原矩阵FAIL保留；BPO／TVCache为原PASS。第2轮BPO→TVCache→GRPO来自补充批次，第3轮TVCache→GRPO→BPO来自超时兜底改动后的续跑批次。两轮均为新跑的原入口PASS，不是本次汇总重跑。三轮不是同日同时运行；等权混合统计描述这九份，不代表环境效应已被单独分离。','',
        '第2轮批次：results/django-slice-v3-supplement-20261113T050808Z/；第3轮批次：results/django-slice-v3-third-round-20261113T062231Z/。第3轮首次TVCache的monitor-stop超时记录仍为FAIL，不纳入45份。','',
        '## 规律一：五任务均值和中位数的PSS/N均为GRPO最高','',
        '单位MiB，数值和排序均重算如下：','',*rule_table('pss_per_guest_mib'),' ',*task_exceptions('pss_per_guest_mib','grpo'),' ',
        '逐轮并非全成立，以下列出全部超过GRPO的反例：','',*round_exceptions(findings['pss_round_exceptions'],' MiB'),' ',
        'Django补至n=3后仍为GRPO最高，三轮均无此项反例。五任务统计方向一致，但C4第3轮与文本编辑第2轮反例说明它不是逐轮必然规律。','',
        '## 规律二：TVCache最长只在四任务的均值下成立，Django取决于统计量','',
        '单位秒：','',*rule_table('schedule_seconds'),' ',*task_exceptions('schedule_seconds','tvcache'),' ',
        '逐轮反例（所有比TVCache更长的调度）：','',*round_exceptions(findings['schedule_round_exceptions'],' 秒'),' ',
        '### Django由n=1到n=3的变化','']
    lines+=table(['轮次','GRPO秒','BPO秒','TVCache秒','从长到短'],[
        [r['round'],*[fmt(r['values']['schedule_seconds'][x]) for x in SCHEDULES],ranking(r,'schedule_seconds')]
        for r in findings['round_rankings'] if r['task']=='django16819'])
    lines+=['','首轮TVCache为36.541秒，确实最短；第2、3轮分别为51.455和52.584秒，均为该轮最长。合并后均值为BPO 49.472 > TVCache 46.860 > GRPO 41.968，BPO比TVCache长2.612秒，因此Django仍是“均值TVCache最长”的任务级反例，但不再是“均值TVCache最短”。中位数为TVCache 51.455 > BPO 49.392 > GRPO 41.946，Django在中位数下不再是反例。首轮低值保留，不因波动剔除。','',
        '## 规律三：current两种峰值与PSS/N没有统一排序','',
        '下表给出全部任务的均值和中位数；current为MiB峰值，PSS/N为MiB终点平均，窗口不同，不可替代。','']
    lines+=table(['任务','统计量','current采集峰值排序与值','current调度峰值排序与值','PSS/N排序与值','采集/调度排序均一致？'],[
        [r['label'],'均值' if r['statistic']=='mean' else '中位数',ranking(r,'slice_current_peak_mib'),ranking(r,'slice_current_schedule_peak_mib'),ranking(r,'pss_per_guest_mib'),
         '是' if all(r['current_matches_pss'].values()) else '否'] for r in findings['task_rankings']])
    counts=findings['current_mismatch_task_counts']
    lines+=['',*table(['统计量','采集峰值排序不一致任务数/5','调度峰值排序不一致任务数/5'],[
        ['均值' if field=='mean' else '中位数',counts[field]['slice_current_peak_mib'],counts[field]['slice_current_schedule_peak_mib']] for field in ('mean','median')]),'',
        '均值下仍是5任务中4个不一致，只有Cython两种峰值都与PSS/N一致。Django新增数据未改变这一计数；它的current两种峰值均为TVCache > BPO > GRPO，而PSS/N为GRPO > TVCache > BPO。中位数下采集峰值5/5不一致、调度峰值4/5不一致：Cython采集峰值中位数的BPO高于TVCache，与PSS/N相反。C4均值和中位数的BPO／TVCache次序也会交换。','',
        '### 逐轮检查：两种current与PSS/N的反向比较','',
        '为使反例完整可查，列出全部15个任务轮次；“A > B（PSS相反）”表示current里A大于B，PSS/N里却B大于A。数值和三调度逐次原值见runs.csv与summary.json的findings.round_rankings。','']
    lines+=table(['任务','轮次','PSS/N排序与值','current采集峰值排序与值','current调度峰值排序与值','采集反向比较','调度反向比较'],[
        [r['label'],r['round'],ranking(r,'pss_per_guest_mib'),ranking(r,'slice_current_peak_mib'),ranking(r,'slice_current_schedule_peak_mib'),
         '；'.join(r['inverted_pairs']['slice_current_peak_mib']) or '无',
         '；'.join(r['inverted_pairs']['slice_current_schedule_peak_mib']) or '无'] for r in findings['round_rankings']])
    lines+=['','## 覆盖边界：五任务45份','',
        '全部是七实例相同原路径＋前缀复用，没有原生七分支。任务实际动作数、逻辑动作数和保存点见TIME.md。mincore每次活跃目标文件：Django10层/10文件、C4九层/9文件、Matplotlib10层/10文件、文本编辑八层/8文件、Cython七层/7文件；不同任务目标不同，不直接比大小推导收益。','',
        '份数按运行计；变化／未知对象数按每次查询内去重后累加，不是跨运行并集。块设备UNKNOWN份数指含至少一个UNKNOWN对象，聚合状态可能为PARTIAL。清单后查询前变化与查询中变化分列；普通文件、块设备、loop不合并相加。','']
    lines+=table(['任务','份数','普通文件严格PARTIAL份数','普通文件窗口PARTIAL份数/未知对象数','块设备含UNKNOWN份数/对象数','loop严格UNKNOWN份数','普通文件查询前变化对象数','普通文件查询中变化对象数','loop查询中变化次数'],[
        [t['label'],t['runs'],t['ordinary_partial_runs'],f"{t['ordinary_window_partial_runs']}/{t['ordinary_unknown_objects']}",
         f"{t['block_unknown_runs']}/{t['block_unknown_objects']}",t['loop_unknown_runs'],t['regular_changed_before_query_objects'],
         t['regular_changed_during_query_objects'],t['loop_changed_during_query_runs']] for t in totals])
    lines+=['','0仅来自保存的完整清单明确无该状态，不补缺失项。逐次对象、阶段和错误见coverage.csv及summary.json的coverage_details。PARTIAL表示严格对象状态不完整；UNKNOWN表示该对象查询无法确认，不等于占用0。','',
        '- 父组只含既定采集窗口，不含更早卷准备；PSS只覆盖七VMM已映射驻留页；mincore只覆盖所列文件。三者不可相加，也不足以声称全部host缓存无遗漏。',
        '- Django第3轮前新增5秒stop超时后定向SIGKILL、最多2秒确认消失的兜底，触发仍FAIL。34项离线测试通过；该轮三次没有超时或强杀，不能称已验证实机强杀路径。前两轮未使用该改动，来源分别保留。',
        '- Django首轮GRPO原FAIL与ACCEPTED_SEGMENTS_V3并存；第3轮首次TVCache业务通过但采集超时，仍FAIL且排除。第3轮正常通过不能证明此前停滞的底层原因。',
        '- Matplotlib首轮GRPO原入口及v2 FAIL（最长段0.600397290秒），已获准按v3接受；其余8份原入口PASS，不称九次原入口全PASS。',
        '- Cython旧首轮TLS EOF、0业务动作、0有效份，旧FAIL和失败卷保留。本表采用获准重跑后的9份；未查明TLS关闭端或原因，后续成功不代替原因诊断。原批次.artifacts/cython-slice-v3-20261112T072624Z/，失败卷.artifacts/storage-isolated-service/20261112T072702Z-1948024/volume.ext4。','',
        '## Django旧Docker范围对照，不混算','',
        '旧值来自另九份运行，仅并列，既不配对，也不作倍率换算。新值均为三次均值：','']
    idx={(g['task'],g['schedule']):g for g in groups}
    lines+=table(['调度','新n','新父组current采集峰值MiB','新父组current调度峰值MiB','新父组file终点MiB','旧Docker n','旧Docker current采集峰值MiB','旧Docker file终点MiB'],[
        [a,3,*[fmt(idx['django16819',a]['metrics'][k]['mean']) for k in ('slice_current_peak_mib','slice_current_schedule_peak_mib','slice_file_p1_mib')],
         len([r for r in historical if r['schedule']==a]),fmt(statistics.mean(r['current_full_peak_mib'] for r in historical if r['schedule']==a)),
         fmt(statistics.mean(r['file_before_p1_mib'] for r in historical if r['schedule']==a))] for a in SCHEDULES])
    lines+=['','## 来源与复核','',
        '45个storage互不重复，按任务／轮次／调度精确绑定。目标mincore字节和、父／直接子组终点字节、七VMM PSS及分母、保存恢复次数和计时与各份原始metrics交叉核对；全部15组、255个指标统计由未舍入逐次数据重算。Django原六份沿用已验证来源，不重跑；历史判定保留。','']
    lines += [f'- {label}：`results/{source}/summary.json`。' for _,label,source,*_ in TASKS]
    lines+=['- v3既存复算：results/closure-segments-v3-20261112T051632Z/summary.json；旧Docker仅对照：results/eight-task-schedules-20261110/summary.json。','',
        '原始路径、来源和原判定见runs.csv／summary.json；source_manifest.json保存读取路径、文件大小及修改时间，结束时检查不变。此次脚本无网络、子进程、cgroup操作或业务入口，只读既存文件、写全新目录。复现：`python3 -B scripts/report_slice_task_summary.py --output results/<新目录>`。','',
        '核对结果见[verification.json](verification.json)，离线回归见[tests.log](tests.log)；独立算术核对脚本为[verify.py](verify.py)。','',
        '## 结论','',
        '五任务各三次的均值与中位数一致支持GRPO的PSS/N最高，但C4、文本编辑存在逐轮反例。TVCache调度窗口最长在四任务的均值下成立；五任务的中位数下均成立。Django是均值层面的反例，补齐后由首轮最短变成均值居中、中位数最长，不能再沿用“Django的TVCache最短”的总体表述。','',
        'current两种峰值与PSS/N的均值排序仍有4/5任务不一致，仅Cython均值一致；中位数下采集峰值为5/5不一致、调度峰值4/5不一致。VMM映射驻留下降不能代替父组总记账下降，也不能直接换算为可多部署多少沙箱。','',
        '三次重复给出了本批波动，不足以证明普遍规律、统计显著性或调度因果；轮次跨时段、动态Git／pip、随机数据及默认回收没有单独控制。严格PARTIAL／UNKNOWN仍存在，未证明全部缓存无遗漏；也没有指定内存预算和延迟预算下的容量实测。','',
        '## 下一步建议（未执行）','',
        '现有排序问题已由45份离线数据回答，无需为此追加重复。若要回答部署密度，下一步先确认内存预算、延迟预算及起点范围，再确定最小容量验证；完成条件是给出该预算下可同时完成任务的沙箱数，而不只比较PSS或记账。']
    return lines


def publish(output, runs, groups, historical, inputs):
    output.mkdir(parents=True, exist_ok=False)

    def write(name, text):
        with (output/name).open('x') as f:
            f.write(text)

    def json_file(name, value):
        write(name, json.dumps(value, ensure_ascii=False, indent=2)+'\n')

    def csv_file(name, rows):
        with (output/name).open('x', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    memory = metric_report(groups, MEMORY, '# 内存总表（MiB）')
    memory[1:1] = ['', '1 MiB = 1,048,576字节。anon／kernel／Σ直接子组current均为终点，PSS和mincore各有自己的终点查询窗口。',
        'PSS/N分母固定7，是七VMM的平均比例驻留；不是整个实验组每沙箱成本。']
    memory += ['', '## mincore目标范围：层数与活跃物理文件数', '',
        '文件按设备号:inode去重；层数及实际文件数逐组核对。不同任务镜像和覆盖对象不同，不能直接比驻留大小判断收益。', '']
    memory += table(['任务', '调度', '原镜像层数', '每次活跃物理文件数'], [
        [g['label'], g['schedule'], '／'.join(map(str, g['logical_layers'])),
         '／'.join(map(str, g['active_physical_files']))] for g in groups])
    write('MEMORY.md', '\n'.join(memory)+'\n')
    timing = metric_report(groups, TIME, '# 时间总表（秒；保存恢复次数另注明）')
    timing[1:1] = ['', '工具累计为实际并发动作耗时之和，可能大于墙钟；不是任务运行时间。各调度实际动作数不同，只列绝对值与同调度波动，不计算跨调度加速比。',
        '控制程序整轮指任务控制程序controller_seconds，不是外层矩阵或卷准备整轮；保存／恢复计时为各次调用耗时之和。']
    timing += ['', '## 原负载与动作数', '', '全部为七实例相同原路径加前缀复用，不是原生七分支。逻辑动作数只描述路径完整性，不当物理工作量。', '']
    timing += table(['任务', '单实例原步数', '保存点（原步数后）', 'GRPO实际／逻辑', 'BPO及TVCache实际／逻辑', 'GRPO保存／恢复', '恢复组保存／恢复'], [
        [label, steps, '／'.join(map(str, saves)), f'{grpo}／{grpo}', f'{restoring}／{grpo}', '0／0', '2／6']
        for _, label, _, _, steps, saves, grpo, restoring, _ in TASKS])
    timing += ['', '逐次保存／恢复事件和耗时见summary.json的runs[].saves／restores，逐次绝对计时见runs.csv。']
    write('TIME.md', '\n'.join(timing)+'\n')
    stats_rows = []
    for g in groups:
        for key, label in (*MEMORY, *TIME):
            stats_rows.append(dict(task=g['task'], schedule=g['schedule'], metric=key, label=label,
                unit='MiB' if (key, label) in MEMORY else '次' if key in ('save_count', 'restore_count') else '秒',
                **g['metrics'][key]))
    csv_file('memory.csv', [r for r in stats_rows if r['unit'] == 'MiB'])
    csv_file('time.csv', [r for r in stats_rows if r['unit'] != 'MiB'])
    run_columns = ['task', 'round', 'schedule', 'source_run_status', 'measurement_origin', 'actual_actions', 'logical_actions',
        *[k for k, _ in (*MEMORY, *TIME)], 'lower_logical_layers', 'lower_physical_files',
        'experiment_parent', 'source_summary', 'revalidation_summary', 'raw', 'metrics_path']
    csv_file('runs.csv', [{k: r.get(k) for k in run_columns} for r in runs])
    totals = coverage_totals(runs)
    csv_file('coverage.csv', [{k: v for k, v in r['coverage'].items() if not isinstance(v, list)} |
        dict(task=r['task'], round=r['round'], schedule=r['schedule'], metrics_path=r['metrics_path']) for r in runs])
    findings = compare_schedules(runs, groups)
    write('REPORT.md', '\n'.join(comparison_report(runs, groups, totals, historical, findings))+'\n')
    retained = ['task', 'label', 'round', 'schedule', 'source_run_status', 'status', 'original_status', 'measurement_origin', 'source_summary', 'revalidation_summary',
        'raw', 'storage', 'metrics_path', 'experiment_parent', 'host_scope', 'experiment_scope_validation',
        'closure_policy', 'periodic_file_closure', 'endpoint_file_closure', 'peak_closure_checks',
        'pss_denominator', 'lower_logical_layers', 'lower_physical_files', 'actual_actions', 'logical_actions',
        'saves', 'restores', *[k for k, _ in (*MEMORY, *TIME)]]
    json_file('summary.json', dict(status='PASS', business_runs_started=0, sampling_calls=0,
        run_count=len(runs), group_count=len(groups), scope='experiment_slice_cgroup', policy='segments_v3',
        statistics=groups, coverage_totals=totals,
        coverage_details=[dict(task=r['task'], round=r['round'], schedule=r['schedule'],
            raw=r['raw'], metrics_path=r['metrics_path'], **r['coverage']) for r in runs],
        runs=[{key: r.get(key) for key in retained} for r in runs], historical_django_docker_records=historical,
        findings=findings))
    unchanged = []
    for path, before in inputs.items():
        st = Path(path).stat()
        after = dict(size=st.st_size, mtime_ns=st.st_mtime_ns)
        assert after == before, 'source changed during report: '+path
        unchanged.append(dict(path=path, before=before, after=after, unchanged=True))
    json_file('source_manifest.json', dict(status='PASS', read_only_inputs=unchanged,
        historical_files_written=0, business_runs_started=0, resampling_calls=0))
    print(json.dumps(dict(status='PASS', result_dir=str(output), runs=len(runs), groups=len(groups),
        business_runs_started=0, historical_files_written=0), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_relative_to(ROOT/'results') and not output.exists()
    runs, historical, inputs = load_runs()
    publish(output, runs, aggregate(runs), historical, inputs)


if __name__ == '__main__':
    main()
