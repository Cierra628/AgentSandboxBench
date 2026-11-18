"""One bounded sub-MiB self-owned memfd/dev-zero fixture; no sandbox or task I/O."""
import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from host_deleted_backing import cachestat,query_deleted_backings,query_deleted_backings_bounded
from host_special_metadata import mapping_line,attach_smaps

ROOT=Path(__file__).resolve().parents[1]
PAGE=os.sysconf('SC_PAGE_SIZE')
LIBC=ctypes.CDLL(None,use_errno=True)
LIBC.mmap.restype=ctypes.c_void_p
LIBC.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
LIBC.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
LIBC.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_ubyte)]


def maps():
    return [r for line in Path('/proc/self/maps').read_text().splitlines() if (r:=mapping_line(line)) is not None]


def open_fds():
    result={}
    for p in list(Path('/proc/self/fd').iterdir()):
        try:result[int(p.name)]=(p.stat().st_dev,p.stat().st_ino)
        except FileNotFoundError:pass
    return result


def mincore(address,length):
    vector=(ctypes.c_ubyte*((length+PAGE-1)//PAGE))()
    if LIBC.mincore(address,length,vector):
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    return [i for i,v in enumerate(vector) if v&1]


def mincore_fd(fd,length):
    address=LIBC.mmap(None,length,0,1,fd,0)  # PROT_NONE, MAP_SHARED; never access the mapping.
    if address==ctypes.c_void_p(-1).value:
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    try:return mincore(address,length)
    finally:
        if LIBC.munmap(address,length):raise OSError(ctypes.get_errno(),'fixture mincore unmap failed')


def hold_worker(ready):
    """Fixture-only blocked child: hold this owner's backing open until killed."""
    from host_deleted_backing import _metadata,_validate_region
    region=json.load(sys.stdin)[0]
    assert region['mapping_path'].startswith('/memfd:asb-deleted-fixture')
    _validate_region(Path('/proc'),region)
    path=Path('/proc')/str(region['pid'])/'map_files'/f"{region['start']:x}-{region['end']:x}"
    identity=os.open(path,os.O_PATH|os.O_CLOEXEC)
    info=_metadata(identity)
    assert (info['device'],info['inode'])==(region['device'],region['inode']) and info['filesystem_magic']==0x01021994
    fd=os.open(Path('/proc/self/fd')/str(identity),os.O_RDONLY|os.O_CLOEXEC)
    ready.write_text(json.dumps(dict(pid=os.getpid(),device=info['device'],inode=info['inode'],open_backing_fd=fd))+'\n')
    while True:signal.pause()


def check_timeout(regions,out):
    from unittest.mock import patch
    ready=out/'timeout-worker-ready.json';popen=subprocess.Popen
    def held_worker(argv,*args,**kwargs):
        assert argv[-1]=='--worker'
        return popen([sys.executable,'-B',str(Path(__file__).resolve()),'--hold-worker',str(ready)],*args,**kwargs)
    with patch('host_deleted_backing.subprocess.Popen',side_effect=held_worker):
        result=query_deleted_backings_bounded(regions,timeout=2)
    assert ready.exists(),'blocked child did not reach the open-backing stage'
    opened=json.loads(ready.read_text())
    assert opened['pid']==result['worker_pid']
    assert (opened['device'],opened['inode'])==(regions[0]['device'],regions[0]['inode'])
    assert result['status']=='TIMEOUT' and result['whole_backing_cache_bytes'] is None
    assert result['worker_returncode']==-signal.SIGKILL and result['worker_reaped']
    try:os.waitpid(result['worker_pid'],os.WNOHANG)
    except ChildProcessError:result['independent_reap_check']='PASS'
    else:raise AssertionError('timed-out child had not been reaped')
    return result


def run(out,*,bounded=False):
    out.mkdir(parents=True,exist_ok=False)
    value=dict(status='FAIL',page_size=PAGE,logical_test_data_bytes=10*PAGE,
        host_kernel=os.uname().release,new_sandbox_actions=0,task_files_read=0,
        query_scope='Self-created memfd and shared dev-zero only; no global cache drops, locking, preloading, service or device changes.',
        checks={},whole_host_physical_saving='NOT_MEASURED',page_charge_owner='NOT_MEASURED')
    if bounded:value['query_mode']='bounded-child-reading-owner'
    stage='preflight';fds=set();created=[];regions=[];before_fds=open_fds()
    def own_fd(fd):fds.add(fd);return fd
    def close_fd(fd):os.close(fd);fds.remove(fd)
    def map_rw(fd,length):
        address=LIBC.mmap(None,length,3,1,fd,0)  # RW MAP_SHARED, for fixture setup only.
        if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'fixture mmap failed')
        created.append((address,length));return address
    def timed_out(signum,frame):raise TimeoutError('owned small fixture timed out')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,timed_out)
    try:
        assert 0<10*PAGE<=2**20
        assert shutil.disk_usage(ROOT).free>2**20
        cap=int(next(l.split(':',1)[1].strip() for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:')),16)
        assert cap&(1<<3),'CAP_FOWNER required for non-fabricated mincore results'
        assert cap&((1<<21)|(1<<40)),'map_files requires CAP_SYS_ADMIN or CAP_CHECKPOINT_RESTORE'
        value['checks']['resource_budget']='PASS'
        stage='create-memfd'
        fd=own_fd(os.memfd_create('asb-deleted-fixture',os.MFD_CLOEXEC))
        os.ftruncate(fd,8*PAGE)
        assert os.pwrite(fd,b'A'*PAGE,0)==PAGE and os.pwrite(fd,b'B'*PAGE,7*PAGE)==PAGE
        a=map_rw(fd,PAGE);alias=map_rw(fd,PAGE)
        ctypes.c_char.from_address(a).value=b'C'  # Setup writes only this fixture's first page.
        memfd_before=mincore_fd(fd,8*PAGE)
        assert 0 in memfd_before and 7 in memfd_before
        close_fd(fd)
        stage='create-shared-dev-zero'
        fd=own_fd(os.open('/dev/zero',os.O_RDWR|os.O_CLOEXEC))
        zero=map_rw(fd,2*PAGE);close_fd(fd)
        ctypes.c_char.from_address(zero).value=b'D'
        zero_before=mincore(zero,2*PAGE)
        ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]
        current=maps()
        for address,length in ((a,PAGE),(alias,PAGE),(zero,2*PAGE)):
            r=next(r for r in current if (r['start'],r['end'])==(address,address+length))
            regions.append(dict(r,pid=os.getpid(),start_ticks=ticks))
        assert regions[0]['mapping_path'].startswith('/memfd:') and regions[2]['mapping_path']=='/dev/zero (deleted)'
        keys={(r['device'],r['inode']) for r in regions}
        assert len(keys)==2 and not keys & set(open_fds().values())
        assert not any((r['device'],r['inode'])==(regions[0]['device'],regions[0]['inode']) and
                       r['offset']<=7*PAGE<r['offset']+r['end']-r['start'] for r in current)
        value['checks']['original_descriptors_closed_and_page7_unmapped']='PASS'
        attach_smaps(Path('/proc/self'),regions)
        value['setup']=dict(memfd_cache_indices_before=memfd_before,dev_zero_cache_indices_before=zero_before,
                           memfd_mapped_rss_bytes=sum(r['smaps_bytes']['Rss'] for r in regions[:2]))
        stage='opath-negative-check'
        link=Path('/proc/self/map_files')/f'{a:x}-{a+PAGE:x}'
        fd=own_fd(os.open(link,os.O_PATH|os.O_CLOEXEC))
        try:
            try:cachestat(fd)
            except OSError as error:
                assert error.errno==errno.EBADF
                value['opath_errno']=error.errno
            else:raise AssertionError('O_PATH unexpectedly accepted')
        finally:close_fd(fd)
        value['checks']['opath_rejected']='PASS'
        stage='query-closed-descriptor-backings'
        begin=time.monotonic_ns()
        query=query_deleted_backings_bounded(regions) if bounded else query_deleted_backings(regions)
        elapsed=time.monotonic_ns()-begin
        value['query']=query;value['query_ns']=elapsed
        assert query['status']=='OBSERVED',query
        if bounded:
            assert query['worker_pid']!=os.getpid() and query['worker_reaped'] and query['worker_returncode']==0
            assert all(o['source_region']['pid']==os.getpid() for o in query['objects'])
            value['checks']['cross_process_owner_identity_and_reap']='PASS'
        assert len(query['objects'])==2 and sorted(o['mapping_count'] for o in query['objects'])==[1,2]
        mem=next(o for o in query['objects'] if o['inode']==regions[0]['inode'] and o['device']==regions[0]['device'])
        dev=next(o for o in query['objects'] if o is not mem)
        assert mem['metadata']['size']==8*PAGE and dev['metadata']['size']==2*PAGE
        assert all(o['metadata']['nlink']==0 and o['metadata']['filesystem_magic']==0x01021994 for o in (mem,dev))
        assert mem['whole_backing_cache_bytes']==len(memfd_before)*PAGE
        assert dev['whole_backing_cache_bytes']==len(zero_before)*PAGE
        assert mem['whole_backing_cache_bytes']>value['setup']['memfd_mapped_rss_bytes']
        assert query['whole_backing_cache_bytes']==mem['whole_backing_cache_bytes']+dev['whole_backing_cache_bytes']
        assert all(o['cache_counters']['nr_evicted']==0 and o['page_charge_owner']=='NOT_MEASURED' for o in (mem,dev))
        value['checks'].update(whole_backing_includes_unmapped_cache='PASS',deduplicated_objects='PASS',tmpfs_identity='PASS')
        if bounded:
            stage='injected-held-backing-timeout'
            value['timeout_query']=check_timeout(regions,out)
            value['checks']['timeout_after_backing_open_killed_and_reaped']='PASS'
        stage='independent-residency-cross-check'
        fd=own_fd(os.open(link,os.O_RDONLY|os.O_CLOEXEC))
        try:memfd_after=mincore_fd(fd,8*PAGE)
        finally:close_fd(fd)
        zero_after=mincore(zero,2*PAGE)
        assert memfd_after==memfd_before and zero_after==zero_before
        assert open_fds()==before_fds
        value['checks']['residency_unchanged_and_query_handles_closed']='PASS'
        value['cross_check']=dict(memfd_cache_indices_after=memfd_after,dev_zero_cache_indices_after=zero_after)
        value['status']='PASS'
    except Exception as error:
        value.update(status='FAIL',failed_stage=stage,error=repr(error))
    finally:
        cleanup_errors=[]
        for address,length in reversed(created):
            if LIBC.munmap(address,length):cleanup_errors.append('munmap failed')
        for fd in list(fds):
            try:close_fd(fd)
            except OSError as error:cleanup_errors.append(repr(error))
        remaining=maps();keys={(r['device'],r['inode']) for r in regions}
        cleanup=dict(errors=cleanup_errors,owned_fds_closed=not fds,
                     initial_fd_set_restored=open_fds()==before_fds,
                     owned_mappings_absent=not any((r['device'],r['inode']) in keys for r in remaining))
        value['cleanup']=cleanup
        if cleanup_errors or not all(cleanup[k] for k in ('owned_fds_closed','initial_fd_set_restored','owned_mappings_absent')):
            value['status']='FAIL'
        (out/'result.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print('status='+value['status']+' stage='+value.get('failed_stage','complete')+' result_dir='+str(out))
    return 0 if value['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'.artifacts/host-deleted-backing'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())))
    parser.add_argument('--bounded',action='store_true',help='Query from an owned child and test timeout after backing open')
    parser.add_argument('--hold-worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.hold_worker:hold_worker(args.hold_worker)
    else:sys.exit(run(args.output.resolve(),bounded=args.bounded))
