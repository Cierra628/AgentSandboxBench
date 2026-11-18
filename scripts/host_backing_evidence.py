"""Opt-in host backing inventory and references; saved-data replay does no live I/O."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import time

VERSION='host-backing-references-v1'
CODE_FILES=('host_backing_evidence.py','host_rootfs_layers.py','host_schedule_files.py',
    'host_schedule_cache_layers.py','host_deleted_backing.py','host_file_monitor.py',
    'host_prefix_compare.py','host_rollout_shared.py','host_special_metadata.py',
    'host_sandbox_monitor.py','isolated_storage.py')


def summarize(raw):
    """Only saved fields. References are not exclusive page ownership or charges."""
    guests=raw['guests'];bindings=raw['bindings']
    if set(guests)!=set(bindings) or not guests or len(set(guests.values()))!=len(guests):
        raise ValueError('sandbox reference identities missing or duplicated')
    if len({v['pid'] for v in bindings.values()})!=len(bindings):raise ValueError('VMM PID shared between sandbox identities')
    by_pid={v['pid']:role for role,v in bindings.items()}
    expanded=raw.get('expanded_files',{});layers=raw.get('cache_layers',{});roots=raw.get('rootfs_inventory',{})
    objects=[]
    tmpfs={}
    for q in layers.get('deleted_tmpfs',{}).get('objects',[]):
        key=(q['device'],q['inode'])
        if key in tmpfs:raise ValueError('duplicate TMPFS backing query identity')
        tmpfs[key]=q

    def references(row):
        roles=set(row.get('users',[]))
        for alias in row.get('aliases',[]):
            if alias.get('role') is not None:roles.add(alias['role'])
            if alias.get('process') in by_pid:roles.add(by_pid[alias['process']])
        if not roles<=guests.keys():raise ValueError('object references an unbound role')
        return [dict(role=r,sandbox_id=guests[r]) for r in sorted(roles)]

    def regular_query(row):
        q=row.get('residency',{});permission=q.get('mincore_permission',{})
        allowed=permission.get('basis') in ('HOST_CAP_FOWNER','HOST_INODE_OWNER','PINNED_FD_WRITABLE')
        good=(row.get('status')=='OBSERVED' and q.get('stable') is True and allowed
            and (q.get('device'),q.get('inode'))==(row['device'],row['inode'])
            and type(row.get('resident_bytes')) is int and row['resident_bytes']>=0
            and row['resident_bytes']==q.get('resident_bytes'))
        return dict(status='OBSERVED' if good else 'UNKNOWN',method='permission_checked_mincore',
            bytes=row['resident_bytes'] if good else None,permission=permission,
            reason=None if good else 'strict stable query with matching inode and permission evidence not available',
            start_monotonic_ns=row.get('start_monotonic_ns'),end_monotonic_ns=row.get('end_monotonic_ns'))

    for row in expanded.get('files',[]):
        refs=references(row)
        separate=row['status']=='SEPARATE_SHMEM_OR_HUGETLB'
        query=regular_query(row)
        if separate:
            q=tmpfs.get((row['device'],row['inode']),{})
            good=(q.get('status')=='OBSERVED' and type(q.get('whole_backing_cache_bytes')) is int
                and q['whole_backing_cache_bytes']>=0)
            query=dict(status='OBSERVED' if good else 'UNKNOWN',method='deleted_tmpfs_cachestat',
                bytes=q['whole_backing_cache_bytes'] if good else None,
                cache_counters=q.get('cache_counters'),source_status=q.get('status','NOT_COLLECTED'),
                start_monotonic_ns=q.get('start_monotonic_ns'),end_monotonic_ns=q.get('end_monotonic_ns'),
                reason=None if good else 'no complete same-inode TMPFS query; swap/hugetlb/missing evidence remains unknown')
        objects.append(dict(object_key=['regular',row['device'],row['inode']],
            kind='shmem_or_hugetlb' if separate else 'regular',
            categories=row['categories'],references=refs,reference_status='BOUND' if refs else 'UNASSIGNED',
            aliases=row['aliases'],source_status=row['status'],query=query))
    for row in layers.get('block_devices',{}).get('objects',[]):
        q=row.get('queries',{}).get('cachestat',{})
        refs=references(row)
        good=(row.get('status')=='OBSERVED' and q.get('status')=='OBSERVED'
            and type(q.get('cache_index_bytes')) is int and q['cache_index_bytes']>=0)
        objects.append(dict(object_key=['block',row['rdev']],kind='block',references=refs,
            reference_status='BOUND' if refs else 'UNASSIGNED',aliases=row['aliases'],source_status=row['status'],
            query=dict(status='OBSERVED' if good else 'UNKNOWN',method='cachestat_cache_index',
                bytes=q['cache_index_bytes'] if good else None,cache_counters=q.get('cache_counters'),
                start_monotonic_ns=q.get('start_monotonic_ns'),end_monotonic_ns=q.get('end_monotonic_ns'),
                reason=None if good else 'no successful strict cachestat; no mincore fallback')))
    keys=[tuple(o['object_key']) for o in objects]
    if len(set(keys))!=len(keys):raise ValueError('duplicate object identity in deduplicated inventory')
    loop=layers.get('outer_loop',{});q=loop.get('residency',{})
    if loop:
        # Containing an experiment filesystem does not prove every guest uses
        # each cached page of that image. Keep this relation separate.
        key=['regular',q['device'],q['inode']] if 'device' in q and 'inode' in q else None
        row=dict(loop,device=q.get('device'),inode=q.get('inode'))
        objects.append(dict(object_key=key,kind='outer_loop',image=loop.get('image'),
            references=[],reference_status='CONTAINER_ONLY',
            contains_sandboxes=[dict(role=r,sandbox_id=sid) for r,sid in guests.items()],
            loop_before=loop.get('loop_before'),loop_after=loop.get('loop_after'),
            query=regular_query(row),query_window=loop.get('query_window')))
    # No cross-layer sum: even an ordinary backing may also appear in another
    # source, and cachestat has cache-index, not mincore-uptodate semantics.
    per_guest={r:dict(sandbox_id=sid,referenced_object_keys=[o['object_key'] for o in objects
        if any(ref['role']==r for ref in o['references'])],exclusive_physical_bytes=None,
        complete_host_cache_bytes=None) for r,sid in guests.items()}
    source_statuses={k:raw.get(k,{}).get('status','NOT_COLLECTED')
        for k in ('rootfs_inventory','expanded_files','cache_layers')}
    value=dict(version=VERSION,status='FAIL' if raw.get('status')=='FAIL' else
        'OBSERVED_LISTED_OBJECTS' if source_statuses==dict(rootfs_inventory='IDENTIFIED',expanded_files='OBSERVED',cache_layers='OBSERVED')
            and all(o['query']['status']=='OBSERVED' for o in objects) else 'PARTIAL',
        source_statuses=source_statuses,guests=guests,bindings=bindings,objects=objects,per_guest=per_guest,
        lower_counts={r:dict(declared=len(g.get('lowers',[])),identified=sum(v['status']=='IDENTIFIED' for v in g.get('lowers',[])))
            for r,g in roots.get('guests',{}).items()},
        coverage=dict(regular_objects=sum(o['kind']=='regular' for o in objects),
            shmem_or_hugetlb_objects=sum(o['kind']=='shmem_or_hugetlb' for o in objects),
            block_objects=sum(o['kind']=='block' for o in objects),
            unknown_query_objects=sum(o['query']['status']!='OBSERVED' for o in objects),
            unassigned_objects=sum(o['reference_status']=='UNASSIGNED' for o in objects),
            multiple_reference_objects=sum(len(o['references'])>1 for o in objects),
            unresolved_sources=expanded.get('unresolved',[]),process_errors=layers.get('process_errors',[])),
        deleted_tmpfs=layers.get('deleted_tmpfs',dict(status='NOT_COLLECTED',whole_backing_cache_bytes=None)),
        page_charge_owner='NOT_MEASURED: cgroup totals do not identify each object/page charge',
        complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None,
        physical_dedup_with_guest_ram='NOT_MEASURED: no backing-object PFNs',
        scope='Listed live references and retained private files only; sequential, non-atomic; references are not per-page access or exclusive ownership',
        exclusions=['source service outside experiment slice','unreferenced old cache outside listed trees',
            'closed unlinked unmapped objects','objects created/deleted between inventory and query',
            'per-page cgroup charges','unregistered or unsupported memory/cache'],
        cost=dict(stages=raw.get('stages'),cpu_ns=raw.get('cpu_ns'),
            start_monotonic_ns=raw.get('start_monotonic_ns'),end_monotonic_ns=raw.get('end_monotonic_ns')))
    if 'single_file_pfn' in raw:
        receipt=raw['single_file_pfn']
        if receipt['status']!='PASS':raise ValueError('single-file PFN receipt failed')
        if receipt.get('bindings_before')!=bindings or receipt.get('bindings_after')!=bindings:
            raise ValueError('PFN receipt VMM identities differ from object inventory')
        selected=[o for o in value['objects'] if o['kind']=='regular' and o['object_key']==receipt['object_key']]
        if len(selected)!=1 or selected[0]['references']!=receipt['references']:
            raise ValueError('PFN receipt sandbox references differ from object table')
        selected[0]['pfn_context']=dict(identity=receipt['identity'],host_boot_id=receipt['host_boot_id'],
            start_monotonic_ns=receipt['start_monotonic_ns'],end_monotonic_ns=receipt['end_monotonic_ns'],
            source='host-backing-single-pfn.json',basis='same-observation live rootfs and VMM checks before/after PFN query')
    return value


def observe(container,out,guests,expected_vms,*,pfn_evidence=False,full_pfn_evidence=False,charge_scope=None):
    """Explicit endpoint only, called after cache collectors stop but before VM deletion."""
    from host_sandbox_monitor import Binding
    from host_prefix_compare import drives_for_vm
    from host_rollout_shared import opened_regular_files
    from host_rootfs_layers import rootfs_inventory,validate_rootfs_inventory
    from host_schedule_files import observe_schedule_files,process_identity
    from host_schedule_cache_layers import observe_cache_layers
    if charge_scope is not None and not full_pfn_evidence:
        raise ValueError('page charge scope requires explicit full-list PFN mode')
    if full_pfn_evidence and (pfn_evidence or len(guests)!=1):
        raise ValueError('full-list PFNs require one private guest and cannot combine with single-range query')
    raw_path=out/'host-backing-raw.json';report_path=out/'host-backing-evidence.json'
    if raw_path.exists() or report_path.exists():raise FileExistsError('host backing evidence already exists')
    started=time.monotonic_ns();cpu=time.thread_time_ns()
    raw=dict(version=VERSION,status='PENDING',guests=guests,bindings={},stages={},start_monotonic_ns=started)
    io_timings=[]
    def write_json(path,value):
        if not full_pfn_evidence:
            with path.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
            return
        row=dict(path=str(path),start_monotonic_ns=time.monotonic_ns(),status='STARTED');io_timings.append(row)
        try:
            text=json.dumps(value,indent=2)+'\n'
            row.update(bytes=len(text.encode()),write_start_monotonic_ns=time.monotonic_ns())
            with path.open('x') as f:f.write(text)
            row.update(write_end_monotonic_ns=time.monotonic_ns(),status='WRITTEN')
        except Exception as error:row.update(status='FAIL',error=repr(error));raise
        finally:row['end_monotonic_ns']=time.monotonic_ns()
    def save_io_timings():
        if full_pfn_evidence:
            with (out/'host-backing-io-timings.json').open('x') as f:
                json.dump(dict(version='host-backing-io-timings-v1',operations=io_timings,
                    scope='Python write and close return, not disk writeback; this small timing receipt itself is excluded'),f,indent=2);f.write('\n')
    @contextmanager
    def stage(name):
        cost=dict(start_monotonic_ns=time.monotonic_ns());c=time.thread_time_ns();raw['stages'][name]=cost
        try:yield
        finally:cost.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-c)
    def check_expected():
        if set(expected_vms)!=set(guests):raise ValueError('expected VMM identities missing')
        for role,sid in guests.items():
            vm=raw['bindings'][role];expected=expected_vms[role]
            if (expected['sandbox_id']!=sid or vm['pid']!=expected['pid'] or
                    str(vm['start_ticks'])!=str(expected['start_time_ticks'])):
                raise ValueError('backing observation differs from RAM collector identity')
            actual=process_identity(Path('/proc')/str(vm['pid']))
            if actual!=dict(start_ticks=str(expected['start_time_ticks']),cgroup='0::'+expected['cgroup']+'\n'):
                raise ValueError('VMM left its attested birth cgroup')
    def recheck():
        validate_rootfs_inventory(raw['rootfs_inventory']);check_expected()
        actual={r:binding.resolve(sid) for r,sid in guests.items()}
        if actual!=raw['bindings']:raise ValueError('sandbox binding changed during backing queries')
        return actual
    try:
        with stage('bindings_and_drives'):
            binding=Binding(container,out)
            raw['bindings']={r:binding.resolve(sid) for r,sid in guests.items()};check_expected()
            raw['drives']={r:drives_for_vm(vm) for r,vm in raw['bindings'].items()}
            raw['cgroup']=str(binding.cgroup)
        env=out.parent.parent/'env'
        with stage('rootfs_inventory'):
            raw['rootfs_inventory']=rootfs_inventory(raw['bindings'],raw['drives'],opened_regular_files(binding.cgroup),{})
        with stage('regular_files'):
            # No reuse of historical P1; every query is from this endpoint.
            raw['expanded_files']=observe_schedule_files(env,raw['rootfs_inventory'],[],binding.cgroup,
                drives=raw['drives'],owned_roots=[env.parent/'run',env.parent/'tmp'],capture_layers=True)
        with stage('cache_layers'):
            raw['cache_layers']=observe_cache_layers(env,raw['bindings'],raw['drives'],raw['expanded_files'],
                binding.cgroup,block_method='cachestat')
        if pfn_evidence:
            from host_backing_pfn_live import observe_one
            with stage('single_file_pfn'):
                raw['single_file_pfn']=observe_one(raw,out,recheck)
                if raw['single_file_pfn']['status']!='PASS':
                    raise RuntimeError('single-file PFN query failed: '+raw['single_file_pfn']['error'])
        if full_pfn_evidence:
            from host_backing_pfn_batch import observe_batch
            with stage('full_file_pfns'):
                raw['batch_file_pfn']=observe_batch(raw,out,recheck,**({'charge_scope':charge_scope} if charge_scope is not None else {}))
                if raw['batch_file_pfn']['status']!='PASS':
                    raise RuntimeError('full-list PFN query failed; see host-backing-full-pfn/result.json: '+raw['batch_file_pfn'].get('error','record budget failure'))
        with stage('final_identity_check'):
            recheck()
        raw['status']='COLLECTED'
    except Exception as error:raw.update(status='FAIL',error=repr(error))
    raw.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
    write_json(raw_path,raw)
    if raw['status']=='FAIL':
        value=dict(version=VERSION,status='FAIL',error=raw['error'],source=str(raw_path),
            complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None)
    else:
        try:value=summarize(raw)
        except (ValueError,KeyError,TypeError) as error:
            value=dict(version=VERSION,status='FAIL',error=repr(error),source=str(raw_path),
                complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None)
    write_json(report_path,value)
    if value['status']=='FAIL':
        save_io_timings()
        raise RuntimeError('host backing evidence failed; see '+str(raw_path)+': '+value['error'])
    pfn={}
    if pfn_evidence:
        from host_backing_pfns import load_query,attach
        joined=attach(value,load_query(out/'host-backing-pfn-query/result.json',out/'host-backing-single-pfn.json'),
            inventory_source=str(raw_path))
        with (out/'host-backing-pfn-evidence.json').open('x') as f:json.dump(joined,f,indent=2);f.write('\n')
        if joined['pfn_attachment']['available_objects']!=1:
            raise RuntimeError('single-file PFN not accepted into backing object table')
        pfn=dict(single_file_pfn_status='PASS',pfn_report='host-backing-pfn-evidence.json',
            pfn_receipt='host-backing-single-pfn.json')
    if full_pfn_evidence:
        from host_backing_pfn_batch import replay_batch,RecordBudget
        from host_file_pfn_query import RAW_BUDGET
        full_root=out/'host-backing-full-pfn'
        replay_cost=dict(operation='full_pfn_replay',start_monotonic_ns=time.monotonic_ns(),status='STARTED');io_timings.append(replay_cost)
        try:
            joined,coverage=replay_batch(raw,full_root)
            replay_cost.update(status='COMPLETE',end_monotonic_ns=time.monotonic_ns())
            budget=RecordBudget(full_root,RAW_BUDGET)
            for name,data in [('evidence.json',joined),('coverage.json',coverage)]:
                cost={};io_timings.append(cost);budget.write(full_root/name,data,timing=cost)
        except Exception as error:
            if replay_cost['status']=='STARTED':replay_cost.update(status='FAIL',error=repr(error),end_monotonic_ns=time.monotonic_ns())
            raise
        finally:save_io_timings()
        pfn=dict(full_file_pfn_status='PASS',full_pfn_report='host-backing-full-pfn/evidence.json',
            full_pfn_coverage='host-backing-full-pfn/coverage.json',full_pfn_record_bytes=budget.used(),
            io_timing_receipt='host-backing-io-timings.json',all_outputs_complete_monotonic_ns=time.monotonic_ns())
    return dict(status=value['status'],raw=raw_path.name,report=report_path.name,cpu_ns=raw['cpu_ns'],
        start_monotonic_ns=started,end_monotonic_ns=raw['end_monotonic_ns'],**pfn)


def replay(source):
    raw=json.loads(source.read_text())
    if raw.get('status')=='FAIL':
        return dict(version=VERSION,status='FAIL',source=str(source),error=raw.get('error'),
            complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None,
            validation='OFFLINE_REPLAY_ONLY; original failure preserved')
    if 'bindings' not in raw:
        return dict(version=VERSION,status='UNAVAILABLE',source=str(source),
            reason='no saved host backing bindings/inventory/queries; paths and phase cgroup totals cannot replace object observations',
            complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None,
            validation='OFFLINE_REPLAY_ONLY')
    if 'guests' not in raw:
        # Old schedule source: bind roles via its recorded PSS identities, never
        # infer a sandbox from a PID or a report's rounded table.
        guests={}
        for row in raw['pss']['processes']:
            if row['role'] in guests or row['identity']!=raw['bindings'][row['role']]:
                raise ValueError('saved PSS binding conflict')
            guests[row['role']]=row['sandbox_id']
        raw=dict(raw,guests=guests)
    return dict(summarize(raw),source=str(source),validation='OFFLINE_REPLAY_ONLY')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved-raw',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();value=replay(a.saved_raw)
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print('status='+value['status']+' result='+str(a.output))


if __name__=='__main__':main()
