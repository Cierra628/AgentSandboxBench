"""Classify saved segment-era unknown PFNs; no live or payload reads."""
import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode,MASK,PRESENT,PHYSICAL_MASK,SWAPPED
from provider_slot_cache import account_provider,PAGE,UNIT,UNKNOWN,segment_sources

FILE=1<<61
ROWS=struct.Struct('<4Q')
GUEST_REASONS=('guest_identity_changed','vmm_pte_changed','file_pfn_conflict')
REASONS=('allocation_changed','segment_record_changed','segment_never_filled','segment_invalidated',
    'offset_not_in_initial_candidates',*GUEST_REASONS,'version_unknown','mixed_versions','multiple_unknown_aliases')


def classify(before,after,host,direct,lower,first,last,*,details=None):
    # Reuse the already verified admission result, then explain only its unknown
    # partition. Candidate failure reasons below are derived from the raw rows.
    args=(before,after,host,direct,lower,first,last);legacy={};segments={}
    account_provider(*args,details=legacy);account_provider(*args,segmented=True,details=segments)
    current=legacy['current']|segments['current'];old=legacy['old']|segments['old']
    unknown=legacy['resident']-current-old
    _,left=decode(before);_,right=decode(after)
    candidates=defaultdict(set);eligible=defaultdict(set)
    for (guest,a,b,c),(other,offset,x,y) in zip(host,direct,strict=True):
        assert guest==other
        if offset==UNKNOWN:continue
        if left[guest]!=right.get(guest):reason='guest_identity_changed'
        elif ((a^b)|(b^c))&PHYSICAL_MASK:reason='vmm_pte_changed'
        elif a&PRESENT and a&FILE and x==y and x&PRESENT and (a&MASK)!=(x&MASK):reason='file_pfn_conflict'
        else:
            reason='current' if not a&(PRESENT|SWAPPED) or a&PRESENT and a&FILE else 'old' if a&PRESENT else 'unknown'
            eligible[offset].add(reason)
        candidates[offset].add(reason)
    reasons=defaultdict(set);source_pages=defaultdict(set);edge=set();excluded=Counter()
    for a,b in zip(first['rows'],last['rows'],strict=True):
        versions=[f[k] for f in (a,b) for k in ('before','after')]
        address,capacity=versions[0][2:4]
        allocation=all(v[2:4]==versions[0][2:4] for v in versions)
        copies=[f['segments'][k] for f in (a,b) for k in ('before','after')]
        sources=segment_sources(a,b,capacity)
        for index,(x,y) in enumerate(zip(a['pagemap'],b['pagemap'],strict=True)):
            if any(v[6]==5 for v in versions):excluded['released']+=1;continue
            if x!=y:excluded['provider_pagemap_changed']+=1;continue
            if not x&PRESENT or not x&MASK:excluded['provider_pfn_unavailable']+=1;continue
            virtual=a['first_address']+index*PAGE;pfn=x&MASK
            if virtual<address or virtual+PAGE>address+capacity:edge.add(pfn);continue
            if pfn not in unknown:continue
            begin=(virtual-address)//UNIT;end=begin+PAGE//UNIT
            pieces=sources[begin:end]
            records=list(zip(*(c[begin:end] for c in copies),strict=True))
            if not allocation:reason='allocation_changed'
            elif any(v[0][0]%2 or any(tuple(r)!=tuple(v[0]) for r in v) for v in records):reason='segment_record_changed'
            elif any(v[0][0]==0 and v[0][1]==UNKNOWN for v in records):reason='segment_never_filled'
            elif any(p is None for p in pieces):reason='segment_invalidated'
            else:
                pages={p//PAGE for p in pieces}
                source_pages[pfn].update(pages)
                if any(p not in candidates for p in pages):reason='offset_not_in_initial_candidates'
                elif any(p not in eligible for p in pages):
                    failed=set().union(*(candidates[p] for p in pages if p not in eligible))
                    reason=next(r for r in GUEST_REASONS if r in failed)
                else:
                    kinds=set().union(*(eligible[p] for p in pages))
                    assert kinds not in ({'current'},{'old'}), 'admissible page left unknown'
                    reason='version_unknown' if kinds=={'unknown'} else 'mixed_versions'
            reasons[pfn].add(reason)
    totals=Counter({r:0 for r in REASONS})
    for pfn in unknown:
        kinds=reasons[pfn];assert kinds
        totals[next(iter(kinds)) if len(kinds)==1 else 'multiple_unknown_aliases']+=1
    assert sum(totals.values())==len(unknown)
    if details is not None:
        details.update(reasons=dict(reasons),source_pages=dict(source_pages),
            candidate_offsets=set(candidates),unknown=unknown)
    return dict(whole_resident_pages=len(legacy['resident']),current_pages=len(current),old_pages=len(old),
        unattributed_pages=len(unknown),unknown_by_reason_pages=dict(totals),boundary_pages=len(edge),
        excluded_virtual_page_observations=dict(excluded))


def report(source,output):
    source=source.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    points=[]
    for saved in map(json.loads,(source/'samples.jsonl').read_text().splitlines()):
        prefix=source/f"pair-{saved['point']:04}"
        read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        rows=lambda suffix:list(ROWS.iter_unpack(read(suffix)))
        raw=json.loads(read('-provider.json'))
        result=classify(read('-before.bin'),read('-after.bin'),rows('-host.bin'),rows('-backend.bin'),
            rows('-lower.bin'),raw['first'],raw['last'])
        for key,metric in dict(whole_resident_pages='provider_whole_allocation_resident_bytes',
                current_pages='host_provider_combined_current_bytes',old_pages='host_provider_combined_old_snapshot_bytes',
                unattributed_pages='provider_segment_unattributed_resident_bytes').items():
            assert result[key]*PAGE==saved[metric],(saved['point'],key)
        points.append(dict(point=saved['point'],**result))
    summary=dict(status='PASS',page_size=PAGE,point_count=len(points),points=points,
        reason_order=REASONS,physical_union='UNCHANGED',
        scope='Unique unknown whole resident PFNs after original and segment current/old attribution.',
        candidate_limit='Missing initial cache-offset candidate does not establish non-cache content.')
    output.mkdir(parents=True);(output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (output/'points.csv').open('w') as stream:
        fields=['point','unattributed_pages',*REASONS];writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows(dict(point=p['point'],unattributed_pages=p['unattributed_pages'],**p['unknown_by_reason_pages']) for p in points)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args();result=report(args.source,args.output)
    print(f"status=PASS points={result['point_count']} result_dir={args.output}")
