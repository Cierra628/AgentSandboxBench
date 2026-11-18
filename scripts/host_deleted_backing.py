"""Opt-in deleted TMPFS backing query. No payload reads, mmap or reclaim calls."""
from contextlib import ExitStack
import ctypes
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time

from host_special_metadata import mapping_line


class CacheRange(ctypes.Structure):
    _fields_=[('off',ctypes.c_uint64),('len',ctypes.c_uint64)]


class CacheStat(ctypes.Structure):
    _fields_=[(k,ctypes.c_uint64) for k in ('nr_cache','nr_dirty','nr_writeback','nr_evicted','nr_recently_evicted')]


def cachestat(fd, *, offset=0, length=0):
    if (type(offset) is not int or type(length) is not int or offset<0 or length<0 or
            offset>=2**64 or length>=2**64 or offset+length>=2**64):
        raise ValueError('cachestat range must fit unsigned 64-bit bytes without overflow')
    if platform.machine()!='x86_64':
        raise NotImplementedError('cachestat syscall number is verified only for x86_64')
    libc=ctypes.CDLL(None,use_errno=True)
    libc.syscall.restype=ctypes.c_long
    libc.syscall.argtypes=[ctypes.c_long,ctypes.c_uint,ctypes.POINTER(CacheRange),ctypes.POINTER(CacheStat),ctypes.c_uint]
    bounds=CacheRange(offset,length);value=CacheStat()
    if libc.syscall(451,fd,ctypes.byref(bounds),ctypes.byref(value),0):
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    return {key:getattr(value,key) for key,_ in value._fields_}


def _metadata(fd):
    s=os.fstat(fd);fields=(ctypes.c_long*32)()
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.fstatfs(fd,ctypes.byref(fields)):
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    return dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mode=s.st_mode,
                nlink=s.st_nlink,filesystem_magic=fields[0])


def _validate_region(proc,region):
    root=proc/str(region['pid'])
    ticks=(root/'stat').read_text().rsplit(')',1)[1].split()[19]
    if ticks!=str(region['start_ticks']):raise ProcessLookupError('PID start identity changed')
    lines=(root/'maps').read_text().splitlines()
    found=next((r for line in lines
                if (r:=mapping_line(line)) is not None and
                (r['start'],r['end'])==(region['start'],region['end'])),None)
    if found is None or any(found[k]!=region[k] for k in found):
        # Use this same maps read, never retry to find a more convenient match.
        overlaps=[r for line in lines if (r:=mapping_line(line)) is not None
                  and r['start']<region['end'] and region['start']<r['end']] if found is None else []
        changed=({k:dict(expected=region[k],observed=found[k]) for k in found if found[k]!=region[k]}
                 if found is not None else dict(range=dict(expected=[region['start'],region['end']],observed=None)))
        error=ValueError('source mapping identity changed')
        error.mapping_diagnostic=dict(reason='EXACT_RANGE_MISSING' if found is None else 'FIELDS_CHANGED',
            maps_source=str(root/'maps'),expected=dict(region),observed=found,
            changed_fields=changed,overlapping_regions=overlaps)
        raise error


def query_deleted_backing(region,*,proc=Path('/proc')):
    """Query one live mapping; caller supplies PID start identity and maps fields.

    Run in a bounded helper process when integrating with a collector. Results
    are sequential cache observations, not atomic memory totals or page owners.
    """
    begin=time.monotonic_ns()
    value=dict(status='UNKNOWN',device=region['device'],inode=region['inode'],
        source_region=dict(region),start_monotonic_ns=begin,page_size=os.sysconf('SC_PAGE_SIZE'),
        cached_bytes=None,whole_backing_cache_bytes=None,cache_counters=None,
        page_charge_owner='NOT_MEASURED',
        scope='Whole ordinary TMPFS cache-index observation, not PSS or a simultaneous physical union. Swap entries leave full residency unknown.')
    try:
        if not region['mapping_path'].endswith(' (deleted)'):
            raise ValueError('expected a deleted backing mapping')
        _validate_region(proc,region)
        path=proc/str(region['pid'])/'map_files'/f"{region['start']:x}-{region['end']:x}"
        with ExitStack() as stack:
            identity_fd=os.open(path,os.O_PATH|os.O_CLOEXEC);stack.callback(os.close,identity_fd)
            identity=_metadata(identity_fd);value['metadata']=identity
            if (identity['device'],identity['inode'])!=(region['device'],region['inode']):
                raise ValueError('map_files backing identity differs')
            if not stat.S_ISREG(identity['mode']) or identity['filesystem_magic']!=0x01021994:
                value['status']='UNSUPPORTED_BACKING'
                value['error']='only ordinary TMPFS backing is queried'
                return value
            fd=os.open(Path('/proc/self/fd')/str(identity_fd),os.O_RDONLY|os.O_CLOEXEC);stack.callback(os.close,fd)
            if _metadata(fd)!=identity:raise ValueError('reopened backing identity or metadata differs')
            value['cache_counters']=cachestat(fd)
            if _metadata(fd)!=identity:raise ValueError('backing metadata changed during query')
            _validate_region(proc,region)
            value['cached_bytes']=value['cache_counters']['nr_cache']*value['page_size']
            value['status']='PARTIAL_SWAP_ENTRIES' if value['cache_counters']['nr_evicted'] else 'OBSERVED'
            if value['status']=='OBSERVED':value['whole_backing_cache_bytes']=value['cached_bytes']
    except (OSError,ValueError,NotImplementedError) as error:
        value.update(status='UNKNOWN',error=repr(error),errno=getattr(error,'errno',None),
                     cached_bytes=None,whole_backing_cache_bytes=None)
        if hasattr(error,'mapping_diagnostic'):value['mapping_diagnostic']=error.mapping_diagnostic
    finally:value['end_monotonic_ns']=time.monotonic_ns()
    return value


def query_deleted_backings(regions,*,proc=Path('/proc')):
    """Once per backing device/inode; failed first identity stays unknown."""
    cpu=time.process_time_ns();unique={}
    for region in regions:unique.setdefault((region['device'],region['inode']),[]).append(region)
    objects=[]
    for entries in unique.values():
        value=query_deleted_backing(entries[0],proc=proc)
        value['mapping_count']=len(entries);objects.append(value)
    complete=bool(objects) and all(v['status']=='OBSERVED' for v in objects)
    return dict(status='OBSERVED' if complete else 'INCOMPLETE' if objects else 'NOT_REQUESTED',
                objects=objects,worker_cpu_ns=time.process_time_ns()-cpu,whole_backing_cache_bytes=sum(v['whole_backing_cache_bytes'] for v in objects) if complete else None,
                page_charge_owner='NOT_MEASURED')


def query_deleted_backings_bounded(regions,*,timeout=4):
    """One owned worker, killed and reaped on timeout; never signal the target."""
    begin=time.monotonic_ns();cpu=time.thread_time_ns()
    result=dict(status='NOT_REQUESTED' if not regions else 'ERROR',objects=[],
        whole_backing_cache_bytes=None,page_charge_owner='NOT_MEASURED',
        start_monotonic_ns=begin,timeout_seconds=timeout,worker_pid=None,
        worker_returncode=None,worker_reaped=None,worker_cpu_ns=None)
    try:
        if not regions:return result
        with subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--worker'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as worker:
            result['worker_pid']=worker.pid
            try:
                stdout,stderr=worker.communicate(json.dumps(regions),timeout=timeout)
            except subprocess.TimeoutExpired:
                worker.kill();worker.communicate()
                result.update(status='TIMEOUT',error='deleted backing worker timed out')
            else:
                if worker.returncode:raise ValueError('worker exit '+str(worker.returncode)+': '+stderr[-2048:])
                value=json.loads(stdout)
                expected={(r['device'],r['inode']) for r in regions}
                actual=[(v['device'],v['inode']) for v in value['objects']]
                if set(actual)!=expected or len(actual)!=len(expected):raise ValueError('worker object identities differ')
                if value['status'] not in ('OBSERVED','INCOMPLETE'):raise ValueError('unexpected worker status')
                complete=all(v['status']=='OBSERVED' and v['whole_backing_cache_bytes'] is not None for v in value['objects'])
                total=sum(v['whole_backing_cache_bytes'] for v in value['objects']) if complete else None
                if value['whole_backing_cache_bytes']!=total or (value['status']=='OBSERVED')!=complete:
                    raise ValueError('worker cache total differs from object observations')
                result.update(value)
            finally:
                result.update(worker_returncode=worker.returncode,worker_reaped=worker.returncode is not None)
    except (OSError,ValueError,KeyError,TypeError) as error:
        result.update(status='ERROR',error=repr(error),objects=[],whole_backing_cache_bytes=None)
    finally:
        result['end_monotonic_ns']=time.monotonic_ns()
        result['cpu_ns']=time.thread_time_ns()-cpu
    return result


if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('usage: host_deleted_backing.py --worker')
    print(json.dumps(query_deleted_backings(json.load(sys.stdin))))
