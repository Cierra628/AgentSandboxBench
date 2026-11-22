"""Offline only: verify the live result against both publications and both real frames."""
import argparse
import csv
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts'))
from fast_cache_metadata import decode_array
from provider_slot_cache import account_observed_provider

def read(p):return json.loads(p.read_text())

def main(raw,out):
    v=read(raw/'result.json')
    roles=[f'restored-{i}' for i in range(1,8)]
    assert v['status']=='PASS' and v['valid']==7 and set(v['query_rows'])==set(roles)
    rows=[];four=set();device=set();att=[];probe_maps=[]
    origin=min(q['start'] for q in v['query_rows'].values())
    for r in roles:
        d=raw/('process-'+r);q=v['query_rows'][r];w=v['workers'][r];life=v['rows'][r]
        assert life['status']=='OBSERVED' and life['content_verified']<=life['first_end']<life['pause_end']<=q['start'] and q['host_end']<=life['resume_end']
        frames=read(d/'frames.json')
        before,after=[(d/(k+'.bin')).read_bytes() for k in ('before','after')]
        keep=set()
        with np.load(d/'evidence.npz') as t,np.load(d/'sets.npz') as sets:
            _,u=account_observed_provider(before,after,t['host'],t['direct'],t['lower'],*frames,
                       decoded=(decode_array(before),decode_array(after)),current_pfns=keep)
            assert u==set(sets['four']) and keep==set(sets['provider']) and len(u)==q['pages']
        four.update(u);device.update(keep)
        probe_maps+=q['maps']
        for i,(src,z) in enumerate(w['attach'].items()):
            att.append(dict(role=r,source=src,start=z['call'],end=z['return'],ms=(z['return']-z['call'])/1e6,
                       probe_cpu_ms=q['probe_cpu_ns'][i]/1e6))
        ms=lambda t:(t-origin)/1e6
        rows.append(dict(role=r,start_ms=ms(q['start']),end_ms=ms(w['end']),wall_ms=(w['end']-q['start'])/1e6,
                   host_start_ms=ms(q['host_start']),host_end_ms=ms(q['host_end']),cpu_ms=w['cpu_ns']/1e6,
                   first_build_ms=frames[0]['stage_costs']['record_build']['elapsed_ns']/1e6,
                   last_build_ms=frames[1]['stage_costs']['record_build']['elapsed_ns']/1e6,
                   first_provider_ms=frames[0]['elapsed_ns']/1e6,first_build_cpu_ms=frames[0]['stage_costs']['record_build']['cpu_ns']/1e6,
                   guest_before=q['guest_before'],guest_after=q['guest_after'],queries=q['pages_queried'],confirmed=q['pages']))
    assert len(set(probe_maps))==14 and len(device)==v['provider_unique_pages'] and len(four)==v['four_unique_pages']
    assert max(a['end'] for a in att)<min(z['restore_start'] for z in v['rows'].values())
    wall=max(z['end_ms'] for z in rows)/1000;cpu=sum(z['cpu_ms'] for z in rows)/1000
    summary=dict(origin_ns=origin,workers_s=wall,host_s=(max(z['host_end_ms'] for z in rows)-min(z['host_start_ms'] for z in rows))/1000,
                 dispatch_to_merged_s=(v['merge_end']-v['go'])/1e9,merge_ms=(v['merge_end']-v['merge_start'])/1e6,
                 skew_ms=max(z['start_ms'] for z in rows),worker_cpu_s=cpu,parallelism=cpu/wall,confirmed=len(four),
                 provider=len(device),total_queries=sum(z['queries'] for z in rows),
                 attach_sum_s=sum(z['ms'] for z in att)/1000,attach_span_s=(max(z['end'] for z in att)-min(z['start'] for z in att))/1e9,
                 scan_probe_cpu_ms=sum(z['probe_cpu_ms'] for z in att))
    for name,records in [('workers.csv',rows),('probes.csv',att)]:
        with (out/name).open('w') as fp:
            w=csv.DictWriter(fp,fieldnames=list(records[0]),lineterminator='\n');w.writeheader();w.writerows(records)
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('guest_cache',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();main(a.guest_cache,a.output)
