"""Offline only; run from repo root. No live service, guest or device access."""
import csv
import gzip
import json
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
from fast_cache_metadata import decode_array,matching_tags
from kernel_cache_metadata import PRESENT,SWAPPED,PHYSICAL_MASK,MASK

OUT=ROOT/'results/segment-sampling-error-20261119'
OUT.mkdir(exist_ok=True,parents=True)
RUNS=['20261118T131857Z-610591','20261118T124041Z-595229','20261118T042313Z-432686']
R=512
SEED=20261119

def records():
    base=ROOT/'.artifacts/storage-isolated-service'
    cases=[]
    for batch,run in enumerate(RUNS):
        raw=base/run/'guest-cache'
        counts=[0,0]
        for role in ([f'restored-{i}-pages' for i in range(1,8)] if batch<2 else ['restored-g1']):
            d=raw/'live-cache'/role
            for point in [[0],[59],[0,1,2,3,4]][batch]:
                prefix=d/f'pair-{point:04}'
                s=json.loads((d/'samples.jsonl').read_text().splitlines()[point])
                h,b,l=[np.fromfile(str(prefix)+'-'+key+'.bin','<u8').reshape(-1,4) for key in ('host','backend','lower')]
                left,right=[decode_array(Path(str(prefix)+'-'+key+'.bin').read_bytes())[1] for key in ('before','after')]
                pg=left[left[:,1]!=0]
                assert all(np.array_equal(m[:,0],pg[:,0]) for m in [h,b,l])
                same=matching_tags(right,pg[:,0])==pg[:,1]
                same &= (((h[:,1]^h[:,2])|(h[:,2]^h[:,3])) & PHYSICAL_MASK)==0
                present=(h[:,1]&PRESENT)!=0;file=(h[:,1]&(1<<61))!=0
                dok=(b[:,2]==b[:,3])&((b[:,2]&PRESENT)!=0)
                same &= ~(present&file&dok&((h[:,1]&MASK)!=(b[:,2]&MASK)))
                current=same & ((~present&((h[:,1]&SWAPPED)==0))|(present&file))
                for k,(layer,m,field) in enumerate([('backend',b,'host_snapshot_current_bytes'),('lower',l,'host_lower_current_bytes')]):
                    of=m[:,1];known=of!=np.iinfo('u8').max
                    order=np.flatnonzero(known)[np.argsort(of[known])]
                    offs=of[order];assert np.all(np.diff(offs)>0)
                    starts=np.r_[0,1+np.flatnonzero(np.diff(offs)!=1)]
                    lens=np.diff(np.r_[starts,len(offs)]);counts[k]+=2*len(lens)
                    qualified=current&(m[:,2]==m[:,3])&((m[:,2]&PRESENT)!=0)
                    # Per-segment additive HT is valid only if no PFN is credited twice.
                    assert not (qualified&~known).any()
                    pfns=m[qualified,2]&MASK
                    assert len(np.unique(pfns))==len(pfns)==s[field]//4096 and s[field]%4096==0
                    y=np.add.reduceat(qualified[order].astype(int),starts)
                    assert sum(y)==len(pfns)>0
                    receipt=json.loads(((d if k==0 else d/'lower-query')/'snapshot-query-result.json').read_text())
                    assert [q['requested_pages'] for q in receipt['queries'][2*point:2*point+2]]==[sum(lens)]*2
                    name=['transition','late','reference'][batch]
                    cases.append((name,d.name,point,layer,offs[starts],lens,y,len(left),len(right)))
        if batch==0: assert counts==[25790,12644]
    return cases

def probs(lengths,n):
    # Solve sum min(1,c*L_i)=n, admitting certainty units.
    pi=np.zeros(len(lengths));free=np.ones(len(pi),bool)
    while free.any():
        left=n-int((~free).sum())
        if left==free.sum():pi[free]=1;break
        pi[free]=left*lengths[free]/lengths[free].sum()
        take=free&(pi>=1)
        if not take.any():break
        pi[take]=1;free[take]=False
    assert np.isclose(pi.sum(),n) and np.all(pi>0) and max(pi)<=1
    return pi

def sample(case,idx,q,method):
    name,role,pt,layer,_,L,y,_,_=case
    N=len(L);n=max(1,int(np.floor(N*q/100+.5)))
    rng=np.random.default_rng(np.random.SeedSequence([SEED,idx,q])) # shared permutations across designs
    pi=probs(L,n) if method=='PPS' else np.full(N,n/N)
    est=[];pages=[]
    for i in range(R):
        perm=rng.permutation(N);u=rng.random()
        if method=='SRS':sel=perm[:n]
        else:
            cs=np.cumsum(pi[perm]);cs[-1]=n
            sel=perm[np.searchsorted(cs,u+np.arange(n),side='right')]
            assert len(np.unique(sel))==n
        est.append((y[sel]/pi[sel]).sum());pages.append(L[sel].sum())
    err=100*(np.array(est)/y.sum()-1)
    row=[name,role,pt,layer,q,method,N,n,int(y.sum()),np.median(abs(err)),np.quantile(abs(err),.95),np.mean(err),pi@L/L.sum()*100,np.std(err,ddof=1)/R**.5]
    return row,est,pages

def main():
    cases=records()
    with gzip.open(OUT/'segments.csv.gz','wt') as f:
        w=csv.writer(f);w.writerow('case role point layer start_page length_pages confirmed_pages'.split())
        for a,r,p,k,start,L,y,*_ in cases:w.writerows((a,r,p,k,*v) for v in zip(start,L,y))
    header='case role point layer pct method segments sampled_n truth median_abs_pct p95_abs_pct mean_signed_pct expected_page_pct bias_mc_se_pct'.split()
    basic={}
    with (OUT/'summary.csv').open('w') as f,gzip.open(OUT/'trials.csv.gz','wt') as ff:
        w=csv.writer(f);w.writerow(header);t=csv.writer(ff);t.writerow('case role point layer pct method case_index repeat sampled_n sampled_pages estimate'.split())
        for idx,case in enumerate(cases):
            for q in [5,10,25]:
                for method in ['SRS','PPS']:
                    row,est,pages=sample(case,idx,q,method);basic[idx,q,method]=row
                    w.writerow(row);t.writerows((*row[:6],idx,i,row[7],pages[i],e) for i,e in enumerate(est))
    # Find first integer grid rate satisfying ALL cases: one failed cell disproves a rate.
    with (OUT/'rate-search.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['test_order','case_index']+header)
        minima={}
        for method in ['SRS','PPS']:
            order=sorted(range(len(cases)),key=lambda i:-basic[i,25,method][10])
            for q in range(1,101):
                for rank,i in enumerate(order):
                    row=basic.get((i,q,method)) or sample(cases[i],i,q,method)[0]
                    w.writerow([rank,i]+row)
                    if row[10]>5:break
                else:
                    minima[method]=q
                    print(method,'minimum tested percent',q,flush=True);break
    joint=max(minima.values())
    with (OUT/'joint-check.csv').open('w') as f:
        w=csv.writer(f);w.writerow(header)
        for method in minima:
            if minima[method]==joint:continue
            for i,case in enumerate(cases):w.writerow(sample(case,i,joint,method)[0])

if __name__=='__main__':main()
