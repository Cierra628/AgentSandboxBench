"""Explicit full-profile schedule smoke: seven VMM PSS reads and 19 initial lowers."""
import json
import os
from pathlib import Path
import time
import traceback

from host_file_monitor import counters, mincore_file
from host_prefix_compare import drives_for_vm
from host_rootfs_layers import file_identity, rootfs_inventory, validate_rootfs_inventory
from host_rollout_shared import opened_regular_files
from host_sandbox_monitor import Binding, read
from prettier_no_proxy import check_cache, install, prepare_metadata
from experiment_memory import HOST_SCOPE, resolve_scope, snapshot


def options(args, *, native=False):
    expanded = getattr(args, 'schedule_expanded_files', False)
    layers = getattr(args, 'schedule_cache_layers', False)
    method=getattr(args,'schedule_block_cache_method',None)
    if getattr(args,'schedule_memory_backing_links',False) and not layers:
        raise ValueError('memory backing links require schedule cache layers')
    if getattr(args,'schedule_split_ram_cache',False) and (not layers or method!='cachestat'):
        raise ValueError('split RAM cache requires cache layers and explicit cachestat')
    if method is not None and (method not in ('mincore','cachestat','compare') or not layers):
        raise ValueError('explicit block cache method requires schedule cache layers')
    if layers and not expanded:raise ValueError('schedule cache layers require expanded schedule files')
    if not getattr(args, 'schedule_smoke_metrics', False):
        if expanded:raise ValueError('expanded schedule files require schedule smoke metrics')
        return False
    prefix = 'prettier6604_' if native else ''
    control=getattr(args,'schedule_sampling_control',False)
    mode=getattr(args,prefix+'monitor_mode','continuous')
    schedule=getattr(args,prefix+'schedule','grpo')
    if expanded and (control or mode!='continuous'):
        raise ValueError('expanded schedule files require continuous monitoring without sampling control')
    if control and schedule!='grpo':raise ValueError('sampling control requires complete GRPO')
    excluded = ('role', 'shared_cache', 'root_count', 'snapshot_cache_pair')
    variants = ('light_monitor', 'host_shared_files', 'host_prefix_count', 'host_full_rootfs_mode',
                'host_scaling_mode', 'host_restore_short', 'host_matched_task', 'host_independent_phase',
                'host_current_pair', 'damon_observe', 'damon_disable', 'host_file_monitor')
    if (getattr(args, prefix + 'schedule', 'grpo') not in ('grpo', 'bpo', 'tvcache') or
            not (mode=='continuous' or control and mode=='off') or
            any(getattr(args, prefix + key, None) for key in excluded) or
            any(getattr(args, key, None) for key in variants)):
        raise ValueError('schedule smoke requires ordinary complete seven-role full monitoring')
    if native and not (getattr(args, 'prettier6604_task', None) and
                       getattr(args, 'prettier6604_export', None) and getattr(args, 'immutable_cache_seed', None)):
        raise ValueError('schedule smoke requires the pinned Prettier export and local immutable seed')
    return True


def install_offline(mount, out):
    seed = Path(out) / 'smoke-local-input'
    seed.mkdir()
    prepare_metadata(seed / 'local-no-proxy-metadata')
    return install(seed, mount, cache_root=Path(mount) / 'env')


def select_initial(roots, expected, roles, *, expected_count=19):
    keys = {(v['device'], v['inode']) for v in expected}
    if len(keys) != expected_count or len(roles) != 7 or roots['status'] != 'IDENTIFIED':
        raise ValueError(f'expected seven live VMMs and {expected_count} distinct initial lower inodes')
    for role in roles:
        present = {(r['device'], r['inode']) for r in roots['guests'][role]['lowers'] if r['status'] == 'IDENTIFIED'}
        if not keys <= present:
            raise ValueError('initial lower identity missing for role ' + role)
    selected = [obj for obj in roots['objects'] if (obj['device'], obj['inode']) in keys]
    if len(selected) != expected_count or any(set(obj['users']) != set(roles) for obj in selected):
        raise ValueError('initial lower aliases or sharing differ')
    return selected


def select_initial_copies(roots, expected, roles, *, expected_count=13):
    """Select pinned image layers, retaining every active inode copy."""
    def logical(row):
        ref = row['reference']
        if not ref.get('digest') or ref.get('size') != row['size']:
            raise ValueError('initial layer digest or descriptor size missing or inconsistent')
        return ref['digest'], row['size']

    roles = set(roles)
    if len(roles) != 7 or set(roots['guests']) != roles or roots['status'] != 'IDENTIFIED':
        raise ValueError('expected seven identified live Python VMMs')
    if any(row['status'] != 'IDENTIFIED' for row in expected):
        raise ValueError('original image references are not identified')
    signature = [logical(row) for row in expected]
    if len(signature) != expected_count or len(set(signature)) != expected_count:
        raise ValueError(f'expected {expected_count} distinct ordered image layers')
    original = {(row['device'], row['inode']) for row in expected}
    if len(original) != expected_count:
        raise ValueError(f'expected {expected_count} original physical image files')
    references = {}
    for role in sorted(roles):
        guest = roots['guests'][role]
        rows = guest['lowers'][:expected_count]
        if (guest['status'] != 'IDENTIFIED' or len(rows) != expected_count or
                any(row['status'] != 'IDENTIFIED' for row in rows) or
                [logical(row) for row in rows] != signature):
            raise ValueError('original image layer order or identity differs for role ' + role)
        keys = [(row['device'], row['inode']) for row in rows]
        if len(set(keys)) != expected_count:
            raise ValueError('original image layer physical aliases within role ' + role)
        for row, key in zip(rows, keys):
            references.setdefault(key, []).append((role, row))
    if not original <= references.keys():
        raise ValueError('original parent image files are no longer active')
    selected = [obj for obj in roots['objects'] if (obj['device'], obj['inode']) in references]
    if (len(selected) != len(references) or
            len({(obj['device'], obj['inode']) for obj in selected}) != len(references)):
        raise ValueError('active image physical objects missing or duplicated')
    for obj in selected:
        refs = references[(obj['device'], obj['inode'])]
        if (obj['status'] == 'UNKNOWN' or set(obj['users']) != {role for role, _ in refs} or
                any(row['path'] not in obj['aliases'] or
                    any(row[k] != obj[k] for k in ('size', 'mtime_ns')) for _, row in refs)):
            raise ValueError('active image file users, aliases or metadata differ')
    return selected


def pss_snapshot(binding, guests, bindings):
    start = time.monotonic_ns(); cpu = time.thread_time_ns(); rows = []
    if len(guests) != 7 or len({vm['pid'] for vm in bindings.values()}) != 7:
        raise ValueError('seven distinct live VMMs required for PSS/N')
    for role, sid in guests.items():
        vm = bindings[role]
        if binding.resolve(sid) != vm:
            raise ValueError('sandbox/VMM identity changed before PSS')
        before = time.monotonic_ns(); value = read(vm['pid'], pss=True)
        if value['start_ticks'] != vm['start_ticks'] or value.get('pss_kib') is None:
            raise ValueError('VMM PSS missing or identity changed')
        uid = (Path('/proc') / str(vm['pid'])).stat().st_uid
        if uid != 0:
            raise ValueError('expected root-owned VMM')
        rows.append(dict(role=role, sandbox_id=sid, identity=vm, uid=uid,
                         start_monotonic_ns=before, end_monotonic_ns=time.monotonic_ns(), **value))
    for role, sid in guests.items():
        if binding.resolve(sid) != bindings[role]:
            raise ValueError('sandbox/VMM identity changed during PSS')
    total = sum(row['pss_kib'] for row in rows) * 1024
    return dict(status='OBSERVED', start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(),
                cpu_ns=time.thread_time_ns()-cpu, processes=rows, total_bytes=total,
                per_sandbox_bytes=total/7, denominator=7,
                scope='Seven root-owned VMMs only; sequential common-window reads, kernel proportional sharing; not sum of individual peaks')


def observe(container, out, guests, paths_end, *, expanded=False, cache_layers=False, block_cache_method=None, split_ram=False,
            initial_files=None, initial_file_count=19, initial_reference_mode='shared_initial_inodes', memory_backing_links=False):
    if memory_backing_links and not cache_layers:raise ValueError('memory backing links require cache layers')
    if initial_reference_mode not in ('shared_initial_inodes', 'all_active_image_layer_copies'):
        raise ValueError('unknown initial image reference mode')
    if initial_reference_mode == 'all_active_image_layer_copies' and (initial_files is None or initial_file_count not in (7,8,9,10,13)):
        raise ValueError('all active image copies requires explicit Cython seven-layer, text editing eight-layer, C4 nine-layer, Django ten-layer or Python thirteen-layer input')
    if split_ram and (not cache_layers or block_cache_method!='cachestat'):
        raise ValueError('split RAM cache requires cache layers and explicit cachestat')
    if block_cache_method is not None and (not cache_layers or block_cache_method not in ('mincore','cachestat','compare')):
        raise ValueError('explicit block cache method requires schedule cache layers')
    if cache_layers and not expanded:raise ValueError('schedule cache layers require expanded files')
    out = Path(out)
    value = dict(status='PENDING', original_paths_end_monotonic_ns=paths_end,
                 start_monotonic_ns=time.monotonic_ns())
    cpu = time.thread_time_ns()
    try:
        binding = Binding(container, out)
        # cgroup and p1 counters retain their original scope. New totals have
        # explicit names; a Docker subgroup is never promoted to a parent total.
        service = getattr(binding, 'private_service_cgroup', None)
        if not isinstance(service, Path):service = binding.cgroup
        context = resolve_scope(service)
        value.update(host_scope=HOST_SCOPE, legacy_host_scope=('private_service_cgroup' if binding.cgroup==service
                     else 'experiment_slice_cgroup'), legacy_cgroup=str(binding.cgroup),
                     private_service_cgroup=str(service), experiment_memory_context=context)
        bindings = {role: binding.resolve(sid) for role, sid in guests.items()}
        inventory = {role: drives_for_vm(vm) for role, vm in bindings.items()}
        value.update(bindings=bindings, drives=inventory, cgroup=str(binding.cgroup))
        env = out.parent.parent / 'env'
        if initial_files is None:
            cached = check_cache(env)
            expected = [file_identity(env / name) for name in cached['local_files']]
        else:
            if initial_file_count not in (7,8,9,10,13):
                raise ValueError('explicit initial files require pinned Cython 7-layer, text editing 8-layer, C4 9-layer, Django 10-layer or Python 13-layer tasks')
            expected = initial_files
        value['initial_reference_files'] = expected
        roots = rootfs_inventory(bindings, inventory, opened_regular_files(binding.cgroup), {})
        value['rootfs_inventory'] = roots
        selected = (select_initial_copies(roots, expected, guests, expected_count=initial_file_count) if initial_reference_mode == 'all_active_image_layer_copies'
                    else select_initial(roots, expected, guests, expected_count=initial_file_count))
        value['initial_layer_selection'] = dict(mode=initial_reference_mode, logical_layer_count=initial_file_count,
            physical_file_count=len(selected), objects=[dict(device=obj['device'], inode=obj['inode'], users=obj['users']) for obj in selected])
        value['pss'] = pss_snapshot(binding, guests, bindings)
        begin = time.monotonic_ns(); thread_cpu = time.thread_time_ns(); before = counters(binding.cgroup); files = []
        for obj in selected:
            row = mincore_file(Path(obj['path']))
            if not row['stable'] or any(row[k] != obj[k] for k in ('device', 'inode', 'size', 'mtime_ns')):
                raise ValueError('initial lower changed during mincore')
            files.append(dict(users=obj['users'], **row))
        value['p1'] = dict(files=files, unique_files=len(files), resident_bytes=sum(f['resident_bytes'] for f in files),
                           reference_mode=initial_reference_mode, logical_layer_count=initial_file_count,
                           start_monotonic_ns=begin, end_monotonic_ns=time.monotonic_ns(),
                           cpu_ns=time.thread_time_ns()-thread_cpu, counters_before=before,
                           counters_after=counters(binding.cgroup))
        value['experiment_memory_p1'] = snapshot(context)
        if os.environ.get('ASB_VMM_CHILD_CGROUP_CHECK'):
            from vmm_child_cgroup_check import endpoint
            value['vmm_child_cgroups']=endpoint(binding,bindings,value['pss'],env,out,files,
                **({'query_owners':False} if os.environ.get('ASB_VMM_ACCOUNTING_ONLY')=='on' else {}))
        if expanded:
            from host_schedule_files import observe_schedule_files
            before = counters(binding.cgroup)
            value['expanded_files'] = observe_schedule_files(env, roots, files, binding.cgroup, drives=inventory,
                **(dict(owned_roots=[env.parent/'run',env.parent/'tmp'],capture_layers=True) if cache_layers else {}))
            value['expanded_files'].update(counters_before=before, counters_after=counters(binding.cgroup))
        if cache_layers:
            from host_schedule_cache_layers import observe_cache_layers
            value['cache_layers'] = observe_cache_layers(env, bindings, inventory, value['expanded_files'], binding.cgroup,
                block_method=block_cache_method or 'mincore',**(dict(split_ram=True) if split_ram else {}))
        if memory_backing_links:
            from host_memory_backing_links import observe as observe_memory_links
            value['memory_backing_links']=observe_memory_links(container,out,guests,bindings,
                value['expanded_files'],value['cache_layers'],opened_regular_files(binding.cgroup))
            if value['memory_backing_links']['status']!='IDENTIFIED':
                raise ValueError('memory backing links incomplete; see memory_backing_links')
        validate_rootfs_inventory(roots)
        if any(binding.resolve(sid) != bindings[role] for role, sid in guests.items()):
            raise ValueError('VMM identity changed during endpoint observation')
        value['status'] = 'OBSERVED'
    except Exception as error:
        value.update(status='FAIL', error=repr(error))
        (out / 'schedule-smoke-failure.txt').write_text(traceback.format_exc())
    value.update(end_monotonic_ns=time.monotonic_ns(), cpu_ns=time.thread_time_ns()-cpu)
    (out / 'schedule-smoke-metrics.json').write_text(json.dumps(value, indent=2)+'\n')
    return value
