"""Classify saved whole-buffer residency; never contacts a guest or reads payloads."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode, MASK, PRESENT, PHYSICAL_MASK, SWAPPED

PAGE=4096
FILE=1<<61
UNKNOWN=(1<<64)-1
ROW=struct.Struct('<4Q')
GUEST_REASONS=('guest_identity_changed','vmm_pte_changed','file_pfn_conflict')
REASONS=('slot_record_changed','slot_record_invalid','outside_valid_read',
    'offset_not_in_initial_candidates',*GUEST_REASONS,'mixed_versions','multiple_unknown_aliases')


def classify(before,after,host,backing,first,last):
    """Partition unique resident PFNs, retaining the original admission rules."""
    _,left=decode(before);_,right=decode(after)
    candidates=defaultdict(set);eligible=defaultdict(set)
    for (guest,a,b,c),(other,offset,x,y) in zip(host,backing,strict=True):
        assert guest==other
        if offset==UNKNOWN:continue
        if left[guest]!=right.get(guest):reason='guest_identity_changed'
        elif ((a^b)|(b^c))&PHYSICAL_MASK:reason='vmm_pte_changed'
        elif a&PRESENT and a&FILE and x==y and x&PRESENT and (a&MASK)!=(x&MASK):
            reason='file_pfn_conflict'
        else:
            reason='current' if not a&(PRESENT|SWAPPED) or a&PRESENT and a&FILE else 'old' if a&PRESENT else 'unknown'
            eligible[offset].add(reason)
        # Backend offsets are page indices, not byte offsets.
        candidates[offset].add(reason)
    known_current=set();known_old=set();resident=set();edge=set();mixed=set()
    reasons=defaultdict(set);excluded=Counter();stable_slots=valid_slots=0
    assert [r['name'] for r in first['rows']]==[r['name'] for r in last['rows']]
    for a,b in zip(first['rows'],last['rows'],strict=True):
        versions=[a['before'],a['after'],b['before'],b['after']]
        row=versions[0];address,capacity,offset,length,state=row[2:7]
        stable=all(v==row for v in versions) and row[1]%2==0
        valid=stable and state==3 and 0<length<=capacity
        stable_slots+=stable;valid_slots+=valid
        assert a['first_address']==b['first_address']
        for index,(x,y) in enumerate(zip(a['pagemap'],b['pagemap'],strict=True)):
            if any(v[6]==5 for v in versions):excluded['released']+=1;continue
            if x!=y:excluded['provider_pagemap_changed']+=1;continue
            if not x&PRESENT or not x&MASK:excluded['provider_pfn_unavailable']+=1;continue
            virtual=a['first_address']+index*PAGE;pfn=x&MASK
            if virtual<address or virtual+PAGE>address+capacity:
                edge.add(pfn);continue
            resident.add(pfn)
            if not stable:reason='slot_record_changed'
            elif not valid:reason='slot_record_invalid'
            elif virtual+PAGE>address+length:reason='outside_valid_read'
            else:
                start=offset+virtual-address
                pages=range(start//PAGE,(start+PAGE-1)//PAGE+1)
                if any(p not in candidates for p in pages):reason='offset_not_in_initial_candidates'
                elif any(p not in eligible for p in pages):
                    failed=set().union(*(candidates[p] for p in pages if p not in eligible))
                    reason=next(r for r in GUEST_REASONS if r in failed)
                else:
                    kinds=set().union(*(eligible[p] for p in pages))
                    if kinds=={'current'}:known_current.add(pfn);continue
                    if kinds=={'old'}:known_old.add(pfn);continue
                    mixed.add(pfn);reason='mixed_versions'
            reasons[pfn].add(reason)
    unknown=resident-known_current-known_old
    counts=Counter({r:0 for r in REASONS})
    for pfn in unknown:
        kinds=reasons[pfn];assert kinds
        counts[next(iter(kinds)) if len(kinds)==1 else 'multiple_unknown_aliases']+=1
    assert sum(counts.values())==len(unknown)
    assert len(resident)==len(known_current|known_old)+len(unknown)
    return dict(whole_resident_pages=len(resident),current_pages=len(known_current),old_pages=len(known_old),
        current_old_overlap_pages=len(known_current&known_old),unattributed_pages=len(unknown),
        unknown_by_reason_pages=dict(counts),boundary_pages=len(edge),mixed_whole_pages=len(mixed),
        mixed_or_boundary_pages=len(edge|mixed),stable_slots=stable_slots,valid_slots=valid_slots,
        excluded_virtual_page_observations=dict(excluded))


def report(source,output):
    source=source.resolve();output=output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    identity=json.loads((source/'provider/identity.json').read_text())
    assert all(s['negotiated_flags']&4 and s['content_validity']=='before_overwrite' for s in identity['slots'])
    points=[]
    for saved in map(json.loads,(source/'samples.jsonl').read_text().splitlines()):
        prefix=source/f"pair-{saved['point']:04}"
        load=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        raw=json.loads(load('-provider.json'))
        counted=classify(load('-before.bin'),load('-after.bin'),list(ROW.iter_unpack(load('-host.bin'))),
            list(ROW.iter_unpack(load('-backend.bin'))),raw['first'],raw['last'])
        expected=dict(whole_resident_pages='provider_whole_allocation_resident_bytes',current_pages='host_provider_current_bytes',
            old_pages='host_provider_old_snapshot_bytes',unattributed_pages='provider_unattributed_resident_bytes')
        assert all(counted[k]*PAGE==saved[v] for k,v in expected.items()), saved['point']
        assert counted['mixed_or_boundary_pages']==saved['provider_mixed_or_edge_resident_pages']
        assert counted['stable_slots']==saved['provider_stable_slots'] and counted['valid_slots']==saved['provider_valid_slots']
        points.append(dict(point=saved['point'],timestamp_ns=saved['timestamp_ns'],**counted))
    result=dict(status='PASS',source=str(source),page_size=PAGE,point_count=len(points),points=points,
        partition='unique stable resident whole allocation PFNs minus current and old attributed PFNs',
        reason_order=list(REASONS),limitations=[
            'Changed slot records take priority: their saved offset and length cannot describe the whole interval.',
            'Outside the current complete read, older tail contents have no saved per-range ownership.',
            'Missing offset means no match among initial sampled cache candidates, not proven non-cache data.',
            'Boundary pages are separate; mixed whole pages also belong to the unknown partition.',
            'Excluded virtual-page observations have no confirmed physical byte total.',
            'Original physical unions and guest unknown capacity are unchanged.'])
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    with (output/'points.csv').open('w') as stream:
        fields=['point','whole_resident_pages','current_pages','old_pages','unattributed_pages',*REASONS,
            'boundary_pages','mixed_whole_pages']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({**{k:p[k] for k in fields if k not in REASONS},**p['unknown_by_reason_pages']} for p in points)
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='provider-buffer-unknown-20261016'
    import matplotlib.pyplot as plt
    labels=dict(slot_record_changed='Slot record changed',slot_record_invalid='Invalid content record',
        outside_valid_read='Outside current complete read',offset_not_in_initial_candidates='No initial cache-offset match',
        guest_identity_changed='Guest cache identity changed',vmm_pte_changed='VMM mapping changed',
        file_pfn_conflict='VMM/backend PFN conflict',mixed_versions='Mixed / unknown versions',
        multiple_unknown_aliases='Multiple alias reasons')
    used=[k for k in REASONS if any(p['unknown_by_reason_pages'][k] for p in points)]
    fig,ax=plt.subplots(figsize=(10,5))
    times=[(p['timestamp_ns']-points[0]['timestamp_ns'])/1e9 for p in points]
    ax.stackplot(times,*[[p['unknown_by_reason_pages'][k]*PAGE/2**20 for p in points] for k in used],
        labels=[labels[k] for k in used])
    ax.set(xlabel='Time after first restored sample (s)',ylabel='Unattributed buffer residency (MiB)',
        title='Whole buffer pages: why current cache ownership is unconfirmed')
    ax.legend(loc='lower left',fontsize=9);ax.grid(alpha=.2);fig.tight_layout()
    fig.savefig(output/'unknown.svg',metadata={'Date':None});fig.savefig(output/'unknown.png');plt.close(fig)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=report(args.source,args.output)
    print('status=PASS points='+str(result['point_count'])+' result_dir='+str(args.output))
