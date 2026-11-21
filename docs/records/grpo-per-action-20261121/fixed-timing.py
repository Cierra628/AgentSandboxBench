"""Elapsed time, per-role accumulated pause, and overlapping measurement windows."""
import csv,json,sys
from pathlib import Path
raw,out=map(Path,sys.argv[1:])
def union(w):
    merged=[]
    for a,b in sorted(w):
        assert a<=b
        if merged and a<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],b)
        else:merged.append([a,b])
    return sum(b-a for a,b in merged)
j=json.loads((raw/'result.json').read_text())
events=[json.loads(s) for p in (raw/'action-points').glob('*.jsonl') for s in p.read_text().splitlines()]
pause=[(r['pause_complete_ns'],r['resume_request_ns']) for r in events if 'pause_complete_ns' in r and 'resume_request_ns' in r]
measure=[(r['ready_seen_host_ns'],r['release_return_ns']) for r in events if 'release_return_ns' in r]
read=[(r['read_start_ns'],r['read_end_ns']) for r in events if 'read_end_ns' in r]
result=dict(status=j['status'],controller_wall_ns=j['controller_elapsed_ns'],pause_role_sum_ns=sum(b-a for a,b in pause),
    pause_union_ns=union(pause),completed_point_measure_role_sum_ns=sum(b-a for a,b in measure),completed_point_measure_union_ns=union(measure),
    host_read_union_ns=union(read),incomplete_points=sum(r.get('status')!='OBSERVED' for r in events))
if 'rollout_end_monotonic_ns' in j:
    result['rollout_wall_ns']=j['rollout_end_monotonic_ns']-j['rollout_start_monotonic_ns']
    result['rollout_outside_completed_point_windows_ns']=result['rollout_wall_ns']-union(measure)
if j.get('paths'):
    result['guest_action_role_sum_ns']=sum(p['guest_action_sum_ns'] for p in j['paths'].values() if 'guest_action_sum_ns' in p)
(out/'timing.json').write_text(json.dumps(result,indent=2)+'\n')
