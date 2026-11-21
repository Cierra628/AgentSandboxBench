"""Summarize existing archives only. Arguments: old five-batch dir, diagnostic dir."""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

out=Path(__file__).resolve().parent
old,diag=map(Path,sys.argv[1:])
stages=defaultdict(dict)
profile=[]
for label,path in [('baseline',old),('diagnostic',diag)]:
    per=defaultdict(list)
    for file in sorted(path.glob('guest-cache/try-*/process-*/frames.json')):
        for k,cost in json.loads(file.read_bytes())[0]['stage_costs'].items():
            per[k].append(cost['elapsed_ns']/1e6)
    for k,v in per.items():
        stages[k].update({label+'_n':len(v),label+'_min_ms':min(v),label+'_median_ms':median(v),label+'_max_ms':max(v)})
for f in sorted(diag.glob('guest-cache/try-1/shared-device/provider-*/profile.jsonl')):
    for index,line in enumerate(f.read_text().splitlines(),1):
        v=json.loads(line);parts=v['parts'];events=v['gc']
        assert len(parts)==32 and len(events)%2==0
        times=[(a[2],a[3],(b[0]-a[0])/1e6) for a,b in zip(events[::2],events[1::2]) if a[1]=='start' and b[1]=='stop' and a[2]==b[2]]
        assert len(times)*2==len(events)
        ms=v['build']['elapsed_ns']/1e6
        before,after=[sum(t[1] for t in parts if t[0]==side)/1e6 for side in ('before','after')]
        g1=sum(t for gen,side,t in times if gen==1 and side=='before')
        profile.append(dict(role=f.parent.name.removeprefix('provider-'),frame=index,build_ms=ms,
                            before_ms=before,after_ms=after,other_ms=ms-before-after,
                            before_gen1_count=sum(gen==1 and s=='before' for gen,s,t in times),
                            before_gen1_ms=g1,gen1_pct=100*g1/ms,
                            minor_faults_in_unpack=sum(t[2] for t in parts),major_faults_in_unpack=sum(t[3] for t in parts)))
def write(name,rows):
    with (out/name).open('w') as f:
        w=csv.DictWriter(f,rows[0],lineterminator='\n');w.writeheader();w.writerows(rows)
write('profile.csv',profile)
write('stages.csv',[dict(stage=k,**v) for k,v in stages.items()])
