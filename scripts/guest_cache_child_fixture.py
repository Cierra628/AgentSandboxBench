"""One unlocked local file: write/read, then evict only this file's clean cache."""
import argparse
import ctypes
import json
import mmap
import os
from pathlib import Path
import struct
import sys
import time

from kernel_cache_metadata import decode

PAGE=4096
SIZE=16*2**20


def wait_for_marker(out,name,timeout_seconds=120):
    deadline=time.monotonic()+timeout_seconds
    while not (out/name).exists():
        assert time.monotonic()<deadline, name+' deadline'
        time.sleep(.02)


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--dynamic-file',action='store_true')
    parser.add_argument('--wait-for-begin',action='store_true')
    parser.add_argument('--finish-timeout-seconds',type=int,choices=(120,180),default=120)
    return parser.parse_args(argv)


def main():
    args=parse_args();out=args.output;out.mkdir()
    dynamic=args.dynamic_file
    path=Path('/workspace/asb-child-cache.bin')
    def save(name,value):
        if dynamic:
            from guest_file_cache_query import file_query
            raw,receipt=file_query(path)
            assert raw is not None, 'fixture file missing during query'
            (out/(name+'-file.bin')).write_bytes(raw)
            value['file_cache']=receipt
        (out/(name+'.json')).write_text(json.dumps(value)+'\n')
    def wait(name):
        wait_for_marker(out,name,args.finish_timeout_seconds if name=='finish' else 120)
    def metadata():
        info,pages=decode(Path('/proc/asb_pagecache').read_bytes())
        return info,pages
    if args.wait_for_begin:
        (out/'waiting').touch();wait('begin')
    with path.open('xb') as f:
        f.write(b'A'*SIZE);f.flush();os.fsync(f.fileno())
    # This is the explicit workload read, not a residency query or prefetch.
    with path.open('r+b') as f, mmap.mmap(f.fileno(),SIZE,access=mmap.ACCESS_WRITE) as mapping:
        assert mapping[:]==b'A'*SIZE
        address=ctypes.addressof(ctypes.c_char.from_buffer(mapping))
        with open('/proc/self/pagemap','rb',buffering=0) as pm:
            raw=os.pread(pm.fileno(),SIZE//PAGE*8,address//PAGE*8)
        entries=struct.unpack('<'+str(SIZE//PAGE)+'Q',raw)
        assert all(e&(1<<63) and e&((1<<55)-1) for e in entries)
        pfns=[e&((1<<55)-1) for e in entries]
        info,pages=metadata()
        assert len(set(pfns))==len(pfns) and all(pages.get(p,0) and not pages[p]&1 for p in pfns)
        tags={str(p):pages[p] for p in pfns}
    save('written',dict(timestamp_ns=time.time_ns(),size_bytes=SIZE,guest_pfns=pfns,
        lifetime_tags=tags,metadata_header=info,locked_file_bytes=0,workload='buffered write and mmap read; mapping then closed'))
    if dynamic:
        wait('evict')
        with path.open('rb') as f:os.posix_fadvise(f.fileno(),0,SIZE,os.POSIX_FADV_DONTNEED)
        save('evicted',dict(timestamp_ns=time.time_ns(),size_bytes=SIZE,
            operation='POSIX_FADV_DONTNEED on this clean closed file only'))
        wait('reread')
        with path.open('rb') as f:assert f.read()==b'A'*SIZE
        save('reread',dict(timestamp_ns=time.time_ns(),size_bytes=SIZE,
            workload='explicit buffered workload read after targeted eviction'))
        wait('finish');path.unlink()
        # The file has been removed, so save this non-query receipt separately.
        (out/'result.json').write_text(json.dumps(dict(status='PASS',locked_file_bytes=0,vm_pause=False,
            version='dynamic-file-v1'))+'\n')
        (out/'done').touch()
        return
    wait('read')
    with path.open('rb') as f:assert f.read()==b'A'*SIZE
    save('read-done',dict(timestamp_ns=time.time_ns(),size_bytes=SIZE,metadata_header=metadata()[0]))
    wait('evict')
    with path.open('rb') as f:os.posix_fadvise(f.fileno(),0,SIZE,os.POSIX_FADV_DONTNEED)
    info,pages=metadata()
    retained=sum(pages.get(p)==tags[str(p)] for p in pfns)
    save('evicted',dict(timestamp_ns=time.time_ns(),same_lifetime_pages_remaining=retained,
        removed_or_reused_pages=len(pfns)-retained,metadata_header=info,
        operation='POSIX_FADV_DONTNEED for this closed, clean 16 MiB file only; no global drop_caches'))
    assert retained<len(pfns), 'targeted eviction removed no known cache pages'
    wait('finish');path.unlink()
    save('result',dict(status='PASS',locked_file_bytes=0,vm_pause=False))
    (out/'done').touch()


if __name__=='__main__':main()
