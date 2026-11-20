"""Read-only replay of archived seven-process PFN results. Usage: replay.py RAW OUT"""
import csv
import ctypes as C
import io
import json
import mmap
import os
from pathlib import Path
import statistics
import sys
from time import perf_counter_ns as clock
import numpy as np

raw,out=map(Path,sys.argv[1:3])
archive=json.loads((raw/'result.json').read_text())
roles=sorted(archive['workers'])
assert len(roles)==archive['valid']==7
paths=[(raw/('process-'+r)/'result.json',raw/('process-'+r)/'sets.npz') for r in roles]
expected=[(r,archive['workers'][r]['pid'],archive['query_rows'][r]['sid']) for r in roles]
lib=C.CDLL(None,use_errno=True)
def cache():
    pages=resident=size=0
    for pair in paths:
        for p in pair:
            with p.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_COPY) as m:
                n=(len(m)+mmap.PAGESIZE-1)//mmap.PAGESIZE
                vec=(C.c_ubyte*n)()
                addr=C.addressof(C.c_char.from_buffer(m))
                if lib.mincore(C.c_void_p(addr),C.c_size_t(len(m)),vec):raise OSError(C.get_errno())
                pages+=n;resident+=sum(x&1 for x in vec);size+=len(m)
    return dict(pages=pages,resident=resident,bytes=size)
def check(i,row):
    assert (row['role'],row['pid'],row['sid'])==expected[i]

def original():
    start=clock();pf=set();pr=set();rows={}
    for i,(j,z) in enumerate(paths):
        row=json.loads(j.read_text());check(i,row)
        with np.load(z,allow_pickle=False) as data:
            assert len(data['four'])==row['pages']
            pf.update(map(int,data['four']));pr.update(map(int,data['provider']))
        rows[roles[i]]=row
    return (pf,pr),clock()-start

def replay(mode):
    start=clock()
    blobs=[(j.read_bytes(),z.read_bytes()) for j,z in paths]
    t1=clock(); arrays=[[],[]];rows=[]
    for i,(js,z) in enumerate(blobs):
        row=json.loads(js);check(i,row);rows.append(row)
        with np.load(io.BytesIO(z),allow_pickle=False) as data:
            a=data['four'];assert len(a)==row['pages'];b=data['provider']
        for col,v in zip(arrays,(a,b)):
            col.append(list(map(int,v)) if mode=='split' else v.tolist() if mode=='tolist' else v)
    t2=clock()
    if mode=='unique':
        u=tuple(np.unique(np.concatenate(col)) for col in arrays)
    else:
        u=(set(),set())
        for union,col in zip(u,arrays):
            for values in col:union.update(values)
    end=clock()
    return u,dict(io=t1-start,convert=t2-t1,union=end-t2,total=end-start)

# Optional fresh-parent control: first use of numpy's NPZ loader in this interpreter.
if len(sys.argv)==4:
    mode=sys.argv[3]
    before=cache()
    if mode=='original':
        u,total=original();d=dict(total=total)
    else:
        u,d=replay(mode)
    assert tuple(map(len,u))==(166440,19)
    print(json.dumps(dict(method=mode,cache=before,**d)))
    sys.exit(0)

state=cache();results=[];ref=None
def once(mode,i):
    global ref
    if mode=='original':
        u,total=original();d=dict(total=total)
    else:
        u,d=replay(mode)
    if mode=='unique':
        u=tuple(set(map(int,v)) for v in u) # only for equality testing, outside timer
    if ref is None:ref=u
    assert u==ref and tuple(map(len,u))==(archive['four_unique_pages'],archive['provider_unique_pages'])
    results.append(dict(trial=i,method=mode,**d))
once('split',0)
after=cache()
modes=['original','split','tolist','unique']
for i in range(1,17):
    for mode in modes[i%4:]+modes[:i%4]:once(mode,i)
with (out/'timings.csv').open('x') as f:
    w=csv.DictWriter(f,fieldnames=['trial','method','io','convert','union','total'],lineterminator='\n');w.writeheader();w.writerows(results)
stats=dict(before=state,after_first=after,first=results[0],input_pages=sum(r['pages'] for r in archive['query_rows'].values()),unique=[len(v) for v in ref],pfn_range=[min(ref[0]),max(ref[0])])
stats['repeat']={}
for m in modes:
    rs=[r for r in results if r['method']==m and r['trial']]
    stats['repeat'][m]={k:dict(mean_ms=statistics.mean(r[k] for r in rs)/1e6,median_ms=statistics.median(r[k] for r in rs)/1e6) for k in rs[0] if k not in ('trial','method')}
(out/'summary.json').write_text(json.dumps(stats,indent=2)+'\n');print(json.dumps(stats,indent=2))
