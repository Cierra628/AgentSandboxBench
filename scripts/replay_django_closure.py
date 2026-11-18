"""Recheck saved Django cgroup measurements; no live queries or workloads."""
import argparse
import json
from pathlib import Path

from experiment_memory import normalize_groups
from report_experiment_schedule import CLOSURE_POLICY, collect_parent, closure_report, read


def replay(source, output):
    saved = read(source)
    assert sorted(r['schedule'] for r in saved['runs'])==['bpo','grpo','tvcache']
    rows=[]; records=[]
    for old in saved['runs']:
        storage=Path(old['storage'])
        new=collect_parent(storage)
        samples=[json.loads(line) for line in Path(new['parent_series']).read_text().splitlines()]
        # Independently recompute signed differences from original group counters.
        for sample in [*samples,new['slice_p1']]:
            parsed=normalize_groups(new['experiment_parent'],sample['groups'])
            assert parsed['file_closure']==sample['experiment_memory']['file_closure']
        row=dict(new,task=old['task'],schedule=old['schedule'],round=old['round'])
        rows.append(row)
        records.append(dict(schedule=old['schedule'],round=old['round'],source_storage=str(storage),
            historical=dict(policy='point_recovery_v1',source_summary=str(source),
                run_status=old['status'],experiment_scope_validation=old['experiment_scope_validation'],
                experiment_scope_errors=old['experiment_scope_errors'],
                periodic_file_closure=old['periodic_file_closure'],
                endpoint_file_closure=old['endpoint_file_closure'],peak_closure_checks=old['peak_closure_checks']),
            recomputed=row))
    output.mkdir(parents=True,exist_ok=False)
    summary=dict(status='PASS' if all(r['experiment_scope_validation']=='PASS' for r in rows) else 'FAIL',
        policy=CLOSURE_POLICY,source_summary=str(source),business_runs_started=0,
        verification='Saved parent/direct-child counters reproduce every periodic and endpoint signed file difference.',
        runs=records)
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines=['# Django父子cgroup：新周期规则离线复算','',
        f"新规则复算：**{summary['status']}**。只读三份已保存数据，新建本报告；未启动沙箱、业务、采样或服务。第二批36次未开始。",'',
        f'实际来源：`{source}`。使用报告中的storage／parent_series路径，不猜测时间戳目录。','',
        *closure_report(rows),'','## 历史判定保留','',
        '| 调度 | 原运行状态 | 历史逐点规则整体判定 | 新分段规则整体判定 |',
        '| --- | --- | --- | --- |']
    for r in records:
        lines.append(f"| {r['schedule']} | {r['historical']['run_status']} | {r['historical']['experiment_scope_validation']} | {r['recomputed']['experiment_scope_validation']} |")
    lines+=['','原始数值和历史PASS／FAIL文件未修改。新旧判定分别存于summary.json的recomputed／historical；新规则通过表示符合本次明确的验收条件，不证明偏离的内核原因。现有现象支持统计刷新不同步的解释，仍缺逐CPU运行时证据。','',
        '## 原始数据与偏离段','']
    for r in rows:
        lines.append(f"- {r['schedule']}：`{r['parent_series']}`；偏离段及恢复索引（从0起）：`{json.dumps(r['periodic_file_closure']['windows'])}`。")
    lines+=['','## 下一步','',
        '本次离线范围完成后停止。待用户确认，再按原顺序推进其余四任务36次；不在本次启动。']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-summary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=replay(args.source_summary.resolve(),args.output.resolve())
    print('status='+result['status']+' result_dir='+str(args.output))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
