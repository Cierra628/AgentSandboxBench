"""Independently recount snapshot/VMM PFN sets and check the calibration stages."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode, MASK, PRESENT, PHYSICAL_MASK, SWAPPED

PAGE=4096
FILE=1<<61
ROW=struct.Struct('<4Q')

def scalar(before,after,host,backing):
    _,left=decode(before);right,last=decode(after)
    vmm=set();backend=set();old=set();confirmed=extra=unknown=mismatch=0
    for (p,a,b,c),(q,offset,x,y) in zip(host,backing,strict=True):
        assert p==q and p in left and left[p]
        backed=x==y and bool(x&PRESENT)
        unknown+=not backed
        if last.get(p)!=left[p] or ((a^b)|(b^c))&PHYSICAL_MASK:continue
        if a&PRESENT and a&FILE and backed and a&MASK!=x&MASK:
            mismatch+=1;continue
        if a&PRESENT:
            vmm.add(a&MASK);confirmed+=1
            if backed:
                (backend if a&FILE else old).add(x&MASK)
        elif not a&SWAPPED and backed:
            backend.add(x&MASK);confirmed+=1;extra+=1
    return dict(host_vmm_confirmed_bytes=len(vmm)*PAGE,host_snapshot_current_bytes=len(backend)*PAGE,
        host_vmm_snapshot_intersection_bytes=len(vmm&backend)*PAGE,host_current_union_bytes=len(vmm|backend)*PAGE,
        snapshot_old_version_bytes=len(old)*PAGE,snapshot_without_vmm_pte_pages=extra,
        backend_query_unconfirmed_pages=unknown,backend_file_pfn_mismatch_pages=mismatch,
        host_current_unconfirmed_guest_capacity_bytes=(right['records']-confirmed)*PAGE)


def audit(source):
    source=source.resolve();d=source/'live-cache/restored'
    samples=list(map(json.loads,(d/'samples.jsonl').read_text().splitlines()))
    raws={};rows={}
    for saved in samples:
        prefix=d/f"pair-{saved['point']:04}"
        load=lambda suffix:Path(str(prefix)+suffix).read_bytes()
        host=list(ROW.iter_unpack(load('-host.bin')));backing=list(ROW.iter_unpack(load('-backend.bin')))
        result=scalar(load('-before.bin'),load('-after.bin'),host,backing)
        assert all(saved[k]==v for k,v in result.items())
        _,before=decode(load('-before.bin'));_,after=decode(load('-after.bin'))
        rows[saved['point']]=(host,backing,before,after)
    phases=[]
    result=json.loads((source/'result.json').read_text())
    if result['task_id']=='snapshot-cache-calibration':
        ready=json.loads((source/'ready.json').read_text())
        assert ready['size_bytes']==16*2**20 and len(set(ready['pfns']))==4096 and 0 not in ready['pfns']
        for phase in result['phases']:
            state=json.loads((source/(phase['name']+'.json')).read_text())
            subset={p for p in state['pfns'] if p}
            written={p for p in state['pfns'][1024:2048] if p}
            observations=[]
            for point in samples:
                if not phase['start_ns']<=point['timestamp_ns']<=phase['end_ns']:continue
                host,backing,before,after=rows[point['point']]
                current=set();vm=set();back=set();old=set();cow=extra=0
                for (p,a,b,c),(q,offset,x,y) in zip(host,backing,strict=True):
                    if p not in subset or before.get(p)!=after.get(p) or ((a^b)|(b^c))&PHYSICAL_MASK:continue
                    current.add(p)
                    if a&PRESENT:vm.add(a&MASK)
                    if x==y and x&PRESENT:
                        if not a&(PRESENT|SWAPPED):back.add(x&MASK);extra+=1
                        elif a&PRESENT and a&FILE and a&MASK==x&MASK:back.add(x&MASK)
                        elif a&PRESENT and not a&FILE:old.add(x&MASK)
                    if p in written and a&PRESENT and not a&FILE:cow+=1
                observations.append(dict(point=point['point'],stable_known_guest_pages=len(current),
                    vmm_physical_bytes=len(vm)*PAGE,current_backend_bytes=len(back)*PAGE,
                    intersection_bytes=len(vm&back)*PAGE,current_union_bytes=len(vm|back)*PAGE,
                    old_backend_bytes=len(old)*PAGE,backed_without_vmm_pte_pages=extra,
                    written_private_anonymous_pages=cow))
            assert observations, 'missing phase samples'
            phases.append(dict(name=phase['name'],known_present_guest_ptes=len(subset),observations=observations))
        assert any(o['backed_without_vmm_pte_pages']>0 for o in phases[0]['observations'])
        assert any(o['written_private_anonymous_pages']==1024 for o in phases[2]['observations'])
        assert all(result['checks'][k]=='PASS' for k in ('file_contents','process_continuity','checkpoint_restore'))
    for path in sorted((source/'live-cache').rglob('*')):
        if path.is_file():raws[str(path.relative_to(source))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(status='PASS',restored_points=len(samples),independent_scalar_recount=True,
        source=str(source),calibration_phases=phases,raw_sha256=raws,
        scope='direct File-backend page cache and VMM current PFNs; lower OverlayBD encoded layers/provider buffers not attributed')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();value=audit(a.source)
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print('status=PASS restored_points='+str(value['restored_points'])+' result_dir='+str(a.output.parent))
