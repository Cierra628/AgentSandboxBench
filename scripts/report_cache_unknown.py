"""Partition saved guest-page uncertainty without contacting a running guest."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from kernel_cache_metadata import decode, PRESENT, SWAPPED, PHYSICAL_MASK, MASK
from snapshot_cache_residency import SnapshotCache, FILE

ROW=struct.Struct('<4Q')
CATEGORIES=('metadata_stale','metadata_before_stale','added_or_reused','vmm_pte_changed',
    'file_pfn_conflict','swapped','ram_region_changed','anonymous_without_pte',
    'backend_pfn_changed','backend_without_pfn')

def classify(left,last,host,backing,offset_after,lower=None,*,details=None):
    host={r[0]:r[1:] for r in host};backing={r[0]:r[1:] for r in backing}
    lower={r[0]:r[1:] for r in lower} if lower is not None else {}
    counts=Counter({k:0 for k in ('confirmed',*CATEGORIES)})
    for p,tag in last.items():
        if not tag:reason='metadata_stale'
        elif p in left and not left[p]:reason='metadata_before_stale'
        elif left.get(p)!=tag:reason='added_or_reused'
        else:
            a,b,c=host[p];offset,x,y=backing[p]
            _,lx,ly=lower.get(p,(0,0,0))
            stable_lower=lx==ly and bool(lx&PRESENT)
            stable_back=x==y and bool(x&PRESENT)
            if ((a^b)|(b^c))&PHYSICAL_MASK:reason='vmm_pte_changed'
            elif a&PRESENT and a&FILE and stable_back and a&MASK!=x&MASK:reason='file_pfn_conflict'
            elif a&PRESENT:reason='confirmed'
            elif a&SWAPPED:reason='swapped'
            elif stable_back or stable_lower:reason='confirmed'
            elif offset!=offset_after[p]:reason='ram_region_changed'
            elif offset==(1<<64)-1:reason='anonymous_without_pte'
            elif x!=y or lx!=ly:reason='backend_pfn_changed'
            else:reason='backend_without_pfn'
        counts[reason]+=1
        if details is not None:details[p]=reason
    assert sum(counts.values())==len(last)
    return dict(counts)

def report(source,output,*,session='restored'):
    assert session in ('restored','child-a','child-b')
    source=source.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source)
    d=source/'live-cache'/session;hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((source/'live-cache').rglob('*')) if p.is_file()}
    points=[]
    for saved in map(json.loads,(d/'samples.jsonl').read_text().splitlines()):
        prefix=d/f"pair-{saved['point']:04}"
        _,left=decode(Path(str(prefix)+'-before.bin').read_bytes())
        info,last=decode(Path(str(prefix)+'-after.bin').read_bytes())
        host=list(ROW.iter_unpack(Path(str(prefix)+'-host.bin').read_bytes()))
        backing=list(ROW.iter_unpack(Path(str(prefix)+'-backend.bin').read_bytes()))
        mapper=SnapshotCache.__new__(SnapshotCache)
        mapper.slots=json.loads((d/'ram-attestation.json').read_text())['slots']
        mapper.regions=json.loads(Path(str(prefix)+'-maps.json').read_text())['after']
        pfns=np.array([r[0] for r in backing],dtype='<u8')
        offsets=dict(zip(map(int,pfns),map(int,mapper.offsets(pfns))))
        lower_path=Path(str(prefix)+'-lower.bin')
        lower=list(ROW.iter_unpack(lower_path.read_bytes())) if lower_path.exists() else None
        counts=classify(left,last,host,backing,offsets,lower)
        unknown=sum(counts[k] for k in CATEGORIES)
        key='host_three_unconfirmed_guest_capacity_bytes' if lower is not None else 'host_current_unconfirmed_guest_capacity_bytes'
        assert unknown*4096==saved[key]
        assert counts['confirmed']+unknown==info['records']
        points.append(dict(point=saved['point'],timestamp_ns=saved['timestamp_ns'],
            unknown_guest_capacity_bytes=unknown*4096,**counts))
    output.mkdir(parents=True)
    result=dict(status='PASS',source=str(source),page_size=4096,points=points,
        first=points[0],peak=max(points,key=lambda p:p['unknown_guest_capacity_bytes']),
        interpretation='Mutually exclusive reasons in priority order; counts are guest capacity, not host physical bytes.',
        limitations=['Backend zero rows do not distinguish cold cache from unavailable PFNs in the saved format.',
                     'An anonymous mapping does not by itself make a resident VMM page unknown.',
                     'Sources are sampled over intervals, not one atomic instant.'],raw_sha256=hashes)
    if session!='restored':result['session']=session
    if any(Path(str(d/f"pair-{p['point']:04}")+'-lower.bin').exists() for p in points):
        result['backend_scope']='direct device and raw memory layer; confirmed when either is stable'
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    with (output/'points.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(points[0]));w.writeheader();w.writerows(points)
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='cache-unknown-20261015'
    import matplotlib.pyplot as plt
    labels=dict(metadata_stale='Metadata retired during export',metadata_before_stale='Unknown initial metadata',
        added_or_reused='Added / reused guest pages',vmm_pte_changed='VMM PTE changed',
        file_pfn_conflict='File PFN conflict',swapped='Swapped',ram_region_changed='File / anonymous region changed',
        anonymous_without_pte='Anonymous region without PTE',backend_pfn_changed='Backend PFN changed',
        backend_without_pfn='Backend PFN unavailable')
    fig,ax=plt.subplots(figsize=(10,5));times=[(p['timestamp_ns']-points[0]['timestamp_ns'])/1e9 for p in points]
    used=[k for k in CATEGORIES if any(p[k] for p in points)]
    ax.stackplot(times,*[[p[k]*4096/2**20 for p in points] for k in used],labels=[labels[k] for k in used])
    ax.set(xlabel='Elapsed time after first restored sample (s)',ylabel='Unconfirmed guest capacity (MiB)',title='Restored guest cache: reasons for uncertainty')
    ax.legend(loc='upper right',fontsize=8);ax.grid(alpha=.2);fig.tight_layout()
    fig.savefig(output/'unknown.svg',metadata={'Date':None});fig.savefig(output/'unknown.png');plt.close(fig)
    assert all(hashlib.sha256((source/p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--session',choices=['restored','child-a','child-b'],default='restored')
    a=p.parse_args();r=report(a.source,a.output,session=a.session)
    print('status='+r['status']+' points='+str(len(r['points']))+' result_dir='+str(a.output))
