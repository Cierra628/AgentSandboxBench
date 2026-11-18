"""Saved dedicated-slice schedule measurements; legacy Docker fields stay intact."""
from collections import Counter
import csv
import json
import math
from pathlib import Path
import statistics

from experiment_memory import HOST_SCOPE, KEYS, FILE_CLOSURE_THRESHOLD_BYTES

MIB = 2**20
ROOT = Path(__file__).resolve().parents[1]
METRICS = (
    ('slice_current_peak_mib','父组current采集窗口峰值，MiB'),
    ('slice_current_schedule_peak_mib','父组current调度窗口峰值，MiB'),
    ('slice_file_peak_mib','父组file采集窗口峰值，MiB'),
    ('slice_file_schedule_peak_mib','父组file调度窗口峰值，MiB'),
    *((f'slice_{k}_p1_mib',f'父组{k}终点，MiB') for k in KEYS),
    *((f'direct_children_{k}_p1_mib',f'直接子组{k}合计终点，MiB') for k in KEYS),
    ('pss_total_mib','七VMM终点PSS合计，MiB'),('lower_p1_mib','原镜像活跃文件P1，MiB'),
    ('pss_per_guest_mib','终点PSS/N，MiB'),
    ('schedule_seconds','调度窗口，秒'),('actions_download_seconds','动作及下载窗口，秒'),
    ('tools_seconds','工具累计，秒'),('controller_seconds','任务控制程序整轮，秒'),
    ('creation_request_span_seconds','创建／恢复请求跨度，秒'),
    ('save_total_seconds','保存调用累计，秒'),('restore_total_seconds','恢复调用累计，秒'),
)


def read(path):return json.loads(Path(path).read_text())


# Historical rule only; it no longer determines the new periodic verdict.
RECOVERY_POINTS = 2
CLOSURE_POLICY = 'segments_v3'
MAX_SEGMENT_SECONDS = 1.0
NOMINAL_INTERVAL_SECONDS = 0.1
# Retained exclusively for the historical segments_v2 calculation.
MAX_CONSECUTIVE_DEVIATIONS = 3
MAX_DEVIATION_FRACTION = 0.05


def historical_periodic_closure(samples):
    """Each mismatching point must recover within its own next two points."""
    states = [r.get('experiment_memory',{}).get('file_closure',{}).get('status') for r in samples]
    bad = [i for i,s in enumerate(states) if s=='MISMATCH']
    incomplete = [i for i,s in enumerate(states) if s not in ('WITHIN_THRESHOLD','MISMATCH')]
    recovered = {}; windows = []
    for i in bad:
        recovered[i] = next((j for j in range(i+1,min(len(samples),i+RECOVERY_POINTS+1))
                             if states[j]=='WITHIN_THRESHOLD'),None)
        if not windows or i!=windows[-1]['end_index']+1:
            windows.append(dict(start_index=i,end_index=i))
        else:windows[-1]['end_index']=i
    for w in windows:
        j=w['end_index']+1
        w['recovery_index']=j if j<len(states) and states[j]=='WITHIN_THRESHOLD' else None
    deltas=[samples[i]['experiment_memory']['file_closure']['parent_minus_children_bytes'] for i in bad]
    return dict(status='FAIL' if incomplete or any(j is None for j in recovered.values()) else 'PASS',
        threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES,recovery_points=RECOVERY_POINTS,
        deviation_count=len(bad),max_positive_bytes=max([0,*deltas]),min_negative_bytes=min([0,*deltas]),
        longest_consecutive_points=max((w['end_index']-w['start_index']+1 for w in windows),default=0),
        unrecovered_indices=[i for i,j in recovered.items() if j is None],incomplete_indices=incomplete,
        recovery_by_index=recovered,windows=windows)


def periodic_closure_v2(samples):
    """Historical segments_v2: at most three points and five percent overall."""
    historical = historical_periodic_closure(samples)
    states = [r.get('experiment_memory',{}).get('file_closure',{}).get('status') for r in samples]
    windows = [dict(w) for w in historical['windows']]
    for w in windows:
        w['recovery_index'] = next((i for i in range(w['end_index']+1,len(states))
                                   if states[i]=='WITHIN_THRESHOLD'),None)
    fraction = historical['deviation_count']/len(samples) if samples else None
    recovered = all(w['recovery_index'] is not None for w in windows)
    requirements = dict(
        all_segments_recovered=dict(actual=recovered,required=True,status='PASS' if recovered else 'FAIL'),
        longest_segment=dict(actual=historical['longest_consecutive_points'],maximum=MAX_CONSECUTIVE_DEVIATIONS,
            status='PASS' if historical['longest_consecutive_points']<=MAX_CONSECUTIVE_DEVIATIONS else 'FAIL'),
        deviation_fraction=dict(actual=fraction,maximum=MAX_DEVIATION_FRACTION,
            status='PASS' if fraction is not None and fraction<=MAX_DEVIATION_FRACTION else 'FAIL'))
    complete = bool(samples) and not historical['incomplete_indices']
    return dict(policy='segments_v2',status='PASS' if complete and all(v['status']=='PASS' for v in requirements.values()) else 'FAIL',
        threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES,total_points=len(samples),
        deviation_count=historical['deviation_count'],deviation_fraction=fraction,
        max_positive_bytes=historical['max_positive_bytes'],min_negative_bytes=historical['min_negative_bytes'],
        longest_consecutive_points=historical['longest_consecutive_points'],
        max_consecutive_points=MAX_CONSECUTIVE_DEVIATIONS,max_deviation_fraction=MAX_DEVIATION_FRACTION,
        all_segments_recovered=recovered,requirements=requirements,
        data_complete=complete,incomplete_indices=historical['incomplete_indices'],
        unrecovered_windows=[w for w in windows if w['recovery_index'] is None],windows=windows)


def periodic_closure(samples):
    """segments_v3: first deviating sample to first recovered sample, <=1 s."""
    old = periodic_closure_v2(samples)
    windows = []
    for previous in old['windows']:
        window = dict(previous)
        start, recovered = window['start_index'], window['recovery_index']
        times = [r.get('monotonic_ns') for r in samples[start:recovered+1]] if recovered is not None else []
        valid_times = (bool(times) and
            all(isinstance(t,(int,float)) and not isinstance(t,bool) and math.isfinite(t) for t in times) and
            all(b>a for a,b in zip(times,times[1:])))
        if valid_times:
            seconds = (times[-1]-times[0])/1e9
            basis, reason = 'monotonic_ns', None
        else:
            seconds = (window['end_index']-start+1)*NOMINAL_INTERVAL_SECONDS
            basis = 'nominal_interval'
            reason = ('no periodic recovery sample' if recovered is None else
                      'missing, non-finite or non-increasing monotonic_ns within segment and recovery')
        window.update(duration_seconds=seconds,duration_basis=basis,duration_fallback_reason=reason)
        windows.append(window)
    longest = max((w['duration_seconds'] for w in windows),default=0.0)
    requirements = dict(old['requirements'],longest_segment=dict(actual=longest,
        maximum=MAX_SEGMENT_SECONDS,unit='seconds',status='PASS' if longest<=MAX_SEGMENT_SECONDS else 'FAIL'))
    result = dict(old,policy=CLOSURE_POLICY,windows=windows,requirements=requirements,
        status='PASS' if old['data_complete'] and all(v['status']=='PASS' for v in requirements.values()) else 'FAIL',
        longest_segment_seconds=longest,max_segment_seconds=MAX_SEGMENT_SECONDS,
        nominal_interval_seconds=NOMINAL_INTERVAL_SECONDS,
        duration_fallback_segments=sum(w['duration_basis']=='nominal_interval' for w in windows),
        unrecovered_windows=[w for w in windows if w['recovery_index'] is None],
        historical_segments_v2=old)
    result.pop('max_consecutive_points')
    return result


def checked_peak(samples, indices, key, closure):
    """Keep raw peaks; replacement uses actual closed boundary samples only."""
    if not indices:return dict(status='UNAVAILABLE',raw_bytes=None,value_bytes=None)
    value=lambda i:samples[i]['experiment_memory']['parent_counters'][key]
    raw=max(indices,key=value)
    closed=lambda i:samples[i]['experiment_memory']['file_closure']['status']=='WITHIN_THRESHOLD'
    result=dict(status='UNCHANGED',raw_index=raw,raw_bytes=value(raw),value_bytes=value(raw),
                raw_monotonic_ns=samples[raw]['monotonic_ns'],affected=not closed(raw))
    if closed(raw):return result
    window=next((w for w in closure['windows'] if w['start_index']<=raw<=w['end_index']),None)
    # A deviation interval contains no closed point: include its adjacent closed
    # boundaries, but never interpolate or use points outside the requested window.
    candidates=[] if window is None else [i for i in (window['start_index']-1,window['recovery_index'])
        if i is not None and i in indices and closed(i)]
    result['deviation_window']=window
    if len(candidates)!=2:
        return dict(result,status='UNAVAILABLE',value_bytes=None,reason='two closed boundary samples required inside peak window')
    replacement=max(candidates,key=value)
    remaining=[i for i in indices if closed(i)]
    chosen=max(remaining,key=value)
    result.update(status='REPLACED_WITH_OBSERVED_CLOSED_SAMPLE',replacement_index=replacement,
        boundary_indices=candidates,boundary_bytes=[value(i) for i in candidates],
        replacement_bytes=value(replacement),value_bytes=value(chosen),selected_index=chosen,
        selected_monotonic_ns=samples[chosen]['monotonic_ns'])
    return result


def deviation_details(samples, periodic, schedule_start):
    """Saved point identity, signed difference and recovery; no new queries."""
    result=[]
    first=samples[0]['monotonic_ns'] if samples else None
    for window in periodic['windows']:
        recovered=window['recovery_index']
        for i in range(window['start_index'],window['end_index']+1):
            row=samples[i];value=row['experiment_memory'];t=row['monotonic_ns']
            result.append(dict(index=i,monotonic_ns=t,
                seconds_since_collection_start=(t-first)/1e9,
                seconds_since_schedule_start=(t-schedule_start)/1e9 if schedule_start is not None else None,
                parent_file_bytes=value.get('parent_counters',{}).get('file'),
                children_file_bytes=value.get('direct_children_sum',{}).get('file'),
                parent_minus_children_bytes=value['file_closure']['parent_minus_children_bytes'],
                recovery_index=recovered,recovery_monotonic_ns=samples[recovered]['monotonic_ns'] if recovered is not None else None))
    return result


def collect_parent(storage, *, creation_receipt=None):
    raw = storage/'external-workload'
    task = read(raw/'result.json'); metrics = read(raw/'schedule-smoke-metrics.json')
    context = task.get('experiment_memory_context',{})
    outer = read(storage/'result.json') if creation_receipt is None else creation_receipt
    creation = outer.get('host_file_creation',{})
    path = raw/'host-file/experiment-memory.jsonl'
    samples = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    endpoint = metrics.get('experiment_memory_p1',{})
    p1 = endpoint.get('experiment_memory',{})
    errors = []
    if context.get('status')!='AVAILABLE':errors.append('experiment parent unavailable: '+str(context))
    parent = context.get('parent')
    if not parent or parent!=creation.get('cgroup') or p1.get('parent')!=parent:
        errors.append('parent paths do not agree with creation and endpoint records')
    if not samples:errors.append('no parent periodic samples')
    observed = []; closure_counts = Counter(); deltas = []; anomalies = []
    for index, row in enumerate([*samples,endpoint]):
        value = row.get('experiment_memory',{})
        closure = value.get('file_closure') or {}
        state = closure.get('status','UNAVAILABLE'); closure_counts[state]+=1
        delta = closure.get('parent_minus_children_bytes')
        if delta is not None:deltas.append(delta)
        valid = (context.get('status')=='AVAILABLE' and parent is not None and
                 value.get('host_scope')==HOST_SCOPE and value.get('status')=='OBSERVED' and
                 value.get('parent')==parent and all(isinstance(value.get('parent_counters',{}).get(k),int) for k in KEYS))
        if not valid or state!='WITHIN_THRESHOLD':
            anomalies.append(dict(source='periodic' if index<len(samples) else 'endpoint',index=index,
                                  monotonic_ns=row.get('monotonic_ns'),value=value))
        if not valid:errors.append(f'invalid parent observation at index {index}')
        if valid and index<len(samples):observed.append(row)
    periodic = periodic_closure(samples)
    if periodic['status']!='PASS':errors.append('periodic file closure failed segment recovery, length, fraction or completeness checks')
    if any(a['value'].get('status')!='OBSERVED' for a in anomalies):errors.append('parent data incomplete')
    if (p1.get('file_closure') or {}).get('status')!='WITHIN_THRESHOLD':errors.append('endpoint file closure failed')
    start = task.get('schedule_start_monotonic_ns'); end = task.get('original_paths_end_monotonic_ns')
    window = [r for r in observed if start is not None and end is not None and start<=r['monotonic_ns']<=end]
    if not window:errors.append('no parent samples in schedule window')
    observed_ids={id(r) for r in observed}
    indices=[i for i,r in enumerate(samples) if id(r) in observed_ids]
    schedule_indices=[i for i in indices if start is not None and end is not None and start<=samples[i]['monotonic_ns']<=end]
    peaks={f'{key}_{scope}':checked_peak(samples,selected,key,periodic)
        for key in ('current','file') for scope,selected in (('collection',indices),('schedule',schedule_indices))}
    if any(v['status']=='UNAVAILABLE' for v in peaks.values()):errors.append('peak has no valid observed closed replacement')
    def peak_row(name):
        v=peaks[name];i=v.get('selected_index',v.get('raw_index'))
        return samples[i] if i is not None and v['value_bytes'] is not None else None
    peak=peak_row('current_collection');schedule_peak=peak_row('current_schedule')
    endpoint_valid = context.get('status')=='AVAILABLE' and parent is not None and p1.get('parent')==parent and p1.get('status')=='OBSERVED'
    p = (p1.get('parent_counters') or {}) if endpoint_valid else {}
    sums = (p1.get('direct_children_sum') or {}) if endpoint_valid else {}
    service = metrics.get('private_service_cgroup',metrics.get('cgroup'))
    docker = (p1.get('direct_children') or {}).get(service)
    if docker is None:errors.append('Docker direct child not present at endpoint')
    files = metrics['p1']['files']
    if any(not f.get('mincore_permission') for f in files):errors.append('mincore permission evidence missing')
    pss = metrics['pss']['processes']
    if len({r['identity']['pid'] for r in pss})!=7:errors.append('seven distinct endpoint VMMs missing')
    calls = read(raw/'calls.json')
    creates = [c for c in calls if c['stage'].startswith(('cold-','restore-')) and not c['stage'].startswith('restored-')]
    span = ((max(c['command_end_monotonic_ns'] for c in creates)-min(c['start_monotonic_ns'] for c in creates))/1e9 if creates else None)
    save_restore=task.get('save_restore')
    pss_total=metrics.get('pss',{}).get('total_bytes')
    result = dict(host_scope=HOST_SCOPE,legacy_host_scope='private_service_cgroup',
        experiment_scope_validation='FAIL' if errors else 'PASS',experiment_scope_errors=errors,
        experiment_parent=parent,experiment_memory_context=context,parent_series=str(path),
        slice_current_peak_mib=peak['experiment_memory']['parent_counters']['current']/MIB if peak else None,
        slice_current_schedule_peak_mib=schedule_peak['experiment_memory']['parent_counters']['current']/MIB if schedule_peak else None,
        slice_current_peak_sample=peak,slice_current_schedule_peak_sample=schedule_peak,
        closure_policy=CLOSURE_POLICY,peak_closure_checks=peaks,periodic_file_closure=periodic,
        historical_periodic_file_closure=historical_periodic_closure(samples),
        historical_periodic_file_closure_v2=periodic['historical_segments_v2'],
        periodic_file_deviation_details=deviation_details(samples,periodic,start),
        pss_denominator=7,pss_per_guest_mib=pss_total/7/MIB if pss_total is not None else None,
        save_total_seconds=sum(int(v['latency_ns']) for v in save_restore['saves'])/1e9 if save_restore is not None else None,
        restore_total_seconds=sum(int(v['latency_ns']) for v in save_restore['restores'])/1e9 if save_restore is not None else None,
        endpoint_file_closure=p1.get('file_closure'),
        slice_file_peak_mib=peaks['file_collection']['value_bytes']/MIB if peaks['file_collection']['value_bytes'] is not None else None,
        slice_file_schedule_peak_mib=peaks['file_schedule']['value_bytes']/MIB if peaks['file_schedule']['value_bytes'] is not None else None,
        slice_p1=endpoint,endpoint_docker_counters=docker,
        file_closure_counts=dict(closure_counts),file_closure_max_abs_bytes=max(map(abs,deltas)) if deltas else None,
        file_closure_min_signed_bytes=min(deltas) if deltas else None,file_closure_max_signed_bytes=max(deltas) if deltas else None,
        file_closure_threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES,file_closure_anomalies=anomalies,
        periodic_parent_samples=len(samples),schedule_parent_samples=len(window),
        parent_query_wall_total_seconds=sum(r['duration_ns'] for r in samples)/1e9,
        parent_query_cpu_total_seconds=sum(r['cpu_ns'] for r in samples)/1e9,
        creation_request_span_seconds=span,creation_requests=creates,
        pss_processes=pss,mincore_files=files,mincore_query=metrics['p1'],
        parent_minus_same_run_docker_file_bytes=(p['file']-docker['file']) if p.get('file') is not None and docker else None,
        observation_window=dict(start_monotonic_ns=samples[0]['monotonic_ns'] if samples else None,
            end_monotonic_ns=samples[-1]['monotonic_ns'] if samples else None,
            meaning='Inner task collector start to cleanup; excludes earlier private volume/input preparation. No full-workflow peak claim.'))
    for key in KEYS:
        result[f'slice_{key}_p1_mib'] = p[key]/MIB if p.get(key) is not None else None
        result[f'direct_children_{key}_p1_mib'] = sums[key]/MIB if sums.get(key) is not None else None
    return result


def stats(values):
    return dict(count=len(values),mean=statistics.mean(values) if values else None,
        median=statistics.median(values) if values else None,minimum=min(values) if values else None,
        maximum=max(values) if values else None,range=max(values)-min(values) if values else None,
        sample_stddev=statistics.stdev(values) if len(values)>1 else None)


def closure_report(runs):
    """Common method and actual/limit tables for live reports and saved replay."""
    fmt=lambda v:'未知' if v is None else f'{v:.6f}'
    yesno=lambda v:'未知' if v is None else '是' if v else '否'
    lines=['## 三类闭合检查方法','',
        f'规则版本：`{CLOSURE_POLICY}`。file闭合阈值固定{FILE_CLOSURE_THRESHOLD_BYTES/MIB:g} MiB（{FILE_CLOSURE_THRESHOLD_BYTES:,}字节），保留父组file减全部直接子组file的有符号差。终点绝对差≤阈值才通过，否则FAIL。原始MISMATCH行不修改。','',
        f'周期三条必须同时通过：每段在周期序列结束前恢复至阈值内；单段持续时间≤{MAX_SEGMENT_SECONDS:g}秒；偏离点数／总周期点数≤{MAX_DEVIATION_FRACTION:.0%}。末尾缺恢复、空序列或缺项均FAIL，不用终点查询补恢复证据。持续时间为首个偏离点至首个恢复点的monotonic_ns之差；段内或恢复点时间戳缺失、无效或不递增时，以偏离点数×0.1秒换算并逐段注明。','',
        'current／file分别检查采集窗口和调度窗口峰值。原峰值落在偏离段时，用该段前后两个闭合边界的实测值替代该段，再与阶段其他闭合点取最大值。两个边界都必须在所报阶段内；缺边界则峰值不可用并FAIL。不插值、不平均父子值。父子一致仅指file满足阈值，不要求current／kernel相等。保存原峰值、两个边界、边界替代值和最终值。','',
        '| 任务／调度／轮次 | 总点数 | 偏离点数 | 占比／上限／结果 | 最长段秒数／上限／结果／时间来源 | 全部恢复／要求／结果 | 终点差MiB／阈值／结果 | 峰值受影响 | 新判定 |',
        '| --- | ---: | ---: | --- | --- | --- | --- | --- | --- |']
    for r in runs:
        c=r.get('periodic_file_closure',{});e=r.get('endpoint_file_closure') or {}
        req=c.get('requirements',{});fraction=c.get('deviation_fraction');d=e.get('parent_minus_children_bytes')
        peaks=r.get('peak_closure_checks',{});affected=any(v.get('affected') for v in peaks.values()) if peaks else None
        lines.append(f"| {r.get('task')}／{r['schedule']}／{r.get('round')} | {c.get('total_points','未知')} | {c.get('deviation_count','未知')} | "
            f"{fmt(fraction*100 if fraction is not None else None)}%／{MAX_DEVIATION_FRACTION:.0%}／{req.get('deviation_fraction',{}).get('status','UNAVAILABLE')} | "
            f"{fmt(c.get('longest_segment_seconds'))}／{MAX_SEGMENT_SECONDS:g}秒／{req.get('longest_segment',{}).get('status','UNAVAILABLE')}／{'含标称换算' if c.get('duration_fallback_segments') else '时间戳'} | "
            f"{yesno(c.get('all_segments_recovered'))}／是／{req.get('all_segments_recovered',{}).get('status','UNAVAILABLE')} | "
            f"{fmt(d/MIB if d is not None else None)}／{FILE_CLOSURE_THRESHOLD_BYTES/MIB:g}／{e.get('status','UNAVAILABLE')} | {yesno(affected)} | {r.get('experiment_scope_validation','UNAVAILABLE')} |")
    lines+=['','| 调度／轮次 | 最大正／负偏差MiB | 未恢复段 | 历史逐点后两点规则判定（不覆盖） | 历史segments_v2判定（不覆盖） |',
        '| --- | --- | --- | --- | --- |']
    for r in runs:
        c=r.get('periodic_file_closure',{});old=r.get('historical_periodic_file_closure',{})
        lines.append(f"| {r['schedule']}／{r.get('round')} | {fmt(c.get('max_positive_bytes',0)/MIB)}／{fmt(c.get('min_negative_bytes',0)/MIB)} | {c.get('unrecovered_windows','未知')} | {old.get('status','UNAVAILABLE')} | {r.get('historical_periodic_file_closure_v2',c.get('historical_segments_v2',{})).get('status','UNAVAILABLE')} |")
    lines+=['','| 调度／轮次 | 峰值指标／窗口 | 检查状态 | 原始峰值MiB | 前／后闭合边界MiB | 窗口边界替代值MiB | 最终观测峰值MiB |',
        '| --- | --- | --- | ---: | --- | ---: | ---: |']
    for r in runs:
        for name,v in r.get('peak_closure_checks',{}).items():
            boundaries='／'.join(fmt(b/MIB) for b in v.get('boundary_bytes',[])) or '未触发替代'
            replacement=fmt(v['replacement_bytes']/MIB) if v.get('replacement_bytes') is not None else '无需替代' if v['status']=='UNCHANGED' else '不可用'
            lines.append(f"| {r['schedule']}／{r.get('round')} | {name} | {v['status']} | {fmt(v['raw_bytes']/MIB if v.get('raw_bytes') is not None else None)} | {boundaries} | {replacement} | {fmt(v['value_bytes']/MIB if v.get('value_bytes') is not None else None)} |")
    return lines


def publish(state, output):
    output=Path(output);output.mkdir(exist_ok=True,parents=True)
    historical = read(ROOT/'results/eight-task-schedules-20261110/summary.json')['runs_detail']
    runs=state['runs']; summary=dict(state,metric_definitions=dict(
        host_scope=HOST_SCOPE,slice_current_peak='New parent series only; inner collector window, not full preparation lifecycle',
        current_peak_mib='Unchanged same-run Docker scope peak; not the experiment total',
        file_p1_mib='Unchanged same-run Docker scope file before lower P1; not the parent total',
        slice_p1='New parent and direct children at their own common sweep after lower P1',
        historical='Separate old Docker records, no paired performance inference or ratio correction'))
    repeated=[]
    for schedule in ('grpo','bpo','tvcache'):
        selected=[r for r in runs if r['schedule']==schedule and r.get('status') in ('PASS','ACCEPTED_SEGMENTS_V3') and r.get('experiment_scope_validation')=='PASS']
        repeated.append(dict(schedule=schedule,metrics={k:stats([r[k] for r in selected if r.get(k) is not None]) for k,_ in METRICS}))
    summary['repeat_statistics']=repeated
    summary['historical_docker_records']=[r for r in historical if r['task']==state['metadata']['profile']]
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    fmt=lambda v:'未知' if v is None else f'{v:.3f}'
    lines=['# 专属slice父组：'+state['metadata']['profile']+'三调度验证','',
        f"状态：{state['status']}；实际记录{len(runs)}／{state['expected_runs']}。本批范围以清单为准，未运行项不补值。",'',
        '父组为实验组记账总量，直接子组只作分解，不与父组相加。下表四项为终点同一轮父子查询，current峰值另列；mincore与PSS分别为自己的查询窗口，不拼成同一时刻总量。','',
        '**采集窗口：**现有入口的任务内采集器启动至其清理结束，不含此前卷／输入准备。本次不改创建流程；不称全准备过程峰值。普通计数0.1秒、VMM周期PSS1秒及终点，保留默认回收。','',
        '| 调度 | 状态 | 父组current峰值／调度峰值 MiB | 父组终点current／file／anon／kernel MiB | Σ直接子组同四项 MiB | Σ PSS MiB | mincore P1 MiB |',
        '| --- | --- | --- | --- | --- | ---: | ---: |']
    for r in runs:
        parent='／'.join(fmt(r.get(f'slice_{k}_p1_mib')) for k in KEYS)
        children='／'.join(fmt(r.get(f'direct_children_{k}_p1_mib')) for k in KEYS)
        lines.append(f"| {r['schedule']} | {r['status']} | {fmt(r.get('slice_current_peak_mib'))}／{fmt(r.get('slice_current_schedule_peak_mib'))} | {parent} | {children} | {fmt(r.get('pss_total_mib'))} | {fmt(r.get('lower_p1_mib'))} |")
    for r in runs:
        if r.get('status')=='ACCEPTED_SEGMENTS_V3':
            lines+=['',f"首轮GRPO为已获确认的v3离线复用，原始状态{r['original_status']}、原失败阶段{r['original_failed_stage']}及旧v2判定保留；本批不重跑该组。复核来源：`{r['revalidation_summary']}`。有效测量纳入重复统计，各调度实际n见统计表；不能将本批称为九次原入口全流程PASS。"]
    lines+=['','## 父组路径、闭合与同次Docker分项','',
        '| 调度 | 父组file MiB | 同次Docker file MiB | 差额 MiB | 闭合最大绝对差 MiB | 检查状态 |',
        '| --- | ---: | ---: | ---: | ---: | --- |']
    for r in runs:
        docker=r.get('endpoint_docker_counters') or {};delta=r.get('parent_minus_same_run_docker_file_bytes');closure=r.get('file_closure_max_abs_bytes')
        lines.append(f"| {r['schedule']} | {fmt(r.get('slice_file_p1_mib'))} | {fmt(docker.get('file')/MIB if docker.get('file') is not None else None)} | {fmt(delta/MIB if delta is not None else None)} | {fmt(closure/MIB if closure is not None else None)} | {r.get('experiment_scope_validation','未取得')} |")
    for r in runs:
        lines+=['',f"- {r['schedule']} 父组：`{r.get('experiment_parent','未取得')}`；原始目录：`{r.get('raw',r.get('log'))}`。",
                '  闭合计数：`'+json.dumps(r.get('file_closure_counts',{}))+'`。']
        if r.get('experiment_scope_errors'):lines+=['  异常：`'+str(r['experiment_scope_errors'])+'`。']
    lines+=['',*closure_report(runs)]
    lines+=['','current／kernel差额保留。全部直接子组的逐次四项、PSS逐进程、目标文件身份／权限／成本保存在summary.json及原始文件。采集器旧ANOMALY仅表示存在原始偏离，验收用以上三类规则。','',
        '## 时间','', '| 调度 | 调度窗口秒 | 动作及下载窗口秒 | 工具累计秒 | 创建／恢复请求跨度秒 | 任务控制程序整轮秒 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        lines.append('| '+r['schedule']+' | '+' | '.join(fmt(r.get(k)) for k in ('schedule_seconds','actions_download_seconds','tools_seconds','creation_request_span_seconds','controller_seconds'))+' |')
    if state.get('reuse_django_first_round') or state.get('reuse_django_first_two_rounds'):
        lines+=['','## 已有第一轮与本批新增来源','',
            ('第一轮使用已有v3判定（GRPO为ACCEPTED_SEGMENTS_V3，原FAIL保留），第二轮为已有三份PASS；本批仅新增第三轮TVCache→GRPO→BPO三次。历史第三轮失败另存previous_failures，不入统计。' if state.get('reuse_django_first_two_rounds') else '第一轮三份使用已保存的v3离线判定；GRPO标为ACCEPTED_SEGMENTS_V3，原入口FAIL不覆盖。第2／3轮计划新增六次，实际完成情况以逐次状态为准；不重跑第一轮，不把旧Docker范围九次并入统计。')+'实际完成情况以逐次状态为准，旧Docker九份不并入统计。','',
            '| 轮次 | 调度 | 来源 | 本次测量状态 | 原运行状态 | 原始目录 |',
            '| --- | --- | --- | --- | --- | --- |']
        for r in runs:
            lines.append(f"| {r['round']} | {r['schedule']} | {r.get('measurement_origin','未知')} | {r['status']} | {r.get('original_status',r['status'])} | `{r.get('raw',r.get('log'))}` |")
    lines+=['','保存／恢复逐次耗时、驱动整轮、每卷大小和最小剩余空间沿用原始字段；工具累计只加实际执行动作，恢复复用的前缀不补算。保存／恢复累计是调用耗时之和，不是并发墙钟窗口。各任务沿用各自原检查，不套用Prettier的0通过2失败；实际退出、最终检查和七份结果见原始result.json。','',
        '## 历史Docker值单列，不混算','',
        '| 调度 | 历史Docker current采集窗口峰值均值 MiB | 历史Docker file终点均值 MiB | 原记录数 |',
        '| --- | ---: | ---: | ---: |']
    for schedule in ('grpo','bpo','tvcache'):
        old=[r for r in summary['historical_docker_records'] if r['schedule']==schedule]
        lines.append(f"| {schedule} | {fmt(statistics.mean(r['current_full_peak_mib'] for r in old) if old else None)} | {fmt(statistics.mean(r['file_before_p1_mib'] for r in old) if old else None)} | {len(old)} |")
    lines+=['','来源：`results/eight-task-schedules-20261110/summary.json`，每份原路径保留在新摘要中。历史值是其他运行的Docker范围，不与新slice值合并、不按倍率换算。同次父子差额才是本次范围核对。','',
        '## 三调度统计对照','',
        '各格顺序为均值／中位数／极差／样本标准差。内存单位MiB，时间单位秒。父组和Σ子组四项均为终点；current调度峰值另列。PSS分母固定N=7，仅七VMM平均，不是父组总量。','']
    comparison_keys=['slice_current_schedule_peak_mib',*[f'slice_{k}_p1_mib' for k in KEYS],
        *[f'direct_children_{k}_p1_mib' for k in KEYS], 'pss_total_mib','lower_p1_mib',
        'schedule_seconds','actions_download_seconds','tools_seconds']
    labels=dict(METRICS)
    lines+=['| 调度 | '+' | '.join(labels[k] for k in comparison_keys)+' |',
        '| --- | '+' | '.join('---' for _ in comparison_keys)+' |']
    for group in repeated:
        values=[]
        for key in comparison_keys:
            v=group['metrics'][key]
            values.append('／'.join(fmt(v[field]) for field in ('mean','median','range','sample_stddev')))
        lines.append('| '+group['schedule']+' | '+' | '.join(values)+' |')
    lines+=['',
        '## 新口径统计','', '| 调度 | 指标 | n | 均值 | 中位数 | 极差 | 样本标准差 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for group in repeated:
        for key,label in METRICS:
            v=group['metrics'][key]
            lines.append(f"| {group['schedule']} | {label} | {v['count']} | {fmt(v['mean'])} | {fmt(v['median'])} | {fmt(v['range'])} | {fmt(v['sample_stddev'])} |")
    lines+=['','统计只纳入新检查PASS的运行及显式ACCEPTED_SEGMENTS_V3复用记录；原FAIL和复核来源分别保留，其余FAIL不入统计。n=1时样本标准差不可计算，留空（表中“未知”），不写成0。单样本极差0不代表没有波动。','',
        '## 异常与下一步','',state.get('error','本批未记录外层执行错误。'),'',
        '本任务完成或实际失败后均停止报告，下一任务须用户确认后才开始；不自动补跑或重试。']
    lines+=['','## 目标文件范围、未知项和保存恢复','',
        '| 轮次／调度 | 原镜像逻辑层／活跃去重文件数 | 七VMM PSS分母 | 扩展普通文件严格状态／窗口状态／未知对象数 | 块设备／loop状态 | 保存／恢复次数 | 保存／恢复累计秒 |',
        '| --- | --- | ---: | --- | --- | --- | --- |']
    for r in runs:
        ordinary=r.get('ordinary_window') or {};block=r.get('block_cachestat') or {};loop=r.get('loop') or {}
        lines.append(f"| {r.get('round')}／{r['schedule']} | {r.get('lower_logical_layers','未知')}／{r.get('lower_physical_files','未知')} | {r.get('pss_denominator','未知')} | {r.get('ordinary_strict_status','未知')}／{ordinary.get('status','未知')}／{ordinary.get('unknown_objects','未知')} | {block.get('status','见原始字段')}／{loop.get('status','未知')} | {len(r['saves']) if 'saves' in r else '未知'}／{len(r['restores']) if 'restores' in r else '未知'} | {fmt(r.get('save_total_seconds'))}／{fmt(r.get('restore_total_seconds'))} |")
    lines+=['','mincore P1只覆盖经驱动身份核对的原镜像层全部活跃文件，按设备号:inode去重；不称全部host缓存。扩展文件、块设备、RAM、loop和未知项按原字段分列，不相加。每次保存恢复的原始事件与耗时见summary.json的saves／restores。原curve若有，仍为Docker范围，不当作新父组曲线。','',
        '## 周期偏离点明细','',
        '| 任务 | 调度 | 轮次 | 索引 | 距采集起点秒 | 距调度起点秒 | 有符号差MiB | 恢复索引 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    details=[]
    for r in runs:
        for point in r.get('periodic_file_deviation_details',[]):
            details.append(dict(task=r.get('task'),schedule=r['schedule'],round=r.get('round'),**point))
            lines.append(f"| {r.get('task')} | {r['schedule']} | {r.get('round')} | {point['index']} | {fmt(point['seconds_since_collection_start'])} | {fmt(point['seconds_since_schedule_start'])} | {fmt(point['parent_minus_children_bytes']/MIB)} | {point['recovery_index']} |")
    (output/'deviations.json').write_text(json.dumps(details,ensure_ascii=False,indent=2)+'\n')
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    columns=['schedule','status','experiment_parent',*[k for k,_ in METRICS],'file_closure_max_abs_bytes','raw']
    with (output/'runs.tsv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns,delimiter='\t',extrasaction='ignore');writer.writeheader();writer.writerows(runs)
