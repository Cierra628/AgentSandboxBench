"""Whole-guest cache paired with live host residency during an existing task."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
import traceback
import numpy as np

from agentenv_whole_cache import bind_vm
from guest_cache_exporter import PAGE, REQUEST, PUBLICATION, CONTROL, STOP
from kernel_cache_metadata import decode_header, PHYSICAL_MASK, PRESENT, MASK
from fast_cache_metadata import decode_array, join_arrays
from measure_file_physical import identity, ram_address
from task_physical_cache import TaskPhysicalCache

ROOT = Path(__file__).resolve().parents[1]
HOST_ROW = struct.Struct('<4Q')


def _timed(costs, name, function, *args, **kwargs):
    started = time.monotonic_ns(); cpu = time.thread_time_ns()
    result = function(*args, **kwargs)
    used = time.thread_time_ns()-cpu; ended = time.monotonic_ns()
    costs[name] = dict(cpu_ns=used, elapsed_ns=ended-started,
                       host_start_ns=started, host_end_ns=ended)
    return result


def _batch_memory(queries):
    """Observe owned receive-array pages without reading or touching their data."""
    started, cpu = time.monotonic_ns(), time.thread_time_ns()
    coverage = {}
    capacity = 0
    for query in queries:
        batch = query.batch_entries
        capacity += batch.buffer_bytes
        for array in (batch.keys, batch.values):
            start, end = array.ctypes.data, array.ctypes.data+array.nbytes
            for page in range(start//PAGE, (end+PAGE-1)//PAGE):
                coverage[page] = coverage.get(page, 0)+min(end,(page+1)*PAGE)-max(start,page*PAGE)
    pages = np.array(sorted(coverage), dtype='<u8')
    present = full = 0
    physical, whole = set(), set()
    with Path('/proc/self/pagemap').open('rb', buffering=0) as pm:
        for run in np.split(pages, np.flatnonzero(np.diff(pages) != 1)+1):
            if not len(run): continue
            raw = os.pread(pm.fileno(), len(run)*8, int(run[0])*8)
            assert len(raw) == len(run)*8, 'short batch buffer pagemap read'
            for page, entry in zip(run, np.frombuffer(raw,dtype='<u8'), strict=True):
                if not int(entry)&PRESENT: continue
                pfn = int(entry)&MASK
                assert pfn, 'batch buffer PFN unavailable'
                present += 1; physical.add(pfn)
                if coverage[int(page)] == PAGE:
                    full += 1; whole.add(pfn)
    rss = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
               if line.startswith('VmRSS:'))
    return dict(receive_capacity_bytes=capacity, covered_virtual_pages=len(coverage),
        resident_virtual_pages=present, whole_resident_virtual_pages=full,
        resident_physical_union_bytes=len(physical)*PAGE,
        whole_resident_physical_bytes=len(whole)*PAGE,
        boundary_only_resident_physical_bytes=len(physical-whole)*PAGE,
        controller_process_rss_bytes=int(rss)*1024,
        cpu_ns=time.thread_time_ns()-cpu, elapsed_ns=time.monotonic_ns()-started)


class LiveTaskCache:
    def __init__(self, out, container, cli, interval=1.0, *, session=None, lower_cache=False, provider_cache=False, batch_lookup=False, expected_kernel='6.1.175-asb-cache-v3'):
        assert session in (None, 'parent', 'restored', 'child-a', 'child-b')
        self.directory = out/'live-cache'
        if session:
            self.directory = self.directory/session
        self.directory.mkdir(parents=True)
        self.cli, self.interval = cli, interval
        self.session = session
        self.expected_kernel = expected_kernel
        self.backend = None
        self.lower_query = None
        self.lower_cache = lower_cache
        assert not provider_cache or lower_cache
        self.provider_cache = provider_cache
        self.batch_lookup = batch_lookup
        self.provider = None
        self.out = out
        # The existing attestation checks the owning controller/run directory.
        self.capture = TaskPhysicalCache(out, container, cli,
            directory=self.directory/'capture' if session else None)
        self.stop_event = threading.Event()
        self.thread = self.sid = self.error = None
        self.samples = []
        self.request = self.raw_bytes = 0
        code = self.directory/'code'; code.mkdir()
        for name in ('live_task_cache.py', 'guest_cache_exporter.py', 'kernel_cache_metadata.py', 'fast_cache_metadata.py', 'snapshot_cache_residency.py', 'snapshot_layer_metadata.py', 'provider_slot_cache.py'):
            shutil.copy2(ROOT/'scripts'/name, code/name)
        (self.directory/'source-sha256.json').write_text(json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                                  for p in code.iterdir()}, indent=2)+'\n')

    def start(self, sid):
        self.sid = sid
        self.capture.ready()
        tools = self.directory/'code'
        self.cli('live-cache-upload', 'upload', sid, tools, '/workspace/asb-export-tools')
        self.guest = '/dev/shm/asb-export-' + sid
        self.cli('live-cache-start', 'exec', sid, 'bash', '-lc',
                 f'nohup timeout 930 python3 -B /workspace/asb-export-tools/guest_cache_exporter.py {sid} {self.guest} >/tmp/asb-export.log 2>&1 </dev/null &')
        self.cli('live-cache-ready', 'exec', sid, 'bash', '-lc',
                 f'for i in $(seq 1 100); do test -f {self.guest}/bootstrap.json && exit 0; test ! -f {self.guest}/failure.txt || break; sleep 0.1; done; cat /tmp/asb-export.log; exit 1', timeout=15)
        self.bootstrap = json.loads(self.cli('live-cache-bootstrap', 'exec', sid, 'cat', self.guest+'/bootstrap.json'))
        assert self.bootstrap['kernel'] == self.expected_kernel
        (self.directory/'marker.json').write_text(json.dumps(self.bootstrap)+'\n')
        (self.directory/'iomem.txt').write_text(self.bootstrap['iomem'])
        shutil.copy2(self.capture.directory/'kvm-slots.tsv', self.directory/'kvm-slots.tsv')
        pids = [int(p) for p in self.capture.cgpath.joinpath('cgroup.procs').read_text().split()
                if Path('/proc', p, 'comm').read_text().strip() == 'firecracker']
        self.vm, self.proof = bind_vm(self.directory, pids, self.capture.cgroup,
                                     os.readlink('/proc/'+str(pids[0])+'/exe'), sid)
        proc = identity(self.vm, Path('/proc'), Path('/sys/fs/cgroup'))
        self.pm = (proc/'pagemap').open('rb', buffering=0)
        self.memory = (proc/'mem').open('r+b', buffering=0)
        self.addresses = [ram_address(p, self.proof['slots']) for p in self.bootstrap['export_pfns']]
        self._start_snapshot_queries(proc)
        self.thread = threading.Thread(target=self._run, name='live-task-cache', daemon=True)
        self.thread.start()

    def _start_snapshot_queries(self, proc):
        if self.session in ('restored', 'child-a', 'child-b'):
            from snapshot_cache_residency import SnapshotCache
            query_options = dict(batch_lookup=True) if self.batch_lookup else {}
            self.backend = SnapshotCache(proc, self.proof['slots'], self.directory, **query_options)
            if self.lower_cache:
                from snapshot_layer_metadata import owned_memory_layer
                self.lower_layer = owned_memory_layer(self.out, proc, self.directory/'lower-metadata')
                query_dir=self.directory/'lower-query';query_dir.mkdir()
                self.lower_query=SnapshotCache(proc, [], query_dir, file_path=self.lower_layer.path, **query_options)
                if self.provider_cache:
                    from provider_slot_cache import ProviderCache
                    self.provider=ProviderCache(self.out,proc,self.directory/'provider')
    def _read(self, offset, size):
        result = bytearray()
        while size:
            index, inner = divmod(offset, PAGE)
            end = index + 1
            limit = min(len(self.addresses), (offset+size+PAGE-1)//PAGE)
            while end < limit and self.addresses[end] == self.addresses[end-1]+PAGE:
                end += 1
            n = min(size, (end-index)*PAGE-inner)
            address = self.addresses[index]
            count = (inner+n+PAGE-1)//PAGE
            def entries():
                raw = os.pread(self.pm.fileno(), count*8, address//PAGE*8)
                assert len(raw) == count*8
                return np.frombuffer(raw, dtype='<u8')
            entry = entries()
            assert np.all((entry & PRESENT) != 0) and np.all((entry & MASK) != 0), 'export memory not host-resident'
            chunk = os.pread(self.memory.fileno(), n, address+inner)
            assert len(chunk) == n
            last = entries()
            assert not np.any((entry ^ last) & PHYSICAL_MASK), 'export host mapping changed'
            result.extend(chunk); offset += n; size -= n
        return bytes(result)

    def _snapshot(self):
        self.request += 1
        os.pwrite(self.memory.fileno(), struct.pack('<Q', self.request), self.addresses[0]+REQUEST)
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            first = self._read(PUBLICATION, CONTROL.size)
            fields = CONTROL.unpack(first)
            request, sequence, size, started, ended, cached, shmem, buffers, cpu = fields
            if request == self.request and sequence and not sequence & 1:
                assert size <= self.bootstrap['capacity_bytes']
                raw = self._read(PAGE, size)
                if first == self._read(PUBLICATION, CONTROL.size):
                    decode_header(raw)
                    return raw, dict(request=request, publication=sequence//2, guest_start_ns=started,
                                     guest_end_ns=ended, guest_cached_bytes=cached, guest_shmem_bytes=shmem,
                                     guest_buffers_bytes=buffers, guest_publication_cpu_ns=cpu)
            time.sleep(.005)
        raise TimeoutError('guest cache publication timed out')

    def _bulk(self):
        values = []
        for slot in self.proof['slots']:
            raw = os.pread(self.pm.fileno(), slot['size']//PAGE*8, slot['host']//PAGE*8)
            assert len(raw) == slot['size']//PAGE*8
            data = np.frombuffer(raw, dtype='<u8'); values.append((slot, data))
        return values

    @staticmethod
    def _entries(pfns, values):
        result = np.empty(len(pfns), dtype='<u8')
        covered = np.zeros(len(pfns), dtype=bool)
        for slot, data in values:
            selected = (pfns*PAGE >= slot['guest']) & (pfns*PAGE < slot['guest']+slot['size'])
            result[selected] = data[(pfns[selected]*PAGE-slot['guest'])//PAGE]
            covered |= selected
        if not np.all(covered):
            raise ValueError('cache page outside attested RAM')
        return result

    def _run(self):
        total_cpu = time.thread_time_ns()
        try:
            while not self.stop_event.is_set():
                started = time.monotonic_ns(); cpu = time.thread_time_ns(); wall = time.time_ns()
                stages = {}
                before, info_before = _timed(stages, 'guest_before', self._snapshot)
                decoded_before = _timed(stages, 'decode_before', decode_array, before)
                pages = decoded_before[1]
                pfns = pages[pages[:, 1] != 0, 0]
                first = _timed(stages, 'vmm_first', self._bulk)
                compact_provider = bool(self.provider and self.provider.segmented)
                if self.provider and not compact_provider:provider_first=_timed(stages, 'provider_first', self.provider.snapshot)
                if self.backend:
                    self.backend.regions = _timed(stages, 'maps_before', self.backend.ram_regions)
                    maps_before = self.backend.regions
                    offsets = _timed(stages, 'backend_offsets_before', self.backend.offsets, pfns)
                    backing_first = _timed(stages, 'backend_query_first', self.backend.query, offsets)
                    if self.lower_query:
                        lower_offsets=_timed(stages, 'lower_offsets', self.lower_layer.offsets, offsets)
                        lower_first=_timed(stages, 'lower_query_first', self.lower_query.query, lower_offsets)
                if compact_provider:provider_first=_timed(stages, 'provider_first', self.provider.snapshot)
                middle = _timed(stages, 'vmm_middle', self._bulk)
                if compact_provider:provider_last=_timed(stages, 'provider_last', self.provider.snapshot)
                after, info_after = _timed(stages, 'guest_after', self._snapshot)
                decoded_after = _timed(stages, 'decode_after', decode_array, after)
                if self.backend:
                    backing_last = _timed(stages, 'backend_query_last', self.backend.query, offsets)
                    if self.lower_query:
                        lower_last=_timed(stages, 'lower_query_last', self.lower_query.query, lower_offsets)
                final = _timed(stages, 'vmm_last', self._bulk)
                if self.provider and not compact_provider:provider_last=_timed(stages, 'provider_last', self.provider.snapshot)
                if self.backend:
                    self.backend.regions = _timed(stages, 'maps_after', self.backend.ram_regions)
                    maps_after = self.backend.regions
                    last_offsets = _timed(stages, 'backend_offsets_after', self.backend.offsets, pfns)
                    backing_last[last_offsets != offsets] = 0
                    if self.lower_query:
                        lower_last[last_offsets != offsets] = 0
                rows = _timed(stages, 'host_rows', lambda: np.column_stack((pfns,
                    self._entries(pfns, first), self._entries(pfns, middle),
                    self._entries(pfns, final))).astype('<u8', copy=False))
                point = _timed(stages, 'vmm_account', join_arrays, decoded_before, decoded_after, rows)
                backing = None
                lower = None
                if self.backend:
                    from snapshot_cache_residency import account
                    backing = _timed(stages, 'backend_rows', lambda:
                        np.column_stack((pfns, offsets, backing_first, backing_last)).astype('<u8', copy=False))
                    point.update(_timed(stages, 'backend_account', account, decoded_before, decoded_after, rows, backing))
                    if self.lower_query:
                        from snapshot_layer_metadata import account_layers
                        lower=_timed(stages, 'lower_rows', lambda:
                            np.column_stack((pfns,lower_offsets,lower_first,lower_last)).astype('<u8',copy=False))
                        point.update(_timed(stages, 'lower_account', account_layers, decoded_before,decoded_after,rows,backing,lower))
                        if self.provider:
                            from provider_slot_cache import account_observed_provider
                            measured,_=_timed(stages, 'provider_account', account_observed_provider,
                                before,after,rows,backing,lower,provider_first,provider_last,
                                decoded=(decoded_before,decoded_after))
                            point.update(measured)
                            point['provider_query_cpu_ns']=provider_first['cpu_ns']+provider_last['cpu_ns']
                            point['provider_query_elapsed_ns']=provider_first['elapsed_ns']+provider_last['elapsed_ns']
                            point['provider_query_metadata_bytes']=provider_first['metadata_bytes_read']+provider_last['metadata_bytes_read']
                            point['provider_pair_elapsed_ns']=provider_last['host_end_ns']-provider_first['host_start_ns']
                            point['provider_pair_gap_ns']=provider_last['host_start_ns']-provider_first['host_end_ns']
                point.update(info_after, point=len(self.samples), timestamp_ns=wall,
                             host_elapsed_ns=time.monotonic_ns()-started,
                             host_thread_cpu_ns=time.thread_time_ns()-cpu,
                             host_sample_start_ns=started, host_stage_costs=stages,
                             guest_publication_before=info_before,
                             unconfirmed_guest_page_capacity_bytes=point['unconfirmed_after_pages']*PAGE)
                if getattr(self, 'batch_lookup', False) and self.backend:
                    point['bpf_batch_memory'] = _batch_memory(
                        [q for q in (self.backend, self.lower_query) if q is not None])
                prefix = self.directory/f'pair-{len(self.samples):04}'
                host = rows.tobytes()
                if self.provider:
                    raw=json.dumps(dict(first=provider_first,last=provider_last)).encode()+b'\n'
                    Path(str(prefix)+'-provider.json').write_bytes(raw);self.raw_bytes+=len(raw)
                if lower is not None:
                    self.raw_bytes += lower.nbytes
                    Path(str(prefix)+'-lower.bin').write_bytes(lower.tobytes())
                self.raw_bytes += len(before)+len(after)+len(host)
                if backing is not None:
                    self.raw_bytes += backing.nbytes
                    Path(str(prefix)+'-backend.bin').write_bytes(backing.tobytes())
                    Path(str(prefix)+'-maps.json').write_text(json.dumps(dict(before=maps_before, after=maps_after))+'\n')
                assert self.raw_bytes < 2**30, 'live cache raw data exceeded 1 GiB budget'
                for suffix, data in [('-before.bin', before), ('-after.bin', after), ('-host.bin', host)]:
                    Path(str(prefix)+suffix).write_bytes(data)
                self.samples.append(point)
                with (self.directory/'samples.jsonl').open('a') as log:
                    log.write(json.dumps(point)+'\n')
                self.stop_event.wait(max(0, self.interval-(time.monotonic_ns()-started)/1e9))
        except BaseException as exc:
            self.error = repr(exc)
            (self.directory/'failure.txt').write_text(traceback.format_exc())
        finally:
            self.thread_total_cpu_ns = time.thread_time_ns() - total_cpu

    def close(self):
        self.stop_event.set()
        try:
            if self.thread:
                self.thread.join(timeout=12)
                assert not self.thread.is_alive(), 'host cache collector did not stop'
            if hasattr(self, 'memory'):
                os.pwrite(self.memory.fileno(), struct.pack('<Q', STOP), self.addresses[0]+REQUEST)
                self.cli('live-cache-done', 'exec', self.sid, 'bash', '-lc',
                         f'for i in $(seq 1 100); do test -f {self.guest}/done && exit 0; test ! -f {self.guest}/failure.txt || break; sleep 0.1; done; cat /tmp/asb-export.log; exit 1', timeout=15)
                guest = json.loads(self.cli('live-cache-result', 'exec', self.sid, 'cat', self.guest+'/result.json'))
                assert guest['status'] == 'PASS'
                (self.directory/'guest-result.json').write_text(json.dumps(guest, indent=2)+'\n')
                identity(self.vm, Path('/proc'), Path('/sys/fs/cgroup'))
            assert not self.error, self.error
            assert len(self.samples) >= 2, 'no repeated live cache samples'
            (self.directory/'result.json').write_text(json.dumps(dict(status='PASS', vm_pause=False,
                samples=len(self.samples), raw_bytes=self.raw_bytes, interval_seconds=self.interval,
                sandbox_id=self.sid, host_identity=self.vm, guest_boot_id=self.bootstrap['boot_id'],
                first_sample_ns=self.samples[0]['timestamp_ns'], last_sample_ns=self.samples[-1]['timestamp_ns'],
                diagnostic_kernel=self.bootstrap['kernel'],
                locked_anonymous_bytes=self.bootstrap['locked_anonymous_bytes'],
                host_thread_total_cpu_ns=self.thread_total_cpu_ns,
                collector_version=('snapshot-provider-segment-pfn-union-v6' if self.provider and self.provider.segmented else 'snapshot-provider-slot-pfn-union-v5') if self.provider_cache else 'snapshot-raw-layer-pfn-union-v4' if self.lower_cache else 'snapshot-mincore-pfn-union-v3',
                scope='whole guest ordinary/shmem cache; VMM PFNs plus direct File-backend current PFNs; unresolved pages and old snapshot versions separate',
                monitoring_impact='NOT_CONTROLLED'), indent=2)+'\n')
        finally:
            if self.provider:
                self.provider.close()
            if self.lower_query:
                self.lower_query.close()
            if self.backend:
                self.backend.close()
            for name in ('memory', 'pm'):
                if hasattr(self, name): getattr(self, name).close()
            self.capture.close()


def session_index(sessions, gap):
    """Keep distinct host identities and the explicitly unobserved restore gap."""
    assert len(sessions) == 2 and all(s['status'] == 'PASS' for s in sessions)
    before, after = sessions
    assert before['sandbox_id'] != after['sandbox_id']
    assert (before['host_identity']['pid'], before['host_identity']['start_time_ticks']) != (
        after['host_identity']['pid'], after['host_identity']['start_time_ticks'])
    assert before['guest_boot_id'] == after['guest_boot_id']
    assert before['diagnostic_kernel'] == after['diagnostic_kernel']
    assert before['last_sample_ns'] < gap['start_ns'] < gap['end_ns']
    assert gap['start_ns'] < after['first_sample_ns']
    value = dict(status='PASS', vm_pause=False, sessions=['parent', 'restored'],
        samples=sum(s['samples'] for s in sessions), raw_bytes=sum(s['raw_bytes'] for s in sessions),
        host_thread_total_cpu_ns=sum(s['host_thread_total_cpu_ns'] for s in sessions),
        interval_seconds=before['interval_seconds'], diagnostic_kernel=before['diagnostic_kernel'],
        collector_version=before['collector_version'], gap=gap,
        scope=before['scope'], monitoring_impact='NOT_CONTROLLED',
        checkpoint_pause='Native snapshot only; sampler stops before checkpoint and restarts after restore')
    return value


def save_sessions(directory, sessions, gap):
    value = session_index(sessions, gap)
    (directory/'result.json').write_text(json.dumps(value, indent=2)+'\n')
    return value
