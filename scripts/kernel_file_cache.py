"""Read one paused x86 guest's file-cache inode types, bound to its exact ELF.

AgentENV's pinned 6.1.175 uses compiled layout proofs; TrEnv-X's pinned
6.1.134 additionally verifies DWARF. Internal PG_lru=4 differs from exported
KPF_LRU=5. Three known pages must verify device, inode and logical index.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import time

from measure_file_physical import identity,ram_address,read_entry,PAGE
from trenvx_whole_cache import dump,records,RECORD,same_physical_mapping

KERNEL_SHA='cd7c5a06a2026a2d8a0cddf60b41a5d76a6ac025e54439eb3ee5c9e57a60c67b'
PADDR_MASK=((1<<52)-1)&~4095
INTERNAL_LRU_BIT=4
BUDDY_OFFSETS={
    KERNEL_SHA:736,
    'a50bbc107954f603fce3c8eb8105c6c65290dbe2224402061acc022c95968730':704,
}


class GuestKernel:
    def __init__(self,proc,slots,pgd):
        self.proc,self.slots,self.pgd=proc,slots,pgd
        self.mem=(proc/'mem').open('rb',buffering=0);self.pm=(proc/'pagemap').open('rb',buffering=0)
        self.cache={};self.levels=4
    def close(self):self.mem.close();self.pm.close()
    def physical_page(self,base):
        host=ram_address(base//PAGE,self.slots)
        assert read_entry(self.pm.fileno(),host)&(1<<63),'kernel RAM not host-resident; refusing to fault it'
        data=os.pread(self.mem.fileno(),PAGE,host);assert len(data)==PAGE
        return data
    def physical(self,address,size):
        chunks=[]
        while size:
            base=address&~4095;offset=address-base;count=min(size,PAGE-offset)
            if base not in self.cache:
                assert len(self.cache)<32768,'kernel metadata read budget exceeded'
                self.cache[base]=self.physical_page(base)
            chunks.append(self.cache[base][offset:offset+count]);address+=count;size-=count
        return b''.join(chunks)
    def u64physical(self,address):return struct.unpack('<Q',self.physical(address,8))[0]
    def translate(self,address):
        assert address>>48==0xffff,'kernel pointer is not canonical supported x86 high address'
        table=self.pgd
        for shift in ((48,39,30,21,12) if self.levels==5 else (39,30,21,12)):
            entry=self.u64physical(table+((address>>shift)&511)*8)
            assert entry&1,'nonpresent guest page-table entry'
            if shift in (30,21) and entry&(1<<7):return (entry&PADDR_MASK&~((1<<shift)-1))+(address&((1<<shift)-1))
            table=entry&PADDR_MASK
        return table+(address&4095)
    def virtual(self,address,size):
        pieces=[]
        while size:
            amount=min(size,PAGE-(address&4095));pieces.append(self.physical(self.translate(address),amount));address+=amount;size-=amount
        return b''.join(pieces)
    def u64(self,address):return struct.unpack('<Q',self.virtual(address,8))[0]
    def page_inode(self,pfn,vmemmap):
        page=vmemmap+pfn*64
        flags=self.u64(page)
        assert flags&(1<<INTERNAL_LRU_BIT),'selected page no longer on LRU'
        assert not self.u64(page+8)&1,'compound tail unsupported'
        mapping=self.u64(page+24);assert mapping and not mapping&3,'no untagged file mapping'
        inode=self.u64(mapping);assert inode,'no mapping inode'
        mode=struct.unpack('<H',self.virtual(inode,2))[0]
        ino=self.u64(inode+64);sb=self.u64(inode+40)
        dev=struct.unpack('<I',self.virtual(sb+16,4))[0]
        return dict(guest_pfn=pfn,mode=mode,inode=ino,device=os.makedev(dev>>20,dev&((1<<20)-1)),
                    index=self.u64(page+32),page_pointer=page,mapping_pointer=mapping,inode_pointer=inode,superblock_pointer=sb)


def allocation_owner(reader,row,ext4_sops,buddy_offset):
    """Require the actual inode to be this ext4 superblock's buddy-cache object."""
    assert row['mode']==0 and row['inode']==1,'not a mode-zero inode-one candidate'
    spans={}
    def field(name,address,size):
        data=reader.virtual(address,size);pieces=[];remaining=size;cursor=address
        while remaining:
            count=min(remaining,PAGE-(cursor&4095))
            pieces.append(dict(physical_address=reader.translate(cursor),size=count))
            cursor+=count;remaining-=count
        spans[name]=dict(address=address,hex=data.hex(),pieces=pieces)
        return int.from_bytes(data,'little')
    assert field('mapping_host',row['mapping_pointer'],8)==row['inode_pointer']
    inode=row['inode_pointer'];sb=row['superblock_pointer']
    assert field('inode_mode',inode,2)==0 and field('inode_number',inode+64,8)==1
    assert field('inode_superblock',inode+40,8)==sb
    assert field('superblock_operations',sb+48,8)==ext4_sops,'not ext4 superblock operations'
    info=field('superblock_private',sb+896,8)
    assert field('buddy_inode',info+buddy_offset,8)==inode,'inode is not actual ext4 allocator cache'
    return dict(status='PASS',inode_pointer=inode,superblock_pointer=sb,ext4_sops=ext4_sops,
                buddy_offset=buddy_offset,fields=spans)


def archive_owner_pages(reader,directory,proofs):
    bases=sorted({piece['physical_address']&~4095 for proof in proofs.values()
                  for field in proof['fields'].values() for piece in field['pieces']})
    pages=[]
    with (directory/'allocation-owner-pages.bin').open('xb') as stream:
        for base in bases:
            data=reader.cache[base];host=ram_address(base//PAGE,reader.slots)
            entry=read_entry(reader.pm.fileno(),host)
            # These identity receipts require actual VMM PTE residency. A missing
            # PTE remains unsupported here even if data has a snapshot fallback.
            assert entry&(1<<63) and entry&((1<<55)-1),'allocation metadata RAM not directly resident'
            assert reader.physical_page(base)==data,'allocation metadata RAM changed'
            last=read_entry(reader.pm.fileno(),host)
            assert same_physical_mapping(entry,last),'allocation metadata mapping changed'
            stream.write(data);pages.append(dict(physical_address=base,entry=entry,last=last,sha256=hashlib.sha256(data).hexdigest()))
    dump(directory/'allocation-owner-proof.json',dict(status='PASS',objects=list(proofs.values()),pages=pages))


def classify(directory,vm,proof,pause,*,backend='agentenv',host_scan=None):
    started=time.monotonic_ns();assert pause.paused and pause.state()=='Paused'
    proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    if backend=='agentenv':
        config=pause.api('GET','/vm/config');target=config['boot-source']['kernel_image_path']
        dump(directory/'vmm-config.json',config)
        assert target.startswith('/workspace/env/deps/kernel/'), 'unexpected private VMM kernel asset'
        expected_sha=KERNEL_SHA
    else:
        assert backend=='trenvx'
        from trenvx_kernel_cache import KERNEL_SHA as expected_sha,verify_layout
        config=pause.api('GET','/api/v1/vm.info');target=config['config']['payload']['kernel']
        dump(directory/'vmm-config.json',config)
        assert target==str(directory.parent/'data/templates/asb-task-base/run/vmlinux'),'unexpected actual TrEnv-X kernel asset'
    kernel=proc/'root'/target.lstrip('/')
    with kernel.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    assert digest==expected_sha,'unsupported exact kernel ELF'
    if backend=='trenvx':verify_layout(kernel,directory)
    before=json.loads((directory/'before.json').read_text());symbols=before['kernel_symbols']
    functions=('_text','folio_mapping','pfn_to_online_page','generic_fillattr')
    assert all(symbols.get(name,0) for name in functions),'guest kernel text symbols hidden/missing'
    wanted=set(functions)|{'init_top_pgt','vmemmap_base','page_offset_base','ext4_sops'}
    compiled={}
    raw=subprocess.check_output(['nm',str(kernel)],timeout=15).decode();(directory/'kernel-symbols.txt').write_text(raw)
    for line in raw.splitlines():
        parts=line.split()
        if len(parts)==3 and parts[2] in wanted:compiled[parts[2]]=int(parts[0],16)
    delta=symbols['_text']-compiled['_text'];assert all(symbols[k]-compiled[k]==delta for k in functions),'guest/ELF text relocation mismatch'
    symbols={name:compiled[name]+delta for name in wanted}
    evidence={}
    for function in ('folio_mapping','pfn_to_online_page','filemap_fdatawrite_wbc','generic_fillattr','folio_isolate_lru','ext4_mb_init_backend','__ext4_fill_super'):
        value=subprocess.check_output(['objdump','-d','--disassemble='+function,str(kernel)],timeout=15)
        (directory/(function+'.asm')).write_bytes(value);evidence[function]=hashlib.sha256(value).hexdigest()
        if function=='folio_isolate_lru':
            assert re.search(rb'lock btrq\s+\$0x4,\(%rdi\)',value),'exact kernel internal PG_lru proof missing'
    buddy_offset=BUDDY_OFFSETS[digest]
    buddy_asm=(directory/'ext4_mb_init_backend.asm').read_bytes()
    assert re.search(rb'mov\s+0x380\(%rdi\),%rax',buddy_asm)
    assert re.search(rb'mov\s+%rax,0x'+format(buddy_offset,'x').encode()+rb'\(%r15\)',buddy_asm)
    assert re.search(rb'movq\s+\$0x1,0x40\(%rax\)',buddy_asm)
    ext4_asm=(directory/'__ext4_fill_super.asm').read_bytes()
    assert re.search(rb'movq\s+\$0x'+format(compiled['ext4_sops'],'x').encode()+rb',0x30\(%rbx\)',ext4_asm)
    iomem=proof['iomem'];match=re.search(r'^\s*([0-9a-f]+)-[0-9a-f]+ : Kernel code$',iomem,re.M);assert match
    code=int(match[1],16)
    physical_symbol=lambda name:code+symbols[name]-symbols['_text']
    if backend=='trenvx':
        from trenvx_kernel_cache import SnapshotGuestKernel
        reader=SnapshotGuestKernel(proc,proof['slots'],physical_symbol('init_top_pgt'),host_scan)
    else:reader=GuestKernel(proc,proof['slots'],physical_symbol('init_top_pgt'))
    rows=[];checks={};types={};unknown=0;allocation_proofs={}
    try:
        vmemmap=reader.u64physical(physical_symbol('vmemmap_base'))
        base=reader.u64physical(physical_symbol('page_offset_base'))
        marker=pause.marker['pfn']*PAGE;levels=[]
        for count in (4,5):
            reader.levels=count
            try:
                if reader.translate(base+marker)==marker:levels.append(count)
            except AssertionError:pass
        assert len(levels)==1,'kernel page-table geometry not uniquely verified'
        reader.levels=levels[0];checks['direct_map_marker_matches_actual_ram']='PASS'
        for file in before['files']:
            for page in file['pages']:
                if page['status']!='PASS':continue
                row=reader.page_inode(page['pfn'],vmemmap)
                assert stat.S_ISREG(row['mode']) and row['device']==file['identity']['st_dev'] and row['inode']==file['identity']['st_ino']
                assert row['index']==page['index'],'file page index differs from kernel mapping'
                assert hashlib.sha256(reader.physical(page['pfn']*PAGE,page['valid_bytes'])).hexdigest()==page['sha256'],'fixture RAM content differs'
        checks['known_file_type_device_inode_index']='PASS'
        checks['known_file_contents_match_ram']='PASS'
        checks['exact_elf_and_runtime_text_relocation']='PASS'
        assert reader.u64(vmemmap+pause.marker['pfn']*64)&(1<<19),'tmpfs marker not swap-backed at compiled flag bit'
        checks['tmpfs_marker_excluded']='PASS'
        # Export actual struct-page flags while all guest vCPUs are paused.
        # The exact ELF folio_mapping tests PG_swapbacked at bit 19.
        from guest_whole_cache_probe import ram_ranges
        candidates=[];ranges=ram_ranges(iomem)
        with (directory/'frozen-page-structs.bin').open('xb') as structs:
            for start,end in ranges:
                raw=reader.virtual(vmemmap+start*64,(end-start)*64);structs.write(raw)
                for i,fields in enumerate(struct.iter_unpack('<8Q',raw)):
                    flag,head=fields[:2]
                    # kpageflags applies folio flags to compound tails. Preserve
                    # their candidate coverage even though inode decoding is unsupported.
                    if head&1:flag=reader.u64(head-1)
                    if flag&(1<<INTERNAL_LRU_BIT) and not flag&(1<<19):candidates.append(start+i)
        dump(directory/'frozen-page-structs.json',dict(ram_ranges_pfns=ranges,record_size=64,vmemmap_base=vmemmap,lru_bit=INTERNAL_LRU_BIT,swapbacked_bit=19,scope='actual exact-kernel struct page flags exported while paused'))
        host_rows=[];fallback=[];nonresident_pfns=[]
        for pfn in candidates:
            try:
                row=reader.page_inode(pfn,vmemmap);kind=stat.S_IFMT(row['mode'])
                name={stat.S_IFREG:'regular_file',stat.S_IFBLK:'block_device',stat.S_IFDIR:'directory',stat.S_IFLNK:'symlink'}.get(kind,'other_inode')
                if row['mode']==0 and row['inode']==1:
                    try:
                        inode=row['inode_pointer']
                        if inode not in allocation_proofs:
                            allocation_proofs[inode]=allocation_owner(reader,row,symbols['ext4_sops'],buddy_offset)
                        name='filesystem_allocation_metadata'
                    except (AssertionError,RuntimeError) as exc:row['allocation_owner_error']=str(exc)
                row.update(status='PASS',inode_type=name)
                types[name]=types.get(name,0)+1
            except (AssertionError,RuntimeError) as exc:row=dict(guest_pfn=pfn,status='UNKNOWN',error=str(exc));unknown+=1
            address=ram_address(pfn,proof['slots']);entry=read_entry(reader.pm.fileno(),address)
            if backend=='trenvx' and not entry&(1<<63) and not entry&(1<<62):
                data=reader.backing_page(pfn*PAGE,required=False)
                if data is None:nonresident_pfns.append(pfn)
                else:fallback.append(dict(guest_pfn=pfn,**data[1]))
            host_rows.append((pfn,entry,0))
            rows.append(row)
        host_rows=[(pfn,first,read_entry(reader.pm.fileno(),ram_address(pfn,proof['slots']))) for pfn,first,_ in host_rows]
        if backend=='trenvx':
            indirect={r['guest_pfn'] for r in fallback}|set(nonresident_pfns)
            assert all(not first&(1<<63) and same_physical_mapping(first,last) for pfn,first,last in host_rows if pfn in indirect),'indirect RAM mapping changed'
            reader.recheck_fallback(fallback);dump(directory/'snapshot-page-fallback.json',fallback)
            for pfn in nonresident_pfns:assert reader.backing_page(pfn*PAGE,required=False) is None,'cold snapshot page became resident'
            dump(directory/'nonresident-snapshot-pfns.json',nonresident_pfns)
        (directory/'frozen-typed-ram.bin').write_bytes(b''.join(RECORD.pack(*r) for r in host_rows))
        assert pause.heartbeat()==pause.receipt['paused_counter'][0]
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        archive_owner_pages(reader,directory,allocation_proofs)
        dump(directory/'kernel-page-owners.json',rows)
        known_types=sum(value for key,value in types.items() if key!='other_inode')
        known_host={first&((1<<55)-1) for _,first,last in host_rows if first&(1<<63) and same_physical_mapping(first,last)}
        known_host.update(row['entry']&((1<<55)-1) for row in fallback)
        missing_host=sum(not first&(1<<63) or not same_physical_mapping(first,last) for _,first,last in host_rows)-len(fallback)-len(nonresident_pfns)
        result=dict(status='PASS',classification_status='PASS' if unknown==0 and not types.get('other_inode',0) else 'PARTIAL',checks=checks,
            frozen_guest_file_cache_physical_bytes=len(known_host)*PAGE,
            frozen_guest_file_cache_host_unknown_pages=missing_host,
            frozen_cache_physical_status='PASS' if missing_host==0 else 'PARTIAL',
            untyped_inode_pages=types.get('other_inode',0),known_inode_type_pages=known_types,
            selected_guest_pages=len(rows),inode_type_pages=types,unknown_guest_pages=unknown,
            classification_source='paused guest kernel page flags and mapping inode, not prior guest export',
            kernel_sha256=digest,compiled_layout_evidence_sha256=evidence,page_table_levels=reader.levels,
            metadata_read_pages=len(reader.cache),metadata_read_bytes=len(reader.cache)*PAGE,
            controller_elapsed_ns=time.monotonic_ns()-started,
            snapshot_fallback_pages=len(fallback),proven_nonresident_pages=len(nonresident_pfns),
            metadata_snapshot_fallback_pages=getattr(reader,'metadata_fallback_pages',0),
            scope='all System RAM file-LRU selected and typed while guest paused; exact ELF and known file inode proof',
            limitations=['Block-device cache can contain filesystem metadata and raw blocks; it is not all regular-file content.',
                'Guest kernel flags and inode types frozen; no byte verification of all regular files or host-provider attribution.',
                'Kernel metadata reads affect host access records; only initially host-resident RAM read.',
                'Filesystem allocation metadata is identified by actual ext4 superblock and buddy-cache pointers; not regular-file content.'])
        dump(directory/'kernel-file-types.json',result);return result
    finally:reader.close()
