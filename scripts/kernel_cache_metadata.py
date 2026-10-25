"""Decode the diagnostic guest's complete file/shmem cache membership and lifetimes."""
import struct

HEADER=struct.Struct('<8s12Q')
RECORD=struct.Struct('<QQ')
FIELDS=('version','page_size','max_pfn','records','generation','shmem_pages',
        'duplicate_adds','missing_removes','out_of_range','moved_pages','stale_records','bitmap_lock_ns')
PRESENT=1<<63
SWAPPED=1<<62
MASK=(1<<55)-1
PHYSICAL_MASK=MASK|PRESENT|SWAPPED|(1<<61)


def decode_header(raw):
    if len(raw)<HEADER.size:
        raise ValueError('short kernel metadata header')
    magic,*values=HEADER.unpack_from(raw)
    info=dict(zip(FIELDS,values))
    if magic!=b'ASBCACH1' or info['version']!=1 or info['page_size']!=4096 or info['max_pfn']!=1<<21:
        raise ValueError('unsupported kernel metadata format')
    if info['records']>info['max_pfn'] or len(raw)!=HEADER.size+info['records']*RECORD.size:
        raise ValueError('kernel metadata size does not match record count')
    if any(info[k] for k in ('duplicate_adds','missing_removes','out_of_range')):
        raise ValueError('kernel cache membership accounting reports missed/duplicate hooks or unsupported PFNs')
    return info


def decode(raw):
    info=decode_header(raw)
    rows=dict(RECORD.iter_unpack(raw[HEADER.size:]))
    if len(rows)!=info['records'] or any(p>=info['max_pfn'] or tag>>1>info['generation'] for p,tag in rows.items()):
        raise ValueError('duplicate/out-of-range PFN or future lifetime')
    if sum(tag==0 for tag in rows.values())!=info['stale_records']:
        raise ValueError('stale record count differs')
    return info,rows


def join(before,after,host_rows):
    left,pages=decode(before);right,last=decode(after)
    wanted={p for p,t in pages.items() if t}
    if len(host_rows)!=len(wanted) or {r['guest_pfn'] for r in host_rows}!=wanted:
        raise ValueError('host rows do not match known initial metadata PFNs')
    known=set();changed=retired=nonresident=swapped=stable=0
    for row in host_rows:
        pfn=row['guest_pfn']
        if last.get(pfn)!=pages[pfn]:
            retired+=1
            continue
        first,middle,end=row['first'],row['middle'],row['last']
        if ((first^middle)|(middle^end))&PHYSICAL_MASK:
            changed+=1
        elif first&PRESENT:
            if not first&MASK:raise ValueError('host PFN hidden')
            known.add(first&MASK);stable+=1
        elif first&SWAPPED:swapped+=1
        else:nonresident+=1
    added=sum(not tag or pages.get(pfn)!=tag for pfn,tag in last.items())
    unresolved=right['records']-stable-nonresident-swapped
    return dict(metadata_before=left,metadata_after=right,
        guest_cache_before_bytes=left['records']*4096,guest_cache_after_bytes=right['records']*4096,
        host_confirmed_cache_bytes=len(known)*4096,stable_resident_guest_pages=stable,
        host_mapping_changed_pages=changed,retired_or_reused_pages=retired,new_or_unknown_after_pages=added,
        host_nonresident_pages=nonresident,host_swapped_pages=swapped,unconfirmed_after_pages=unresolved,
        interpretation='confirmed host-resident PFN union for unchanged guest cache lifetimes across this sampling interval')
