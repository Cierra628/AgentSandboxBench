"""Summarize all attempted rounds, without inserting zero timings for failures."""
import csv,json,sys
from pathlib import Path
import numpy as np


def write(path,rows):
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w') as f:
        w=csv.DictWriter(f,keys,lineterminator='\n');w.writeheader();w.writerows(rows)

def main(raw,out):
    series=json.loads((raw/'result.json').read_text())
    rounds=[];workers=[];known=set();pids=set()
    for attempt in series['attempts']:
        n=attempt['attempt'];p=raw/f'try-{n}';v=json.loads((p/'result.json').read_text())
        rec=dict(attempt=n,status=attempt['status'],cleanup=attempt.get('cleanup'))
        rounds.append(rec)
        spans=[a for f in p.glob('process-*/attach.json') for a in json.loads(f.read_text()).values() if 'return' in a]
        if len(spans)==14:
            rec['attach_s']=(max(a['return'] for a in spans)-min(a['call'] for a in spans))/1e9
        if 'ns' not in attempt:
            rec['error']=v.get('error','unknown');continue
        assert v['snapshot']==series['snapshot'] and v['valid']==7
        union=[set(),set()];att=[]
        go=v['go'];ms=lambda t:(t-go)/1e6
        barrier=max(q['sweep_done'] for q in v['query_rows'].values())
        last=max(v['workers'],key=lambda r:v['workers'][r]['end'])
        slow=max(v['query_rows'],key=lambda r:v['query_rows'][r]['sweep_done'])
        for r,q in v['query_rows'].items():
            w=v['workers'][r];life=v['rows'][r]
            assert q['sid'] not in known and w['pid'] not in pids
            known.add(q['sid']);pids.add(w['pid'])
            att.extend(w['attach'].values())
            assert life['content_verified']<life['pause_end']<=q['start']<q['host_end']<=life['resume_end']
            frames=json.loads((p/('process-'+r)/'frames.json').read_text())
            with np.load(p/('process-'+r)/'sets.npz',allow_pickle=False) as z:
                assert len(z['four'])==q['pages']
                for dest,key in zip(union,('four','provider')):dest.update(map(int,z[key]))
            st=q['stages']
            elapsed=lambda keys:sum(st[k][1]-st[k][0] for k in keys)/1e6
            workers.append(dict(attempt=n,role=r,start_ms=ms(q['start']),ready_ms=ms(w['end']),worker_wall_ms=(w['end']-q['start'])/1e6,
                first_build_ms=frames[0]['stage_costs']['record_build']['elapsed_ns']/1e6,confirmed_pages=q['pages'],
                sweep_done_ms=ms(q['sweep_done']),account_start_ms=ms(q['account_start']),
                account_ms=(q['write_start']-q['account_start'])/1e6,output_ms=(w['end']-q['write_start'])/1e6,
                direct_ms=elapsed(['direct_offsets','direct_first','direct_last']),lower_ms=elapsed(['lower_offsets','lower_first','lower_last']),
                provider_ms=elapsed(['provider_first','provider_last'])))
        assert len(att)==14 and max(a['return'] for a in att)<min(l['restore_start'] for l in v['rows'].values())
        assert [len(u) for u in union]==[v['four_unique_pages'],v['provider_unique_pages']]
        rec.update(ready_ms=ms(v['workers'][last]['end']),merge_ms=(v['merge_end']-v['merge_start'])/1e6,
                   total_ms=ms(v['merge_end']),four=len(union[0]),provider=len(union[1]),
                   attach_s=(max(a['return'] for a in att)-min(a['call'] for a in att))/1e9,
                   sweep_critical=slow,sweep_barrier_ms=ms(barrier),last_ready=last)
    write(out/'rounds.csv',rounds);write(out/'workers.csv',workers) if workers else None
    good=[r for r in rounds if 'total_ms' in r]
    def stats(x):
        a=np.array(x)
        return dict(n=len(x),min=a.min(),median=np.median(a),p95=np.percentile(a,95),max=a.max(),span=np.ptp(a),sample_sd=a.std(ddof=1) if len(a)>1 else None)
    summary={k:stats([r[k] for r in good]) for k in ('ready_ms','merge_ms','total_ms','attach_s')} if good else {}
    attach=[r['attach_s'] for r in rounds if 'attach_s' in r]
    if attach:summary['attach_s']=stats(attach)
    if workers:summary['first_build_ms']=stats([r['first_build_ms'] for r in workers])
    summary.update(attempts=len(rounds),valid=len(good),under_one_second=sum(r['total_ms']<1000 for r in good),snapshot=series['snapshot'])
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if __name__=='__main__':main(*map(Path,sys.argv[1:]))
