"""Measure one common prefix with all owned VMs paused; join host PFNs once."""
import ctypes
import json
import mmap
import os
from pathlib import Path
import resource
import time

from agentenv_whole_cache import layer_inventory
from measure_file_physical import PAGE, PFN_MASK, identity, ram_address, read_entry
from trenvx_whole_cache import dump, records, RECORD, scan_backing, resident, same_physical_mapping, file_identity


def union_summary(sets):
    joined = set().union(*sets)
    return dict(summed_bytes=sum(map(len, sets))*PAGE, unique_bytes=len(joined)*PAGE,
                duplicate_bytes_if_summed=(sum(map(len, sets))-len(joined))*PAGE)


def backing_recheck(path, directory, index):
    """Revisit initially resident pages only, while every guest remains paused."""
    initial = records(directory/f'backing-{index}-pages.bin')
    rows = []
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_COPY) as mapped:
        mapped.madvise(mmap.MADV_RANDOM)
        address = ctypes.addressof(ctypes.c_char.from_buffer(mapped))
        bitmap = resident(mapped, address)
        with open('/proc/self/pagemap','rb',buffering=0) as pm:
            for offset, first, last in initial:
                entry = 0
                if bitmap[offset]:
                    unused = mapped[offset*PAGE]
                    entry = read_entry(pm.fileno(), address+offset*PAGE)
                rows.append((offset, last, entry))
        after = resident(mapped, address)
    (directory/f'backing-{index}-joint-recheck.bin').write_bytes(b''.join(RECORD.pack(*r) for r in rows))
    (directory/f'backing-{index}-joint-resident.bin').write_bytes(after)


def recount(raw):
    """Recompute intersections from local raw PFNs, never from saved totals."""
    directory = raw/'shared-cache'
    members = json.loads((directory/'members.json').read_text())
    scans = json.loads((directory/'backing/scans.json').read_text())
    caches = []; typed = {}; unknown = 0; per_vm = []
    for member in members:
        point = raw/'physical-cache'/member['segment']
        from report_file_inventory import kernel_audit, audit_frozen_fixture
        proof = json.loads((point/'whole-cache/ram-attestation.json').read_text())
        kernel = kernel_audit(point/'file-inventory', proof)
        assert audit_frozen_fixture(point/'file-inventory', require_complete_cache=False)['status']=='PASS'
        pause = json.loads((point/'whole-cache/paused-boundary.json').read_text())
        assert pause['status']=='PASS' and pause['heartbeat_continuity']=='PASS'
        if member.get('calibration_pinned_bytes'):
            before=json.loads((point/'file-inventory/before.json').read_text())
            assert before['fixture_retention']['mode']=='mlock'
            assert before['fixture_retention']['bytes']==member['calibration_pinned_bytes']==3*PAGE
        owners = {r['guest_pfn']:r for r in json.loads((point/'file-inventory/kernel-page-owners.json').read_text())}
        initial = {p:(a,b) for p,a,b in records(point/'file-inventory/frozen-typed-ram.bin')}
        final = records(point/'file-inventory/joint-recheck.bin')
        assert {p for p,_,_ in final}==set(initial)==set(owners)
        values = set(); missing = 0
        for pfn, second, third in final:
            first, last = initial[pfn]; assert second==last
            if not first&(1<<63) or not same_physical_mapping(first,last) or not same_physical_mapping(last,third):
                missing += 1; continue
            host = first&PFN_MASK; assert host
            values.add(host)
            typed.setdefault(owners[pfn]['inode_type'],set()).add(host)
        unknown += missing; caches.append(values)
        per_vm.append(dict(role=member['role'], sandbox_id=member['sid'], confirmed_guest_cache_bytes=len(values)*PAGE,
                           unknown_guest_pages=missing, calibration_content_bytes=3*PAGE,
                           selected_guest_pages=kernel['selected_guest_pages']))
    backing_sets=[]; backing_types={}; unresolved=0
    for index, scan in enumerate(scans):
        initial = records(directory/f'backing/backing-{index}-pages.bin')
        final = records(directory/f'backing/backing-{index}-joint-recheck.bin')
        before=(directory/f'backing/backing-{index}-before.bin').read_bytes()
        after=(directory/f'backing/backing-{index}-after.bin').read_bytes()
        final_resident=(directory/f'backing/backing-{index}-joint-resident.bin').read_bytes()
        assert len(before)==len(after)==len(final_resident)==(scan['identity']['st_size']+PAGE-1)//PAGE
        assert {i for i,v in enumerate(before) if v}=={r[0] for r in initial}
        assert [r[0] for r in initial]==[r[0] for r in final]
        values=set()
        for (offset,first,last),(other,second,third) in zip(initial,final,strict=True):
            assert offset==other and second==last and first&(1<<63) and first&(1<<61) and first&PFN_MASK
            if not after[offset] or not final_resident[offset] or not same_physical_mapping(first,last) or not same_physical_mapping(last,third):
                unresolved+=1;continue
            values.add(first&PFN_MASK)
        backing_sets.append(values);backing_types.setdefault(scan['role'],set()).update(values)
    attributed=[set().union(*(backing_sets[i] for i in member['backing_indices'])) for member in members]
    guest_union=set().union(*caches); backing_union=set().union(*backing_sets)
    return dict(status='PARTIAL' if unknown or unresolved else 'PASS',unit='bytes',page_size=PAGE,
                boundary='after identical original steps 1-11, before diverging frozen paths',
                instance_count=len(members),per_sandbox=per_vm,guest_cache=union_summary(caches),
                guest_cache_types_unique_bytes={k:len(v)*PAGE for k,v in typed.items()},
                backing_cache=union_summary(attributed),backing_cache_types_unique_bytes={k:len(v)*PAGE for k,v in backing_types.items()},
                guest_backing_overlap_bytes=len(guest_union&backing_union)*PAGE,
                combined_unique_bytes=len(guest_union|backing_union)*PAGE,
                unknown_guest_pages=unknown,unresolved_backing_pages=unresolved,
                distinct_backing_file_count=len(scans),
                scope='all paused guest file-LRU plus digest-bound shared layer/private upper/attached-drive residency; excludes provider anonymous memory',
                limitations=[('Measured image layers are warm converted-seed copies on the owned ext4 loop volume; the separate RAM preparation cache is excluded.' if any(m.get('calibration_pinned_bytes') for m in members) else 'Shared layer files are the warm immutable RAM seed, not task-exclusive disk cache.'),
                             'PFNs rechecked across a joint pause; host is not frozen, transient migration is not excluded.',
                             'Counts include filesystem and layer metadata; guest/host logical memory and VMM PSS are separate.',
                             'No cold-cache or causal performance comparison; sampling and paused analysis remain enabled.'])


def measure(probe, by_role, image):
    directory=probe.out/'shared-cache';directory.mkdir();backing=directory/'backing';backing.mkdir()
    contexts=[]; scans=[]; members=[]; seen={}
    started=time.monotonic_ns();usage=resource.getrusage(resource.RUSAGE_SELF)
    try:
        for role,sid in by_role.items():
            context=probe.prepare(sid,'prefix11-'+role,11,retain_fixture_pages=True);contexts.append(context)
            members.append(dict(role=role,sid=sid,segment=context['segment'],backing_indices=[],calibration_pinned_bytes=3*PAGE))
            # Freeze this freshly attested fixture before preparing the next VM.
            # Otherwise reclaim can invalidate its guest PFNs during that wait.
            context['pause'].pause()
        joint_start=time.time_ns()
        for context in contexts:probe.capture(context)
        daemons=[int(pid) for pid in probe.cgpath.joinpath('cgroup.procs').read_text().split()
                 if Path('/proc',pid,'comm').read_text().strip()=='uvm-ublk-daemon']
        assert len(daemons)==1
        for context,member in zip(contexts,members,strict=True):
            evidence=backing/member['role'];evidence.mkdir()
            inventory=layer_inventory(Path('/proc')/str(context['vm']['pid']),Path('/proc')/str(daemons[0]),image,evidence=evidence)
            dump(evidence/'inventory.json',inventory)
            for item in inventory['files']:
                key=tuple(item['identity'][k] for k in ('st_dev','st_ino'))
                if key not in seen:
                    index=len(scans); seen[key]=index
                    scan=scan_backing(Path(item['path']),backing,index,allow_unaligned=True)
                    assert scan['identity']==item['identity'],'backing identity changed before scan'
                    scans.append(dict(scan,role=item['role']))
                else:assert scans[seen[key]]['role']==item['role'] and scans[seen[key]]['identity']==item['identity']
                member['backing_indices'].append(seen[key])
        for context in contexts:
            vm,proof,pause=context['vm'],context['proof'],context['pause'];assert pause.state()=='Paused'
            proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
            with (proc/'pagemap').open('rb',buffering=0) as pm:
                rows=[(p,last,read_entry(pm.fileno(),ram_address(p,proof['slots'])))
                      for p,first,last in records(context['file_dir']/'frozen-typed-ram.bin')]
            (context['file_dir']/'joint-recheck.bin').write_bytes(b''.join(RECORD.pack(*r) for r in rows))
            pause.snapshot_recheck()
        for index,scan in enumerate(scans):
            path=Path(scan['path']);assert file_identity(path)==scan['identity']
            backing_recheck(path,backing,index);assert file_identity(path)==scan['identity']
        dump(backing/'scans.json',scans);dump(directory/'members.json',members)
        dump(directory/'joint-pause.json',dict(status='PASS',all_paused_start_timestamp_ns=joint_start,
             all_paused_end_timestamp_ns=time.time_ns(),sandbox_ids=list(by_role.values())))
        for context in contexts:context['pause'].resume();context['pause'].save(True)
        for context in contexts:probe.finish(context)
        result=recount(probe.out)
        result.update(analysis_elapsed_ns=time.monotonic_ns()-started,
                      collector_cpu_seconds=resource.getrusage(resource.RUSAGE_SELF).ru_utime+resource.getrusage(resource.RUSAGE_SELF).ru_stime-usage.ru_utime-usage.ru_stime)
        dump(directory/'summary.json',result)
        return result
    finally:
        for context in contexts:
            if context['pause'].paused:context['pause'].resume();context['pause'].save(False)
