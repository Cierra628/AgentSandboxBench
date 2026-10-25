"""One owned diagnostic guest: periodic clean-file reclaim and bounded demand."""
import argparse
import ctypes
import json
import mmap
import os
from pathlib import Path
import resource
import struct
import threading
import time
import traceback

from kernel_cache_metadata import decode_header, decode

PAGE=4096
MIB=2**20
PERIOD=.05
PHASES=[('observe',None,3),('high',128*MIB,8),('low',64*MIB,8),('high-return',128*MIB,8)]


def counters():
    begin=time.monotonic_ns()
    header=decode_header(Path('/proc/asb_pagecache').read_bytes())
    mem={p[0].rstrip(':'):int(p[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()
         if len(p:=line.split())==3 and p[2]=='kB'}
    return dict(start_ns=begin,end_ns=time.monotonic_ns(),cache_bytes=header['records']*PAGE,
                shmem_metadata_bytes=header['shmem_pages']*PAGE,metadata=header,
                memory={k:mem[k] for k in ('Cached','Shmem','AnonPages','SwapTotal','SwapFree','Dirty','Writeback','Slab')})


def reclaim(current,target):
    requested=max(0,(current-target+PAGE-1)//PAGE)
    if not requested:return dict(requested_pages=0,invalidated_entries=0,scanned_folios=0,elapsed_ns=0,write_ns=0)
    begin=time.monotonic_ns()
    Path('/proc/asb_cache_reclaim').write_text(str(requested)+'\n')
    elapsed=time.monotonic_ns()-begin
    stats={k:int(v) for k,v in (s.split() for s in Path('/proc/asb_cache_reclaim').read_text().splitlines())}
    assert stats['requested_pages']==requested
    return dict(stats,write_ns=elapsed)


def capabilities():
    p=Path('/sys/module/damon_reclaim/parameters')
    params={v.name:v.read_text().strip() for v in p.iterdir() if v.is_file()}
    cgroup=Path('/sys/fs/cgroup')
    return dict(kernel=os.uname().release,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        cmdline=Path('/proc/cmdline').read_text().strip(),damon=params,
        cgroup_mounts=[s for s in Path('/proc/mounts').read_text().splitlines() if ' cgroup' in s],
        cgroup_controllers=(cgroup/'cgroup.controllers').read_text().strip() if (cgroup/'cgroup.controllers').exists() else None,
        root_memory_reclaim_exists=(cgroup/'memory.reclaim').exists(),
        root_memory_reclaim_writable=os.access(cgroup/'memory.reclaim',os.W_OK),
        process_cgroup=Path('/proc/self/cgroup').read_text(),swaps=Path('/proc/swaps').read_text(),
        clean_reclaim_interface=Path('/proc/asb_cache_reclaim').exists())


def set_damon(enabled):
    p=Path('/sys/module/damon_reclaim/parameters')
    (p/'enabled').write_text(enabled+'\n')
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        now=(p/'enabled').read_text().strip()
        pid=int((p/'kdamond_pid').read_text())
        if now==enabled and (pid==-1 if enabled=='N' else pid>0):return dict(enabled=now,kdamond_pid=pid)
        time.sleep(.05)
    raise TimeoutError('owned guest DAMON state did not settle')


def checkpoint_damon(path,action):
    assert path==Path('/workspace/asb-damon-before-checkpoint.json')
    before=capabilities();assert before['kernel']=='6.1.175-asb-cache-v3-reclaim'
    if action=='prepare':
        original=before['damon']['enabled']
        assert original in ('Y','N')
        value=dict(status='PASS',original_enabled=original,capabilities_before=before,before=counters())
        try:
            value['disabled']=set_damon('N')
            value['after']=counters()
            path.write_text(json.dumps(value,indent=2)+'\n')
        except BaseException:
            set_damon(original)
            raise
        return value
    assert action=='observe'
    saved=json.loads(path.read_text());assert saved['status']=='PASS'
    assert saved['original_enabled'] in ('Y','N')
    assert before['damon']['enabled']=='N' and int(before['damon']['kdamond_pid'])==-1
    return dict(status='PASS',original_enabled=saved['original_enabled'],
                damon=before['damon'],observed=counters())


def resident_pfns(memory):
    address=ctypes.addressof(ctypes.c_char.from_buffer(memory))
    first=address//PAGE;count=(address%PAGE+len(memory)+PAGE-1)//PAGE
    with Path('/proc/self/pagemap').open('rb',buffering=0) as stream:
        raw=os.pread(stream.fileno(),count*8,first*8)
    entries=[v[0] for v in struct.iter_unpack('<Q',raw)]
    assert all(v>>63 for v in entries), 'anonymous guard not resident'
    return {v&((1<<55)-1) for v in entries}


def main(directory):
    directory.mkdir(exist_ok=False)
    assert directory.parent==Path('/workspace') and directory.name=='asb-cache-capacity'
    before=capabilities();assert before['kernel']=='6.1.175-asb-cache-v3-reclaim'
    assert before['clean_reclaim_interface']
    (directory/'capabilities-before.json').write_text(json.dumps(before,indent=2)+'\n')
    status='FAIL';error=None;reader=None;stop=threading.Event();guard=None;original=before['damon']['enabled']
    result=dict(status='FAIL',vm_pause=False,hard_limit=False,period_seconds=PERIOD,phases=[],reader={})
    try:
        result['damon_disabled']=set_damon('N')
        assert len(before['swaps'].strip().splitlines())==1, 'guest swap must be inactive for this validation'
        guard=mmap.mmap(-1,8*MIB,flags=mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS)
        guard[:]=b'G'*(8*MIB)
        pfns=resident_pfns(guard);_,members=decode(Path('/proc/asb_pagecache').read_bytes())
        assert not pfns.intersection(members), 'anonymous guard was counted as file cache'
        shmem=Path('/dev/shm/asb-capacity-guard');shmem.write_bytes(b'S'*MIB)
        block_size=512*1024;pattern=bytes(range(256))*(block_size//256)
        fixture=directory/'demand.bin';size=256*MIB
        with fixture.open('wb',buffering=0) as stream:
            for offset in range(0,size,block_size):
                stream.write((offset//block_size).to_bytes(8,'little')+pattern[8:])
            os.fsync(stream.fileno())
        initial=counters();assert initial['cache_bytes']>128*MIB, 'fixture did not create cache demand above high target'
        stats=dict(bytes=0,reads=0,read_elapsed_ns=0,max_read_ns=0,error=None)
        def demand():
            try:
                with fixture.open('rb',buffering=0) as stream:
                    offset=0
                    while not stop.is_set():
                        started=time.monotonic_ns();value=os.pread(stream.fileno(),block_size,offset)
                        used=time.monotonic_ns()-started
                        assert value[:8]==(offset//block_size).to_bytes(8,'little') and value[8:]==pattern[8:], 'fixture data changed'
                        stats['bytes']+=len(value);stats['reads']+=1;stats['read_elapsed_ns']+=used
                        stats['max_read_ns']=max(stats['max_read_ns'],used)
                        offset=(offset+block_size)%size
                        stop.wait(.02)
            except BaseException as exc:stats['error']=repr(exc);stop.set()
        reader=threading.Thread(target=demand,name='bounded-file-demand');reader.start()
        with (directory/'samples.jsonl').open('x',buffering=1) as log:
            for name,target,duration in PHASES:
                started=time.monotonic_ns();phase=dict(name=name,target_bytes=target,start_ns=started,reader_start=dict(stats))
                result['phases'].append(phase)
                while time.monotonic_ns()-started<duration*10**9:
                    assert reader.is_alive() and not stats['error'],stats
                    tick=time.monotonic_ns();cpu=time.thread_time_ns()
                    left=counters()
                    action=reclaim(left['cache_bytes'],target) if target is not None else None
                    right=counters() if action and action['requested_pages'] else left
                    value=dict(phase=name,target_bytes=target,before=left,after=right,action=action,
                        reader_bytes=stats['bytes'],reader_reads=stats['reads'],
                        controller_cpu_ns=time.thread_time_ns()-cpu,cycle_elapsed_ns=time.monotonic_ns()-tick)
                    log.write(json.dumps(value)+'\n')
                    stop.wait(max(0,PERIOD-value['cycle_elapsed_ns']/1e9))
                phase.update(end_ns=time.monotonic_ns(),reader_end=dict(stats))
        stop.set();reader.join(timeout=3);assert not reader.is_alive() and not stats['error']
        assert stats['bytes']>=2*size, 'fixture was not read through twice'
        assert guard[:]==b'G'*(8*MIB) and shmem.read_bytes()==b'S'*MIB
        last_pfns=resident_pfns(guard);_,members=decode(Path('/proc/asb_pagecache').read_bytes())
        assert not last_pfns.intersection(members)
        result.update(initial=initial,final=counters(),reader=dict(stats),
            anonymous_guard_bytes=8*MIB,anonymous_guard_resident_pages_before=len(pfns),
            anonymous_guard_resident_pages_after=len(last_pfns),anonymous_excluded_from_cache=True,
            shmem_guard_bytes=MIB,content_checks='PASS')
        status='PASS'
    except BaseException as exc:
        error=repr(exc);(directory/'failure.txt').write_text(traceback.format_exc())
    finally:
        stop.set()
        if reader:reader.join(timeout=3)
        try:result['damon_restored']=set_damon(original)
        except BaseException as exc:status='FAIL';result['restore_error']=repr(exc)
        if guard:guard.close()
        usage=resource.getrusage(resource.RUSAGE_SELF)
        result.update(status=status,error=error,cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime,
                      maxrss_kib=usage.ru_maxrss,capabilities_after=capabilities())
        (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if status=='PASS' else 1


def serve(directory,target_mib,restore_damon=None,*,python_target=False):
    assert directory==Path('/workspace/asb-task-capacity')
    assert (type(target_mib) is int and target_mib>0) if python_target else target_mib in (None,64,128)
    before=capabilities();assert before['kernel']=='6.1.175-asb-cache-v3-reclaim' and before['clean_reclaim_interface']
    if restore_damon is not None:
        assert restore_damon in ('Y','N') and before['damon']['enabled']=='N' and int(before['damon']['kdamond_pid'])==-1
    directory.mkdir(exist_ok=False);original=before['damon']['enabled'] if restore_damon is None else restore_damon;status='FAIL';error=None
    target=None if target_mib is None else target_mib*MIB
    result=dict(status='FAIL',hard_limit=False,period_seconds=PERIOD,target_bytes=target,capabilities_before=before)
    if target is None:result['baseline_no_reclaim']=True
    if restore_damon is not None:result['parent_original_damon_enabled']=restore_damon
    try:
        result['damon_disabled']=set_damon('N')
        started=time.monotonic_ns()
        result.update(io_start_ns=started,diskstats_before=Path('/proc/diskstats').read_text(),
            vmstat_before=Path('/proc/vmstat').read_text(),
            disk_partitions=[p.name for p in Path('/sys/class/block').iterdir() if (p/'partition').exists()])
        with (directory/'samples.jsonl').open('x',buffering=1) as log:
            while not (directory/'stop').exists():
                if time.monotonic_ns()-started>180*10**9:raise TimeoutError('owned cache controller lifetime exceeded')
                tick=time.monotonic_ns();cpu=time.thread_time_ns();left=counters()
                action=(dict(requested_pages=0,invalidated_entries=0,scanned_folios=0,elapsed_ns=0,write_ns=0)
                        if target is None else reclaim(left['cache_bytes'],target))
                right=counters() if action['requested_pages'] else left
                value=dict(before=left,after=right,action=action,target_bytes=target,
                           controller_cpu_ns=time.thread_time_ns()-cpu,cycle_elapsed_ns=time.monotonic_ns()-tick)
                log.write(json.dumps(value)+'\n')
                (directory/'ready').touch()
                time.sleep(max(0,PERIOD-value['cycle_elapsed_ns']/1e9))
        status='PASS'
    except BaseException as exc:
        error=repr(exc);(directory/'failure.txt').write_text(traceback.format_exc())
    finally:
        try:
            result.update(io_end_ns=time.monotonic_ns(),diskstats_after=Path('/proc/diskstats').read_text(),
                          vmstat_after=Path('/proc/vmstat').read_text())
        except OSError as exc:status='FAIL';result['counter_error']=repr(exc)
        try:result['damon_restored']=set_damon(original)
        except BaseException as exc:status='FAIL';result['restore_error']=repr(exc)
        usage=resource.getrusage(resource.RUSAGE_SELF)
        result.update(status=status,error=error,cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime,
                      maxrss_kib=usage.ru_maxrss)
        (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if status=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--serve-target-mib',type=int,choices=(64,128))
    parser.add_argument('--serve-python-target-mib',type=int,help='Explicit Python task target in positive MiB; old Prettier choices unchanged')
    parser.add_argument('--serve-baseline',action='store_true',help='Observe at the same period with owned DAMON disabled; never request reclaim')
    parser.add_argument('--observe',action='store_true',help='Read one metadata/counter boundary without changing state')
    parser.add_argument('--restore-damon',choices=('Y','N'),help='New checkpoint comparison only; restore the saved parent state instead of the inherited disabled state')
    parser.add_argument('--checkpoint-damon',choices=('prepare','observe'))
    args=parser.parse_args()
    assert sum((args.serve_target_mib is not None,args.serve_python_target_mib is not None,args.serve_baseline,args.observe,args.checkpoint_damon is not None))<=1
    if args.serve_python_target_mib is not None:
        assert args.restore_damon is None
        raise SystemExit(serve(args.directory,args.serve_python_target_mib,python_target=True))
    if args.observe:
        assert args.restore_damon is None
        print(json.dumps(counters()));raise SystemExit(0)
    if args.serve_baseline:
        assert args.restore_damon is None
        raise SystemExit(serve(args.directory,None))
    if args.checkpoint_damon:
        assert args.serve_target_mib is None and args.restore_damon is None
        print(json.dumps(checkpoint_damon(args.directory,args.checkpoint_damon)));raise SystemExit(0)
    assert args.restore_damon is None or args.serve_target_mib is not None
    raise SystemExit(main(args.directory) if args.serve_target_mib is None else serve(args.directory,args.serve_target_mib,args.restore_damon))
