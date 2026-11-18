"""One explicit positive calibration: 64 KiB synthetic disk file, no VM.

Only calibration setup writes and faults the synthetic file. The actual query
still uses its untouched PROT_NONE mapping. Raw PFNs stay local.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import time
import traceback

import numpy as np

from host_file_pfn_query import PAGE, FOLIO, MASK, file_identity, observe, decode_rows
from host_schedule_files import filesystem_magic, permission_evidence
from kernel_cache_metadata import PRESENT, SWAPPED, PHYSICAL_MASK

PAGES=16
VERSION='host-file-pfn-positive-v1'


def compare(before,after,rows):
    """Require a stable mapped reference and an exact offset-by-offset match."""
    if before.shape!=(PAGES,) or after.shape!=before.shape or rows.shape!=(PAGES,4):
        raise ValueError('reference/query dimensions differ from the fixed 16-page range')
    if np.any(((before^after)&PHYSICAL_MASK)!=0):
        raise ValueError('reference physical mapping changed; no replacement or retry')
    if (np.any((before&PRESENT)==0) or np.any(before&SWAPPED) or
            np.any((before&(1<<61))==0) or np.any((before&MASK)==0)):
        raise ValueError('reference is not present file-backed memory with visible PFNs')
    summary=decode_rows(rows)
    if not np.array_equal(rows[:,0],np.arange(PAGES,dtype='<u8')):
        raise ValueError('query file offsets do not exactly match reference')
    if np.any(rows[:,2]!=1) or np.any((rows[:,3]&np.uint64(~MASK & ((1<<64)-1)))!=FOLIO):
        raise ValueError('positive calibration requires one valid folio return per page')
    if not np.array_equal(rows[:,3]&MASK,before&MASK):
        raise ValueError('kernel-return PFN differs from independent pagemap reference')
    return dict(status='PASS',matched_pages=PAGES,unknown_pages=summary['unknown_pages'],
        unique_physical_pages=len(np.unique(before&MASK)),
        reference_physical_bytes=len(np.unique(before&MASK))*PAGE,
        query_physical_bytes=summary['confirmed_pfn_union_bytes'],
        mincore_set_pages=summary['mincore_set_pages'],
        reference_stable=True,snapshot_atomic=False)


def read_reference(address,path):
    start=time.monotonic_ns()
    fd=os.open('/proc/self/pagemap',os.O_RDONLY|os.O_CLOEXEC)
    try:raw=os.pread(fd,PAGES*8,(address//PAGE)*8)
    finally:os.close(fd)
    if len(raw)!=PAGES*8:raise ValueError('short reference pagemap read')
    with path.open('xb') as f:f.write(raw)
    return np.frombuffer(raw,dtype='<u8'),dict(start_monotonic_ns=start,
        end_monotonic_ns=time.monotonic_ns(),source='/proc/self/pagemap',
        binary=path.name,address=address,pages=PAGES,dtype='<u8')


def mapping_proof(address,info):
    for line in Path('/proc/self/maps').read_text().splitlines():
        fields=line.split(maxsplit=5);start,end=(int(x,16) for x in fields[0].split('-'))
        if start<=address and address+PAGES*PAGE<=end:
            major,minor=(int(x,16) for x in fields[3].split(':'))
            if (fields[1]!='r--s' or int(fields[2],16)!=0 or
                    (major,minor)!=(os.major(info.st_dev),os.minor(info.st_dev)) or
                    int(fields[4])!=info.st_ino):
                raise ValueError('independent reference mapping has wrong file identity or permissions')
            return line
    raise ValueError('independent reference mapping missing')


def calibrate(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    result=dict(version=VERSION,status='FAIL',pages=PAGES,page_size=PAGE,
        business_runs=0,sandbox_starts=0,query_invocations=0,
        scope='one newly written synthetic ordinary file; not workload cache attribution',
        complete_sandbox_physical_bytes=None)
    fd=None;address=None;libc=None;stage='permission_and_budget';started=time.monotonic_ns()
    try:
        result['permission']=permission_evidence()
        result['free_disk_before_bytes']=os.statvfs(output).f_bavail*os.statvfs(output).f_frsize
        if result['free_disk_before_bytes']<64*2**20:raise ValueError('less than 64 MiB free for bounded calibration')
        stage='create_synthetic_file';path=(output/'synthetic.bin').resolve()
        payload=b''.join(bytes([i+1])*PAGE for i in range(PAGES))
        with path.open('xb') as f:f.write(payload)
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW);info=os.fstat(fd)
        if filesystem_magic(fd) in (0x01021994,0x958458f6):
            raise ValueError('calibration must use ordinary disk storage, not TMPFS/hugetlb')
        result.update(file=str(path),identity=file_identity(info),process_id=os.getpid(),
            process_start_ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],
            host_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        libc=ctypes.CDLL(None,use_errno=True)
        libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
        libc.mmap.restype=ctypes.c_void_p
        libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        stage='map_reference_and_fault_synthetic_pages'
        pointer=libc.mmap(None,PAGES*PAGE,1,1,fd,0)  # PROT_READ, MAP_SHARED reference only.
        if pointer==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'reference mmap failed')
        address=pointer
        # Controlled calibration preparation; never part of the production query.
        warm_start=time.monotonic_ns()
        values=[ctypes.c_ubyte.from_address(address+i*PAGE).value for i in range(PAGES)]
        if values!=list(range(1,PAGES+1)):raise ValueError('synthetic reference page contents differ')
        result['reference_prepare_elapsed_ns']=time.monotonic_ns()-warm_start
        result['mapping_before']=mapping_proof(address,info)
        stage='reference_before';before,record=read_reference(address,output/'reference-before.bin')
        result['reference_before']=record
        with (output/'reference-before.json').open('x') as f:json.dump(record,f,indent=2);f.write('\n')
        # Preflight reference visibility before the sole traced query.
        if (np.any((before&PRESENT)==0) or np.any(before&SWAPPED) or
                np.any((before&(1<<61))==0) or np.any((before&MASK)==0)):
            raise ValueError('reference pages not resident or host PFNs hidden')
        stage='host_file_query';result['query_invocations']=1
        query=observe(path,(info.st_dev,info.st_ino),0,PAGES,output/'query')
        result['query_status']=query['status']
        if query['status']!='OBSERVED_NONATOMIC':raise RuntimeError('query did not complete: '+query['status'])
        stage='reference_after';after,record=read_reference(address,output/'reference-after.bin')
        result['reference_after']=record;result['mapping_after']=mapping_proof(address,info)
        with (output/'reference-after.json').open('x') as f:json.dump(record,f,indent=2);f.write('\n')
        if file_identity(os.fstat(fd))!=result['identity'] or file_identity(path.lstat())!=result['identity']:
            raise ValueError('synthetic file changed during calibration')
        stage='compare_physical_pages'
        chunk=query['chunks'][0]
        rows=np.fromfile(output/'query'/chunk['binary'],dtype='<u8').reshape(-1,4)
        result['comparison']=compare(before,after,rows)
        result['status']='PASS'
    except Exception as exc:
        result.update(error=repr(exc),failed_stage=stage)
        with (output/'failure.txt').open('x') as f:f.write(traceback.format_exc())
    finally:
        cleanup=dict(reference_unmapped=address is None,reference_fd_closed=fd is None)
        try:
            if address is not None:
                if libc.munmap(address,PAGES*PAGE):raise OSError(ctypes.get_errno(),'reference munmap failed')
                cleanup['reference_unmapped']=True
        except Exception as exc:result.update(status='FAIL',cleanup_error=repr(exc))
        try:
            if fd is not None:os.close(fd);cleanup['reference_fd_closed']=True
        except Exception as exc:result.update(status='FAIL',fd_cleanup_error=repr(exc))
        result.update(cleanup=cleanup,elapsed_ns=time.monotonic_ns()-started,
            free_disk_after_bytes=os.statvfs(output).f_bavail*os.statvfs(output).f_frsize)
        with (output/'result.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live-calibration',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not a.live_calibration:p.error('explicit --live-calibration required; importing does not collect')
    result=calibrate(a.output)
    print('status='+result['status']+' result_dir='+str(a.output))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
