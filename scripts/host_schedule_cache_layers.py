"""Explicit endpoint cache layers. Metadata/mincore/cachestat only; no data reads."""
import array
from contextlib import ExitStack, contextmanager
import fcntl
import os
from pathlib import Path
import stat
import struct
import time

from host_deleted_backing import query_deleted_backings_bounded, _validate_region, cachestat
from host_file_monitor import counters, mincore_file, pids
from host_schedule_files import process_identity
from isolated_storage import mount_identity


def capacity(fd):
    value=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,value,True)
    if value[0]<=0:raise ValueError('empty block capacity')
    return value[0]


def check_process(ref, identities, proc_root):
    try:
        pid=ref.get('process')
        if pid is not None and (pid not in identities or process_identity(proc_root/str(pid))!=identities[pid]):
            raise ValueError('source process identity/cgroup changed')
        if ref.get('region'):_validate_region(proc_root,ref['region'])
        s=Path(ref['path']).stat()
        if (s.st_dev,s.st_ino,s.st_rdev)!=tuple(ref[k] for k in ('device','inode','rdev')):
            raise ValueError('source block alias changed')
    except (OSError,ValueError) as error:
        error.failed_reference=dict(ref)
        raise


@contextmanager
def timed(stages, name):
    value=dict(start_monotonic_ns=time.monotonic_ns());cpu=time.thread_time_ns()
    stages[name]=value
    try:yield
    finally:value.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)


def check_pinned_device(row):
    s=os.fstat(row['fd'])
    if (not stat.S_ISBLK(s.st_mode) or (s.st_dev,s.st_ino,s.st_rdev)!=row['identity'] or
            capacity(row['fd'])!=row['mapping_length']):
        raise ValueError('pinned block identity or capacity changed')


def query_device(row, method, identities, proc_root):
    result=dict(method=method,status='UNKNOWN',resident_bytes=None,cache_index_bytes=None,
        range=dict(offset=0,length=row.get('mapping_length')),stages={})
    begin=time.monotonic_ns();cpu=time.thread_time_ns()
    try:
        with timed(result['stages'],'identity_before'):
            if row.get('reference_errors'):raise ValueError('one or more block aliases failed validation')
            if 'fd' not in row:raise ValueError('no verified live block reference')
            check_pinned_device(row)
            for ref in row['aliases']:check_process(ref,identities,proc_root)
        with timed(result['stages'],'core_query'):
            if method=='mincore':
                value=mincore_file(Path('/proc/self/fd')/str(row['fd']),
                    block_identity=row['identity'],block_length=row['mapping_length'])
                result['residency']=value
                if not value['stable']:raise ValueError('block identity/capacity changed during mincore')
            else:
                result['cache_counters']=cachestat(row['fd'],offset=0,length=row['mapping_length'])
        with timed(result['stages'],'identity_after'):
            check_pinned_device(row)
            for ref in row['aliases']:check_process(ref,identities,proc_root)
        if method=='mincore':result['resident_bytes']=value['resident_bytes']
        else:result['cache_index_bytes']=result['cache_counters']['nr_cache']*os.sysconf('SC_PAGE_SIZE')
        result['status']='OBSERVED'
    except (OSError,ValueError,NotImplementedError) as error:
        result.update(error=repr(error),errno=getattr(error,'errno',None))
        for name in ('failed_reference','mapping_diagnostic'):
            if hasattr(error,name):result[name]=getattr(error,name)
    result.update(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
    return result


def ram_anchor_plan(references, identities):
    ram={r['rdev'] for r in references if r.get('source')=='vmm_ram_mapping'}
    result={}
    for rdev in sorted(ram):
        candidates=[r for r in references if r['rdev']==rdev and r.get('source')=='open_block_fd'
                    and r.get('region') is None and r.get('process') in identities]
        result[rdev]=min(candidates,key=lambda r:(r.get('role') is not None,r['process'],r['path'])) if candidates else None
    return result


def observe_fixed_ram(row, anchor, identities, proc_root, stack):
    """One preplanned extra cachestat, never a retry of the strict observation."""
    begin=time.monotonic_ns();cpu=time.thread_time_ns();stages={}
    fixed=dict(aliases=[] if anchor is None else [anchor],mapping_length=row.get('mapping_length'))
    errors=[r for r in row.get('reference_errors',[]) if 'mapping_diagnostic' not in r]
    with timed(stages,'prepare'):
        try:
            if errors:raise ValueError('non-mapping reference validation failed')
            if anchor is None:raise ValueError('no preselected group-owned open block descriptor')
            check_process(anchor,identities,proc_root)
            fd=os.open(anchor['path'],os.O_RDONLY|os.O_CLOEXEC);stack.callback(os.close,fd)
            s=os.fstat(fd);key=(s.st_dev,s.st_ino,s.st_rdev)
            if not stat.S_ISBLK(s.st_mode) or key!=tuple(anchor[k] for k in ('device','inode','rdev')):
                raise ValueError('fixed RAM anchor identity differs')
            length=capacity(fd)
            if row.get('mapping_length') is not None and length!=row['mapping_length']:
                raise ValueError('fixed RAM capacity differs from inventory')
            fixed.update(fd=fd,identity=key,mapping_length=length)
            check_process(anchor,identities,proc_root)
        except (OSError,ValueError) as error:
            fixed['reference_errors']=[dict(error=repr(error))]
            errors=errors+[dict(error=repr(error))]
    query=query_device(fixed,'cachestat',identities,proc_root)
    return dict(rdev=row['rdev'],anchor=anchor,query=query,prepare_errors=errors,stages=stages,
        start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        scope='Global cache index of the preselected group-held RAM device; not VMM mapping continuity or guest page ownership.')


def source_relationships(query, count, inventory_errors):
    mapping_errors=[r for r in inventory_errors if 'mapping_diagnostic' in r]
    changed='mapping_diagnostic' in query or bool(mapping_errors)
    result=dict(status='CHANGED' if changed else 'OBSERVED' if query['status']=='OBSERVED' else 'UNKNOWN',
        expected_aliases=count,before_complete='core_query' in query['stages'],
        after_complete=query['status']=='OBSERVED',
        scope='Original strict reference checks. A cache-query failure can leave the final relationship check unperformed, not prove a mapping change.')
    for field in ('error','failed_reference','mapping_diagnostic'):
        if field in query:result[field]=query[field]
    if inventory_errors:result['inventory_errors']=inventory_errors
    return result


def observe_devices(references, identities, *, proc_root=Path('/proc'), method='mincore', split_ram=False):
    if method not in ('mincore','cachestat','compare'):raise ValueError('unknown block cache query method')
    if split_ram and method!='cachestat':raise ValueError('split RAM cache requires explicit cachestat')
    if split_ram:references=list(references);anchors=ram_anchor_plan(references,identities)
    methods=('mincore','cachestat') if method=='compare' else (method,)
    begin=time.monotonic_ns();cpu=time.thread_time_ns();objects={};errors=[];stages={}
    # Keep one verified descriptor per rdev for the whole observation to prevent
    # device reuse. Check every alias, but close extra descriptors immediately.
    with ExitStack() as stack:
        with timed(stages,'inventory'):
            for ref in references:
                row=objects.setdefault(ref['rdev'],dict(object_key=['block',ref['rdev']],rdev=ref['rdev'],
                    aliases=[],status='PENDING',resident_bytes=None,cache_index_bytes=None))
                row['aliases'].append(ref)
                fd=None
                try:
                    check_process(ref,identities,proc_root)
                    fd=os.open(ref['path'],os.O_RDONLY|os.O_CLOEXEC)
                    s=os.fstat(fd);key=(s.st_dev,s.st_ino,s.st_rdev)
                    if not stat.S_ISBLK(s.st_mode) or key!=tuple(ref[k] for k in ('device','inode','rdev')):
                        raise ValueError('block reference identity differs')
                    length=capacity(fd)
                    if 'mapping_length' in row and row['mapping_length']!=length:
                        raise ValueError('same-rdev alias capacity differs')
                    check_process(ref,identities,proc_root)
                    row.setdefault('mapping_length',length)
                    if 'fd' not in row:
                        stack.callback(os.close,fd);row.update(fd=fd,identity=key);fd=None
                except (OSError,ValueError) as error:
                    failure=dict(reference=ref,error=repr(error),resident_bytes=None)
                    if hasattr(error,'mapping_diagnostic'):failure['mapping_diagnostic']=error.mapping_diagnostic
                    errors.append(failure);row.setdefault('reference_errors',[]).append(failure)
                finally:
                    if fd is not None:os.close(fd)
        with timed(stages,'queries'):
            for row in objects.values():
                start=time.monotonic_ns();thread_cpu=time.thread_time_ns()
                if split_ram and row['rdev'] in anchors:
                    with ExitStack() as fixed_stack:
                        row['fixed_ram_device']=observe_fixed_ram(row,anchors[row['rdev']],identities,proc_root,fixed_stack)
                row['method']=method;row['queries']={}
                for selected in methods:
                    value=query_device(row,selected,identities,proc_root);row['queries'][selected]=value
                    if selected=='mincore':
                        row['resident_bytes']=value['resident_bytes']
                        if 'residency' in value:row['residency']=value['residency']
                    else:row['cache_index_bytes']=value['cache_index_bytes']
                if 'fixed_ram_device' in row:
                    row['fixed_ram_device']['source_relationships']=source_relationships(row['queries']['cachestat'],len(row['aliases']),row.get('reference_errors',[]))
                row['status']='OBSERVED' if all(q['status']=='OBSERVED' for q in row['queries'].values()) else 'UNKNOWN'
                row.pop('fd',None)
                row.update(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-thread_cpu)
    rows=list(objects.values());complete=not errors and all(r['status']=='OBSERVED' for r in rows)
    totals={}
    for selected,field in (('mincore','resident_bytes'),('cachestat','cache_index_bytes')):
        requested=selected in methods
        good=[r['queries'][selected] for r in rows if requested and r['queries'][selected]['status']=='OBSERVED']
        totals[selected]=dict(status='NOT_REQUESTED' if not requested else 'OBSERVED' if len(good)==len(rows) and not errors else 'PARTIAL',
            observed_objects=len(good),unknown_objects=len(rows)-len(good) if requested else 0,
            observed_subtotal_bytes=sum(q[field] for q in good) if requested else None,
            complete_bytes=sum(q[field] for q in good) if requested and len(good)==len(rows) and not errors else None)
    result=dict(status='OBSERVED' if complete else 'PARTIAL',method=method,objects=rows,errors=errors,
        start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,stages=stages,
        stable_resident_bytes=totals['mincore']['observed_subtotal_bytes'],
        stable_cache_index_bytes=totals['cachestat']['observed_subtotal_bytes'],method_totals=totals,
        scope='Live block-device cache, deduplicated by pinned rdev and verified aliases/capacity; separate from ordinary files and VMM PSS.',
        interpretation='mincore counts uptodate cache pages; cachestat counts existing cache-index pages, including not-yet-uptodate pages. Evicted block entries are historical, not TMPFS swap or current cache. compare queries mincore then cachestat per pinned object; sequential, not simultaneous. No fallback or physical total.')
    if split_ram:
        selected=[r['fixed_ram_device'] for r in rows if 'fixed_ram_device' in r]
        good=[r for r in selected if r['query']['status']=='OBSERVED']
        subtotal=sum(r['query']['cache_index_bytes'] for r in good) if selected else None
        result['fixed_ram_totals']=dict(status='NOT_APPLICABLE' if not selected else 'OBSERVED' if len(good)==len(selected) else 'PARTIAL',
            observed_objects=len(good),unknown_objects=len(selected)-len(good),
            observed_subtotal_bytes=subtotal,complete_bytes=subtotal if selected and len(good)==len(selected) else None,
            wall_seconds=sum((r['end_monotonic_ns']-r['start_monotonic_ns'])/1e9 for r in selected),
            cpu_seconds=sum(r['cpu_ns']/1e9 for r in selected))
        result.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
    return result


def loop_status(fd):
    # Linux loop_info64, LOOP_GET_STATUS64. This ioctl changes no device state.
    layout='=QQQQQIIII64s64s32sQQ';buffer=bytearray(struct.calcsize(layout))
    fcntl.ioctl(fd,0x4c05,buffer,True);v=struct.unpack(layout,buffer)
    return dict(backing_device=v[0],backing_inode=v[1],backing_rdev=v[2],offset=v[3],sizelimit=v[4],
                number=v[5],flags=v[8],direct_io=bool(v[8]&16),read_only=bool(v[8]&1))


def observe_loop(mount, image, *, include_query_window=False):
    begin=time.monotonic_ns();cpu=time.thread_time_ns()
    result=dict(status='UNKNOWN',image=str(image),resident_bytes=None,start_monotonic_ns=begin)
    if include_query_window:
        result['query_window']=dict(status='UNKNOWN',resident_bytes=None,range=None,rejected_checks=[],
            scope='Pinned exact loop backing residency over sequential queries; mtime may change. Non-atomic, separate from strict residency and other cache layers.')
    stage='mount_before'
    try:
        device=Path('/dev')/(Path('/sys/dev/block')/f'{os.major(mount.stat().st_dev)}:{os.minor(mount.stat().st_dev)}').resolve(strict=True).name
        result['device_path']=str(device);mount_identity(mount,str(device))
        fd=os.open(device,os.O_RDONLY|os.O_CLOEXEC)
        try:
            stage='loop_identity_before'
            if not stat.S_ISBLK(os.fstat(fd).st_mode) or os.fstat(fd).st_rdev!=mount.stat().st_dev:
                raise ValueError('loop does not own private mount')
            before=loop_status(fd);result['loop_before']=before
            stage='backing_identity_before'
            pinned=os.open(image,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
            try:
                s=os.fstat(pinned)
                if (s.st_dev,s.st_ino)!=(before['backing_device'],before['backing_inode']):
                    raise ValueError('loop backing file differs from this run image')
                stage='mincore_query'
                value=mincore_file(image,file_descriptor=pinned,live=True,
                    **(dict(include_metadata=True) if include_query_window else {}));result['residency']=value
            finally:os.close(pinned)
            stage='loop_configuration_after'
            after=loop_status(fd);result['loop_after']=after
            stage='mount_after'
            mount_identity(mount,str(device))
            stage='window_validation'
            if include_query_window:
                window=result['query_window'];rejected=window['rejected_checks']
                window['range']=dict(offset=0,length=value['mapping_length'])
                if before!=after:rejected.append('LOOP_CONFIGURATION_CHANGED')
                if not value['identity_stable'] or (value['device'],value['inode'],value['rdev'])!=(s.st_dev,s.st_ino,s.st_rdev):
                    rejected.append('FILE_IDENTITY_CHANGED')
                if not value['size_stable'] or value['size']!=s.st_size:rejected.append('FILE_SIZE_CHANGED')
                if not rejected:
                    window.update(status='OBSERVED',resident_bytes=value['resident_bytes'])
                    if not value['stable']:window['strict_rejection_reason']='MTIME_CHANGED'
            if before!=after or not value['stable']:raise ValueError('loop/backing changed during query')
            result.update(status='OBSERVED',resident_bytes=value['resident_bytes'])
        finally:os.close(fd)
    except (OSError,ValueError) as error:
        result.update(error=repr(error),resident_bytes=None)
        if include_query_window and result['query_window']['status']=='UNKNOWN':
            result['query_window'].update(error=repr(error),failure_stage=stage)
    result.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        scope='Exact outer loop backing file cache and effective loop flags; not the inner filesystem or a physical union.')
    return result


def deleted_tmpfs(expanded):
    regions=[];missing=[]
    for row in expanded['files']:
        if row['status']!='SEPARATE_SHMEM_OR_HUGETLB':continue
        if row.get('filesystem_magic')!=0x01021994:
            missing.append(dict(object_key=row['object_key'],reason='unsupported non-TMPFS backing'));continue
        found=[a['region'] for a in row['aliases'] if a.get('region') and a['region']['mapping_path'].endswith(' (deleted)')]
        if not found:missing.append(dict(object_key=row['object_key'],reason='no complete live deleted mapping'))
        regions.extend(found)
    value=query_deleted_backings_bounded(regions)
    value['missing_sources']=missing
    if missing:value.update(status='PARTIAL',whole_backing_cache_bytes=None)
    return value


def observe_cache_layers(env, bindings, drives, expanded, group, *, block_method='mincore', split_ram=False):
    if split_ram and block_method!='cachestat':raise ValueError('split RAM cache requires explicit cachestat')
    begin=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(group)
    identities={int(k):v for k,v in expanded['process_identities'].items()};references=[]
    for role,entries in drives.items():
        references.extend(dict(d,process=bindings[role]['pid'],role=role,source='firecracker_drive',region=None)
                          for d in entries if d['block_device'])
    for ref in expanded['special_block_references']:
        role=next((role for role,vm in bindings.items() if vm['pid']==ref['process']),None)
        region=ref['region']
        ram=bool(role and region and region['permissions']=='rw-p' and region['mapping_path'].startswith('/dev/ublkb'))
        references.append(dict(ref,role=role,source='vmm_ram_mapping' if ram else ref['source']))
    devices=observe_devices(references,identities,method=block_method,**(dict(split_ram=True) if split_ram else {}))
    tmpfs=deleted_tmpfs(expanded)
    loop=observe_loop(env.parent,env.parent.parent/'volume.ext4',include_query_window=True)
    errors=[]
    for pid,identity in identities.items():
        try:
            if process_identity(Path('/proc')/str(pid))!=identity:raise ValueError('process identity/cgroup changed')
        except (OSError,ValueError) as error:errors.append(dict(pid=pid,error=repr(error)))
    if pids(group)!=sorted(identities):errors.append(dict(error='service process set changed since inventory'))
    complete=devices['status']==loop['status']=='OBSERVED' and tmpfs['status'] in ('OBSERVED','NOT_REQUESTED') and not errors
    return dict(status='OBSERVED' if complete else 'PARTIAL',block_devices=devices,deleted_tmpfs=tmpfs,
        outer_loop=loop,process_errors=errors,counters_before=before,counters_after=counters(group),
        start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        worker_cpu_ns=tmpfs.get('worker_cpu_ns'),
        interpretation='Separate cache layers, sequential non-atomic observations. Do not add layers/current/file/PSS as a physical total. Unknown is not zero; no unreferenced old cache ownership or guest Cached attribution.')
