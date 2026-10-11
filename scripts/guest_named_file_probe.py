"""Hold existing resident task-file pages for host inspection, without full-file pre-read."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import resource
import stat
import struct
import sys
import time

PAGE=4096
MASK=(1<<55)-1


def file_identity(st):
    return {key:getattr(st,key) for key in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}


def resident(mapped,address):
    vector=(ctypes.c_ubyte*((len(mapped)+PAGE-1)//PAGE))()
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
    if libc.mincore(address,len(mapped),vector):raise OSError(ctypes.get_errno(),'mincore')
    return [bool(value&1) for value in vector]


def capture(records,label):
    started=time.monotonic_ns();files=[]
    with open('/proc/self/pagemap','rb',buffering=0) as pm,open('/proc/kpageflags','rb',buffering=0) as flags:
        maps=Path('/proc/self/maps').read_text()
        for path,fd,mapped,address,original,selected in records:
            assert file_identity(os.fstat(fd))==file_identity(path.stat())==original,'file identity changed'
            rows=[]
            for index in selected:
                offset=index*PAGE;valid=min(PAGE,len(mapped)-offset)
                # Only pages that mincore reported resident before the probe are touched.
                digest=hashlib.sha256(mapped[offset:offset+valid]).hexdigest() if label=='before' else None
                entry=struct.unpack('<Q',os.pread(pm.fileno(),8,(address//PAGE+index)*8))[0]
                if not entry&(1<<63):
                    assert label=='after','initially resident page could not be mapped'
                    rows.append(dict(index=index,offset=offset,valid_bytes=valid,pfn=0,entry=entry,flags=None,sha256=None))
                    continue
                assert entry&(1<<63) and entry&MASK,'resident guest PFN hidden or missing'
                if label=='after':
                    digest=hashlib.sha256(mapped[offset:offset+valid]).hexdigest()
                    assert entry==struct.unpack('<Q',os.pread(pm.fileno(),8,(address//PAGE+index)*8))[0],'guest PFN changed during content read'
                pfn=entry&MASK;kflags=struct.unpack('<Q',os.pread(flags.fileno(),8,pfn*8))[0]
                rows.append(dict(index=index,offset=offset,valid_bytes=valid,pfn=pfn,entry=entry,
                                 flags=kflags,sha256=digest))
            relevant=[]
            for line in maps.splitlines():
                fields=line.split(maxsplit=5);start,end=[int(x,16) for x in fields[0].split('-')]
                if start<=address<end:relevant.append(line)
            assert len(relevant)==1,'file mapping missing or split'
            files.append(dict(path=str(path),identity=original,address=address,
                selected_indices=selected,total_pages=(len(mapped)+PAGE-1)//PAGE,
                mincore_after=resident(mapped,address),mapping=relevant[0],pages=rows))
    return dict(status='PASS',label=label,page_size=PAGE,pid=os.getpid(),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                timestamp_ns=time.time_ns(),elapsed_ns=time.monotonic_ns()-started,files=files)


def wait_for(path):
    deadline=time.monotonic()+60
    while not path.exists():
        if time.monotonic()>deadline:raise TimeoutError('host inspection deadline')
        time.sleep(.05)


def main():
    manifest,out=Path(sys.argv[1]),Path(sys.argv[2]);cfg=json.loads(manifest.read_text())
    assert os.sysconf('SC_PAGE_SIZE')==PAGE and cfg['page_size']==PAGE
    paths=[Path(p) for p in cfg['files']]
    assert paths and len(set(paths))==len(paths)
    assert all(p.is_absolute() and '..' not in p.parts and str(p).startswith('/testbed/') for p in paths)
    out.mkdir(parents=True,exist_ok=False);records=[];initial=[];started=time.monotonic_ns()
    try:
        for path in paths:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            st=os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=32*2**20:
                os.close(fd);raise RuntimeError('only nonempty regular files <=32 MiB supported')
            mapped=mmap.mmap(fd,0,access=mmap.ACCESS_COPY)
            address=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
            before=resident(mapped,address);selected=[i for i,value in enumerate(before) if value]
            records.append((path,fd,mapped,address,file_identity(st),selected))
            initial.append(dict(path=str(path),resident_before=before))
        value=capture(records,'before');value.update(sandbox_id=cfg['sandbox_id'],initial_residency=initial)
        (out/'before.json').write_text(json.dumps(value,indent=2)+'\n');(out/'ready').touch()
        wait_for(out/'capture-after')
        value=capture(records,'after');value['sandbox_id']=cfg['sandbox_id']
        (out/'after.json').write_text(json.dumps(value,indent=2)+'\n');(out/'after-ready').touch()
        wait_for(out/'release')
        # Full content hashes are deliberately taken only after both mapping checks.
        hashes=[]
        for path,fd,mapped,address,original,selected in records:
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            assert file_identity(os.fstat(fd))==file_identity(path.stat())==original
            hashes.append(dict(path=str(path),sha256=digest,identity=original))
        usage=resource.getrusage(resource.RUSAGE_SELF)
        (out/'result.json').write_text(json.dumps(dict(status='PASS',files=hashes,
            elapsed_ns=time.monotonic_ns()-started,maxrss_kib=usage.ru_maxrss,
            cpu_seconds=usage.ru_utime+usage.ru_stime,
            scope='resident-only inspection; full hashes after host checks may warm cache'),indent=2)+'\n')
        (out/'done').touch()
    except BaseException:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());raise
    finally:
        for _,fd,mapped,*_ in records:mapped.close();os.close(fd)


if __name__=='__main__':main()
