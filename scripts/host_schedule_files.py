"""Opt-in endpoint file inventory for all three schedules; metadata and mincore only."""
import ctypes
import os
from pathlib import Path
import stat
import time

from host_file_monitor import mincore_file, pids
from host_rootfs_layers import local_path
from host_special_metadata import mapping_line


def process_identity(root):
    return dict(start_ticks=(root/'stat').read_text().rsplit(')', 1)[1].split()[19],
                cgroup=(root/'cgroup').read_text())


def filesystem_magic(fd):
    fields = (ctypes.c_long*32)(); libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, ctypes.byref(fields)):
        error = ctypes.get_errno(); raise OSError(error, os.strerror(error))
    return fields[0] & 0xffffffff


def permission_evidence():
    # Linux may return an all-resident vector for a caller lacking ownership or
    # write permission. Require host-root CAP_FOWNER for this explicit profile.
    uid_map = (Path('/proc/self/uid_map')).read_text().split()
    caps = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                    if line.startswith('CapEff:')), 16)
    allowed = os.geteuid()==0 and uid_map==['0', '0', '4294967295'] and bool(caps & (1 << 3))
    if not allowed:raise PermissionError('host-root CAP_FOWNER not established; mincore may hide residency')
    return dict(euid=os.geteuid(), uid_map=uid_map, cap_eff=hex(caps), host_root_cap_fowner=True)


def primary_category(row):
    tags=row['categories']
    for category in ('initial_lower','rootfs_upper_data','rootfs_upper_index','active_snapshot_rootfs_lower'):
        if category in tags:return category
    paths=[alias['path'] for alias in row['aliases']]
    if any('/mem_overlaybd/' in path for path in paths):return 'snapshot_mem_overlaybd_file'
    if any('/memory/image.json' in path and 'snapshot' in path for path in paths):return 'snapshot_memory_config'
    return next((c for c in tags if c.startswith('owned_')),
                'regular_drive_backing' if 'regular_drive_backing' in tags else 'external_open_or_mapped')


def query_window_summary(rows, unresolved):
    disk=[r for r in rows if r['status']!='SEPARATE_SHMEM_OR_HUGETLB']
    valid=[]
    for row in disk:
        q=row.get('residency',{})
        good=(row['status'] in ('OBSERVED','CHANGED_DURING_QUERY') and q.get('stable') is True
              and (q.get('device'),q.get('inode'))==(row['device'],row['inode'])
              and row['resident_bytes'] is not None and row['resident_bytes']==q.get('resident_bytes'))
        row['query_window_status']='OBSERVED' if good else 'UNKNOWN'
        if good:valid.append(row)
    complete=len(valid)==len(disk) and not unresolved
    subtotal=sum(r['resident_bytes'] for r in valid)
    return dict(status='OBSERVED' if complete else 'PARTIAL',observed_objects=len(valid),
        unknown_objects=len(disk)-len(valid),unresolved_sources=len(unresolved),observed_subtotal_bytes=subtotal,
        complete_bytes=subtotal if complete else None,
        changed_before_query_objects=sum(r['status']=='CHANGED_DURING_QUERY' for r in valid),
        changed_before_query_bytes=sum(r['resident_bytes'] for r in valid if r['status']=='CHANGED_DURING_QUERY'),
        interpretation='Each object once at its own successful stable query window, including original P1 reuse. Not simultaneous, not inventory-stable. Strict subtotal and PARTIAL stay unchanged.')


def observe_schedule_files(env, roots, initial, group, *, drives=None, proc_root=Path('/proc'), owned_roots=None, capture_layers=False):
    """Reconcile retained paths, live lower/upper references, open FDs and mappings.

    This is a sequential endpoint observation, not all host page cache and not
    guest Cached attribution. An unresolved source never contributes zero.
    """
    start = time.monotonic_ns(); cpu = time.thread_time_ns(); env = Path(env)
    permission = permission_evidence()
    objects = {}; unresolved = []; excluded = []; processes = {}; symlinks = []; special_references = []
    prior = {(r['device'], r['inode']): r for r in initial}

    def unknown(source, error, **fields):
        unresolved.append(dict(source=source, error=str(error), resident_bytes=None, **fields))

    def add(path, category, *, expected=None, role=None, process=None, follow=False, source=None, region=None):
        path = Path(path)
        try:
            s = path.stat() if follow else path.lstat()
            key = (s.st_dev, s.st_ino)
            if expected is not None and key != tuple(expected):raise ValueError('reference inode changed')
            if not stat.S_ISREG(s.st_mode):
                if category in ('mapped_file','owned_symlink_file'):
                    excluded.append(dict(path=str(path), mode=s.st_mode, reason='REFERENCED_NONREGULAR_OBJECT',
                                         device=s.st_dev, inode=s.st_ino, source=source, resident_bytes=None))
                    if capture_layers and stat.S_ISBLK(s.st_mode):
                        special_references.append(dict(path=str(path), device=s.st_dev, inode=s.st_ino, rdev=s.st_rdev,
                            process=process, source=source, region=region))
                    return
                raise ValueError('reference is not an ordinary file')
            row = objects.setdefault(key, dict(object_key=['regular', *key], device=key[0], inode=key[1],
                size=s.st_size, mtime_ns=s.st_mtime_ns, categories=[], users=[], aliases=[],
                status='PENDING', resident_bytes=None))
            if capture_layers:row.setdefault('inventory_first_monotonic_ns',time.monotonic_ns())
            if category not in row['categories']:row['categories'].append(category)
            if role is not None and role not in row['users']:row['users'].append(role)
            alias = dict(path=str(path), follow=follow, process=process, source=source or category)
            if capture_layers and region is not None:alias['region']=region
            if alias not in row['aliases']:row['aliases'].append(alias)
        except (OSError, ValueError) as error:
            unknown(source or category, error, path=str(path), role=role)

    # The retained private-service tree also includes closed snapshot files.
    def walk_error(error):unknown('owned_tree_walk', error, path=error.filename)
    trees = [env, *(Path(p) for p in (owned_roots or []))]
    for tree in trees:
        if not tree.is_dir():unknown('owned_tree_missing', 'private directory missing', path=str(tree));continue
        for parent, dirs, names in os.walk(tree, followlinks=False, onerror=walk_error):
            for name in sorted(dirs + names):
                path = Path(parent)/name
                try:
                    s = path.lstat()
                    if stat.S_ISLNK(s.st_mode):
                        symlinks.append(dict(path=str(path), target=os.readlink(path)))
                        if path.is_dir():
                            unknown('owned_tree_symlink_directory', 'directory not traversed', path=str(path))
                        else:add(path, 'owned_symlink_file', follow=True)
                    elif stat.S_ISREG(s.st_mode):
                        relative = path.relative_to(tree)
                        category = ('owned_'+tree.name if tree!=env else 'owned_snapshot_memory'
                                    if 'memory' in relative.parts and 'snapshot' in str(relative) else 'owned_'+relative.parts[0])
                        add(path, category)
                    elif not stat.S_ISDIR(s.st_mode):
                        excluded.append(dict(path=str(path), mode=s.st_mode, reason='NOT_REGULAR', resident_bytes=None))
                except OSError as error:unknown('owned_tree_entry', error, path=str(path))
            dirs[:] = [name for name in dirs if not (Path(parent)/name).is_symlink()]

    for role, entries in (drives or {}).items():
        for drive in entries:
            if drive['block_device']:
                excluded.append(dict(source='firecracker_drive', role=role, drive=drive, resident_bytes=None,
                                     reason='BLOCK_DEVICE_SEPARATE_CACHE_LAYER'))
            else:add(drive['path'], 'regular_drive_backing', expected=(drive['device'],drive['inode']), role=role)

    # Every active lower is included, not only lowers shared by >=2 guests.
    for role, guest in roots['guests'].items():
        process = proc_root/str(guest['pid']); config = Path(guest['image_config'])
        for lower in guest['lowers']:
            if lower['status'] != 'IDENTIFIED':
                unknown('active_rootfs_lower', lower.get('error', 'not identified'), role=role, reference=lower)
                continue
            key = (lower['device'], lower['inode'])
            add(lower['path'], 'initial_lower' if key in prior else 'active_snapshot_rootfs_lower', expected=key, role=role)
        for kind in ('data', 'index'):
            try:
                name = guest.get('upper', {}).get(kind)
                if not name:raise ValueError('upper path missing')
                add(local_path(name, config, process), 'rootfs_upper_'+kind, role=role)
            except (OSError, ValueError) as error:unknown('rootfs_upper_'+kind, error, role=role)

    # Include descriptors outside env, and unlinked files; list failures are evidence.
    members = pids(group)
    for pid in members:
        root = proc_root/str(pid)
        try:
            identity = process_identity(root); processes[pid] = identity
            membership=next(line[3:] for line in identity['cgroup'].splitlines() if line.startswith('0::'))
            if not (Path('/sys/fs/cgroup')/membership.lstrip('/')).is_relative_to(group):
                raise ValueError('enumerated process no longer belongs to this service cgroup')
            for path in (root/'fd').iterdir():
                try:
                    s = path.stat()
                    if stat.S_ISREG(s.st_mode):
                        add(path, 'open_regular_file', expected=(s.st_dev, s.st_ino), process=pid, follow=True,
                            source='open_fd:'+os.readlink(path))
                    elif capture_layers and stat.S_ISBLK(s.st_mode):
                        special_references.append(dict(path=str(path),device=s.st_dev,inode=s.st_ino,rdev=s.st_rdev,
                            process=pid,source='open_block_fd',region=None))
                except OSError as error:unknown('open_fd', error, path=str(path), pid=pid)
            # Closed descriptors can still leave a file-backed mapping alive.
            for line in (root/'maps').read_text().splitlines():
                region = mapping_line(line)
                if region is None:
                    unknown('process_maps', 'unparsed maps row', pid=pid); continue
                if not region['inode']:continue  # Anonymous memory is not a file-cache candidate.
                key = (region['device'], region['inode'])
                mapped = region['mapping_path']
                path = root/'map_files'/f"{region['start']:x}-{region['end']:x}"
                # map_files can require a capability absent in a root container.
                # A pathname fallback is accepted only after matching device:inode.
                if mapped.startswith('/') and not mapped.endswith(' (deleted)'):
                    fallback = root/'root'/mapped.lstrip('/')
                    try:
                        s = fallback.stat()
                        if stat.S_ISREG(s.st_mode) and (s.st_dev, s.st_ino) == key:path = fallback
                    except OSError:pass
                add(path, 'mapped_file', expected=key, process=pid, follow=True, source='mapping:'+mapped, region=dict(region, pid=pid, start_ticks=identity['start_ticks']))
            if process_identity(root) != identity:raise ValueError('process identity/cgroup changed during inventory')
        except (OSError, ValueError) as error:unknown('process_inventory', error, pid=pid)
    for key, initial_row in prior.items():
        add(initial_row['path'], 'initial_lower', expected=key)

    inventory_end=time.monotonic_ns();inventory_cpu=time.thread_time_ns()
    for key, row in objects.items():
        begin = time.monotonic_ns(); thread_cpu = time.thread_time_ns(); errors = []
        try:
            # Reuse the original immutable-lower observation: no second mincore.
            if key in prior:
                value = prior[key]
                if not value['stable'] or (value['size'], value['mtime_ns']) != (row['size'], row['mtime_ns']):
                    raise ValueError('initial lower metadata differs from original query')
                row.update(status='OBSERVED', resident_bytes=value['resident_bytes'], residency=value,
                           query_reused=True, filesystem='INITIAL_REGULAR_LOWER')
                continue
            for alias in row['aliases']:
                pinned = readable = None
                try:
                    pid = alias['process']
                    if pid is not None and process_identity(proc_root/str(pid)) != processes[pid]:
                        raise ValueError('process identity/cgroup changed before query')
                    flags = os.O_PATH|os.O_CLOEXEC|(0 if alias['follow'] else os.O_NOFOLLOW)
                    pinned = os.open(alias['path'], flags); s = os.fstat(pinned)
                    if not stat.S_ISREG(s.st_mode) or (s.st_dev, s.st_ino) != key:raise ValueError('pinned inode differs')
                    magic = filesystem_magic(pinned); row['filesystem_magic'] = magic
                    row['nlink'] = s.st_nlink; row['queried_alias'] = alias
                    if magic in (0x01021994, 0x958458f6):
                        row.update(status='SEPARATE_SHMEM_OR_HUGETLB', resident_bytes=None,
                                   reason='Not disk-file cache; shmem/swap or hugetlb needs separate accounting')
                        break
                    readable = os.open('/proc/self/fd/'+str(pinned), os.O_RDONLY|os.O_CLOEXEC)
                    value = mincore_file(alias['path'], file_descriptor=readable, live=True)
                    if (value['device'], value['inode']) != key:raise ValueError('query inode differs')
                    if pid is not None and process_identity(proc_root/str(pid)) != processes[pid]:
                        raise ValueError('process identity/cgroup changed during query')
                    stable = value['stable'] and (value['size'], value['mtime_ns']) == (row['size'], row['mtime_ns'])
                    row.update(status='OBSERVED' if stable else 'CHANGED_DURING_QUERY',
                               resident_bytes=value['resident_bytes'], residency=value, query_reused=False,
                               change_phase='NONE' if stable else 'BETWEEN_INVENTORY_AND_QUERY' if value['stable'] else 'DURING_QUERY')
                    break
                except (OSError, ValueError) as error:errors.append(dict(path=alias['path'], error=str(error)))
                finally:
                    if readable is not None:os.close(readable)
                    if pinned is not None:os.close(pinned)
            else:row.update(status='QUERY_FAILED', errors=errors)
        except (OSError, ValueError) as error:row.update(status='QUERY_FAILED', error=str(error))
        finally:row.update(start_monotonic_ns=begin, end_monotonic_ns=time.monotonic_ns(), cpu_ns=time.thread_time_ns()-thread_cpu)
    queries_end=time.monotonic_ns();queries_cpu=time.thread_time_ns()
    final_members = pids(group)
    if final_members != members:unknown('cgroup_members', 'process set changed', before=members, after=final_members)
    for pid, identity in processes.items():
        try:
            if process_identity(proc_root/str(pid)) != identity:raise ValueError('identity or cgroup changed')
        except (OSError, ValueError) as error:unknown('process_recheck', error, pid=pid)

    recheck_end=time.monotonic_ns();recheck_cpu=time.thread_time_ns()
    rows = list(objects.values())
    for row in rows:
        row['primary_category']=primary_category(row)
    observed = [r for r in rows if r['status']=='OBSERVED']
    failures = [r for r in rows if r['status'] not in ('OBSERVED', 'SEPARATE_SHMEM_OR_HUGETLB')]
    def subtotal(selected):return sum(r['resident_bytes'] for r in selected if r['status']=='OBSERVED')
    value = dict(status='PARTIAL' if unresolved or failures else 'OBSERVED',
        scope='Endpoint regular files: private-service env, all active rootfs lower/upper references, service-cgroup open regular files and file mappings. Not all host RAM or all host page cache.',
        start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(), cpu_ns=time.thread_time_ns()-cpu,
        page_size=os.sysconf('SC_PAGE_SIZE'), permission_evidence=permission,
        permission_basis='Host-root CAP_FOWNER and full identity UID mapping checked; not an unprivileged all-resident vector.',
        files=rows, unique_objects=len(rows), queried_regular_objects=sum(r.get('query_reused') is False for r in rows),
        reused_initial_objects=sum(bool(r.get('query_reused')) for r in rows),
        stable_regular_resident_bytes=subtotal(observed),
        new_stable_regular_resident_bytes=subtotal([r for r in observed if (r['device'],r['inode']) not in prior]),
        categories={c:dict(objects=sum(c in r['categories'] for r in rows),
                           stable_resident_bytes=subtotal([r for r in observed if c in r['categories']]))
                    for c in sorted({c for r in rows for c in r['categories']})},
        primary_categories={c:dict(objects=sum(r['primary_category']==c for r in rows),
                                  stable_resident_bytes=subtotal([r for r in observed if r['primary_category']==c]),
                                  unknown_or_changed=sum(r['primary_category']==c and r['status'] not in ('OBSERVED','SEPARATE_SHMEM_OR_HUGETLB') for r in rows))
                            for c in sorted({r['primary_category'] for r in rows})},
        category_policy='Tags overlap; primary categories do not. Sum object rows once, never sum overlapping tags.',
        unresolved=unresolved, excluded_entries=excluded, symlinks=symlinks, process_identities=processes,
        unknown_objects=len(failures), separate_shmem_or_hugetlb_objects=sum(r['status']=='SEPARATE_SHMEM_OR_HUGETLB' for r in rows),
        exclusions=['VMM anonymous guest RAM', 'ublk/block-device cache (different cache layer)',
                    'outer loop backing image (different cache layer)', 'unreferenced files outside private env',
                    'closed/unlinked and unmapped files', 'objects created or deleted outside the observation window'],
        interpretation='Stable bytes are an observed subtotal, not zero for unknown objects; sequential and non-atomic. dev:inode dedup is a VFS object union, not a proof across filesystem aliases. Do not add current/file/PSS/device cache. PARTIAL must be shown, not silently accepted as full coverage.')

    if capture_layers:
        value.update(owned_roots=list(map(str, trees)), special_block_references=special_references,
            query_window_regular=query_window_summary(rows, unresolved),
            stages=dict(
                inventory=dict(start_monotonic_ns=start,end_monotonic_ns=inventory_end,cpu_ns=inventory_cpu-cpu),
                queries=dict(start_monotonic_ns=inventory_end,end_monotonic_ns=queries_end,cpu_ns=queries_cpu-inventory_cpu),
                process_recheck=dict(start_monotonic_ns=queries_end,end_monotonic_ns=recheck_end,cpu_ns=recheck_cpu-queries_cpu)),
            scope=value['scope'].replace('private-service env,', 'private-service env/run/tmp,'))
        value.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
    return value
