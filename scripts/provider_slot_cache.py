"""Read opt-in slot metadata and pagemap; never read provider payload memory."""
import hashlib
import json
import os
from pathlib import Path
import struct
import time

from kernel_cache_metadata import decode, PRESENT, SWAPPED, PHYSICAL_MASK, MASK

PAGE=4096
MAGIC=0x31544f4c53425341
ROW=struct.Struct('<8Q')
FILE=1<<61
UNIT=512
SEGMENT=struct.Struct('<2Q')
UNKNOWN=(1<<64)-1


def content_method(identity):
    method=identity.get('content_validity','before_fetch')
    assert method in ('before_fetch','before_overwrite')
    if method=='before_overwrite':
        assert identity['negotiated_flags']&4, 'GET_DATA was not negotiated'
        return 'invalid before user fill or kernel GET_DATA; valid during fetch'
    return 'invalid before kernel fetch'


class ProviderCache:
    def __init__(self,out,vm_proc,directory,*,binding=None,vm=None,device_path=None):
        self.directory=directory;directory.mkdir()
        base=out.parent.parent/'env'
        ownership=json.loads((directory.parent/'lower-metadata/ownership.json').read_text())
        config=Path(ownership['config'])
        image='/workspace/env/'+str(config.relative_to(base))
        self.binding=binding
        self.owner=None
        root=Path('/proc') if binding is None else binding.proc_root
        if binding is not None:
            from snapshot_lower_owner import service_openers,select_config
            assert vm is not None and device_path is not None
            selected=select_config((base/'logs/server.log').read_text(),vm['sandbox_id'],device_path,
                list((base/'snapshot-local-cache/runtime').glob('*/memory/image.json')),base)
            if selected!=config:raise ValueError('provider config differs from restored device')
            layer=base/ownership['layer']['file'].removeprefix('/workspace/env/')
            owners=service_openers(binding,vm,layer)
            saved={(r['pid'],r['start_ticks'],r['boot_id'],r['device'],r['inode']) for r in ownership['open_descriptors']}
            if any((r['pid'],r['start_ticks'],r['boot_id'],r['device'],r['inode']) not in saved for r in owners):
                raise ValueError('provider owner changed since lower query')
            self.owner=owners[0]
        candidates=[]
        for pid in {r['pid'] for r in ownership['open_descriptors']}:
            proc=root/str(pid)
            if (proc/'exe').resolve().name=='uvm-ublk-daemon':candidates.append(proc)
        assert len(candidates)==1, 'one owned provider required'
        self.proc=candidates[0]
        if binding is None:assert (self.proc/'cgroup').read_text()==(vm_proc/'cgroup').read_text()
        else:self._check_owner()
        self.start=(self.proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        nspid=[line.split()[1:] for line in (self.proc/'status').read_text().splitlines() if line.startswith('NSpid:')][0][-1]
        identities=[];self.slots=[]
        for path in sorted((base/'provider-slots').glob('*.json')):
            value=json.loads(path.read_text())
            if binding is not None and value['image_config']!=image:continue
            assert value['schema']==1 and value['image_config']==image
            if binding is not None:
                assert value['dev_id']==int(device_path.removeprefix('/dev/ublkb')), 'provider slot device differs'
                assert value['content_validity']=='before_overwrite' and value['negotiated_flags']&4, 'explicit provider requires GET_DATA'

            assert value['pid']==int(nspid)
            assert value['process_stat'].rsplit(')',1)[1].split()[19]==self.start
            data=path.with_suffix('.bin');st=data.stat()
            # The provider must actually map this exact metadata file.
            assert any(int(line.split()[4])==st.st_ino and line.split()[-1].endswith('/'+data.name)
                       and (binding is None or tuple(int(v,16) for v in line.split()[3].split(':'))==(os.major(st.st_dev),os.minor(st.st_dev)))
                       for line in (self.proc/'maps').read_text().splitlines())
            segments=value.get('segments')
            if segments is not None:
                assert value['negotiated_flags']&4 and value['content_validity']=='before_overwrite'
                assert segments==dict(schema=1,unit_bytes=UNIT,entry_offset=ROW.size,
                    entry_bytes=SEGMENT.size,count=value['max_io_buf_bytes']//UNIT)
            self.slots.append((data,None,segments))
            identities.append(dict(name=data.name,device=st.st_dev,inode=st.st_ino,**value))
        assert identities and len({r['dev_id'] for r in identities})==1
        assert len(identities)==identities[0]['nr_queues']*identities[0]['queue_depth']
        assert len({(r['queue'],r['slot']) for r in identities})==len(identities)
        if binding is not None:
            queues=identities[0]['nr_queues'];depth=identities[0]['queue_depth']
            assert all(r['nr_queues']==queues and r['queue_depth']==depth for r in identities)
            assert {(r['queue'],r['slot']) for r in identities}=={(q,t) for q in range(queues) for t in range(depth)}, 'missing or invalid provider slots'
            self._check_owner()
        methods={content_method(r) for r in identities};assert len(methods)==1
        self.segmented=any('segments' in r for r in identities)
        assert not self.segmented or all('segments' in r for r in identities)
        self.pm=(self.proc/'pagemap').open('rb',buffering=0)
        opened=[]
        try:
            for (path,_,segments),saved in zip(self.slots,identities,strict=True):
                file=path.open('rb',buffering=0);opened.append((path,file,segments))
                if binding is not None:
                    st=os.fstat(file.fileno())
                    if (st.st_dev,st.st_ino)!=(saved['device'],saved['inode']):raise ValueError('provider metadata file replaced')
            self._check_owner()
        except BaseException:
            for _,file,_ in opened:file.close()
            self.pm.close()
            raise
        self.slots=opened
        self.identity=dict(host_pid=int(self.proc.name),start_ticks=self.start,image_config=image,
            cgroup=(self.proc/'cgroup').read_text(),slots=identities,
            **(dict(binary_sha256=hashlib.file_digest((self.proc/'exe').open('rb'),'sha256').hexdigest()) if binding is None else dict(ownership_mode='explicit_private_service_sibling',owner=self.owner,binary_path=str((self.proc/'exe').resolve()))),
            method='metadata and pagemap only; no payload reads; '+methods.pop())
        (directory/'identity.json').write_text(json.dumps(self.identity,indent=2)+'\n')

    def _check_owner(self):
        if self.owner is None:return
        from host_sandbox_monitor import identity
        r=self.owner;proc=self.binding.proc_root/str(r['pid'])
        now=identity(r['pid'],self.binding.proc_root)
        if (now['start_ticks'],now['boot_id'])!=(r['start_ticks'],r['boot_id']):
            raise ValueError('provider process identity changed')
        if (proc/'cgroup').read_text().strip()!=r['cgroup'] or r['pid'] not in map(int,(self.binding.private_service_cgroup/'cgroup.procs').read_text().split()):
            raise ValueError('provider left owned service')
        st=(proc/'fd'/r['fd']).stat()
        if (st.st_dev,st.st_ino)!=(r['device'],r['inode']):raise ValueError('provider layer FD changed')

    def snapshot(self):
        started=time.monotonic_ns();cpu=time.thread_time_ns()
        costs={}
        wall=started;thread=cpu
        def mark(name):
            nonlocal wall,thread
            end=time.monotonic_ns();end_cpu=time.thread_time_ns()
            row=costs.setdefault(name,dict(calls=0,elapsed_ns=0,cpu_ns=0))
            row['calls']+=1;row['elapsed_ns']+=end-wall;row['cpu_ns']+=end_cpu-thread
            wall=end;thread=end_cpu
        self._check_owner()
        assert (self.proc/'stat').read_text().rsplit(')',1)[1].split()[19]==self.start
        mark('identity_before')
        rows=[];metadata_bytes=0
        for path,file,segments in self.slots:
            size=ROW.size+(segments['count']*SEGMENT.size if segments else 0)
            left=os.pread(file.fileno(),size,0);assert len(left)==size
            mark('metadata_before_read')
            before=ROW.unpack_from(left)
            assert before[0]==MAGIC and 0<before[3]<=2**20
            address,capacity=before[2:4]
            first=address//PAGE;count=(address+capacity+PAGE-1)//PAGE-first
            mark('header_decode')
            raw=os.pread(self.pm.fileno(),count*8,first*8)
            assert len(raw)==count*8
            mark('pagemap_read')
            right=os.pread(file.fileno(),size,0);assert len(right)==size
            mark('metadata_after_read')
            after=ROW.unpack_from(right);metadata_bytes+=len(left)+len(right)
            row=dict(name=path.name,before=before,after=after,
                first_address=first*PAGE,pagemap=list(struct.unpack('<'+str(count)+'Q',raw)))
            if segments:
                assert capacity==segments['count']*UNIT and address%UNIT==0
                row['segments']=dict(schema=1,unit_bytes=UNIT,
                    before=list(SEGMENT.iter_unpack(left[ROW.size:])),
                    after=list(SEGMENT.iter_unpack(right[ROW.size:])))
            rows.append(row)
            mark('record_build')
        self._check_owner()
        mark('identity_after')
        ended=wall;ended_cpu=thread
        return dict(rows=rows,metadata_bytes_read=metadata_bytes,
            host_start_ns=started,host_end_ns=ended,
            elapsed_ns=ended-started,cpu_ns=ended_cpu-cpu,
            stage_costs=costs,stage_timing_version=1)

    def close(self, *, final_snapshot=True):
        try:
            if not final_snapshot:return  # event windows have ended; release descriptors only
            final=self.snapshot()
            (self.directory/'final.json').write_text(json.dumps(final)+'\n')
        finally:
            for _,file,_ in self.slots:file.close()
            self.pm.close()


def segment_sources(first,last,capacity):
    frames=[first['segments'],last['segments']]
    assert all(f['schema']==1 and f['unit_bytes']==UNIT for f in frames)
    copies=[f[k] for f in frames for k in ('before','after')]
    assert all(len(c)==capacity//UNIT for c in copies)
    return [values[0][1] if values[0][0]%2==0 and values[0][1]!=UNKNOWN
        and all(tuple(v)==tuple(values[0]) for v in values) else None
        for values in zip(*copies,strict=True)]


def full_coverage(coverage):
    fully=set()
    for guest,ranges in coverage.items():
        end=0
        for start,stop in sorted(ranges):
            if start>end:break
            end=max(end,stop)
        if end==PAGE:fully.add(guest)
    return fully


def provider_context(before,after,host,direct,lower,*,decoded=None):
    """Prepare one immutable candidate lookup for both buffer admission paths.

    The optional array path is for the live collector's already validated,
    sorted, non-stale metadata and its complete corresponding numpy rows.
    Existing byte-input callers keep their original scalar validation contract.
    """
    if decoded is not None:
        import numpy as np
        from fast_cache_metadata import matching_tags
        left,right=decoded
        wanted=left[1][left[1][:,1]!=0]
        assert np.array_equal(host[:,0],wanted[:,0])
        assert np.array_equal(direct[:,0],wanted[:,0]) and np.array_equal(lower[:,0],wanted[:,0])
        valid=matching_tags(right[1],wanted[:,0])==wanted[:,1]
        valid &= (((host[:,1]^host[:,2])|(host[:,2]^host[:,3]))&PHYSICAL_MASK)==0
        present=(host[:,1]&PRESENT)!=0;file_pte=(host[:,1]&FILE)!=0
        current=((host[:,1]&(PRESENT|SWAPPED))==0)|(present&file_pte)
        dok=(direct[:,2]==direct[:,3])&((direct[:,2]&PRESENT)!=0)
        lok=(lower[:,2]==lower[:,3])&((lower[:,2]&PRESENT)!=0)
        valid &= ~(present&file_pte&dok&((host[:,1]&MASK)!=(direct[:,2]&MASK)))
        three=set(map(int,host[valid&present,1]&MASK))
        three.update(map(int,direct[valid&current&dok,2]&MASK))
        three.update(map(int,lower[valid&current&lok,2]&MASK))
        confirmed=set(map(int,host[valid&(present|(current&(dok|lok))),0]))
        selected=valid&(direct[:,1]!=UNKNOWN)
        eligible={}
        for guest,offset,is_current,is_present in zip(host[selected,0],direct[selected,1],
                current[selected],present[selected],strict=True):
            eligible.setdefault(int(offset),[]).append((int(guest),
                'current' if is_current else 'old' if is_present else 'unknown'))
        return right[0]['records'],eligible,confirmed,three
    _,left=decode(before);header,right=decode(after)
    vm=set();device=set();layer=set();confirmed=set();eligible={}
    for (p,a,b,c),(q,offset,x,y),(r,_,u,v) in zip(host,direct,lower,strict=True):
        p,a,b,c,q,offset,x,y,r,u,v=map(int,(p,a,b,c,q,offset,x,y,r,u,v))
        assert p==q==r
        if left[p]!=right.get(p) or ((a^b)|(b^c))&PHYSICAL_MASK:continue
        dok=x==y and bool(x&PRESENT);lok=u==v and bool(u&PRESENT)
        if a&PRESENT and a&FILE and dok and a&MASK!=x&MASK:continue
        current=not a&(PRESENT|SWAPPED) or bool(a&PRESENT and a&FILE)
        if a&PRESENT:vm.add(a&MASK)
        if current and dok:device.add(x&MASK)
        if current and lok:layer.add(u&MASK)
        if a&PRESENT or current and (dok or lok):confirmed.add(p)
        if offset!=(1<<64)-1:
            # Multiple guest mappings of one snapshot offset must agree.
            eligible.setdefault(offset,[]).append((p,'current' if current else 'old' if a&PRESENT else 'unknown'))
    return header['records'],eligible,confirmed,vm|device|layer


def account_provider(before,after,host,direct,lower,first,last,*,segmented=False,details=None,_context=None):
    """Byte-range attribution followed by physical PFN set union."""
    records,eligible,confirmed,three=(_context if _context is not None else
        provider_context(before,after,host,direct,lower))
    current=set();old=set();resident=set();mixed=set();coverage={}
    stable_slots=valid_slots=0
    assert [r['name'] for r in first['rows']]==[r['name'] for r in last['rows']]
    for a,b in zip(first['rows'],last['rows'],strict=True):
        row=a['before'];address,capacity,offset,length,state=row[2:7]
        stable=(row==a['after']==b['before']==b['after'] and row[1]%2==0)
        stable_slots+=stable;valid=stable and state==3 and 0<length<=capacity
        valid_slots+=valid
        sources=segment_sources(a,b,capacity) if segmented else None
        allocation_stable=all(frame[key][2:4]==row[2:4] for frame in (a,b) for key in ('before','after'))
        assert a['first_address']==b['first_address']
        for i,(x,y) in enumerate(zip(a['pagemap'],b['pagemap'],strict=True)):
            if any(frame[key][6]==5 for frame in (a,b) for key in ('before','after')):continue
            if x!=y or not x&PRESENT or not x&MASK:continue
            virtual=a['first_address']+i*PAGE;pfn=x&MASK
            if virtual<address or virtual+PAGE>address+capacity:
                mixed.add(pfn);continue
            resident.add(pfn)
            if segmented:
                if not allocation_stable:continue
                begin=(virtual-address)//UNIT;pieces=sources[begin:begin+PAGE//UNIT]
                if len(pieces)!=PAGE//UNIT or any(p is None for p in pieces):continue
                spans=[(p,p+UNIT) for p in pieces]
            else:
                if not valid or virtual+PAGE>address+length:continue
                start=offset+virtual-address;spans=[(start,start+PAGE)]
            fragments=[];states=set()
            for start,end in spans:
                keys=range(start//PAGE,(end-1)//PAGE+1)
                matches=[eligible.get(k,[]) for k in keys]
                if any(not m for m in matches):break
                for key,match in zip(keys,matches,strict=True):
                    for guest,kind in match:
                        states.add(kind)
                        fragments.append((guest,max(start,key*PAGE)-key*PAGE,
                            min(end,(key+1)*PAGE)-key*PAGE))
            else:
                if states=={'current'}:
                    current.add(pfn)
                    for guest,start,end in fragments:coverage.setdefault(guest,[]).append((start,end))
                elif states=={'old'}:old.add(pfn)
                else:mixed.add(pfn)
    newly=full_coverage(coverage)
    result=dict(host_provider_current_bytes=len(current)*PAGE,
        host_provider_old_snapshot_bytes=len(old)*PAGE,
        host_provider_intersection_three_bytes=len(current&three)*PAGE,
        host_four_current_union_bytes=len(three|current)*PAGE,
        host_four_unconfirmed_guest_capacity_bytes=(records-len(confirmed|newly))*PAGE,
        provider_resolved_guest_pages=len(newly-confirmed),
        provider_stable_slots=stable_slots,provider_valid_slots=valid_slots,
        provider_whole_allocation_resident_bytes=len(resident)*PAGE,
        provider_unattributed_resident_bytes=len(resident-current-old)*PAGE,
        provider_mixed_or_edge_resident_pages=len(mixed))
    if details is not None:
        details.update(current=current,old=old,resident=resident,coverage=coverage,
            confirmed=confirmed,three=three,records=records)
    return result,three|current


def account_observed_provider(before,after,host,direct,lower,first,last,*,decoded=None,current_pfns=None):
    args=(before,after,host,direct,lower,first,last)
    segmented=any('segments' in r for r in first['rows'])
    if not segmented and decoded is None:return account_provider(*args)
    context=provider_context(before,after,host,direct,lower,decoded=decoded)
    if not segmented:return account_provider(*args,_context=context)
    assert all('segments' in r for frame in (first,last) for r in frame['rows'])
    legacy={};segments={}
    result,pfns=account_provider(*args,details=legacy,_context=context)
    values,_=account_provider(*args,segmented=True,details=segments,_context=context)
    current=legacy['current']|segments['current'];old=legacy['old']|segments['old']
    if current_pfns is not None:current_pfns.update(current)
    coverage={guest:list(spans) for guest,spans in legacy['coverage'].items()}
    for guest,spans in segments['coverage'].items():coverage.setdefault(guest,[]).extend(spans)
    newly=full_coverage(coverage)
    result.update(host_provider_segment_current_bytes=len(segments['current'])*PAGE,
        host_provider_segment_added_current_bytes=len(segments['current']-legacy['current'])*PAGE,
        host_provider_segment_old_snapshot_bytes=len(segments['old'])*PAGE,
        host_provider_segment_added_old_snapshot_bytes=len(segments['old']-legacy['old'])*PAGE,
        host_provider_combined_current_bytes=len(current)*PAGE,
        host_provider_combined_old_snapshot_bytes=len(old)*PAGE,
        host_four_segment_current_union_bytes=len(pfns|segments['current'])*PAGE,
        host_four_segment_unconfirmed_guest_capacity_bytes=(legacy['records']-len(legacy['confirmed']|newly))*PAGE,
        provider_segment_resolved_guest_pages=len(newly-legacy['confirmed']),
        provider_segment_unattributed_resident_bytes=len(legacy['resident']-current-old)*PAGE,
        provider_segment_mixed_or_edge_resident_pages=values['provider_mixed_or_edge_resident_pages'])
    return result,pfns|segments['current']


def recount_point(directory,saved):
    prefix=directory/f"pair-{saved['point']:04}"
    read=lambda suffix:Path(str(prefix)+suffix).read_bytes()
    rows=lambda suffix:list(struct.Struct('<4Q').iter_unpack(read(suffix)))
    value=json.loads(read('-provider.json'))
    result,pfns=account_observed_provider(read('-before.bin'),read('-after.bin'),rows('-host.bin'),
        rows('-backend.bin'),rows('-lower.bin'),value['first'],value['last'])
    assert all(saved[k]==v for k,v in result.items()), 'provider recount differs'
    return result,pfns
