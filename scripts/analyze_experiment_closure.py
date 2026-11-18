"""Read saved cgroup rows around closure anomalies; never query live counters."""
import argparse
import json
from pathlib import Path

from experiment_memory import normalize_groups, FILE_CLOSURE_THRESHOLD_BYTES


def analyze(storage, output):
    raw=storage/'external-workload'
    task=json.loads((raw/'result.json').read_text())
    calls=json.loads((raw/'calls.json').read_text())
    rows=[json.loads(l) for l in (raw/'host-file/experiment-memory.jsonl').read_text().splitlines()]
    parent=task['experiment_memory_context']['parent']
    anomalies=[]; time0=task['schedule_start_monotonic_ns']
    for index,row in enumerate(rows):
        parsed=normalize_groups(parent,row['groups'])
        # Validate arithmetic directly from the saved raw values as well as the
        # shared parser; do not replace the original observation or threshold.
        children={p:v for p,v in row['groups'].items() if Path(p).parent==Path(parent)}
        delta=row['groups'][parent]['file']-sum(v['file'] for v in children.values())
        assert delta==parsed['file_closure']['parent_minus_children_bytes']
        assert delta==row['experiment_memory']['file_closure']['parent_minus_children_bytes']
        if abs(delta)<=FILE_CLOSURE_THRESHOLD_BYTES:continue
        begin=row['monotonic_ns']; end=begin+row['duration_ns']
        overlapping=[dict(stage=c['stage'],start_monotonic_ns=c['start_monotonic_ns'],
                          end_monotonic_ns=c.get('command_end_monotonic_ns',c['end_monotonic_ns']))
                     for c in calls if c['start_monotonic_ns']<=end and
                     c.get('command_end_monotonic_ns',c['end_monotonic_ns'])>=begin and
                     not c['stage'].startswith('monitor-collect')]
        context=[]
        for j in range(max(0,index-1),min(len(rows),index+2)):
            r=rows[j];v=r['experiment_memory']
            context.append(dict(index=j,relative_seconds=(r['monotonic_ns']-time0)/1e9,
                monotonic_ns=r['monotonic_ns'],duration_ns=r['duration_ns'],
                parent_counters=v['parent_counters'],direct_children=v['direct_children'],
                file_difference_bytes=v['file_closure']['parent_minus_children_bytes']))
        anomalies.append(dict(index=index,relative_seconds=(begin-time0)/1e9,
            delta_bytes=delta,duration_ns=row['duration_ns'],overlapping_calls=overlapping,neighbors=context))
    value=dict(status='ANOMALIES_CONFIRMED' if anomalies else 'NO_ANOMALIES',source_storage=str(storage),
        raw_business_status=task['status'],samples=len(rows),anomaly_count=len(anomalies),anomalies=anomalies,
        unique_direct_child_sets=len({tuple(sorted(r['experiment_memory']['direct_children'])) for r in rows}),
        arithmetic_replay_all_rows='PASS',threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES,
        interpretation='Observed parent/child statistics update at different samples. Kernel aggregation/flush timing is a supported explanation, not a per-event traced causal finding.')
    with output.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=analyze(args.storage,args.output)
    print('status='+result['status']+' anomalies='+str(result['anomaly_count'])+' output='+str(args.output))
