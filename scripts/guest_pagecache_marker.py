"""Three known pages calibrate whole-cache membership while a private guest runs."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import time

from kernel_cache_metadata import decode


def main():
    sid,directory=sys.argv[1],Path(sys.argv[2])
    assert directory.parent==Path('/dev/shm') and directory.name.startswith('asb-whole-')
    directory.mkdir(exist_ok=False)
    maps=[];paths=[];libc=ctypes.CDLL(None,use_errno=True)
    libc.mlock.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    libc.munlock.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    try:
        marker=mmap.mmap(-1,4096,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS)
        text=('ASB-WHOLE:'+sid).encode();marker[:len(text)]=text
        address=ctypes.addressof(ctypes.c_char.from_buffer(marker))
        assert libc.mlock(address,4096)==0
        maps.append((marker,address))
        known=[]
        with open('/proc/self/pagemap','rb',buffering=0) as pm:
            pfn=lambda address:struct.unpack('<Q',os.pread(pm.fileno(),8,address//4096*8))[0]&((1<<55)-1)
            marker_pfn=pfn(address)
            for name,path in [('regular',Path('/workspace/asb-known-file.bin')),('shmem',directory/'known-shmem.bin')]:
                data=(name.encode()+b'_'*4096)[:4096]
                with path.open('xb') as f:f.write(data)
                paths.append(path)
                fd=os.open(path,os.O_RDWR)
                mapped=mmap.mmap(fd,4096,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)
                os.close(fd)
                a=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
                assert libc.mlock(a,4096)==0
                maps.append((mapped,a))
                known.append(dict(kind=name,pfn=pfn(a),sha256=hashlib.sha256(data).hexdigest()))
            info,entries=decode(Path('/proc/asb_pagecache').read_bytes())
            assert entries.get(known[0]['pfn'],0)>0 and not entries[known[0]['pfn']]&1
            assert entries.get(known[1]['pfn'],0)&1
            assert marker_pfn not in entries, 'anonymous marker counted as page cache'
        bootstrap=dict(sandbox_id=sid,pfn=marker_pfn,text=text.decode(),known_pages=known,
            iomem=Path('/proc/iomem').read_text(),kernel=os.uname().release,
            pid=os.getpid(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            validation='regular locked file and tmpfs included; anonymous marker excluded',
            cache_header=info)
        (directory/'bootstrap.json').write_text(json.dumps(bootstrap)+'\n')
        deadline=time.monotonic()+150;count=0
        while not (directory/'release').exists():
            assert time.monotonic()<deadline, 'host release timeout'
            count+=1;struct.pack_into('<Q',marker,128,count);time.sleep(.02)
        (directory/'result.json').write_text(json.dumps(dict(status='PASS',heartbeats=count))+'\n')
    except BaseException:
        import traceback
        (directory/'failure.txt').write_text(traceback.format_exc());raise
    finally:
        for mapped,address in maps:libc.munlock(address,4096);mapped.close()
        for path in paths:path.unlink(missing_ok=True)
    (directory/'done').touch()


if __name__=='__main__':main()
