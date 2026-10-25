"""Read only metadata of one raw immutable OverlayBD memory layer."""
from bisect import bisect_right
import hashlib
import json
import os
from pathlib import Path
import struct
import numpy as np

UNKNOWN=(1<<64)-1
MAGIC=0x00020100544d534c
MAGIC1=bytes.fromhex('657e63d29444084ca2d2c8ec4fcfae8a')
HEADER=struct.Struct('<Q16sIIQQQ')
SEGMENT=struct.Struct('<QQ')

class RawMemoryLayer:
    def __init__(self,path,directory):
        self.path=path;self.stat=path.stat()
        with path.open('rb',buffering=0) as f:
            head=os.pread(f.fileno(),4096,0)
            tail=os.pread(f.fileno(),4096,self.stat.st_size-4096)
            h=HEADER.unpack_from(head);t=HEADER.unpack_from(tail)
            assert h[0]==t[0]==MAGIC and h[1]==t[1]==MAGIC1, 'requires raw LSMT, without tar/zfile wrapping'
            assert h[3]&1 and not t[3]&1 and t[3]&6==6
            index_offset,count,self.virtual_size=t[4:]
            assert index_offset%4096==0 and self.stat.st_size%4096==0
            assert 0<count<=2**21 and index_offset+count*16<=self.stat.st_size-4096
            raw=os.pread(f.fileno(),count*16,index_offset)
            assert len(raw)==count*16
        self.segments=self.decode_index(raw,index_offset)
        self.starts=[r[0] for r in self.segments]
        self.tags=[high>>56 for low,high in SEGMENT.iter_unpack(raw) if low>>50]
        assert self.starts==sorted(self.starts) and all(a[1]<=b[0] for a,b in zip(self.segments,self.segments[1:]))
        if directory is not None:
            directory.mkdir()
            for name,data in [('header.bin',head),('trailer.bin',tail),('index.bin',raw)]:
                (directory/name).write_bytes(data)
            (directory/'metadata.json').write_text(json.dumps(dict(path=str(path),device=self.stat.st_dev,
                inode=self.stat.st_ino,size_bytes=self.stat.st_size,mtime_ns=self.stat.st_mtime_ns,
                virtual_size=self.virtual_size,index_offset=index_offset,index_count=count,
                index_sha256=hashlib.sha256(raw).hexdigest(),method='metadata pages only; no payload reads'),indent=2)+'\n')

    @staticmethod
    def decode_index(raw,index_offset):
        segments=[]
        for low,high in SEGMENT.iter_unpack(raw):
            start=low&((1<<50)-1);length=low>>50;physical=high&((1<<55)-1);zero=bool(high&(1<<55))
            if not length:continue
            if not zero:assert physical*512>=4096 and (physical+length)*512<=index_offset
            segments.append((start,start+length,physical,zero))
        return segments

    @classmethod
    def from_saved(cls,directory):
        obj=cls.__new__(cls)
        metadata=json.loads((directory/'metadata.json').read_text())
        raw=(directory/'index.bin').read_bytes()
        assert hashlib.sha256(raw).hexdigest()==metadata['index_sha256']
        obj.segments=cls.decode_index(raw,metadata['index_offset'])
        obj.starts=[r[0] for r in obj.segments]
        obj.tags=[high>>56 for low,high in SEGMENT.iter_unpack(raw) if low>>50]
        return obj

    def offsets(self,memory_pages):
        result=[]
        for page in memory_pages:
            if int(page)==UNKNOWN:result.append(UNKNOWN);continue
            sector=int(page)*8;i=bisect_right(self.starts,sector)-1
            if i<0:result.append(UNKNOWN);continue
            start,end,physical,zero=self.segments[i]
            offset=(physical+sector-start)*512
            if not zero and sector+8<=end and offset%4096==0:
                result.append(offset//4096);continue
            # New cross-segment mappings need explicit single-file provenance.
            tags=getattr(self,'tags',None)
            if zero or offset%4096 or not start<=sector<end or tags is None or tags[i]!=0:
                result.append(UNKNOWN);continue
            cursor=end
            while cursor<sector+8:
                i+=1
                if i>=len(self.segments):break
                next_start,next_end,next_physical,next_zero=self.segments[i]
                if (next_start!=cursor or next_end<=cursor or next_zero or tags[i]!=0
                        or next_physical!=physical+cursor-start):break
                cursor=next_end
            result.append(offset//4096 if cursor>=sector+8 else UNKNOWN)
        return np.array(result,dtype='<u8')


def owned_memory_layer(out,proc,directory):
    env=out.parent.parent/'env'
    configs=list((env/'snapshot-local-cache/runtime').glob('*/memory/image.json'))
    assert len(configs)==1, 'one owned memory snapshot config required'
    value=json.loads(configs[0].read_text());lowers=value['lowers']
    assert len(lowers)==1 and not value.get('upper',{}).get('data'), 'one immutable memory lower required'
    lower=lowers[0];name=lower['file']
    assert name.startswith('/workspace/env/snapshot-store/repository/managed-layers/')
    path=env/name.removeprefix('/workspace/env/')
    assert path.is_file() and not path.is_symlink() and path.stat().st_size==lower['size']
    assert lower['digest'].replace(':','_') in path.name
    # The provider must actually have this immutable layer open; a config alone
    # is insufficient ownership/usage proof. Restrict to the owned service cgroup.
    from pathlib import Path as P
    group=(proc/'cgroup').read_text()
    found=[]
    for p in P('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if (p/'cgroup').read_text()!=group:continue
            for fd in (p/'fd').iterdir():
                try:
                    st=fd.stat()
                    if (st.st_dev,st.st_ino)==(path.stat().st_dev,path.stat().st_ino):found.append(dict(pid=int(p.name),fd=fd.name))
                except FileNotFoundError:continue
        except (FileNotFoundError,ProcessLookupError):continue
    assert found, 'memory layer not open in owned service'
    layer=RawMemoryLayer(path,directory)
    (directory/'ownership.json').write_text(json.dumps(dict(config=str(configs[0]),layer=lower,open_descriptors=found),indent=2)+'\n')
    return layer


def account_layers(before,after,host,direct,lower):
    """Count current RAM copies at three levels; old snapshot copies stay separate."""
    from fast_cache_metadata import matching_tags
    from kernel_cache_metadata import PRESENT,SWAPPED,PHYSICAL_MASK,MASK
    FILE=1<<61
    pages=before[1];pages=pages[pages[:,1]!=0]
    assert np.array_equal(host[:,0],pages[:,0]) and np.array_equal(direct[:,0],pages[:,0]) and np.array_equal(lower[:,0],pages[:,0])
    valid=matching_tags(after[1],pages[:,0])==pages[:,1]
    valid &= (((host[:,1]^host[:,2])|(host[:,2]^host[:,3]))&PHYSICAL_MASK)==0
    present=(host[:,1]&PRESENT)!=0;file_pte=(host[:,1]&FILE)!=0;swapped=(host[:,1]&SWAPPED)!=0
    direct_ok=(direct[:,2]==direct[:,3])&((direct[:,2]&PRESENT)!=0)
    valid &= ~(present&file_pte&direct_ok&((host[:,1]&MASK)!=(direct[:,2]&MASK)))
    direct_current=valid&direct_ok&((~present&~swapped)|(present&file_pte))
    lower_ok=(lower[:,2]==lower[:,3])&((lower[:,2]&PRESENT)!=0)
    lower_current=valid&lower_ok&((~present&~swapped)|(present&file_pte))
    vmm=set(map(int,host[valid&present,1]&MASK))
    device=set(map(int,direct[direct_current,2]&MASK))
    layer=set(map(int,lower[lower_current,2]&MASK))
    old=set(map(int,lower[valid&present&~file_pte&lower_ok,2]&MASK))
    confirmed=valid&(present|direct_current|lower_current)
    return dict(host_lower_current_bytes=len(layer)*4096,
        host_three_current_union_bytes=len(vmm|device|layer)*4096,
        host_lower_intersection_with_vmm_device_bytes=len(layer&(vmm|device))*4096,
        host_lower_old_snapshot_bytes=len(old)*4096,
        lower_resolved_guest_pages=int(np.count_nonzero(lower_current&~present&~direct_current)),
        host_three_unconfirmed_guest_capacity_bytes=(after[0]['records']-int(np.count_nonzero(confirmed)))*4096)
