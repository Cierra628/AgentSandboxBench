"""Read an existing run, keeping partial points and gaps; never opens live resources."""
import csv,json,sys,statistics
from pathlib import Path
def read(p):return json.loads(p.read_text())
def lines(p):return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
def table(p):return list(csv.DictReader(p.open(),delimiter='\t')) if p.exists() else []
def write(p,rows):
    if rows:
        with p.open('w') as f:
            w=csv.DictWriter(f,list(dict.fromkeys(k for row in rows for k in row)));w.writeheader();w.writerows(rows)
def run(raw,out):
    base=Path('.artifacts/storage-isolated-service/20261107T084731Z-3799106/prettier6604')
    frozen=base/'frozen-input';paths=read(frozen/'rollouts.json')
    ids={r['role']:r['sandbox_id'] for r in table(raw/'sandbox-ids.tsv')}
    rounds=lines(raw/'light-memory.jsonl');points=[];pss=[];summary=[]
    for role,path in paths.items():
        want=[(s,r['step']) for s in path for r in table(frozen/'segments'/s/'actions.tsv')]
        pts=lines(raw/'action-points'/(role+'.jsonl'));points+=pts
        got=[r for r in pts if r['status']=='OBSERVED']; actual=[(r['segment'],r['step']) for r in got]
        valid=all(r['sandbox_id']==ids.get(role) and r['role']==role and r['action']==i and
                  r['pause_request_ns']<=r['pause_complete_ns']<=r['read_start_ns']<=r['read_end_ns']<=r['resume_request_ns']<=r['resume_complete_ns']
                  for i,r in enumerate(got,1)) and actual==want[:len(got)]
        t=[]
        for row in rounds:
            if role not in row['vmms']:continue
            v=row['vmms'][role];assert v['sandbox_id']==ids[role]
            t.append(v['start_monotonic_ns']);pss.append(dict(role=role,sid=v['sandbox_id'],time_ns=t[-1],pss_kib=v['counters']['pss_kib'],end_ns=v['end_monotonic_ns']))
        gaps=[(b-a)/1e6 for a,b in zip(t,t[1:])]
        final=raw/'rollouts'/role/path[-1]/'patch.diff'
        op=raw/f'official-result-{role}.json'
        def official(p):
            o=read(p);o['summary'].pop('startTime',None);return o
        summary.append(dict(role=role,expected=len(want),observed=len(got),identities_and_order=valid if got else None,
          missing=len(want)-len(got),pss_points=len(t),period_median_ms=statistics.median(gaps) if gaps else None,max_gap_ms=max(gaps,default=None),
          pause_ms=sum((r['resume_request_ns']-r['pause_complete_ns'])/1e6 for r in pts if 'resume_request_ns'in r and 'pause_complete_ns'in r),
          patch_equal=(final.read_bytes()==(base/final.relative_to(raw)).read_bytes()) if final.exists() else None,
          official_equal=official(op)==official(base/op.name) if op.exists() else None))
    write(out/'pss.csv',pss);write(out/'points.csv',points);write(out/'roles.csv',summary)
    print(json.dumps(summary,indent=2))
if __name__=='__main__':run(Path(sys.argv[1]),Path(sys.argv[2]))
