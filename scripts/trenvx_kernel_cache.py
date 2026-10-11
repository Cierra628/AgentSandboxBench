"""Exact TrEnv-X kernel layout and resident-only private RAM snapshot reads."""
import ctypes
import hashlib
import json
import mmap
from pathlib import Path
import re
import subprocess

from kernel_file_cache import GuestKernel
from measure_file_physical import PAGE,PFN_MASK,ram_address,read_entry
from trenvx_whole_cache import dump,file_identity,resident,same_physical_mapping

KERNEL_SHA='a50bbc107954f603fce3c8eb8105c6c65290dbe2224402061acc022c95968730'


def verify_layout(kernel,directory):
    expected={'page':{'flags':(0,8),'mapping':(24,8),'index':(32,8),'compound_head':(8,8)},
              'inode':{'i_mode':(0,2),'i_sb':(40,8),'i_ino':(64,8)},
              'address_space':{'host':(0,8)},'super_block':{'s_dev':(16,4),'s_op':(48,8),'s_fs_info':(896,8)},
              'ext4_sb_info':{'s_buddy_cache':(704,8)}}
    hashes={}
    for name,fields in expected.items():
        value=subprocess.check_output(['pahole','-j1','-C',name,str(kernel)],timeout=30)
        (directory/(name+'-layout.txt')).write_bytes(value);hashes[name]=hashlib.sha256(value).hexdigest()
        for field,(offset,size) in fields.items():
            assert re.search(r'\b'+field+r'\s*;\s*/\*\s*'+str(offset)+r'\s+'+str(size)+r'\s*\*/',value.decode()),(name,field,'DWARF offset mismatch')
        if name=='page':assert re.search(rb'/\* size: 64,',value),'unexpected page stride'
    flags=subprocess.check_output(['pahole','-j1','-C','pageflags',str(kernel)],timeout=30)
    assert re.search(rb'PG_lru\s*= 4,',flags) and re.search(rb'PG_swapbacked\s*= 19,',flags)
    (directory/'internal-pageflags.txt').write_bytes(flags)
    dump(directory/'layout-proof.json',dict(status='PASS',kernel_sha256=KERNEL_SHA,layout_sha256=hashes,flags_sha256=hashlib.sha256(flags).hexdigest(),lru_bit=4,swapbacked_bit=19,page_struct_size=64))


class SnapshotGuestKernel(GuestKernel):
    def __init__(self,proc,slots,pgd,scan):
        super().__init__(proc,slots,pgd)
        self.backings=[item for item in scan['backing_files'] if item['role']=='vm-memory-snapshot']
        assert self.backings,'no attested private memory snapshot'
        self.metadata_fallback_pages=0

    def backing_page(self,gpa,required=True):
        host=ram_address(gpa//PAGE,self.slots);matches=[]
        for item in self.backings:
            for region in item['regions']:
                if region['start']<=host<region['end']:
                    assert region['permissions']=='rw-p'
                    matches.append((item,host-region['start']+region['offset']))
        assert len(matches)==1,'RAM snapshot mapping missing or ambiguous'
        item,offset=matches[0];assert offset%PAGE==0
        path=Path(item['path']);assert file_identity(path)==item['identity'],'memory snapshot changed'
        with path.open('rb') as stream,mmap.mmap(stream.fileno(),PAGE,access=mmap.ACCESS_COPY,offset=offset) as mapped:
            mapped.madvise(mmap.MADV_RANDOM);address=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
            if not resident(mapped,address)[0]:
                assert not required,'kernel metadata snapshot page cold; refusing to fault it'
                return None
            data=mapped[:]
            with open('/proc/self/pagemap','rb',buffering=0) as pm:
                first=read_entry(pm.fileno(),address);last=read_entry(pm.fileno(),address)
            assert first&(1<<63) and first&(1<<61) and first&PFN_MASK and same_physical_mapping(first,last)
            assert resident(mapped,address)[0]
        assert file_identity(path)==item['identity']
        return data,dict(backing_index=item['index'],page_index=offset//PAGE,entry=first,last=last,sha256=hashlib.sha256(data).hexdigest())

    def physical_page(self,base):
        host=ram_address(base//PAGE,self.slots);entry=read_entry(self.pm.fileno(),host)
        if entry&(1<<63):return super().physical_page(base)
        assert not entry&(1<<62),'swapped kernel metadata unsupported'
        data,_=self.backing_page(base);self.metadata_fallback_pages+=1
        assert same_physical_mapping(entry,read_entry(self.pm.fileno(),host)),'snapshot RAM PTE changed'
        return data

    def recheck_fallback(self,rows):
        for row in rows:
            data,final=self.backing_page(row['guest_pfn']*PAGE)
            assert final['backing_index']==row['backing_index'] and final['page_index']==row['page_index']
            assert final['sha256']==row['sha256'] and same_physical_mapping(final['entry'],row['entry'])
            row['recheck_entry']=final['entry']


async def start_fixture(sb,out,tool,name):
    directory=out/(name+'-files');directory.mkdir()
    guest='/dev/shm/asb-file-inventory-'+sb.id+'-'+name
    cfg=dict(sandbox_id=sb.id,mode='fixture',kernel_file_types=True,root='/rom/overlay/asb-kernel-fixture-'+name)
    dump(directory/'config.json',cfg)
    path='/opt/asb-exp1/'+name+'-inventory.json'
    await sb.filesystem.write_bytes(path,json.dumps(cfg).encode(),timeout=30)
    await tool(sb,name+'-fixture-start',f'nohup timeout 210 env PYTHONDONTWRITEBYTECODE=1 ASB_CACHE_WAIT_SECONDS=180 python3 /opt/asb-exp1/guest_cached_file_inventory.py {path} {guest} >/dev/shm/{name}-inventory.log 2>&1 </dev/null &')
    await wait_fixture(sb,tool,guest,'ready',name)
    for filename in ('before.json','inventory-before.json','inventory-before-flags.bin','iomem.txt'):
        (directory/filename).write_bytes(await sb.download_file(guest+'/'+filename,timeout=15))
    return directory,guest


async def wait_fixture(sb,tool,guest,marker,name):
    await tool(sb,name+'-fixture-wait-'+marker,f'for i in $(seq 1 250); do test -f {guest}/{marker} && exit 0; test -f {guest}/failure.txt && break; sleep .1; done; cat {guest}/failure.txt /dev/shm/{name}-inventory.log; exit 1',timeout=30)


async def finish_fixture(sb,directory,guest,tool,name):
    await tool(sb,name+'-fixture-after',f'touch {guest}/capture-after');await wait_fixture(sb,tool,guest,'after-ready',name)
    for filename in ('after.json','inventory-after.json','inventory-after-flags.bin'):
        (directory/filename).write_bytes(await sb.download_file(guest+'/'+filename,timeout=15))
    await tool(sb,name+'-fixture-release',f'touch {guest}/release');await wait_fixture(sb,tool,guest,'done',name)
    before=json.loads((directory/'before.json').read_text());after=json.loads((directory/'after.json').read_text())
    assert before['catalog']['regular_paths']==6 and len(before['files'])==5
    final={r['path']:r for r in after['files']};hot=0
    for file in before['files']:
        end=final[file['path']];assert file['status']==end['status']=='PASS' and file['identity']==end['identity']
        expected=[0,1] if len(file['aliases'])==2 else [0] if file['path'].endswith('/hot-tail') else []
        assert file['resident_indices']==end['resident_indices']==expected
        assert len(file['pages'])==len(end['pages'])
        from guest_file_type_check import is_file_lru
        for first,last in zip(file['pages'],end['pages']):
            assert all(first[key]==last[key] for key in ('index','valid_bytes','pfn','sha256','status'))
            assert is_file_lru(first['flags']) and is_file_lru(last['flags'])
        hot+=len(file['pages'])
    assert hot==3
    result=dict(status='PASS',fixture_hot_pages=hot,cold_and_sparse_stayed_nonresident=True,hardlink_dedup='PASS',
                scope='three known guest content pages; not full task or full cache',guest_elapsed_ns=after['guest_elapsed_ns'])
    dump(directory/'fixture-result.json',result);return result
