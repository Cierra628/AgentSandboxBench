"""Host cgroup file accounting, mapped PSS and non-faulting file residency checks."""
import csv
from contextlib import nullcontext
import ctypes
import array
import fcntl
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

CGROOT = Path('/sys/fs/cgroup')
PAGE = os.sysconf('SC_PAGE_SIZE')
STAT_KEYS = ('file', 'anon', 'shmem', 'kernel', 'file_mapped', 'file_dirty', 'file_writeback')
_MINCORE_BITS = bytes(value & 1 for value in range(256))


def count_resident(vector):
    # Only bit 0 is defined; translate/count run in C rather than Python per page.
    return bytes(vector).translate(_MINCORE_BITS).count(b'\x01')


def measurement_group():
    """Require the fresh systemd slice created by run-host-file-monitor.sh."""
    unit = os.environ.get('ASB_HOST_FILE_SLICE', '')
    if not re.fullmatch(r'asbhost-[a-z0-9]+\.slice', unit):
        raise ValueError('host file monitoring requires scripts/run-host-file-monitor.sh')
    value = next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    parts = Path(value).parts
    if unit not in parts:
        raise ValueError('controller was not born inside the measurement slice')
    index = parts.index(unit)
    return CGROOT.joinpath(*parts[1:index+1]), unit


def counters(group, *, extra_keys=()):
    values = dict(line.split() for line in (group/'memory.stat').read_text().splitlines())
    if not {'file', 'anon', 'shmem'} <= values.keys():
        raise ValueError('required cgroup memory counters missing')
    if not set(extra_keys)<=values.keys():raise ValueError('requested extra cgroup memory counters missing')
    return dict(current=int((group/'memory.current').read_text()),
                **{key: int(values[key]) if key in values else None for key in (*STAT_KEYS,*extra_keys)})


def controller_state(group):
    return dict(path=str(group),controllers=(group/'cgroup.controllers').read_text().strip(),
                subtree_control=(group/'cgroup.subtree_control').read_text().strip(),
                io_stat_exists=(group/'io.stat').exists(),
                io_stat=(group/'io.stat').read_text() if (group/'io.stat').exists() else None)


def pids(group):
    found = set()
    for path in group.rglob('cgroup.procs'):
        try: found.update(map(int, path.read_text().split()))
        except FileNotFoundError: pass  # A child cgroup was removed during enumeration.
    return sorted(found)


def pss_snapshot(group, proc=Path('/proc')):
    before = time.monotonic_ns()
    entries, gone = [], []
    for pid in pids(group):
        root = proc/str(pid)
        try:
            start = (root/'stat').read_text().rsplit(')', 1)[1].split()[19]
            values = dict(line.split(':', 1) for line in (root/'smaps_rollup').read_text().splitlines() if ':' in line)
            if 'Pss' not in values: raise ValueError('PSS unavailable')
            row = dict(pid=pid, start_ticks=start, comm=(root/'comm').read_text().strip(),
                       **{key: int(values[field].split()[0])*1024 if field in values else None
                          for key, field in [('pss', 'Pss'), ('pss_anon', 'Pss_Anon'),
                                             ('pss_file', 'Pss_File'), ('pss_shmem', 'Pss_Shmem')]})
            if (root/'stat').read_text().rsplit(')', 1)[1].split()[19] != start:
                raise ProcessLookupError('PID reused')
            membership = next(line[3:] for line in (root/'cgroup').read_text().splitlines() if line.startswith('0::'))
            if not (proc == Path('/proc') and (CGROOT/membership.lstrip('/')).is_relative_to(group)):
                if proc == Path('/proc'): raise ProcessLookupError('process left measured group')
            entries.append(row)
        except (FileNotFoundError, ProcessLookupError): gone.append(pid)
    totals = {key: sum(row[key] for row in entries) if all(row[key] is not None for row in entries) else None
              for key in ('pss', 'pss_anon', 'pss_file', 'pss_shmem')}
    return dict(status='PARTIAL' if gone else 'OBSERVED', start_monotonic_ns=before,
                end_monotonic_ns=time.monotonic_ns(), processes=entries, disappeared_pids=gone, **totals)


def mincore_file(path, *, block_identity=None, block_length=None, include_resident_indices=False, live=False):
    """Only mmap(PROT_NONE)+mincore; no payload read, touch, lock or readahead."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | (0 if block_identity is not None else os.O_NOFOLLOW))
    try:
        info = os.fstat(fd)
        is_block=stat.S_ISBLK(info.st_mode)
        length=info.st_size
        if block_identity is not None:
            if not is_block or (info.st_dev,info.st_ino,info.st_rdev)!=tuple(block_identity):
                raise ValueError('block backing identity mismatch')
            capacity=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,capacity,True)  # BLKGETSIZE64, no content read.
            if not block_length or capacity[0]!=block_length:raise ValueError('block backing capacity mismatch')
            length=block_length
        elif not stat.S_ISREG(info.st_mode): raise ValueError('residency input must be a regular backing file')
        identity = dict(device=info.st_dev, inode=info.st_ino, size=info.st_size, mtime_ns=info.st_mtime_ns)
        pages = (length+PAGE-1)//PAGE
        resident = 0; indices = []
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
        libc.mmap.restype = ctypes.c_void_p
        libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte)]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        # Bounded vector size even for a large sparse image; PROT_NONE prevents accidental data reads.
        for offset in range(0, length, 256*2**20):
            size = min(256*2**20, length-offset)
            address = libc.mmap(None, size, 0, 1, fd, offset)  # MAP_SHARED, PROT_NONE.
            if address == ctypes.c_void_p(-1).value: raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            try:
                vector = (ctypes.c_ubyte*((size+PAGE-1)//PAGE))()
                if libc.mincore(address, size, vector): raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
                resident += count_resident(vector)
                if include_resident_indices:
                    indices.extend(offset//PAGE+i for i,value in enumerate(vector) if value & 1)
            finally:
                if libc.munmap(address, size): raise OSError(ctypes.get_errno(), 'munmap failed')
        last = os.fstat(fd)
        same_identity=(last.st_dev,last.st_ino,last.st_rdev)==(info.st_dev,info.st_ino,info.st_rdev)
        metadata_changed=(last.st_size,last.st_mtime_ns)!=(info.st_size,info.st_mtime_ns)
        if is_block:
            final_capacity=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,final_capacity,True)
            stable=same_identity and final_capacity[0]==length
        else:stable=same_identity and not metadata_changed
        result = dict(path=str(path), **identity, pages=pages, resident_pages=resident,
                    resident_bytes=resident*PAGE, stable=stable,metadata_changed=metadata_changed,
                    block_device=is_block,rdev=info.st_rdev,mapping_length=length)
        if include_resident_indices: result['resident_indices'] = indices
        if live: result.update(identity_stable=same_identity,size_stable=last.st_size==info.st_size)
        return result
    finally: os.close(fd)


def backing_roots(source, mount, image, *, cache_seed=None, restore=False):
    """Keep existing backing roots first, then the confirmed missing deps/log/state trees."""
    roots = [source/'image-cache/commits', mount/'env/image-cache', image]
    if cache_seed is not None: roots.append(cache_seed/'memory/volume.ext4')
    if restore: roots += [mount/'env/snapshot-store', mount/'env/snapshot-local-cache']
    return roots + [source/'deps', mount/'env/deps', mount/'env/logs',
                    mount/'env/persisted-sandboxes', mount/'env/template-builds']


def residency_check(group, roots, output, *, live=False, before_last_file=None):
    """Check listed retained files before owned mounts and services are cleaned."""
    started = time.monotonic_ns(); cpu = time.process_time_ns()
    before = counters(group); seen = set(); files = []; errors = []
    if before_last_file is not None:
        assert not live and roots and roots[-1].is_file(), "final callback requires one explicit last file"
    for root in roots:
        if before_last_file is not None and root==roots[-1]:before_last_file()
        if not root.exists(): raise FileNotFoundError(root)
        candidates=sorted(root.rglob('*')) if root.is_dir() else [root]
        for path in candidates:
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode): continue
                key = (info.st_dev, info.st_ino)
                if key in seen: continue
                seen.add(key)
                if live:
                    query_start=time.monotonic_ns();row=mincore_file(path,live=True)
                    row.update(start_monotonic_ns=query_start,end_monotonic_ns=time.monotonic_ns());files.append(row)
                else:files.append(mincore_file(path))
            except (FileNotFoundError, OSError) as error:
                errors.append(dict(path=str(path), error=repr(error)))
    after = counters(group)
    resident = sum(row['resident_bytes'] for row in files)
    deficit = max(0, resident-max(before['file'], after['file']))
    valid=all(row['identity_stable'] and row['size_stable'] if live else row['stable'] for row in files)
    result = dict(status='OBSERVED' if not errors and valid else 'INCOMPLETE',
        start_monotonic_ns=started, end_monotonic_ns=time.monotonic_ns(),
        roots=list(map(str, roots)), files=files, errors=errors, page_size=PAGE,
        resident_bytes=resident, cgroup_before=before, cgroup_after=after,
        minimum_unaccounted_or_timing_bytes=deficit,
        file_residency_lower_bound_bytes=max(resident, before['file'], after['file']),
        accounting='SHORTFALL_REQUIRES_RESOLUTION' if deficit else 'NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING',
        scope='Retained regular files under listed roots (inputs, cache, deps, logs/state, snapshots when listed) and explicit loop backing images, deduplicated by device+inode; excludes removed files and ublk device cache. Not guest cache attribution.',
        comparison='Different times and scopes. Never add all mincore bytes to file: they may overlap. PSS covers mappings, not unmapped file cache.',
        cpu_ns=time.process_time_ns()-cpu)
    if live:result.update(observation_mode='LIVE',metadata_changed_files=[row['path'] for row in files if row['metadata_changed']],
        metadata_policy='Identity and size must remain stable; mtime changes are recorded, not treated as an invalid residency query. Sequential observations, not an atomic snapshot.')
    Path(output).write_text(json.dumps(result, indent=2)+'\n')
    return result


class HostFileMonitor:
    def __init__(self, output, group, *, include_pss=True, extra_stat_keys=()):
        self.output, self.group = Path(output), Path(group)
        self.include_pss = include_pss
        self.extra_stat_keys=tuple(extra_stat_keys)
        self.output.mkdir(parents=True, exist_ok=False)
        self.stop_event = threading.Event(); self.ready = threading.Event(); self.error = None
        self.count = 0; self.cost_ns = 0; self.cpu_ns = 0
        self.thread = threading.Thread(target=self.collect, name='host-file-monitor', daemon=True)
        self.thread.start()
        if not self.ready.wait(5): raise TimeoutError('host collector did not become ready')
        if self.error: raise RuntimeError(self.error)

    def collect(self):
        cpu = time.thread_time_ns(); started = time.monotonic_ns(); next_pss = started
        try:
            with (self.output/'memory.tsv').open('x', buffering=1) as stream, ((self.output/'pss.jsonl').open('x', buffering=1) if self.include_pss else nullcontext(None)) as pss:
                writer = csv.DictWriter(stream, fieldnames=['monotonic_ns', 'elapsed_ns', 'read_duration_ns', 'current', *STAT_KEYS, *self.extra_stat_keys], delimiter='\t')
                writer.writeheader()
                while True:
                    now = time.monotonic_ns(); values = counters(self.group,extra_keys=self.extra_stat_keys)
                    duration = time.monotonic_ns()-now; self.cost_ns += duration
                    writer.writerow(dict(monotonic_ns=now, elapsed_ns=now-started, read_duration_ns=duration, **values))
                    self.count += 1; self.ready.set()
                    if pss is not None and now >= next_pss:
                        pss.write(json.dumps(pss_snapshot(self.group))+'\n'); next_pss=now+10**9
                    if self.stop_event.is_set(): break
                    if now-started > 1800*10**9: raise TimeoutError('host collector lifetime exceeded')
                    self.stop_event.wait(max(0, .1-(time.monotonic_ns()-now)/1e9))
        except BaseException as error:
            self.error = repr(error); self.ready.set()
        finally:
            self.cpu_ns = time.thread_time_ns()-cpu

    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=10)
        if self.thread.is_alive(): raise TimeoutError('host collector still running')
        receipt = dict(status='FAIL' if self.error else 'PASS', error=self.error, cgroup=str(self.group),
                       samples=self.count, interval_seconds=.1, pss_interval_seconds=1 if self.include_pss else None,
                       thread_cpu_ns=self.cpu_ns, counter_read_total_ns=self.cost_ns,
                       scope='Fresh rollout slice: input preparation, controller, private service and all its descendants; host file includes shmem, not guest Cached.')
        (self.output/'result.json').write_text(json.dumps(receipt, indent=2)+'\n')
        if self.error: raise RuntimeError(self.error)
        return receipt


def linear_reference(a, b, n):
    if not isinstance(n, int) or isinstance(n, bool) or n < 1 or not math.isfinite(a) or not math.isfinite(b) or a <= 0 or b < 0:
        raise ValueError('require finite A>0, B>=0 and integer N>=1')
    return dict(single_bytes=a, multiple_bytes=b, sandboxes=n, linear_reference_bytes=n*a,
                reduction_vs_linear_reference=(n*a-b)/(n*a),
                meaning='Extrapolation from one sandbox, not a measured no-sharing control; common overhead and cache state affect the ratio.')
