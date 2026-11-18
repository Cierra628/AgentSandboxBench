"""Independently mark guest bytes for saved segment records; no live access."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
import struct

from audit_lower_cache import check_point
from audit_provider_get_data import restore_audit
from kernel_cache_metadata import decode,MASK,PRESENT,PHYSICAL_MASK,SWAPPED

PAGE=4096
UNIT=512
FILE=1<<61
UNKNOWN=(1<<64)-1
ROWS=struct.Struct('<4Q')


def independent_segments(before,after,host,direct,lower,first,last,three):
    _,left=decode(before);header,right=decode(after)
    lookup=defaultdict(list);confirmed=set()
    for (guest,a,b,c),(other,offset,x,y),(third,_,u,v) in zip(host,direct,lower,strict=True):
        assert guest==other==third
        if left[guest]!=right.get(guest) or ((a^b)|(b^c))&PHYSICAL_MASK:continue
        direct_ok=x==y and x&PRESENT
        if a&PRESENT and a&FILE and direct_ok and (a&MASK)!=(x&MASK):continue
        version='current' if not a&(PRESENT|SWAPPED) or a&PRESENT and a&FILE else 'old' if a&PRESENT else 'unknown'
        if a&PRESENT or version=='current' and (direct_ok or u==v and u&PRESENT):confirmed.add(guest)
        if offset!=UNKNOWN:lookup[offset].append((guest,version))
    current=[set(),set()];old=[set(),set()];resident=set();mixed=set();coverage={}

    def admit(sources,pfn,mode):
        if any(source is None for source in sources):return
        fragments=[];kinds=set()
        for source in sources:
            page,inner=divmod(source,PAGE)
            assert source%UNIT==0 and inner+UNIT<=PAGE
            if page not in lookup:return
            for guest,kind in lookup[page]:
                kinds.add(kind);fragments.append((guest,inner,inner+UNIT))
        if kinds=={'current'}:
            current[mode].add(pfn)
            for guest,start,end in fragments:
                coverage.setdefault(guest,bytearray(PAGE))[start:end]=b'\1'*(end-start)
        elif kinds=={'old'}:old[mode].add(pfn)
        elif mode==1:mixed.add(pfn)

    assert [r['name'] for r in first['rows']]==[r['name'] for r in last['rows']]
    for a,b in zip(first['rows'],last['rows'],strict=True):
        versions=[f[k] for f in (a,b) for k in ('before','after')]
        row=versions[0];address,capacity,offset,length,state=row[2:7]
        assert address%UNIT==0 and capacity%UNIT==0
        tables=[f['segments'][k] for f in (a,b) for k in ('before','after')]
        assert all(f['segments']['schema']==1 and f['segments']['unit_bytes']==UNIT for f in (a,b))
        assert all(len(t)==capacity//UNIT for t in tables)
        legacy=all(v==row for v in versions) and row[1]%2==0 and state==3 and 0<length<=capacity
        allocation=all(v[2:4]==row[2:4] for v in versions)
        for index,(x,y) in enumerate(zip(a['pagemap'],b['pagemap'],strict=True)):
            if any(v[6]==5 for v in versions) or x!=y or not x&PRESENT or not x&MASK:continue
            virtual=a['first_address']+index*PAGE;pfn=x&MASK
            if virtual<address or virtual+PAGE>address+capacity:mixed.add(pfn);continue
            resident.add(pfn)
            start=virtual-address
            if legacy and start+PAGE<=length:admit([offset+start+i for i in range(0,PAGE,UNIT)],pfn,0)
            if not allocation:continue
            sources=[]
            for i in range(start//UNIT,(start+PAGE)//UNIT):
                copies=[t[i] for t in tables];generation,source=copies[0]
                sources.append(source if all(tuple(c)==tuple(copies[0]) for c in copies)
                    and generation%2==0 and source!=UNKNOWN else None)
            admit(sources,pfn,1)
    combined=current[0]|current[1];combined_old=old[0]|old[1]
    full={guest for guest,marks in coverage.items() if all(marks)}
    values=dict(host_provider_segment_current_bytes=len(current[1])*PAGE,
        host_provider_segment_added_current_bytes=len(current[1]-current[0])*PAGE,
        host_provider_segment_old_snapshot_bytes=len(old[1])*PAGE,
        host_provider_segment_added_old_snapshot_bytes=len(old[1]-old[0])*PAGE,
        host_provider_combined_current_bytes=len(combined)*PAGE,
        host_provider_combined_old_snapshot_bytes=len(combined_old)*PAGE,
        host_four_segment_current_union_bytes=len(three|combined)*PAGE,
        host_four_segment_unconfirmed_guest_capacity_bytes=(header['records']-len(confirmed|full))*PAGE,
        provider_segment_resolved_guest_pages=len(full-confirmed),
        provider_segment_unattributed_resident_bytes=len(resident-combined-combined_old)*PAGE,
        provider_segment_mixed_or_edge_resident_pages=len(mixed))
    return values,three|combined


def audit(storage):
    result=restore_audit(storage)
    directory=storage/'guest-cache/live-cache/restored'
    points=[];costs=[]
    for saved in map(json.loads,(directory/'samples.jsonl').read_text().splitlines()):
        prefix=directory/f"pair-{saved['point']:04}"
        read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        rows=lambda suffix:list(ROWS.iter_unpack(read(suffix)))
        raw=json.loads(read('-provider.json'));_,three=check_point(directory,saved)
        counted,physical=independent_segments(read('-before.bin'),read('-after.bin'),rows('-host.bin'),
            rows('-backend.bin'),rows('-lower.bin'),raw['first'],raw['last'],three)
        assert all(saved[k]==v for k,v in counted.items()),(saved['point'],counted)
        from provider_slot_cache import recount_point
        _,online_physical=recount_point(directory,saved)
        assert physical==online_physical,('physical PFN set differs',saved['point'])
        points.append(dict(point=saved['point'],**counted))
        assert saved['provider_query_metadata_bytes']==sum(raw[f]['metadata_bytes_read'] for f in ('first','last'))
        costs.append(saved['provider_query_metadata_bytes'])
    identities=json.loads((directory/'provider/identity.json').read_text())['slots']
    assert all(s['segments']==dict(schema=1,unit_bytes=UNIT,entry_offset=64,entry_bytes=16,count=512) for s in identities)
    metadata_files=list((storage/'service-provider-slots').glob('*.bin'))
    assert len(metadata_files)==16
    for file in metadata_files:
        data=file.read_bytes();assert len(data)==12288
        entries=list(struct.Struct('<2Q').iter_unpack(data[64:64+512*16]))
        assert all(g%2==0 and offset==UNKNOWN for g,offset in entries)
    result.update(segment_status='PASS',segment_points=points,segment_physical_sets_equal=True,
        segment_coverage='POSITIVE' if any(p['host_provider_segment_added_current_bytes'] for p in points) else 'NO_ADDITIONAL_CURRENT_COPIES',
        binary_metadata_mapped_bytes=sum(f.stat().st_size for f in metadata_files),
        metadata_read_bytes_per_point=statistics.mean(costs),all_segment_entries_released_invalid=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('storage',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result=audit(args.storage.resolve())
    with args.output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print('status=PASS coverage='+result['segment_coverage']+' result_dir='+str(args.output.parent))
