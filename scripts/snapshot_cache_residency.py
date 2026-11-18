"""Query snapshot cache PFNs through mincore, without faulting file contents.

Linux x86-64 with BTF and vmemmap_base is required. Only the active reader thread's mincore
lookups are traced. BPF maps carry synchronous results, not lossy event streams.
"""
import ctypes
import errno
import json
import mmap
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import threading

import numpy as np
from measure_file_physical import parse_maps, ram_address
from kernel_cache_metadata import MASK, PRESENT, PHYSICAL_MASK, SWAPPED
from fast_cache_metadata import matching_tags

PAGE = 4096
FILE = 1 << 61
ROW = np.dtype('<u8')
UNKNOWN = (1 << 64)-1


def _query_clock():
    return time.monotonic_ns(), time.thread_time_ns()


def _query_cost(start):
    cpu = time.thread_time_ns(); ended = time.monotonic_ns()
    return dict(cpu_ns=cpu-start[1], elapsed_ns=ended-start[0],
                host_start_ns=start[0], host_end_ns=ended)


class BatchEntries:
    """Read one complete, quiescent ordinary HASH map; never delete entries."""
    def __init__(self, lib, fd, info, *, value_words=2):
        if value_words not in (2, 8):
            raise ValueError('unsupported batch value width')
        self.value_words = value_words
        if (info.get('type'), info.get('bytes_key'), info.get('bytes_value'), info.get('flags')) != ('hash', 8, value_words*8, 0):
            raise ValueError(f'unsupported batch entries layout: {info}')
        capacity = info.get('max_entries', 0)
        if not isinstance(capacity, int) or not 0 < capacity <= 131072:
            raise ValueError(f'unsupported batch entries capacity: {capacity}')
        self.lookup = lib.bpf_map_lookup_batch
        self.lookup.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self.lookup.restype = ctypes.c_int
        self.fd, self.info, self.capacity = fd, dict(info), capacity
        self.keys = np.empty(capacity, dtype='<u8')
        self.values = np.empty((capacity, value_words), dtype='<u8')
        self.buffer_bytes = self.keys.nbytes+self.values.nbytes
        self.last_statistics = None

    def read(self, ordered, token):
        count, cursor = ctypes.c_uint32(self.capacity), ctypes.c_uint64()
        self.last_costs = {}
        started = _query_clock()
        rc = self.lookup(self.fd, None, ctypes.byref(cursor), self.keys.ctypes.data,
                         self.values.ctypes.data, ctypes.byref(count), None)
        self.last_costs['batch_lookup'] = _query_cost(started)
        self.last_statistics = dict(mode='batch', returncode=rc,
            returned_entries=None if rc == -errno.EFAULT else count.value)
        if rc != -errno.ENOENT:
            raise RuntimeError(f'incomplete BPF batch lookup: {self.last_statistics}')
        if count.value > self.capacity:
            raise RuntimeError(f'BPF batch count exceeds capacity: {count.value} > {self.capacity}')
        started = _query_clock()
        keys, values = self.keys[:count.value], self.values[:count.value]
        selected = (values[:, 0] == token) & np.isin(keys, ordered, assume_unique=True)
        payload = map(int, values[selected, 1]) if self.value_words == 2 else (tuple(map(int, row)) for row in values[selected, 1:])
        result = dict(zip(map(int, keys[selected]), payload))
        self.last_costs['batch_filter'] = _query_cost(started)
        return result


# bpftrace map names are fixed at compile time. Give each simultaneous reader
# its own result map so a batch lookup never traverses a live neighbour's map.
READER_BANKS = 7


def _probe_program(pid, *, dispatch_counts=False):
    routes = ' else '.join(
        f'if ($bank == {i}) {{ @entries{i}[@idx[tid]]=(@active[tid],$pfn); }}'
        for i in range(1, READER_BANKS+1))
    clears = ' '.join(f'clear(@entries{i});' for i in range(1, READER_BANKS+1))
    program = r'''
BEGIN { @token[(uint64)0]=((uint64)0,(uint64)0,(uint64)0,(uint64)0); printf("READY\n"); }
tracepoint:syscalls:sys_enter_mincore /pid == TARGET/ {
 $r=@token[(uint64)tid];
 if ($r.0 && args->start >= $r.1 && args->start < $r.2 && args->len <= $r.2-args->start) {
  @active[tid]=$r.0; @bank[tid]=$r.3;
 }
}
tracepoint:syscalls:sys_exit_mincore /pid == TARGET && @active[tid]/ { delete(@active[tid]); delete(@bank[tid]); }
kprobe:filemap_get_entry /@active[tid]/ { @idx[tid]=arg1; }
kretprobe:filemap_get_entry /@active[tid]/ {
 $f=(struct folio *)retval; $pfn=0;
 if (retval && !(retval & 3)) {
  $base=*(uint64*)kaddr("vmemmap_base");
  $pfn=(retval-$base)/sizeof(struct page)+@idx[tid]-$f->index;
 }
 $bank=@bank[tid]; ROUTES delete(@idx[tid]);
}
interval:s:900 { exit(); }
END { clear(@active); clear(@idx); clear(@bank); CLEARS clear(@token); }
'''.replace('TARGET', str(pid)).replace('ROUTES', routes).replace('CLEARS', clears)

    if dispatch_counts:
        program=program.replace('$r=@token', '@nent[(uint64)tid]=@nent[(uint64)tid]+1; $r=@token')
        old=f'/pid == {pid} && @active[tid]/ {{ delete(@active[tid]); delete(@bank[tid]); }}'
        assert old in program
        program=program.replace(old,f'/pid == {pid}/ {{ @nexit[(uint64)tid]=@nexit[(uint64)tid]+1; if (@active[tid]) {{ delete(@active[tid]); delete(@bank[tid]); }} }}')
        program=program.replace('clear(@token);','clear(@token); clear(@nent); clear(@nexit);')
    return program


class SourceTracer:
    """A process-local attachment for direct backing OR immutable lower."""
    lock = threading.Lock()
    sources = {}

    def __init__(self, key, directory, lib, *, dispatch_counts=False):
        self.key = key
        self.dispatch_counts = dispatch_counts
        self.fds, self.info, self.used, self.leased = {}, {}, set(), set()
        self.proc = None
        self.pending_receipts = []
        program = directory/'snapshot-query.bt'
        program.write_text(_probe_program(os.getpid(),dispatch_counts=True) if dispatch_counts else _probe_program(os.getpid()))
        try:
            with (directory/'snapshot-query.stdout').open('x') as stdout, (directory/'snapshot-query.stderr').open('x') as stderr:
                self.proc = subprocess.Popen(['bpftrace','-q','-B','line',program],
                    stdout=stdout, stderr=stderr, env=dict(os.environ,BPFTRACE_MAX_MAP_KEYS='131072'))
            deadline = time.monotonic()+20
            while 'READY' not in (directory/'snapshot-query.stdout').read_text():
                if self.proc.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError(f'shared snapshot probe attach failed: {directory}/snapshot-query.stderr')
                time.sleep(.05)
            required = {'token'} | {f'entries{i}' for i in range(1,READER_BANKS+1)}
            if dispatch_counts:required.update(('nent','nexit'))
            seen = set()
            for info in Path('/proc',str(self.proc.pid),'fdinfo').iterdir():
                match = re.search(r'map_id:\s*(\d+)',info.read_text())
                if not match or (mid:=int(match[1])) in seen:continue
                seen.add(mid)
                d=json.loads(subprocess.check_output(['bpftool','-j','map','show','id',str(mid)],timeout=5))
                name=d['name'].removeprefix('AT_')
                if name not in required:continue
                if (d['type'],d['bytes_key'],d['bytes_value']) != ('hash',8,32 if name=='token' else 8 if name in ('nent','nexit') else 16):
                    raise ValueError(f'unexpected shared probe map layout: {d}')
                fd=lib.bpf_map_get_fd_by_id(mid)
                if fd<0:raise OSError(f'cannot open {name} map')
                self.fds[name],self.info[name]=fd,d
            if set(self.fds)!=required:raise RuntimeError(f'missing shared probe maps: {required-self.fds.keys()}')
        except BaseException:
            self.stop()
            raise

    def detach(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)

    def stop(self):
        try:self.detach()
        finally:
            for fd in self.fds.values():os.close(fd)
            self.fds.clear()

    @classmethod
    def acquire(cls, source, directory, lib, *, dispatch_counts=False):
        key=(os.getpid(),source)
        with cls.lock:
            service=cls.sources.get(key)
            if service is None:
                service=cls.sources[key]=cls(key,directory,lib,dispatch_counts=True) if dispatch_counts else cls(key,directory,lib)
            elif service.proc.poll() is not None:
                raise RuntimeError(f'{source} shared probe exited')
            if dispatch_counts != getattr(service,'dispatch_counts',False):raise RuntimeError('shared tracer counter mode conflict')
            for bank in range(1, READER_BANKS+1):
                if bank not in service.leased:break
            else:raise RuntimeError(f'{source} shared probe has {READER_BANKS} active readers')
            if bank in service.used:
                # Retired reader's offsets and tokens cannot survive a new lease.
                fd=service.fds[f'entries{bank}']; k=ctypes.c_uint64()
                while lib.bpf_map_get_next_key(fd,None,ctypes.byref(k))==0:
                    if lib.bpf_map_delete_elem(fd,ctypes.byref(k)):
                        raise RuntimeError('could not clear retired result map')
                if ctypes.get_errno()!=errno.ENOENT:raise RuntimeError('could not enumerate retired result map')
            service.used.add(bank);service.leased.add(bank)
            return service,bank

    def release(self, bank, receipt, data):
        with self.lock:
            self.leased.remove(bank)
            if self.leased:
                data.update(returncode=self.proc.poll(),tracer_cpu_ns=0,shared_tracer_last_close=False)
                receipt.write_text(json.dumps(data,indent=2)+'\n')
                self.pending_receipts.append(receipt)
                return
            del self.sources[self.key]
            try:
                cpu=None
                if self.proc.poll() is None:
                    cpu=time.clock_gettime_ns(((~self.proc.pid)<<3)|2)
            finally:self.stop()
            data.update(returncode=self.proc.returncode,tracer_cpu_ns=cpu,shared_tracer_last_close=True)
            receipt.write_text(json.dumps(data,indent=2)+'\n')
            # A reader may finish before its tracer. Publish the actual exit
            # status to those receipts only after it exists (never assume zero).
            for path in self.pending_receipts:
                previous=json.loads(path.read_text())
                previous['returncode']=self.proc.returncode
                path.write_text(json.dumps(previous,indent=2)+'\n')


class SnapshotCache:
    def __init__(self, proc, slots, directory, *, file_path=None, batch_lookup=False, dispatch_counts=False):
        # Each batch scan still traverses one quiescent, reader-private map.
        self.batch_entries = None
        self.proc, self.slots, self.directory = proc, slots, directory
        self.regions = self.ram_regions() if file_path is None else []
        paths = set(r['path'] for r in self.regions if r['path'].startswith('/dev/ublkb'))
        if file_path is None:
            assert len(paths) == 1, 'expected one snapshot memory backend'
        path = proc/'root'/next(iter(paths)).lstrip('/') if file_path is None else file_path
        self.fd = os.open(path, os.O_RDONLY)
        st = os.fstat(self.fd)
        assert all((r['device'], r['inode']) == (st.st_dev, st.st_ino) and r['permissions'] == 'rw-p'
                   for r in self.regions if r['path'] in paths), 'backend identity/private mapping mismatch'
        self.identity = (st.st_dev, st.st_ino, st.st_rdev)
        length = max(r['offset']+r['end']-r['start'] for r in self.regions if r['path'] in paths) if file_path is None else st.st_size
        if file_path is not None:
            assert stat.S_ISREG(st.st_mode), 'snapshot layer must be a regular file'
        self.mapping = mmap.mmap(self.fd, length, access=mmap.ACCESS_COPY)
        self.address = ctypes.addressof(ctypes.c_char.from_buffer(self.mapping))
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        self.lib = ctypes.CDLL('libbpf.so.1', use_errno=True)
        self.lib.bpf_map_get_fd_by_id.argtypes = [ctypes.c_uint]
        self.lib.bpf_map_delete_elem.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self.lib.bpf_map_lookup_elem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        self.lib.bpf_map_update_elem.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulonglong]
        self.query_lock = threading.Lock()
        self.token = 0
        self.query_statistics = []
        self.query_costs = []
        self.mapfds = {}
        self.service = self.tracer = None
        self.limit = self.address+((length+PAGE-1)//PAGE)*PAGE
        try:
            self.service,self.bank = SourceTracer.acquire('backend' if file_path is None else 'lower',directory,self.lib,dispatch_counts=dispatch_counts)
            self.tracer = self.service.proc
            name = f'entries{self.bank}'
            self.mapfds = {'token':self.service.fds['token'],'entries':self.service.fds[name]}
            if batch_lookup:
                self.batch_entries = BatchEntries(self.lib,self.mapfds['entries'],self.service.info[name])
            (directory/'snapshot-backend.json').write_text(json.dumps(dict(
                regions=self.regions, identity=dict(device=st.st_dev,inode=st.st_ino,rdev=st.st_rdev),
                block_device=stat.S_ISBLK(st.st_mode), mapping_length=length, page_size=PAGE,
                tracer_pid=self.tracer.pid, reader_bank=self.bank, host_kernel=os.uname().release, query_binding='controller PID, caller native TID, reader mapping and generation',
                method='mincore + synchronous filemap_get_entry return PFN; no content read or fault',
                offset_rule='raw file offsets from saved LSMT metadata' if file_path is not None else 'KVM host + guest PFN*4096 - KVM guest; then VMA offset + host address - VMA start',
                **(dict(batch_lookup=dict(map_info=self.batch_entries.info,
                    receive_buffer_bytes=self.batch_entries.buffer_bytes,
                    mincore_max_gap_pages=2)) if batch_lookup else {})),indent=2)+'\n')
        except BaseException:
            self.close(); raise

    def ram_regions(self):
        raw = (self.proc/'maps').read_text()
        regions = []
        for line, region in zip(raw.splitlines(), parse_maps(raw)):
            if any(s['host'] < region['end'] and s['host']+s['size'] > region['start'] for s in self.slots):
                fields = line.split(maxsplit=5)
                region['path'] = fields[5] if len(fields) == 6 else ''
                if region['path'] and not region['path'].startswith('/dev/ublkb'):
                    assert region['inode'] == 0 or region['path'].startswith('/dev/zero'), 'unexpected RAM backend'
                regions.append(region)
        assert regions
        return regions

    def offsets(self, pfns):
        guest = np.array([int(pfn)*PAGE for pfn in pfns])
        if not len(guest):
            return np.array([], dtype='<u8')
        addresses = np.empty(len(guest), dtype='<u8')
        hits = np.zeros(len(guest), dtype=np.intp)
        for slot in self.slots:
            selected = (guest >= slot['guest']) & (guest < slot['guest']+slot['size'])
            if np.any(selected):
                addresses[selected] = guest[selected]-slot['guest']+slot['host']
            hits += selected
        bad = np.flatnonzero(hits != 1)
        if len(bad):
            index = bad[0]
            raise RuntimeError(f'guest PFN {int(guest[index])//PAGE} has {hits[index]} RAM slots')
        hits.fill(0)
        regions = np.empty(len(guest), dtype=np.intp)
        for index, region in enumerate(self.regions):
            selected = (addresses >= region['start']) & (addresses < region['end'])
            hits += selected
            regions[selected] = index
        assert np.all(hits == 1)
        offsets = np.full(len(guest), (1<<64)-1, dtype='<u8')
        for index, r in enumerate(self.regions):
            selected = regions == index
            if not np.any(selected) or not r.get('path','/dev/ublkb').startswith('/dev/ublkb'):
                continue
            if hasattr(self,'identity'):
                assert (r['device'],r['inode']) == self.identity[:2], 'RAM backend replaced'
                assert r['permissions'] == 'rw-p', 'RAM backend is no longer private'
            offsets[selected] = (r['offset']+addresses[selected]-r['start'])//PAGE
        return offsets

    def query(self, offsets):
        if self.batch_entries is not None:
            started = _query_clock()
        if not self.query_lock.acquire(blocking=False):
            raise RuntimeError('overlapping queries on the same snapshot reader')
        try:
            if self.fd is None:raise RuntimeError('closed snapshot reader')
            st = os.fstat(self.fd)
            assert (st.st_dev, st.st_ino, st.st_rdev) == self.identity
            assert self.tracer.poll() is None
            self.token += 1
            key = ctypes.c_uint64(threading.get_native_id())
            owner = (ctypes.c_uint64*4)(self.token, self.address, self.limit, self.bank)
            fd = self.mapfds['token']
            if self.lib.bpf_map_update_elem(fd, ctypes.byref(key), ctypes.byref(owner), 1):
                raise RuntimeError('could not bind snapshot query to its caller')
            try:
                return self._query_owned(offsets, started if self.batch_entries is not None else None)
            finally:
                if self.lib.bpf_map_delete_elem(fd, ctypes.byref(key)):
                    # A stale TID/range must never survive reuse of this bank.
                    # Keep peers' map descriptors valid until their final close.
                    with self.service.lock:self.service.detach()
                    raise RuntimeError('could not disarm snapshot query; shared probe detached')
        finally:
            self.query_lock.release()

    def _query_owned(self, offsets, started):
        ordered = np.unique(offsets[offsets != np.uint64((1<<64)-1)])
        residency = {}
        if self.batch_entries is not None:
            splits = np.flatnonzero(np.diff(ordered) > 3)+1
            stages = dict(prepare=_query_cost(started))
            scan_started = _query_clock()
        else:
            splits = np.flatnonzero(np.diff(ordered) != 1)+1
        for run in np.split(ordered, splits):
            if not len(run):continue
            if self.batch_entries is not None:
                start = int(run[0]); length = int(run[-1])-start+1
                vector = (ctypes.c_ubyte*length)()
                assert self.libc.mincore(self.address+start*PAGE,length*PAGE,vector) == 0
                # Gap pages may be queried, but never enter original statistics.
                residency.update((offset,vector[offset-start]) for offset in map(int,run))
            else:
                vector = (ctypes.c_ubyte*len(run))()
                assert self.libc.mincore(self.address+int(run[0])*PAGE,len(run)*PAGE,vector) == 0
                residency.update(zip(map(int,run),vector))
        if self.batch_entries is not None:
            stages['mincore_scan'] = _query_cost(scan_started)
            batch = self.batch_entries.read(ordered, self.token)
            stages.update(self.batch_entries.last_costs)
            restore_started = _query_clock()
            result = self._batch_result(offsets, ordered, residency, batch)
            stages['result_restore'] = _query_cost(restore_started)
            total = _query_cost(started)
            self.query_costs.append(dict(query=self.token, **total, stages=stages,
                unmeasured_cpu_ns=total['cpu_ns']-sum(s['cpu_ns'] for s in stages.values()),
                unmeasured_elapsed_ns=total['elapsed_ns']-sum(s['elapsed_ns'] for s in stages.values())))
            return result
        result = []
        missing = 0
        for offset in offsets:
            if int(offset) == (1<<64)-1:
                result.append(0);continue
            key = ctypes.c_uint64(int(offset)); value = (ctypes.c_uint64*2)()
            ok = self.lib.bpf_map_lookup_elem(self.mapfds['entries'], ctypes.byref(key), value)
            missing += ok != 0 or value[0] != self.token
            # Missing query results (including map capacity/missed return probes)
            # remain unknown, never interpreted as a cold cache page.
            entry = (PRESENT|int(value[1])) if ok == 0 and value[0] == self.token and value[1] and residency[int(offset)] & 1 else 0
            result.append(entry)
        self.query_statistics.append(dict(query=self.token,requested_pages=len(ordered),
            missing_kernel_results=missing,mincore_resident_pages=sum(v&1 for v in residency.values()),
            confirmed_pfn_pages=sum(bool(v&PRESENT) for v in result),
            no_file_mapping_pages=int(np.count_nonzero(offsets == np.uint64((1<<64)-1)))))
        return np.array(result, dtype='<u8')

    def _batch_result(self, offsets, ordered, residency, batch):
        """Expand current-token results to original rows, keeping missing separate."""
        values = [batch.get(int(offset)) for offset in ordered]
        found = np.fromiter((value is not None for value in values),dtype=bool,count=len(values))
        pfns = np.fromiter((0 if value is None else value for value in values),dtype='<u8',count=len(values))
        resident = np.fromiter((residency[int(offset)]&1 for offset in ordered),dtype=bool,count=len(ordered))
        entries = np.where(found & (pfns != 0) & resident, pfns | np.uint64(PRESENT), np.uint64(0))
        file_mask = offsets != np.uint64(UNKNOWN)
        indices = np.searchsorted(ordered,offsets[file_mask])
        result = np.zeros(len(offsets),dtype='<u8')
        result[file_mask] = entries[indices]
        missing = int(np.count_nonzero(~found[indices]))
        self.query_statistics.append(dict(query=self.token,requested_pages=len(ordered),
            missing_kernel_results=missing,mincore_resident_pages=sum(v&1 for v in residency.values()),
            confirmed_pfn_pages=int(np.count_nonzero(result & np.uint64(PRESENT))),
            no_file_mapping_pages=int(np.count_nonzero(offsets == np.uint64(UNKNOWN)))))
        if self.batch_entries is not None:
            self.query_statistics[-1]['bpf_lookup'] = dict(self.batch_entries.last_statistics)
        return result

    def close(self):
        with self.query_lock:
            if self.fd is None:return
            try:
                if self.service:
                    self.service.release(self.bank,self.directory/'snapshot-query-result.json',dict(
                        tracer_pid=self.tracer.pid,reader_bank=self.bank,
                        tracer_cpu_scope='whole shared process before stop; charged only on last close',
                        queries=self.query_statistics,
                        **(dict(query_costs=self.query_costs) if self.batch_entries is not None else {}),
                        event_transport='none; synchronous BPF maps; missing results stay unknown'))
            finally:
                self.service=None
                self.mapping.close();os.close(self.fd);self.fd=None


def account(before, after, host, backing):
    """Independent backend rows: guest PFN, file page offset, first PFN, last PFN."""
    pages = before[1]; pages = pages[pages[:,1] != 0]
    assert np.array_equal(host[:,0],pages[:,0]) and np.array_equal(backing[:,0],pages[:,0])
    same = matching_tags(after[1],pages[:,0]) == pages[:,1]
    stable = (((host[:,1]^host[:,2]) | (host[:,2]^host[:,3])) & PHYSICAL_MASK) == 0
    valid = same & stable
    present = (host[:,1] & PRESENT) != 0
    swap = (host[:,1] & SWAPPED) != 0
    backed = (backing[:,2] == backing[:,3]) & ((backing[:,2] & PRESENT) != 0)
    vmm = set(map(int,host[valid & present,1] & MASK))
    # A resident private anonymous PTE is the current COW version; its old
    # snapshot page is reported separately. A file PTE must match the backend.
    file_pte = (host[:,1] & FILE) != 0
    mismatch = valid & present & file_pte & backed & ((host[:,1]&MASK) != (backing[:,2]&MASK))
    valid &= ~mismatch
    vmm = set(map(int,host[valid & present,1] & MASK))
    current_backing = valid & backed & ((~present & ~swap) | (present & file_pte))
    backend = set(map(int,backing[current_backing,2] & MASK))
    old = set(map(int,backing[valid & present & ~file_pte & backed,2] & MASK))
    confirmed = valid & (present | current_backing)
    return dict(host_vmm_confirmed_bytes=len(vmm)*PAGE,
        host_snapshot_current_bytes=len(backend)*PAGE,
        host_vmm_snapshot_intersection_bytes=len(vmm & backend)*PAGE,
        host_current_union_bytes=len(vmm | backend)*PAGE,
        snapshot_old_version_bytes=len(old)*PAGE,
        snapshot_without_vmm_pte_pages=int(np.count_nonzero(current_backing & ~present)),
        backend_query_unconfirmed_pages=int(np.count_nonzero(~backed)),
        backend_file_pfn_mismatch_pages=int(np.count_nonzero(mismatch)),
        host_current_unconfirmed_guest_capacity_bytes=(after[0]['records']-int(np.count_nonzero(confirmed)))*PAGE)
