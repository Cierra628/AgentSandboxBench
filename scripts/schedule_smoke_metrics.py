"""Explicit full-profile schedule smoke: seven VMM PSS reads and 19 initial lowers."""
import json
from pathlib import Path
import time
import traceback

from host_file_monitor import counters, mincore_file
from host_prefix_compare import drives_for_vm
from host_rootfs_layers import file_identity, rootfs_inventory, validate_rootfs_inventory
from host_rollout_shared import opened_regular_files
from host_sandbox_monitor import Binding, read
from prettier_no_proxy import check_cache, install, prepare_metadata


def options(args, *, native=False):
    if not getattr(args, 'schedule_smoke_metrics', False):
        return False
    prefix = 'prettier6604_' if native else ''
    excluded = ('role', 'shared_cache', 'root_count', 'snapshot_cache_pair')
    variants = ('light_monitor', 'host_shared_files', 'host_prefix_count', 'host_full_rootfs_mode',
                'host_scaling_mode', 'host_restore_short', 'host_matched_task', 'host_independent_phase',
                'host_current_pair', 'damon_observe', 'damon_disable', 'host_file_monitor')
    if (getattr(args, prefix + 'schedule', 'grpo') not in ('grpo', 'bpo', 'tvcache') or
            getattr(args, prefix + 'monitor_mode', 'continuous') != 'continuous' or
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


def select_initial(roots, expected, roles):
    keys = {(v['device'], v['inode']) for v in expected}
    if len(keys) != 19 or len(roles) != 7 or roots['status'] != 'IDENTIFIED':
        raise ValueError('expected seven live VMMs and 19 distinct initial lower inodes')
    for role in roles:
        present = {(r['device'], r['inode']) for r in roots['guests'][role]['lowers'] if r['status'] == 'IDENTIFIED'}
        if not keys <= present:
            raise ValueError('initial lower identity missing for role ' + role)
    selected = [obj for obj in roots['objects'] if (obj['device'], obj['inode']) in keys]
    if len(selected) != 19 or any(set(obj['users']) != set(roles) for obj in selected):
        raise ValueError('initial lower aliases or sharing differ')
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


def observe(container, out, guests, paths_end):
    out = Path(out)
    value = dict(status='PENDING', original_paths_end_monotonic_ns=paths_end,
                 start_monotonic_ns=time.monotonic_ns())
    cpu = time.thread_time_ns()
    try:
        binding = Binding(container, out)
        bindings = {role: binding.resolve(sid) for role, sid in guests.items()}
        inventory = {role: drives_for_vm(vm) for role, vm in bindings.items()}
        env = out.parent.parent / 'env'; cached = check_cache(env)
        expected = [file_identity(env / name) for name in cached['local_files']]
        roots = rootfs_inventory(bindings, inventory, opened_regular_files(binding.cgroup), {})
        selected = select_initial(roots, expected, guests)
        value.update(bindings=bindings, drives=inventory, rootfs_inventory=roots,
                     cgroup=str(binding.cgroup), pss=pss_snapshot(binding, guests, bindings))
        begin = time.monotonic_ns(); thread_cpu = time.thread_time_ns(); before = counters(binding.cgroup); files = []
        for obj in selected:
            row = mincore_file(Path(obj['path']))
            if not row['stable'] or any(row[k] != obj[k] for k in ('device', 'inode', 'size', 'mtime_ns')):
                raise ValueError('initial lower changed during mincore')
            files.append(dict(users=obj['users'], **row))
        value['p1'] = dict(files=files, unique_files=len(files), resident_bytes=sum(f['resident_bytes'] for f in files),
                           start_monotonic_ns=begin, end_monotonic_ns=time.monotonic_ns(),
                           cpu_ns=time.thread_time_ns()-thread_cpu, counters_before=before,
                           counters_after=counters(binding.cgroup))
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
