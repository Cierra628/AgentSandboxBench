"""Break down saved Prettier on/off timing; never starts a task or reads payloads."""
import argparse
import csv
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[1]
CATEGORIES=('cold_start','monitor_control','segments','official_tests','other_calls','delete','unlogged')


def read(path):return json.loads(Path(path).read_text())


def category(stage):
    if stage=='cold-start':return 'cold_start'
    if stage.startswith('monitor-'):return 'monitor_control'
    if stage.startswith('segment-'):return 'segments'
    if stage=='official-task-tests':return 'official_tests'
    if stage=='delete-owned-sandbox':return 'delete'
    return 'other_calls'


def breakdown(row):
    raw=Path(row['raw']);events=read(raw/'events.json');task=read(raw/'result.json')
    assert row['status']==task['status']=='PASS'
    elapsed=task['controller_elapsed_ns']
    assert abs(elapsed-round(row['controller_seconds']*1e9))<=1
    parents=[e for e in events if e['stage'].startswith('segment-')]
    selected=[];overlapping=[];children=[]
    for e in events:
        assert e['exit_code']==0 and e['duration_ns']>=0
        if e['stage'].startswith('monitor-collect-'):
            overlapping.append(e);continue  # Long-lived background command, not extra blocking time.
        if e['stage'].startswith('controller-call-'):
            assert any(p['start_monotonic_ns']<=e['start_monotonic_ns'] and
                e['start_monotonic_ns']+e['duration_ns']<=p['start_monotonic_ns']+p['duration_ns'] for p in parents)
            children.append(e);continue  # Already included in the segment interval.
        selected.append(e)
    selected.sort(key=lambda e:e['start_monotonic_ns'])
    assert all(a['start_monotonic_ns']+a['duration_ns']<=b['start_monotonic_ns'] for a,b in zip(selected,selected[1:])), 'blocking intervals overlap'
    values={key:0 for key in CATEGORIES};controls={}
    for e in selected:
        key=category(e['stage']);values[key]+=e['duration_ns']
        if key=='monitor_control':
            entry=controls.setdefault(e['stage'],dict(calls=0,duration_ns=0))
            entry['calls']+=1;entry['duration_ns']+=e['duration_ns']
    values['unlogged']=elapsed-sum(values.values());assert values['unlogged']>=0
    assert sum(values.values())==elapsed
    if row['mode']=='off':assert not controls and not overlapping
    else:
        assert row['mode']=='on' and len(overlapping)==1
        assert min(e['start_monotonic_ns'] for e in overlapping)>=max(e['start_monotonic_ns']+e['duration_ns'] for e in selected if e['stage']=='cold-start')
    return dict(round=row['round'],mode=row['mode'],raw=str(raw.relative_to(ROOT)) if raw.is_relative_to(ROOT) else str(raw),
        controller_ns=elapsed,categories_ns=values,monitor_calls=controls,
        background_collect_ns=sum(e['duration_ns'] for e in overlapping),nested_calls_excluded=len(children))


def analyze(path):
    rows=[r for r in read(path) if r['task']=='prettier6604'];assert len(rows)==6
    runs=[breakdown(r) for r in rows];pairs=[]
    for number in (1,2,3):
        selected=[r for r in runs if r['round']==number];assert len(selected)==2
        modes={r['mode']:r for r in selected};assert set(modes)=={'on','off'}
        delta={key:modes['on']['categories_ns'][key]-modes['off']['categories_ns'][key] for key in CATEGORIES}
        total=modes['on']['controller_ns']-modes['off']['controller_ns'];assert sum(delta.values())==total
        pairs.append(dict(round=number,controller_difference_ns=total,categories_difference_ns=delta))
    return dict(status='PASS_OFFLINE_TIMING_BREAKDOWN',runs=runs,pairs=pairs,
        direct_monitor_control_median_ns=statistics.median(r['categories_ns']['monitor_control'] for r in runs if r['mode']=='on'),
        scope='Saved Prettier three pairs; sums exclude nested segment calls and overlapping collector lifetime; differences are observations, not causal attribution',
        live_actions=0)


def markdown(value):
    lines=['# Prettier轻量采样耗时分解','',
        '只读取已有三对记录；单位秒。每行分类之和等于整轮差值，保留正负值。','',
        '| 轮次 | 整轮差值 | 冷启动 | 直接采样控制 | 原动作段 | 官方检查 | 其他调用 | 删除 | 未记录间隔 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for p in value['pairs']:
        vals=[p['controller_difference_ns'],*[p['categories_difference_ns'][k] for k in CATEGORIES]]
        lines.append('| '+str(p['round'])+' | '+' | '.join(f'{v/1e9:.6f}' for v in vals)+' |')
    lines+=['','直接采样控制包含上传、初始化、就绪、阶段标记、停止、回收确认和下载；不含后台采样命令的整个存活时间。',
        '冷启动先于采样启动，不能将其差值归因于运行中的采样器；原动作段含边界记录和调用等待，差值也不全部归因于采样。',
        '各分类中位数不能相加代替整轮差值中位数。','',
        f"开启组直接采样控制中位数：{value['direct_monitor_control_median_ns']/1e9:.6f}秒。",'']
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=Path,default=ROOT/'.artifacts/light-monitor-controls-20261019T082344Z/runs.json')
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    value=analyze(args.runs);args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'breakdown.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    (args.output/'SUMMARY.md').write_text(markdown(value))
    with (args.output/'runs.tsv').open('w') as stream:
        writer=csv.writer(stream,delimiter='\t');writer.writerow(['round','mode','controller_ns',*CATEGORIES])
        for r in value['runs']:writer.writerow([r['round'],r['mode'],r['controller_ns'],*[r['categories_ns'][k] for k in CATEGORIES]])
    print('status='+value['status']+' result_dir='+str(args.output.resolve()))


if __name__=='__main__':main()
