"""Audit saved GET_DATA protocol traces and independently attribute buffer bytes."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import struct

from audit_lower_cache import check_point
from kernel_cache_metadata import decode, MASK, PRESENT, PHYSICAL_MASK, SWAPPED

PAGE=4096
FILE=1<<61
MAGIC=0x31544f4c53425341
ROWS=struct.Struct('<4Q')


def device_audit(directory):
    events=[]
    log=(directory.parent/'device-check.log').read_text()
    assert 'Lost ' not in log and 'ERROR:' not in log
    for line in log.splitlines():
        if line.startswith('copy '):
            events.append({k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',line)})
    assert events and all(e['magic']==MAGIC and e['generation']%2==0 for e in events)
    writes=[e for e in events if e['dir']==0]
    assert len(writes)==64 and all(e['state']==4 and e['valid_len']==0 for e in writes)
    result=dict(status='PASS',kernel_copy_counts={str(k):v for k,v in Counter(
        (e['epoch'],e['dir'],e['state']) for e in events).items()},modes={})
    for epoch,name in enumerate(['default','get-data'],1):
        base=directory/name
        identity=json.loads(next((base/'slots').glob('*.json')).read_text())
        io=json.loads((base/'io.json').read_text())
        reads=[e for e in events if e['epoch']==epoch and e['dir']==1]
        assert reads and all(e['state']==(3 if epoch==2 else 4) for e in reads)
        assert len([e for e in writes if e['epoch']==epoch])==32
        assert bool(identity['negotiated_flags']&4)==(epoch==2)
        assert (base/'status').read_text().strip()=='PASS'
        assert all(io[k]=='PASS' for k in ('read_read','read_write_read','error_then_read'))
        released=struct.unpack('<8Q',next((base/'slots').glob('*.bin')).read_bytes()[:64])
        assert released[6]==5 and released[5]==0
        result['modes'][name]=dict(negotiated_flags=identity['negotiated_flags'],
            content_validity=identity['content_validity'],write_operations=32,
            read_median_ns=statistics.median(t['read_ns'] for t in io['timings']),
            write_median_ns=statistics.median(t['write_ns'] for t in io['timings']),
            checks={k:io[k] for k in ('read_read','read_write_read','error_then_read')},
            released_invalid=True)
    return result


def independent_provider(directory,saved):
    """Mark covered guest bytes explicitly; do not call online provider accounting."""
    prefix=directory/f"pair-{saved['point']:04}"
    load=lambda suffix:Path(str(prefix)+suffix).read_bytes()
    _,before=decode(load('-before.bin'));header,after=decode(load('-after.bin'))
    host=list(ROWS.iter_unpack(load('-host.bin')))
    direct=list(ROWS.iter_unpack(load('-backend.bin')))
    lower=list(ROWS.iter_unpack(load('-lower.bin')))
    three,pfns=check_point(directory,saved)
    lookup=defaultdict(list);confirmed=set()
    for h,d,l in zip(host,direct,lower,strict=True):
        guest,a,b,c=h;other,offset,x,y=d
        assert guest==other==l[0]
        if before[guest]!=after.get(guest) or ((a^b)|(b^c))&PHYSICAL_MASK:continue
        direct_ok=x==y and x&PRESENT
        lower_ok=l[2]==l[3] and l[2]&PRESENT
        if a&PRESENT and a&FILE and direct_ok and (a&MASK)!=(x&MASK):continue
        version='current' if not a&(PRESENT|SWAPPED) or a&PRESENT and a&FILE else 'old' if a&PRESENT else 'unknown'
        if a&PRESENT or version=='current' and (direct_ok or lower_ok):confirmed.add(guest)
        # Saved backend offsets are page indices; slot offsets below are bytes.
        if offset!=(1<<64)-1:lookup[offset].append((guest,version))
    raw=json.loads(load('-provider.json'))
    current=set();old=set();resident=set();mixed=set();coverage={}
    stable=valid=0
    assert [r['name'] for r in raw['first']['rows']]==[r['name'] for r in raw['last']['rows']]
    for first,last in zip(raw['first']['rows'],raw['last']['rows'],strict=True):
        versions=[first['before'],first['after'],last['before'],last['after']]
        row=versions[0];address,capacity,offset,length,state=row[2:7]
        stable_row=all(r==row for r in versions) and row[1]%2==0
        usable=stable_row and state==3 and 0<length<=capacity
        stable+=stable_row;valid+=usable
        assert first['first_address']==last['first_address']
        for index,(x,y) in enumerate(zip(first['pagemap'],last['pagemap'],strict=True)):
            if any(r[6]==5 for r in versions) or x!=y or not x&PRESENT or not x&MASK:continue
            address_page=first['first_address']+index*PAGE;physical=x&MASK
            if address_page<address or address_page+PAGE>address+capacity:
                mixed.add(physical);continue
            resident.add(physical)
            if not usable or address_page+PAGE>address+length:continue
            logical_start=offset+address_page-address;logical_end=logical_start+PAGE
            segments=[]
            for page in range(logical_start//PAGE,(logical_end-1)//PAGE+1):
                entries=lookup.get(page)
                if not entries:break
                for guest,version in entries:
                    segments.append((guest,version,max(logical_start,page*PAGE)-page*PAGE,
                        min(logical_end,(page+1)*PAGE)-page*PAGE))
            else:
                kinds={segment[1] for segment in segments}
                if kinds=={'current'}:
                    current.add(physical)
                    for guest,_,start,end in segments:
                        coverage.setdefault(guest,bytearray(PAGE))[start:end]=b'\1'*(end-start)
                elif kinds=={'old'}:old.add(physical)
                else:mixed.add(physical)
    fully_covered={guest for guest,marks in coverage.items() if all(marks)}
    values=dict(host_provider_current_bytes=len(current)*PAGE,
        host_provider_old_snapshot_bytes=len(old)*PAGE,
        host_provider_intersection_three_bytes=len(current&pfns)*PAGE,
        host_four_current_union_bytes=len(pfns|current)*PAGE,
        host_four_unconfirmed_guest_capacity_bytes=(header['records']-len(confirmed|fully_covered))*PAGE,
        provider_resolved_guest_pages=len(fully_covered-confirmed),
        provider_stable_slots=stable,provider_valid_slots=valid,
        provider_whole_allocation_resident_bytes=len(resident)*PAGE,
        provider_unattributed_resident_bytes=len(resident-current-old)*PAGE,
        provider_mixed_or_edge_resident_pages=len(mixed))
    assert all(saved[k]==v for k,v in values.items()), (saved['point'],values)
    return dict(point=saved['point'],**three,**values)


def restore_audit(storage):
    receipt=json.loads((storage/'result.json').read_text());assert receipt['status']=='PASS'
    source=storage/'guest-cache';directory=source/'live-cache/restored'
    task=json.loads((source/'result.json').read_text());assert task['status']=='PASS'
    assert all(task['checks'][k]=='PASS' for k in ('file_contents','process_continuity','checkpoint_restore'))
    ready=json.loads((source/'ready.json').read_text())
    for name in ['waiting','read-done','write-done']:
        state=json.loads((source/(name+'.json')).read_text())
        assert all(state[k]==ready[k] for k in ('pid','boot_id','start_ticks'))
    samples={name:[json.loads(line) for line in (source/'live-cache'/name/'samples.jsonl').read_text().splitlines()]
        for name in ['parent','restored']}
    identity=json.loads((directory/'provider/identity.json').read_text())
    assert identity['binary_sha256']==receipt['provider_binary']['sha256']
    slots=identity['slots'];assert len(slots)==16
    assert all(r['negotiated_flags']&4 and r['content_validity']=='before_overwrite' for r in slots)
    independent=[independent_provider(directory,point) for point in samples['restored']]
    costs=[]
    for point in samples['restored']:
        raw=json.loads((directory/f"pair-{point['point']:04}-provider.json").read_text())
        costs.append({k:raw['first'][k]+raw['last'][k] for k in ('cpu_ns','elapsed_ns')})
    released=[struct.unpack('<8Q',p.read_bytes()[:64]) for p in (storage/'service-provider-slots').glob('*.bin')]
    assert len(released)==16 and all(r[6]==5 and r[5]==0 and r[1]%2==0 for r in released)
    phase_points={phase['name']:[p['point'] for p in samples['restored'] if phase['start_ns']<=p['timestamp_ns']<=phase['end_ns']]
        for phase in task['phases']}
    assert all(phase_points.values())
    logs=next(p for p in (storage/'service-logs/serial').glob('*/firecracker.log') if 'Paused' in p.read_text()).read_text().splitlines()
    def ns(line):
        sec,frac=line.split()[0].split('.')
        return int(datetime.fromisoformat(sec).replace(tzinfo=timezone.utc).timestamp())*10**9+int(frac.ljust(9,'0'))
    pause_request=next(ns(x) for x in logs if 'received a Patch request' in x and 'Paused' in x)
    pause_done=next(ns(x) for x in logs if "'pause vm' VMM action" in x)
    resume_request=next(ns(x) for x in logs if 'received a Patch request' in x and 'Resumed' in x and ns(x)>pause_request)
    resume_done=next(ns(x) for x in logs if "'resume vm' VMM action" in x and ns(x)>resume_request)
    events=json.loads((source/'events.json').read_text())
    counters={k:max(p[side][k] for group in samples.values() for p in group for side in ('metadata_before','metadata_after'))
        for k in ('duplicate_adds','missing_removes','out_of_range','moved_pages','stale_records')}
    sessions={name:json.loads((source/'live-cache'/name/'result.json').read_text()) for name in samples}
    return dict(status='PASS',storage_run=storage.name,sample_counts={k:len(v) for k,v in samples.items()},
        coverage='POSITIVE' if any(p['host_provider_current_bytes'] for p in independent) else 'NO_ATTRIBUTED_CURRENT_BUFFER',
        negotiated_flags=sorted({s['negotiated_flags'] for s in slots}),complete_reads_after_release=sum(r[7] for r in released),
        first_guest_cached_bytes=samples['restored'][0]['guest_cached_bytes'],points=independent,phase_points=phase_points,
        fixture_checks=task['checks'],metadata_counters=counters,
        native_checkpoint_pause_bounds_ns=[resume_request-pause_done,resume_done-pause_request],
        checkpoint_interface_ns=next(e['duration_ns'] for e in events if e['stage']=='checkpoint'),
        observation_gap_ns=samples['restored'][0]['timestamp_ns']-samples['parent'][-1]['timestamp_ns'],
        costs=dict(provider_query_cpu_mean_ns=statistics.mean(c['cpu_ns'] for c in costs),
            provider_query_elapsed_mean_ns=statistics.mean(c['elapsed_ns'] for c in costs),
            restored_pair_cpu_mean_ns=statistics.mean(p['host_thread_cpu_ns'] for p in samples['restored']),
            restored_pair_elapsed_mean_ns=statistics.mean(p['host_elapsed_ns'] for p in samples['restored']),
            session_thread_cpu_ns={name:state['host_thread_total_cpu_ns'] for name,state in sessions.items()}),
        storage_budget=receipt['budget'],host_available_min_bytes=receipt['observed_host_available_min_bytes'],
        cleanup=receipt['cleanup'],all_slots_released_invalid=True,
        monitoring_impact='NOT_CONTROLLED; one short restore, dev daemon')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',type=Path);p.add_argument('--storage',type=Path)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    assert args.device or args.storage
    value=dict(status='PASS')
    if args.device:value['device']=device_audit(args.device.resolve())
    if args.storage:value['restore']=restore_audit(args.storage.resolve())
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS result_dir='+str(args.output.parent))
