"""Validate the batch reader using one new, unpinned HASH map; no probes or VM.

Explicit BPF calls create, inspect, update and read the owned map; no program is
attached. Token publication and mincore are simulated: this is not a cache or
sandbox measurement. All owned map FDs are closed, including on failure.
"""
import argparse
import ctypes
import threading
import errno
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from snapshot_cache_residency import BatchEntries, SnapshotCache, PAGE


class MapInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ('type', 'id', 'key_size', 'value_size', 'max_entries', 'map_flags')]


def map_info(lib, fd):
    lib.bpf_obj_get_info_by_fd.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_uint32)]
    lib.bpf_obj_get_info_by_fd.restype = ctypes.c_int
    info = MapInfo()
    size = ctypes.c_uint32(ctypes.sizeof(info))
    rc = lib.bpf_obj_get_info_by_fd(fd, ctypes.byref(info), ctypes.byref(size))
    if rc != 0: raise RuntimeError(f'map info failed: {rc}')
    if size.value < ctypes.sizeof(info): raise RuntimeError('incomplete map info')
    if info.type != 1: raise RuntimeError(f'created map is not HASH: {info.type}')
    return dict(type='hash', id=info.id, bytes_key=info.key_size,
        bytes_value=info.value_size, max_entries=info.max_entries, flags=info.map_flags)


def query(lib, fd, batch):
    """Exercise the unchanged query reconstruction with synthetic mincore bits."""
    s = SnapshotCache.__new__(SnapshotCache)
    s.query_lock = threading.Lock()
    s.bank, s.limit = 1, 1<<40
    st = os.fstat(fd)
    s.fd, s.identity = fd, (st.st_dev, st.st_ino, st.st_rdev)
    s.tracer = SimpleNamespace(poll=lambda: None)
    s.mapfds, s.token, s.query_statistics = {'entries':fd, 'token':-1}, 4, []
    s.query_costs = []
    s.batch_entries, s.address = batch, 100*PAGE
    def token_update(fd, key, value, flags):
        assert fd == -1 and ctypes.cast(value,ctypes.POINTER(ctypes.c_uint64))[0] == 5
        return 0
    s.lib = SimpleNamespace(bpf_map_update_elem=token_update,bpf_map_delete_elem=lambda *args:0,
                            bpf_map_lookup_elem=lib.bpf_map_lookup_elem)
    def mincore(address, length, vector):
        first = (address-s.address)//PAGE
        for i in range(length//PAGE): vector[i] = 0 if first+i == 5 else 1
        return 0
    s.libc = SimpleNamespace(mincore=mincore)
    offsets = np.array([5,1,2,3,3,4,(1<<64)-1,1,7],dtype='<u8')
    result = s.query(offsets)
    stats = dict(s.query_statistics[0])
    details = stats.pop('bpf_lookup',None)
    return result,stats,details


def read_rows(lib, fd, rows):
    result = {}
    for key in rows:
        k, value = ctypes.c_uint64(key), (ctypes.c_uint64*2)()
        rc = lib.bpf_map_lookup_elem(fd,ctypes.byref(k),value)
        if rc != 0: raise RuntimeError(f'owned scalar lookup failed: {rc}')
        result[key] = tuple(value)
    return result


def validate(output):
    output.mkdir(parents=True, exist_ok=False)
    result = dict(status='FAIL', stage='load_library', host_kernel=os.uname().release,
        scope='one newly created unpinned HASH map; synthetic token/mincore; no probes or sandbox',
        map_created=False, own_fd_closed=False, map_pinned=False, programs_attached=0)
    fd = None
    try:
        lib = ctypes.CDLL('libbpf.so.1',use_errno=True)
        lib.bpf_map_create.argtypes = [ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,
                                       ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
        lib.bpf_map_create.restype = ctypes.c_int
        lib.bpf_map_lookup_elem.argtypes = [ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p]
        lib.bpf_map_lookup_elem.restype = ctypes.c_int
        lib.bpf_map_update_elem.argtypes = [ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint64]
        lib.bpf_map_update_elem.restype = ctypes.c_int
        result['stage'] = 'create_owned_map'
        fd = lib.bpf_map_create(1,b'asb_batch_test',8,16,8,None)
        if fd < 0: raise RuntimeError(f'owned map creation failed: {fd}')
        result['map_created'] = True
        try:
            result['stage'] = 'map_info'
            info = map_info(lib,fd)
            result['map_info'] = info
            batch = BatchEntries(lib,fd,info)
            result['receive_buffer_bytes'] = batch.buffer_bytes
            rows = {}
            phases = result['phases'] = []
            for name, additions in (
                    ('empty', {}),
                    ('partial', {1:(5,101),2:(4,102),4:(5,0),5:(5,105)}),
                    ('full', {7:(5,107),99:(5,199),100:(5,200),101:(5,201)})):
                result['stage'] = name
                for key, value in additions.items():
                    k, v = ctypes.c_uint64(key), (ctypes.c_uint64*2)(*value)
                    rc = lib.bpf_map_update_elem(fd,ctypes.byref(k),v,0)
                    if rc != 0: raise RuntimeError(f'owned synthetic update failed: {rc}')
                rows.update(additions)
                before = read_rows(lib,fd,rows)
                a,scalar_stats,_ = query(lib,fd,None)
                b,batch_stats,details = query(lib,fd,batch)
                after = read_rows(lib,fd,rows)
                if not np.array_equal(a,b) or scalar_stats != batch_stats:
                    raise RuntimeError('scalar/batch reconstruction differs')
                if before != after or before != rows:
                    raise RuntimeError('read-only lookup changed synthetic rows')
                if details != dict(mode='batch',returncode=-errno.ENOENT,returned_entries=len(rows)):
                    raise RuntimeError(f'unexpected complete traversal: {details}')
                phases.append(dict(phase=name,stored_entries=len(rows),output_equal=True,
                    original_statistics_equal=True,rows_unchanged=True,
                    batch_lookup=details,statistics=batch_stats))
        finally:
            active_stage = result['stage']
            result['stage'] = 'close_owned_fd'
            os.close(fd)
            try: fcntl.fcntl(fd,fcntl.F_GETFD)
            except OSError as exc:
                if exc.errno != errno.EBADF: raise
                result['own_fd_closed'] = True
            else: raise RuntimeError('owned FD still open')
            result['stage'] = active_stage
        result.update(status='PASS',stage='complete',batch_calls=3,
            release='only owned FD closed; map never pinned or referenced by a BPF program',
            performance_measured=False)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"status={result['status']} stage={result['stage']} result_dir={output}")
    return result['status'] == 'PASS'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    raise SystemExit(0 if validate(args.output) else 1)
