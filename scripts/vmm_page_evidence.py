"""Opt-in full VMM mapping metadata and non-RAM pagemap triples; no payload reads."""
import argparse
import json
import os
from pathlib import Path
import struct
import time

import numpy as np

from host_schedule_files import process_identity
from host_special_metadata import mapping_line, attach_smaps
from kernel_cache_metadata import PRESENT, SWAPPED, MASK, PHYSICAL_MASK
from ram_page_evidence import pack, physical_sets, manifest as ram_manifest

PAGE = 4096
VERSION = 'vmm-mapping-pagemap-v1'
IO_VERSION = 'vmm-mapping-pagemap-io-uring-v2'
IO_POLICY = 'io-uring-named-mixed-v1'
IO_PATH = 'anon_inode:[io_uring]'
IO_OFFSETS = {0, 0x8000000, 0x10000000}  # SQ/CQ ring and SQEs only.
SPECIAL_FLAGS = {'pf', 'io', 'mm', 'ht'}
REGION_KEYS = ('start', 'end', 'permissions', 'offset', 'device', 'inode', 'mapping_path')


def regions(text):
    values=[]
    for line in text.splitlines():
        r=mapping_line(line)
        if r is None or r['start']>=r['end'] or r['start']%PAGE or r['end']%PAGE:
            raise ValueError('invalid full VMM maps row')
        values.append(r)
    if not values or any(a['end']>b['start'] for a,b in zip(values,values[1:])):
        raise ValueError('empty, unordered or overlapping VMM maps')
    return values


def io_uring_region(r):
    flags=set(r.get('vm_flags',()))
    return (r.get('smaps_status')=='OBSERVED' and r['mapping_path']==IO_PATH and
            r['permissions']=='rw-s' and r['inode']>0 and r['device']>0 and os.major(r['device'])==0 and
            r['offset'] in IO_OFFSETS and {'rd','wr','sh','mm'}<=flags and
            not {'pf','io','ht'}.intersection(flags))


def supported(r,*,io_uring=False):
    if io_uring and r['mapping_path']==IO_PATH:return io_uring_region(r)
    return (r.get('smaps_status')=='OBSERVED' and not SPECIAL_FLAGS.intersection(r['vm_flags'])
            and r['mapping_path']!='[vsyscall]')


def layout(before, slots,*,io_uring=False):
    """Partition virtual ranges by actual KVM host addresses, including partial VMAs."""
    boundaries=sorted({n for s in slots for n in (s['host'],s['host']+s['size'])})
    result=[];offset=0;ram_bytes=0
    for index,r in enumerate(before):
        points=[r['start'],*(b for b in boundaries if r['start']<b<r['end']),r['end']]
        for a,b in zip(points,points[1:]):
            is_ram=any(s['host']<=a and b<=s['host']+s['size'] for s in slots)
            row=dict(region_index=index,start=a,end=b,pages=(b-a)//PAGE,
                     kind='RAM' if is_ram else 'NON_RAM',row_offset=None)
            if is_ram:ram_bytes+=b-a
            elif supported(r,io_uring=io_uring):row['row_offset']=offset;offset+=row['pages']
            result.append(row)
    if ram_bytes!=sum(s['size'] for s in slots):
        raise ValueError('full maps does not cover registered RAM')
    return result,offset


def metadata(maps,smaps):
    value=regions(maps)
    for r in value:r['smaps_status']='NOT_OBSERVED'
    attach_smaps(None,value,text=smaps)
    return value


def kernel_build_id(notes):
    """Decode the existing GNU build-ID note; do not calculate a hash."""
    ids=[];offset=0
    while offset<len(notes):
        if offset+12>len(notes):raise ValueError('truncated kernel note header')
        namesz,descsz,typ=struct.unpack_from('<III',notes,offset);offset+=12
        end=offset+((namesz+3)//4)*4+((descsz+3)//4)*4
        if end>len(notes):raise ValueError('truncated kernel note data')
        name=notes[offset:offset+namesz];offset+=((namesz+3)//4)*4
        desc=notes[offset:offset+descsz];offset=end
        if name==b'GNU\0' and typ==3:ids.append(desc.hex())
    if len(ids)!=1 or not ids[0]:raise ValueError('one kernel GNU build-ID required for io_uring evidence')
    return ids[0]


def kernel_identity(proc_root,sys_root):
    return dict(release=(proc_root/'sys/kernel/osrelease').read_text().strip(),
        version=(proc_root/'version').read_text().strip(),
        gnu_build_id=kernel_build_id((sys_root/'kernel/notes').read_bytes()),
        source_build_match='NOT_VERIFIED')


def io_policy(body):
    if body['version']==VERSION:
        if 'io_uring_policy' in body:raise ValueError('legacy VMM version conflicts with io_uring policy')
        return False
    if body['version']!=IO_VERSION or body.get('io_uring_policy')!=IO_POLICY:
        raise ValueError('unsupported VMM mapping version/policy')
    k=body.get('host_kernel_identity',{})
    if (not all(isinstance(k.get(n),str) and k[n] for n in ('release','version','gnu_build_id')) or
            k.get('source_build_match')!='NOT_VERIFIED'):
        raise ValueError('io_uring evidence kernel identity missing or invalid')
    try:
        if not bytes.fromhex(k['gnu_build_id']):raise ValueError()
    except ValueError as exc:raise ValueError('invalid kernel GNU build-ID') from exc
    return True


def analyze_arrays(body,rows,ram_rows):
    io_uring=io_policy(body)
    reads=body['reads']
    if ([r['column'] for r in reads]!=[0,1,2] or
            not all(body['start_monotonic_ns']<=r['start_monotonic_ns']<=r['end_monotonic_ns']<=body['end_monotonic_ns'] for r in reads) or
            any(a['end_monotonic_ns']>b['start_monotonic_ns'] for a,b in zip(reads,reads[1:]))):
        raise ValueError('VMM triple observation timestamps/columns invalid')
    meta=body['ram_manifest']
    if meta!=ram_manifest(meta['sandbox_id'],meta['guest_boot_id'],meta['host_identity'],meta['slots']):
        raise ValueError('invalid RAM manifest in VMM evidence')
    if not body['host_boot_id']:raise ValueError('host boot identity missing')
    before=metadata(body['maps_before'],body['smaps_before'])
    after=metadata(body['maps_after'],body['smaps_after'])
    segments,count=layout(before,meta['slots'],io_uring=io_uring)
    if rows.shape!=(count,3) or ram_rows.shape!=(meta['total_rows'],3):
        raise ValueError('VMM/RAM evidence length differs from mapping layout')
    last={tuple(r[k] for k in REGION_KEYS):r for r in after}
    stable_keys=set(last).intersection(tuple(r[k] for k in REGION_KEYS) for r in before)
    for s in segments:
        if s['kind']=='RAM' and tuple(before[s['region_index']][k] for k in REGION_KEYS) not in stable_keys:
            raise ValueError('RAM mapping identity changed during VMM observation')
    ram_summary,ram_set=physical_sets(ram_rows)
    all_pfns=[];unknown=0;details=[];io_pfns=[];other_pfns=[];io_unknown=io_listed=io_queried=0
    for s in segments:
        if s['kind']=='RAM':continue
        r=before[s['region_index']];key=tuple(r[k] for k in REGION_KEYS);tail=last.get(key)
        row=dict(s,virtual_bytes=s['pages']*PAGE,physical_union_bytes=None,
                 confirmed_resident_virtual_pages=0,unknown_virtual_pages=s['pages'])
        if tail is None:row['status']='MAPPING_CHANGED'
        elif not supported(r,io_uring=io_uring) or not supported(tail,io_uring=io_uring):row['status']='SPECIAL_OR_SMAPS_UNKNOWN'
        else:
            x=rows[s['row_offset']:s['row_offset']+s['pages']]
            stable=(((x[:,0]^x[:,1])|(x[:,1]^x[:,2]))&PHYSICAL_MASK)==0
            present=(x[:,0]&PRESENT)!=0;hidden=stable&present&((x[:,0]&MASK)==0)
            good=stable&present&~hidden;pfns=np.unique(x[good,0]&MASK)
            missing=int(np.count_nonzero(~stable|hidden));all_pfns.append(pfns)
            if io_uring:
                (io_pfns if r['mapping_path']==IO_PATH else other_pfns).append(pfns)
            row.update(status='PARTIAL' if missing else 'OBSERVED_NONATOMIC',unknown_virtual_pages=missing,
                changed_pagemap_virtual_pages=int(np.count_nonzero(~stable)),
                hidden_pfn_virtual_pages=int(np.count_nonzero(hidden)),
                stable_swapped_virtual_pages=int(np.count_nonzero(stable&~present&((x[:,0]&SWAPPED)!=0))),
                stable_nonresident_virtual_pages=int(np.count_nonzero(stable&((x[:,0]&(PRESENT|SWAPPED))==0))),
                confirmed_resident_virtual_pages=int(np.count_nonzero(good)),physical_union_bytes=int(pfns.size)*PAGE)
        if io_uring and r['mapping_path']==IO_PATH:
            io_listed+=1;io_queried+=s['row_offset'] is not None;io_unknown+=row['unknown_virtual_pages']
        unknown+=row['unknown_virtual_pages'];details.append(row)
    nonram=np.unique(np.concatenate(all_pfns)) if all_pfns else np.array([],dtype='<u8')
    added=[r for r in after if tuple(r[k] for k in REGION_KEYS) not in stable_keys]
    # New end-of-window ranges have no first/middle pagemap observations.
    added_bytes=sum(r['end']-r['start'] for r in added)
    result=dict(coverage='PARTIAL' if unknown or added or ram_summary['changed_guest_pages'] else 'OBSERVED_LISTED_MAPPINGS',
        complete_vmm_host_memory_bytes=None,complete_sandbox_host_memory_bytes=None,
        snapshot_atomic=False,ram_stable_host_union_bytes=int(ram_set.size)*PAGE,
        ordinary_nonram_stable_host_union_bytes=int(nonram.size)*PAGE,
        ram_nonram_physical_overlap_bytes=int(np.intersect1d(ram_set,nonram).size)*PAGE,
        ordinary_vmm_stable_host_union_bytes=int(np.union1d(ram_set,nonram).size)*PAGE,
        unknown_nonram_virtual_pages=unknown,added_or_changed_end_mapping_virtual_bytes=added_bytes,
        ram_changed_virtual_pages=ram_summary['changed_guest_pages'],
        nonram_segments=details,added_or_changed_end_mappings=added,
        scope='Non-atomic observed ordinary mapped PFN union; excludes unsupported mappings, unmapped kernel allocations and host backing cache. Same PFN between reads does not exclude unobserved reuse.')
    if io_uring:
        result['scope']='Non-atomic observed ordinary and restricted io_uring mapped PFN union; excludes other unsupported mappings, unmapped kernel allocations and host backing cache. Same PFN between reads does not exclude unobserved reuse.'
        io=np.unique(np.concatenate(io_pfns)) if io_pfns else np.array([],dtype='<u8')
        other=np.unique(np.concatenate(other_pfns)) if other_pfns else np.array([],dtype='<u8')
        result['io_uring']=dict(listed_segments=io_listed,queried_segments=io_queried,
            stable_host_union_bytes=int(io.size)*PAGE,ram_overlap_bytes=int(np.intersect1d(io,ram_set).size)*PAGE,
            other_nonram_overlap_bytes=int(np.intersect1d(io,other).size)*PAGE,unknown_virtual_pages=io_unknown,
            scope='Mapped kernel buffers, not guest/host file page cache; subset of non-RAM union, never add again.')
    return result


class VmmPageEvidence:
    def __init__(self,meta,host_boot_id,*,proc_root=Path('/proc'),io_uring=False,sys_root=Path('/sys')):
        self.meta=meta;self.host_boot_id=host_boot_id;self.proc_root=proc_root
        self.proc=proc_root/str(meta['host_identity']['pid'])
        self.io_uring=io_uring;self.version=IO_VERSION if io_uring else VERSION
        self.host_kernel_identity=kernel_identity(proc_root,sys_root) if io_uring else None

    def check_identity(self):
        vm=self.meta['host_identity']
        if (process_identity(self.proc)!=dict(start_ticks=str(vm['start_time_ticks']),cgroup='0::'+vm['cgroup']+'\n') or
                os.readlink(self.proc/'exe')!=vm['executable'] or
                (self.proc_root/'sys/kernel/random/boot_id').read_text().strip()!=self.host_boot_id):
            raise ValueError('VMM or host boot identity changed')

    def begin(self,*,remaining_bytes):
        self.check_identity();self.body=dict(version=self.version,ram_manifest=self.meta,host_boot_id=self.host_boot_id,
            start_monotonic_ns=time.monotonic_ns(),maps_before=(self.proc/'maps').read_text(),
            smaps_before=(self.proc/'smaps').read_text(),reads=[])
        if self.io_uring:
            self.body.update(io_uring_policy=IO_POLICY,host_kernel_identity=self.host_kernel_identity)
            io_policy(self.body)
        self.before=metadata(self.body['maps_before'],self.body['smaps_before'])
        self.segments,count=layout(self.before,self.meta['slots'],io_uring=self.io_uring)
        # Bound the allocation before reading any non-RAM pagemap or writing files.
        if count*24+len(json.dumps(self.body).encode())>=remaining_bytes:
            raise ValueError('VMM evidence would exceed existing 1 GiB raw-data budget')
        self.rows=np.empty((count,3),dtype='<u8');self.next_column=0

    def read(self,column,pm):
        if column!=self.next_column:raise ValueError('VMM pagemap columns must be first/middle/last')
        start=time.monotonic_ns()
        for s in self.segments:
            if s['row_offset'] is None:continue
            # Bound temporary syscall buffers to 1 MiB; never access process data.
            for offset in range(0,s['pages'],131072):
                n=min(131072,s['pages']-offset)
                raw=os.pread(pm.fileno(),n*8,(s['start']//PAGE+offset)*8)
                if len(raw)!=n*8:raise ValueError('short non-RAM pagemap read')
                a=s['row_offset']+offset;self.rows[a:a+n,column]=np.frombuffer(raw,dtype='<u8')
        self.body['reads'].append(dict(column=column,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns()))
        self.next_column+=1

    def save(self,prefix,first,middle,last,*,remaining_bytes):
        if self.next_column!=3:raise ValueError('VMM pagemap triple incomplete')
        self.body.update(maps_after=(self.proc/'maps').read_text(),smaps_after=(self.proc/'smaps').read_text(),
                         end_monotonic_ns=time.monotonic_ns())
        error=None
        try:
            self.check_identity()
            ram=pack(self.meta,first,middle,last)
            summary=analyze_arrays(self.body,self.rows,ram)
        except (OSError,ValueError,KeyError) as exc:
            error=exc;summary=dict(status='FAIL',error=repr(exc))
        binary=Path(str(prefix)+'-vmm-nonram.bin');path=Path(str(prefix)+'-vmm.json')
        body=dict(self.body,nonram_path=binary.name,ram_path=prefix.name+'-ram.bin',summary=summary)
        text=json.dumps(body,indent=2)+'\n';size=self.rows.nbytes+len(text.encode())
        if size>=remaining_bytes:raise ValueError('VMM evidence would exceed existing 1 GiB raw-data budget')
        if path.exists() or binary.exists():raise FileExistsError('VMM evidence already exists')
        with binary.open('xb') as f:self.rows.tofile(f)
        with path.open('x') as f:f.write(text)
        if error is not None:raise ValueError('VMM analysis failed; evidence retained at '+str(path)) from error
        return dict(version=self.version,path=path.name,nonram_path=binary.name,raw_bytes=size,coverage=summary['coverage'],
                    ordinary_vmm_stable_host_union_bytes=summary['ordinary_vmm_stable_host_union_bytes'])


def analyze(live,*,collector=None):
    read=lambda p:json.loads(p.read_text())
    collector=collector if collector is not None else read(live/'result.json')
    meta=read(live/'ram-page-manifest.json') if (live/'ram-page-manifest.json').exists() else None
    samples=list(map(json.loads,(live/'samples.jsonl').read_text().splitlines()))
    if not any('vmm_page_evidence' in s for s in samples):
        return dict(version=VERSION,status='UNAVAILABLE',reason='full VMM mapping evidence not saved',points=[],
                    complete_vmm_host_memory_bytes=None)
    if [s.get('point') for s in samples]!=list(range(len(samples))):
        raise ValueError('VMM evidence sample indices incomplete or duplicated')
    if meta is None or meta['host_identity']!=collector['host_identity'] or meta['sandbox_id']!=collector['sandbox_id'] or meta['guest_boot_id']!=collector['guest_boot_id']:
        raise ValueError('VMM evidence collector identity conflict')
    binding=read(live/'vmm-child-binding.json');boot=binding['vmm_binding']['boot_id'];points=[];versions=set();kernels=[]
    for s in samples:
        prefix=f"pair-{s['point']:04}";receipt=s['vmm_page_evidence'];path=live/(prefix+'-vmm.json');body=read(path)
        enabled=io_policy(body);versions.add(body['version'])
        if enabled:kernels.append(body['host_kernel_identity'])
        if (receipt['version']!=body['version'] or body['ram_manifest']!=meta or body['host_boot_id']!=boot or
                body['nonram_path']!=prefix+'-vmm-nonram.bin' or body['ram_path']!=prefix+'-ram.bin' or
                receipt['path']!=path.name or receipt['nonram_path']!=body['nonram_path']):
            raise ValueError('VMM evidence manifest/paths/boot conflict')
        if ((live/body['nonram_path']).stat().st_size%24 or
                (live/body['ram_path']).stat().st_size!=meta['bytes_per_sample']):
            raise ValueError('VMM/RAM binary length differs from triple format')
        data=np.fromfile(live/body['nonram_path'],dtype='<u8')
        ram=np.fromfile(live/body['ram_path'],dtype='<u8')
        if data.size%3 or ram.size!=meta['total_rows']*3:raise ValueError('VMM/RAM triple truncated')
        summary=analyze_arrays(body,data.reshape(-1,3),ram.reshape(-1,3))
        size=path.stat().st_size+(live/body['nonram_path']).stat().st_size
        if (summary!=body['summary'] or receipt['raw_bytes']!=size or receipt['coverage']!=summary['coverage'] or
                receipt['ordinary_vmm_stable_host_union_bytes']!=summary['ordinary_vmm_stable_host_union_bytes'] or
                summary['ram_stable_host_union_bytes']!=s['ram_page_evidence']['stable_host_physical_union_bytes']):
            raise ValueError('VMM physical analysis differs from saved evidence')
        points.append(dict(point=s['point'],**summary))
    if len(points)!=collector['samples']:raise ValueError('incomplete VMM evidence samples')
    if len(versions)!=1 or any(k!=kernels[0] for k in kernels):raise ValueError('mixed VMM policies/kernel identities in one session')
    result=dict(version=versions.pop(),status='OBSERVED_EVIDENCE',points=points,
                coverage='PARTIAL' if any(p['coverage']=='PARTIAL' for p in points) else 'OBSERVED_LISTED_MAPPINGS',
                complete_vmm_host_memory_bytes=None,complete_sandbox_host_memory_bytes=None)
    if kernels:result.update(io_uring_policy=IO_POLICY,host_kernel_identity=kernels[0])
    return result


def main():
    p=argparse.ArgumentParser(description='Replay saved VMM mapping evidence only; no live queries')
    p.add_argument('--live-directory',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();live=args.live_directory.resolve();output=args.output.resolve()
    if output.exists() or output.is_relative_to(live) or live.is_relative_to(output):
        raise ValueError('output must be a new file outside the source directory')
    value=analyze(live)
    with output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print('status='+value['status']+' result='+str(output))


if __name__=='__main__':main()
