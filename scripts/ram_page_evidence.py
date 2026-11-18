"""Persist already-read RAM pagemap triples; no live queries or payload access."""
import argparse
import json
from pathlib import Path

import numpy as np

from kernel_cache_metadata import PRESENT,SWAPPED,MASK,PHYSICAL_MASK
from fast_cache_metadata import decode_array,join_arrays

PAGE=4096
VERSION='ram-pagemap-triple-v1'


def manifest(sandbox_id,guest_boot_id,vm,slots):
    if vm['sandbox_id']!=sandbox_id or not guest_boot_id:raise ValueError('RAM evidence sandbox/boot identity missing')
    rows=[];offset=0;guest_end=0;host_ranges=[]
    for s in sorted(slots,key=lambda s:s['guest']):
        guest,host,size=(int(s[k]) for k in ('guest','host','size'))
        if size<=0 or any(n%PAGE for n in (guest,host,size)) or guest<guest_end:
            raise ValueError('unaligned or overlapping attested RAM slots')
        if any(host<end and start<host+size for start,end in host_ranges):raise ValueError('overlapping host RAM slots')
        rows.append(dict(guest=guest,host=host,size=size,row_offset=offset,pages=size//PAGE))
        guest_end=guest+size;host_ranges.append((host,host+size));offset+=size//PAGE
    if not rows:raise ValueError('empty RAM attestation')
    return dict(version=VERSION,page_size_bytes=PAGE,sandbox_id=sandbox_id,guest_boot_id=guest_boot_id,
        host_identity=vm,slots=rows,total_rows=offset,bytes_per_sample=offset*3*8,
        columns=['first','middle','last'],dtype='<u8',
        scope='attested guest RAM only; excludes VMM mappings outside RAM and host backing objects',
        cache_page_identity=['sandbox_id','guest_boot_id','guest_pfn','lifetime_tag'],
        raw_physical_page_numbers='LOCAL_ONLY; do not publish')


def pack(meta,first,middle,last):
    """No reread: the caller supplies the three existing _bulk results."""
    snapshots=[{int(slot['guest']):(slot,data) for slot,data in values} for values in (first,middle,last)]
    keys={s['guest'] for s in meta['slots']}
    for values,original in zip(snapshots,(first,middle,last)):
        if set(values)!=keys or len(values)!=len(original):raise ValueError('RAM slot set changed between reads')
    result=np.empty((meta['total_rows'],3),dtype='<u8')
    for s in meta['slots']:
        begin=s['row_offset'];end=begin+s['pages']
        for column,values in enumerate(snapshots):
            actual,data=values[s['guest']]
            if any(actual[k]!=s[k] for k in ('guest','host','size')) or data.shape!=(s['pages'],):
                raise ValueError('RAM slot identity or pagemap length changed')
            result[begin:end,column]=data
    return result


def physical_sets(rows):
    if rows.ndim!=2 or rows.shape[1]!=3:raise ValueError('RAM evidence must have three columns')
    stable=(((rows[:,0]^rows[:,1])|(rows[:,1]^rows[:,2]))&PHYSICAL_MASK)==0
    present=(rows[:,0]&PRESENT)!=0
    accepted=stable&present
    pfns=rows[accepted,0]&MASK
    if np.any(pfns==0):raise ValueError('host physical PFNs hidden in RAM evidence')
    physical=np.unique(pfns)
    return dict(stable_host_physical_union_bytes=int(physical.size)*PAGE,
        stable_resident_guest_pages=int(np.count_nonzero(accepted)),
        changed_guest_pages=int(np.count_nonzero(~stable)),
        stable_nonresident_guest_pages=int(np.count_nonzero(stable&((rows[:,0]&(PRESENT|SWAPPED))==0))),
        stable_swapped_guest_pages=int(np.count_nonzero(stable&~present&((rows[:,0]&SWAPPED)!=0))),
        attested_guest_page_capacity_bytes=len(rows)*PAGE,
        interpretation='stable host-resident RAM PFN union across three reads; not total VMM PSS or complete sandbox cost',
        snapshot_atomic=False),physical


def save(prefix,meta,first,middle,last,*,remaining_bytes):
    size=meta['bytes_per_sample']
    # Same strict 1 GiB collector budget; never write a partial oversized sample.
    if size>=remaining_bytes:raise ValueError('full RAM evidence would exceed existing 1 GiB raw-data budget')
    rows=pack(meta,first,middle,last);summary,_=physical_sets(rows)
    path=Path(str(prefix)+'-ram.bin')
    with path.open('xb') as f:rows.tofile(f)
    return dict(version=VERSION,path=path.name,raw_bytes=size,**summary)


def project(meta,rows,guest_pfns):
    """Select RAM triples at guest PFNs, using actual slots rather than 0..RAM size."""
    if rows.shape!=(meta['total_rows'],3):raise ValueError('RAM evidence length differs from manifest')
    output=np.empty((len(guest_pfns),4),dtype='<u8');output[:,0]=guest_pfns;covered=np.zeros(len(guest_pfns),dtype=bool)
    for s in meta['slots']:
        selected=(guest_pfns>=s['guest']//PAGE)&(guest_pfns<(s['guest']+s['size'])//PAGE)
        positions=(guest_pfns[selected]-s['guest']//PAGE).astype(np.int64)+s['row_offset']
        output[selected,1:]=rows[positions];covered|=selected
    if not np.all(covered):raise ValueError('guest cache PFN outside attested RAM')
    return output


def analyze(live,*,collector=None):
    read=lambda path:json.loads(path.read_text())
    marker=read(live/'marker.json');collector=collector if collector is not None else read(live/'result.json')
    assert marker['sandbox_id']==collector['sandbox_id'] and marker['boot_id']==collector['guest_boot_id']
    scope=dict(sandbox_id=marker['sandbox_id'],guest_boot_id=marker['boot_id'],host_identity=collector['host_identity'])
    if not (live/'ram-page-manifest.json').exists():
        return dict(version=VERSION,status='UNAVAILABLE',**scope,points=[],
            reason='full RAM vectors were not persisted; cache-selected rows cannot fill unobserved RAM',
            stable_ram_physical_union_bytes=None)
    meta=read(live/'ram-page-manifest.json')
    if (meta['version']!=VERSION or meta['sandbox_id']!=scope['sandbox_id'] or
            meta['guest_boot_id']!=scope['guest_boot_id'] or meta['host_identity']!=scope['host_identity']):
        raise ValueError('RAM manifest belongs to another sandbox/boot/VMM')
    expected=manifest(meta['sandbox_id'],meta['guest_boot_id'],meta['host_identity'],meta['slots'])
    if meta!=expected:raise ValueError('RAM manifest dimensions or slots are inconsistent')
    points=[]
    for sample in map(json.loads,(live/'samples.jsonl').read_text().splitlines()):
        prefix=live/f"pair-{sample['point']:04}";path=Path(str(prefix)+'-ram.bin')
        rows=np.fromfile(path,dtype='<u8')
        if rows.size!=meta['total_rows']*3:raise ValueError('short or excessive RAM evidence')
        rows=rows.reshape(-1,3);summary,ram_set=physical_sets(rows)
        before=decode_array(Path(str(prefix)+'-before.bin').read_bytes())
        after=decode_array(Path(str(prefix)+'-after.bin').read_bytes())
        selected=np.fromfile(Path(str(prefix)+'-host.bin'),dtype='<u8').reshape(-1,4)
        projected=project(meta,rows,selected[:,0])
        if not np.array_equal(projected,selected):raise ValueError('RAM evidence differs from original cache pagemap triples')
        cache=join_arrays(before,after,selected)
        if cache['host_confirmed_cache_bytes']!=sample['host_confirmed_cache_bytes']:
            raise ValueError('original cache physical union differs')
        # The stable RAM set can include cache pages whose guest lifetime changed.
        # Do not call the set difference "non-cache memory".
        surviving=np.isin(selected[:,0],after[1][:,0])
        from fast_cache_metadata import matching_tags
        before_tags=matching_tags(before[1],selected[:,0]);after_tags=matching_tags(after[1],selected[:,0])
        stable=(((selected[:,1]^selected[:,2])|(selected[:,2]^selected[:,3]))&PHYSICAL_MASK)==0
        confirmed=surviving&(before_tags!=0)&(before_tags==after_tags)&stable&((selected[:,1]&PRESENT)!=0)
        cache_set=np.unique(selected[confirmed,1]&MASK)
        if not np.all(np.isin(cache_set,ram_set)):raise ValueError('confirmed cache outside stable RAM physical set')
        saved=sample['ram_page_evidence']
        if any(saved.get(k)!=v for k,v in summary.items()) or saved['raw_bytes']!=path.stat().st_size or saved['path']!=path.name:
            raise ValueError('RAM sample receipt differs from raw evidence')
        points.append(dict(point=sample['point'],timestamp_ns=sample['timestamp_ns'],**summary,
            confirmed_guest_cache_host_union_bytes=int(cache_set.size)*PAGE,
            ram_not_confirmed_as_cache_host_bytes=int(np.setdiff1d(ram_set,cache_set).size)*PAGE,
            guest_cache_unconfirmed_pages=cache['unconfirmed_after_pages'],
            cache_tags_before=str(prefix)+'-before.bin',cache_tags_after=str(prefix)+'-after.bin',ram_evidence=str(path)))
    if len(points)!=collector['samples'] or len(points)<2:raise ValueError('RAM evidence sample coverage incomplete')
    return dict(version=VERSION,status='OBSERVED',**scope,points=points,
        cache_page_key=meta['cache_page_identity'],
        file_identity_coverage='declared-file sidecars only; whole-guest tags do not yet contain inode/page-offset',
        first_touch_owner='NOT_INFERRED: memcg charge and shared references are separate',
        validation='offline evidence consistency; live overhead and behavior require one opt-in validation')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live-directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();value=analyze(a.live_directory)
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print('status='+value['status']+' result='+str(a.output))


if __name__=='__main__':main()
