"""Whole guest file-LRU and owned backing-cache observations, with PFN unions.

This is a boundary analysis, not passive periodic sampling. Image cache includes
filesystem metadata; an anonymous image mapping is not called page cache.
"""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import time

from guest_whole_cache_probe import selected_flags
from measure_file_physical import PAGE,PFN_MASK,identity,parse_maps,ram_address,read_entry

RECORD=struct.Struct('<QQQ')
PHYSICAL_MASK=PFN_MASK|(1<<61)|(1<<62)|(1<<63)


def same_physical_mapping(first,last):
    # pagemap bit 56 is exclusive mapping, not a PFN or residency change.
    return first&PHYSICAL_MASK==last&PHYSICAL_MASK


def dump(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def records(path):
    raw=path.read_bytes();assert len(raw)%RECORD.size==0
    return list(RECORD.iter_unpack(raw))


def resident(mapped,address):
    size=(len(mapped)+PAGE-1)//PAGE
    vector=(ctypes.c_ubyte*size)()
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
    if libc.mincore(address,len(mapped),vector):raise OSError(ctypes.get_errno(),'mincore')
    return bytes(value&1 for value in vector)


def file_identity(path):
    s=path.stat();return {key:getattr(s,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}


def owned_image_paths(proc,out,maps_raw):
    """Resolve namespace aliases by device/inode, never by filename alone."""
    import stat
    candidates={}
    for path in (out/'data/templates').rglob('*'):
        if not (path.name.endswith('.ext4') or path.name=='memory-ranges') or not path.is_file():continue
        if path.is_symlink():continue
        info=file_identity(path)
        candidates.setdefault((info['st_dev'],info['st_ino']),path.resolve(strict=True))
    paths={};regions=parse_maps(maps_raw)
    for line,region in zip(maps_raw.splitlines(),regions):
        parts=line.split(maxsplit=5)
        if len(parts)<6 or not (parts[5].endswith('.ext4') or parts[5].endswith('/memory-ranges')):continue
        key=(region['device'],region['inode'])
        assert key in candidates,'VMM mapped image has no owned device/inode match'
        path=candidates[key]
        paths.setdefault(str(path),dict(path=path,regions=[],identity=file_identity(path),sources=[]))['regions'].append(region)
        paths[str(path)]['sources'].append(dict(kind='VMM mapping',namespace_path=parts[5]))
    # Virtio upper images have open file descriptors but no VMM mmap.
    for fd in (proc/'fd').iterdir():
        try:target=os.readlink(fd);st=fd.stat()
        except FileNotFoundError:continue
        if not (target.endswith('.ext4') or target.endswith('/memory-ranges')) or not stat.S_ISREG(st.st_mode):continue
        key=(st.st_dev,st.st_ino);assert key in candidates,'VMM open image outside owned inventory'
        path=candidates[key]
        paths.setdefault(str(path),dict(path=path,regions=[],identity=file_identity(path),sources=[]))['sources'].append(
            dict(kind='VMM open fd',descriptor=fd.name,namespace_path=target))
    assert paths,'no owned ext4 images in VMM maps or descriptors'
    return paths


def scan_backing(path,directory,index,*,allow_unaligned=False):
    """Fault only initially resident pages; save the before/after mincore maps."""
    started=time.monotonic_ns();original=file_identity(path)
    assert 0<original['st_size']<=32*2**30
    assert allow_unaligned or original['st_size']%PAGE==0
    rows=[]
    with path.open('rb') as stream,mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_COPY) as mapped:
        mapped.madvise(mmap.MADV_RANDOM)
        address=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
        before=resident(mapped,address);assert sum(before)<=2**20,'resident scan budget exceeded'
        with open('/proc/self/pagemap','rb',buffering=0) as pm:
            for offset,value in enumerate(before):
                if not value:continue
                unused=mapped[offset*PAGE]
                first=read_entry(pm.fileno(),address+offset*PAGE)
                assert first&(1<<63) and first&(1<<61),'expected resident host file page'
                rows.append((offset,first,0))
            after=resident(mapped,address)
            rows=[(offset,first,read_entry(pm.fileno(),address+offset*PAGE)) for offset,first,_ in rows]
    assert file_identity(path)==original,'backing image changed'
    (directory/f'backing-{index}-before.bin').write_bytes(before)
    (directory/f'backing-{index}-after.bin').write_bytes(after)
    (directory/f'backing-{index}-pages.bin').write_bytes(b''.join(RECORD.pack(*r) for r in rows))
    return dict(path=str(path),identity=original,index=index,
        controller_elapsed_ns=time.monotonic_ns()-started,
        scope='entire image cache including metadata; only initially resident pages touched; MADV_RANDOM',
        new_resident_pages=sum(b==0 and a!=0 for b,a in zip(before,after)),
        lost_resident_pages=sum(b!=0 and a==0 for b,a in zip(before,after)))


def scan_host(directory,vm,proof,guest_before,out):
    started=time.monotonic_ns();proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    maps_raw=(proc/'maps').read_text();(directory/'vmm-maps.txt').write_text(maps_raw)
    paths=owned_image_paths(proc,out,maps_raw)
    selected=selected_flags((directory/'before-flags.bin').read_bytes(),guest_before['ram_ranges_pfns'])
    ram_rows=[];image_rows=[]
    with (proc/'pagemap').open('rb',buffering=0) as pm:
        for pfn in selected:
            address=ram_address(pfn,proof['slots']);entry=read_entry(pm.fileno(),address)
            ram_rows.append((pfn,entry,0))
        # Read VMM image PTEs in ranges; no content read, no fault of cold mappings.
        for index,item in enumerate(paths.values()):
            rows=[]
            for region in item['regions']:
                if item['path'].name=='memory-ranges':continue
                count=(region['end']-region['start'])//PAGE
                raw=os.pread(pm.fileno(),count*8,region['start']//PAGE*8)
                assert len(raw)==count*8
                for i,(entry,) in enumerate(struct.iter_unpack('<Q',raw)):
                    if entry&(1<<63):
                        assert entry&PFN_MASK,'resident image PFN hidden'
                        offset=region['offset']//PAGE+i
                        rows.append((offset,entry,0))
            image_rows.append(rows)
    backing=[]
    for index,item in enumerate(paths.values()):
        value=scan_backing(item['path'],directory,index)
        value.update(sources=item['sources'],regions=item['regions'],
                     role='vm-memory-snapshot' if item['path'].name=='memory-ranges' else 'filesystem-image')
        backing.append(value)
    with (proc/'pagemap').open('rb',buffering=0) as pm:
        ram_rows=[(pfn,entry,read_entry(pm.fileno(),ram_address(pfn,proof['slots']))) for pfn,entry,_ in ram_rows]
        for index,item in enumerate(paths.values()):
            rows=[]
            for offset,entry,_ in image_rows[index]:
                matches=[r['start']+offset*PAGE-r['offset'] for r in item['regions']
                    if r['offset']<=offset*PAGE<r['offset']+r['end']-r['start']]
                assert len(matches)==1,'ambiguous image mapping'
                rows.append((offset,entry,read_entry(pm.fileno(),matches[0])))
            (directory/f'vmm-image-{index}-pages.bin').write_bytes(b''.join(RECORD.pack(*r) for r in rows))
    (directory/'ram-pages.bin').write_bytes(b''.join(RECORD.pack(*r) for r in ram_rows))
    assert (proc/'maps').read_text()==maps_raw,'VMM maps changed during scan'
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    return dict(status='PASS',protocol_version=2,backing_files=backing,controller_elapsed_ns=time.monotonic_ns()-started,
        raw_record_columns=['guest_pfn_or_file_page_index','host_pagemap_before','host_pagemap_after'],
        raw_record_encoding='little-endian 3 uint64 (24 bytes)')


def recount(directory):
    load=lambda name:json.loads((directory/name).read_text())
    before=load('before.json');after=load('after.json');scan=load('host-scan.json')
    version=scan.get('protocol_version',1)
    same=same_physical_mapping if version>=2 else lambda first,last:first==last
    assert before['ram_ranges_pfns']==after['ram_ranges_pfns'] and before['page_size']==after['page_size']==PAGE
    a=set(selected_flags((directory/'before-flags.bin').read_bytes(),before['ram_ranges_pfns']))
    b=set(selected_flags((directory/'after-flags.bin').read_bytes(),after['ram_ranges_pfns']))
    assert len(a)==before['selected_file_lru_pages'] and len(b)==after['selected_file_lru_pages']
    ram=set();backing=set();memory_backing=set();mapped=set();copies=set();unstable=set()
    guest_unresolved=0;guest_nonresident=0;guest_backing_resident=0
    stable_backing={};cold_backing={}
    rows=records(directory/'ram-pages.bin');assert {r[0] for r in rows}==a
    for item in scan['backing_files']:
        index=item['index'];initial=(directory/f'backing-{index}-before.bin').read_bytes()
        final=(directory/f'backing-{index}-after.bin').read_bytes()
        rows=records(directory/f'backing-{index}-pages.bin')
        assert {r[0] for r in rows}=={i for i,v in enumerate(initial) if v}
        stable_backing[index]={};cold_backing[index]={i for i,v in enumerate(initial) if not v and not final[i]}
        for offset,entry,recheck in rows:
            assert entry&(1<<63) and entry&(1<<61) and entry&PFN_MASK
            if not same(entry,recheck) or not final[offset]:unstable.add(entry&PFN_MASK)
            else:
                stable_backing[index][offset]=entry&PFN_MASK
                (memory_backing if item.get('role')=='vm-memory-snapshot' else backing).add(entry&PFN_MASK)
        for offset,entry,recheck in records(directory/f'vmm-image-{index}-pages.bin'):
            assert entry&(1<<63) and entry&PFN_MASK
            if not same(entry,recheck):unstable.add(entry&PFN_MASK)
            elif entry&(1<<61):mapped.add(entry&PFN_MASK)
            else:copies.add(entry&PFN_MASK)
    for pfn,entry,recheck in records(directory/'ram-pages.bin'):
        if entry&(1<<63):
            assert entry&PFN_MASK,'resident RAM PFN hidden'
            if not same(entry,recheck) or pfn not in b:unstable.add(entry&PFN_MASK)
            else:ram.add(entry&PFN_MASK)
            continue
        if not same(entry,recheck) or pfn not in b or entry&(1<<62):
            guest_unresolved+=1;continue
        # No VMM PTE: private file mapping still has its immutable backing page.
        # Count it only if mincore and the backing PFN recheck prove residency.
        proof=load('ram-attestation.json');address=ram_address(pfn,proof['slots'])
        candidates=[]
        for item in scan['backing_files']:
            if item.get('role')!='vm-memory-snapshot':continue
            for region in item['regions']:
                if region['start']<=address<region['end']:
                    assert region['permissions']=='rw-p','RAM fallback requires a private file mapping'
                    candidates.append((item['index'],(address-region['start']+region['offset'])//PAGE))
        assert len(candidates)==1,'nonresident RAM lacks one owned snapshot backing'
        index,offset=candidates[0]
        if offset in stable_backing[index]:
            ram.add(stable_backing[index][offset]);guest_backing_resident+=1
        elif offset in cold_backing[index]:guest_nonresident+=1
        else:guest_unresolved+=1
    # A changed PFN is excluded from stable union even when encountered elsewhere.
    ram-=unstable;backing-=unstable;memory_backing-=unstable;mapped-=unstable;copies-=unstable
    conflict=copies&(ram|backing|mapped)
    assert not conflict,'anonymous/file categories conflict'
    result=dict(status='PASS',measurement_status='PARTIAL' if unstable or a!=b or guest_unresolved else 'PASS',
        page_size=PAGE,guest_file_lru_before_pages=len(a),guest_file_lru_after_pages=len(b),
        guest_classification_added_pages=len(b-a),guest_classification_removed_pages=len(a-b),
        guest_file_cache_stable_physical_bytes=len(ram)*PAGE,
        guest_file_lru_backing_resident_without_vmm_pte_pages=guest_backing_resident,
        guest_file_lru_nonresident_without_backing_cache_pages=guest_nonresident,
        guest_file_lru_unresolved_pages=guest_unresolved,
        backing_cache_stable_physical_bytes=len(backing)*PAGE,
        memory_snapshot_backing_cache_stable_physical_bytes=len(memory_backing)*PAGE,
        vmm_image_file_mapping_stable_physical_bytes=len(mapped)*PAGE,
        vmm_image_private_mapping_stable_physical_bytes=len(copies)*PAGE,
        cache_union_stable_physical_bytes=len(ram|backing|mapped)*PAGE,
        cache_and_private_image_union_stable_physical_bytes=len(ram|backing|mapped|copies)*PAGE,
        all_cache_including_memory_snapshot_union_stable_physical_bytes=len(ram|backing|memory_backing|mapped)*PAGE,
        duplicate_cache_pages_removed=len(ram)+len(backing)+len(mapped)-len(ram|backing|mapped),
        unstable_observed_unique_pages=len(unstable),
        whole_task_file_content_physical_bytes=None,
        scope='one sandbox: all guest RAM file-LRU, mapped/open owned ext4 images and RAM snapshot backing; host PFN dedup',
        limitations=['non-atomic boundary observations; changed pages excluded, new guest pages not mapped',
            'file-LRU and image cache may include filesystem metadata; not inode-attributed task contents',
            'private image mappings are not page cache or byte-verified file copies',
            'probe tmpfs and Python memory affect guest/host totals; scanner faults initially resident backing pages',
            'not a multi-VM sharing result; never add this physical subset to cgroup memory or VMM PSS'])
    if version>=2:
        final_rows=records(directory/'ram-final-pages.bin');assert {r[0] for r in final_rows}==b
        final_pfns=set();final_unknown=0;final_cold=0
        proof=load('ram-attestation.json')
        for pfn,entry,last in final_rows:
            if not same(entry,last) or entry&(1<<62):final_unknown+=1;continue
            if entry&(1<<63):
                assert entry&PFN_MASK;final_pfns.add(entry&PFN_MASK);continue
            address=ram_address(pfn,proof['slots']);candidates=[]
            for item in scan['backing_files']:
                if item.get('role')!='vm-memory-snapshot':continue
                for region in item['regions']:
                    if region['start']<=address<region['end']:
                        assert region['permissions']=='rw-p'
                        candidates.append((item['index'],(address-region['start']+region['offset'])//PAGE))
            assert len(candidates)==1,'missing RAM snapshot fallback'
            index,offset=candidates[0]
            if offset in stable_backing[index]:final_pfns.add(stable_backing[index][offset])
            elif offset in cold_backing[index]:final_cold+=1
            else:final_unknown+=1
        result.update(protocol_version=2,
            guest_file_cache_end_observed_physical_bytes=len(final_pfns)*PAGE,
            guest_file_cache_end_unknown_pages=final_unknown,
            guest_file_cache_end_nonresident_pages=final_cold,
            cache_union_end_observed_physical_bytes=len(final_pfns|backing|mapped)*PAGE,
            cache_observation_scope='all final guest file-LRU and whole owned filesystem images across one analysis interval; not atomic',
            exclusive_attribute_only_changes=sum(first!=last and same(first,last)
                for path in directory.glob('*pages.bin') for _,first,last in records(path)))
    return result


async def run(sb,out,tool,capture,paused_boundary=False,kernel_file_types=False,name='whole-cache'):
    analysis_name=name
    if name!='whole-cache':
        original_tool=tool
        async def tool(sandbox,label,*args,**kwargs):return await original_tool(sandbox,analysis_name+'-'+label,*args,**kwargs)
    started=time.monotonic_ns();directory=out/name;directory.mkdir()
    guest='/dev/shm/asb-cache-'+sb.id+'-'+name
    for name in ('vmm-identity.json','ram-attestation.json'):
        (directory/name).write_bytes((capture.directory/name).read_bytes())
    vm=json.loads((directory/'vmm-identity.json').read_text());assert vm['sandbox_id']==sb.id
    proof=json.loads((directory/'ram-attestation.json').read_text())
    pause=None;fixture=None
    command=(f'guest_cache_marker.py {guest} {sb.id} --heartbeat' if paused_boundary else f'guest_whole_cache_probe.py {guest}')
    prefix='env PYTHONDONTWRITEBYTECODE=1 ' if paused_boundary else ''
    await tool(sb,'whole-cache-start',f'nohup timeout 210 {prefix}python3 /opt/asb-exp1/{command} >/dev/shm/asb-cache.log 2>&1 </dev/null &')
    async def wait(marker):
        await tool(sb,'whole-cache-wait-'+marker,f'for i in $(seq 1 200); do test -f {guest}/{marker} && exit 0; test -f {guest}/failure.txt && break; sleep 0.1; done; cat {guest}/failure.txt /dev/shm/asb-cache.log; exit 1',timeout=25)
    async def download(name):
        (directory/name).write_bytes(await sb.download_file(guest+'/'+name,timeout=15))
    try:
        await wait('ready')
        for name in ('before.json','before-flags.bin','iomem.txt'):await download(name)
        assert (directory/'iomem.txt').read_text()==proof['iomem'],'RAM layout changed'
        # Move the blocking host scan off the event loop: host sampling continues.
        import asyncio
        if paused_boundary:
            await wait('marker.json');await download('marker.json')
            from paused_agentenv_boundary import PausedBoundary
            pause=PausedBoundary(directory,vm,proof,'/tmp',backend='trenvx')
            if kernel_file_types:
                from trenvx_kernel_cache import start_fixture
                fixture=await start_fixture(sb,out,tool,analysis_name)
            await asyncio.to_thread(pause.pause)
        scan=await asyncio.to_thread(scan_host,directory,vm,proof,json.loads((directory/'before.json').read_text()),out)
        dump(directory/'host-scan.json',scan)
        if kernel_file_types:
            from kernel_file_cache import classify
            typed=await asyncio.to_thread(classify,fixture[0],vm,proof,pause,backend='trenvx',host_scan=scan)
            assert typed['frozen_cache_physical_status']=='PASS','new file-LRU physical point incomplete'
        if pause is not None:
            await asyncio.to_thread(pause.snapshot_recheck)
            await asyncio.to_thread(pause.resume);pause.save(True)
        if fixture is not None:
            from trenvx_kernel_cache import finish_fixture
            await finish_fixture(sb,*fixture,tool,analysis_name)
        await tool(sb,'whole-cache-request-after',f'touch {guest}/capture-after');await wait('after-ready')
        for name in ('after.json','after-flags.bin'):await download(name)
        # Map every PFN in the second guest boundary, including newly added pages.
        final_pfns=selected_flags((directory/'after-flags.bin').read_bytes(),json.loads((directory/'after.json').read_text())['ram_ranges_pfns'])
        proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'));rows=[]
        with (proc/'pagemap').open('rb',buffering=0) as pm:
            for pfn in final_pfns:
                address=ram_address(pfn,proof['slots']);rows.append((pfn,read_entry(pm.fileno(),address),0))
            rows=[(pfn,entry,read_entry(pm.fileno(),ram_address(pfn,proof['slots']))) for pfn,entry,_ in rows]
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        (directory/'ram-final-pages.bin').write_bytes(b''.join(RECORD.pack(*row) for row in rows))
        result=recount(directory);result['controller_elapsed_ns']=time.monotonic_ns()-started
        dump(directory/'result.json',result)
        return result
    finally:
        if pause is not None and pause.paused:
            try:await asyncio.to_thread(pause.resume)
            finally:pause.save(False)
        await tool(sb,'whole-cache-release',f'touch {guest}/capture-after {guest}/release')
