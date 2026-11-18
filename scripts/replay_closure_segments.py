"""Recheck saved segments_v2 records as v3; no live queries or workloads."""
import argparse
import json
from pathlib import Path

from experiment_memory import normalize_groups
from report_experiment_schedule import collect_parent, closure_report, periodic_closure_v2, read


def replay(sources, output):
    records=[];rows=[]
    for source in sources:
        for saved in read(source)['runs']:
            old=saved.get('recomputed',saved)
            storage=Path(saved.get('source_storage',old.get('storage','')))
            new=collect_parent(storage)
            samples=[json.loads(line) for line in Path(new['parent_series']).read_text().splitlines()]
            # Historical v2 must be reproduced exactly, not inferred from task status.
            assert periodic_closure_v2(samples)==old['periodic_file_closure'], (source,storage)
            for sample in [*samples,new['slice_p1']]:
                value=normalize_groups(new['experiment_parent'],sample['groups'])
                assert value['status']=='OBSERVED'
                assert value['file_closure']==sample['experiment_memory']['file_closure']
            assert new['peak_closure_checks']==old['peak_closure_checks']
            row=dict(new,task=old['task'],schedule=old['schedule'],round=old['round'])
            rows.append(row)
            records.append(dict(task=row['task'],schedule=row['schedule'],round=row['round'],
                source_summary=str(source),source_storage=str(storage),
                historical=dict(policy='segments_v2',run_status=saved.get('status',saved.get('historical',{}).get('run_status')),
                    experiment_scope_validation=old['experiment_scope_validation'],
                    periodic_file_closure=old['periodic_file_closure'],
                    endpoint_file_closure=old['endpoint_file_closure'],peak_closure_checks=old['peak_closure_checks']),
                recomputed=row))
    identities=[r['source_storage'] for r in records]
    assert len(identities)==len(set(identities)), 'duplicate saved run'
    output.mkdir(parents=True,exist_ok=False)
    summary=dict(status='PASS' if all(r['experiment_scope_validation']=='PASS' for r in rows) else 'FAIL',
        policy='segments_v3',business_runs_started=0,run_count=len(rows),
        raw_arithmetic_replay='PASS',historical_v2_replay='PASS',peak_checks_match_saved='PASS',runs=records)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# 周期闭合segments_v3：已保存记录离线复算','',
        f"复算{len(rows)}份，结果**{summary['status']}**。只读已有JSON／JSONL，不启动沙箱、业务、服务或采样。v2原判定和原始数据未修改；本目录另存historical／recomputed。",'',
        '## 逐次判定','',
        '| 任务 | 调度 | 轮次 | 总点数 | 偏离点数 | 占比／上限 | 最长段秒数／上限 | 全部恢复 | 终点差MiB | 峰值受影响 | v2判定 | v3判定 |',
        '| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- | --- | --- |']
    for r in records:
        new=r['recomputed'];c=new['periodic_file_closure']
        lines.append(f"| {r['task']} | {r['schedule']} | {r['round']} | {c['total_points']} | {c['deviation_count']} | "
            f"{100*c['deviation_fraction']:.6f}%／5% | {c['longest_segment_seconds']:.9f}／1秒 | "
            f"{'是' if c['all_segments_recovered'] else '否'} | {new['endpoint_file_closure']['parent_minus_children_bytes']/2**20:.6f} | "
            f"{'是' if any(p['affected'] for p in new['peak_closure_checks'].values()) else '否'} | "
            f"{r['historical']['experiment_scope_validation']} | {new['experiment_scope_validation']} |")
    fallback=sum(r['periodic_file_closure']['duration_fallback_segments'] for r in rows)
    lines+=['',f'共{sum(r["periodic_file_closure"]["total_points"] for r in rows):,}个周期点，时间换算段数{fallback}。最长段按首个偏离点到首个恢复点计算，包含最后一个偏离采样周期；不是末偏离点减首偏离点。逐段首尾索引、实际秒数、时间来源及回退原因在summary.json的windows中。','',
        'Matplotlib原运行和v2采集FAIL仍保留。v3通过是本次检查规则下的离线结论，不改写为原入口当时PASS，不证明内核刷新原因。其余任务不重跑或补值。','',
        '## 规则与代码','',
        '`scripts/report_experiment_schedule.py`的`periodic_closure()`执行v3；`periodic_closure_v2()`保留原3点规则。显式常量为`MAX_SEGMENT_SECONDS=1.0`、`NOMINAL_INTERVAL_SECONDS=0.1`、`MAX_DEVIATION_FRACTION=0.05`；file阈值仍为`FILE_CLOSURE_THRESHOLD_BYTES=1048576`。原始采样器、终点和峰值算法不改。','',
        '新增`longest_segment_seconds`、逐段`duration_seconds/duration_basis/duration_fallback_reason`、`duration_fallback_segments`及`historical_periodic_file_closure_v2`。报告自动列出秒数、时间来源、实测值和上限；原有最长点数只作诊断，不参与v3时长判定。','',
        *closure_report(rows),'','## 来源','']
    for r in records:
        lines.append(f"- {r['task']} r{r['round']} {r['schedule']}：来源`{r['source_summary']}`；序列`{r['recomputed']['parent_series']}`。")
    lines+=['','## 清理与下一步','',
        '指定80 GiB失败卷的删除清单、SHA256与前后空间另见本目录的CLEANUP.md／cleanup-receipt.json；这里只执行离线解析，不负责删除。全部通过且清理后达标，可经用户确认继续Matplotlib剩余8次；本轮不启动。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-summaries',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    value=replay([p.resolve() for p in args.source_summaries],args.output.resolve())
    print('status='+value['status']+' records='+str(value['run_count'])+' result_dir='+str(args.output))
    raise SystemExit(0 if value['status']=='PASS' else 1)
