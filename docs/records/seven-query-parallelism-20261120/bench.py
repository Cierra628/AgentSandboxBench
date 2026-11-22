"""Linux synthetic replay. No probes, sandbox, real PFNs, or experiment volume."""
import csv
import ctypes as C
import json
import mmap
import multiprocessing as mp
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / 'scripts'))
from snapshot_cache_residency import SnapshotCache

PAGE = 4096
lib = C.CDLL('libbpf.so.1', use_errno=True)
libc = C.CDLL(None, use_errno=True)
libc.mincore.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
lib.bpf_map_lookup_elem.argtypes = [C.c_int, C.c_void_p, C.c_void_p]
lib.bpf_map_update_elem.argtypes = [C.c_int, C.c_void_p, C.c_void_p, C.c_uint64]
lib.bpf_map_create.argtypes = [C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_uint, C.c_void_p]
tables = {}
U64 = C.c_uint64


def build(role, layer, rows, rng):
    lengths = [int(r['run_length']) for r in rows for _ in range(int(r['count']))]
    rng.shuffle(lengths)
    pos, runs = 0, []
    for length in lengths:
        runs.append(np.arange(pos, pos + length, dtype='<u8'))
        pos += length + 1
    valid = np.concatenate(runs)
    offsets = np.r_[valid, np.full(int(rows[0]['missing_offsets']), 2**64-1, dtype='<u8')]
    rng.shuffle(offsets)
    # Read faults on a private anonymous mapping use the zero page; no file I/O.
    mem = mmap.mmap(-1, pos * PAGE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
    fd = lib.bpf_map_create(1, b'asb_synth', 8, 16, len(valid), None)
    if fd < 0:
        raise OSError(C.get_errno(), 'create temporary private BPF hash map')
    for off in valid:
        k = int(off)
        if lib.bpf_map_update_elem(fd, C.byref(U64(k)), (U64*2)(1,k+1), 0):
            raise OSError(C.get_errno(), 'populate synthetic entries')
    reader = SimpleNamespace(batch_entries=None, libc=libc, lib=lib,
                             mapfds={'entries': fd}, token=1, query_statistics=[],
                             address=C.addressof(C.c_char.from_buffer(mem)))
    tables[role,layer] = (reader, mem, offsets, len(valid))


def work(role):
    # Populate PTEs in the actual worker: fork need not inherit zero-page PTEs.
    for layer in ("backend", "lower"):
        assert sum(tables[role,layer][1][::PAGE]) == 0
    gate.wait(timeout=45)
    start=time.monotonic_ns()
    cpu=time.thread_time_ns()
    n=0
    for layer in ('backend','lower','backend','lower'):
        reader, _, offsets, count=tables[role,layer]
        SnapshotCache._query_owned(reader,offsets,None)
        n+=count
    elapsed_cpu=time.thread_time_ns()-cpu
    end=time.monotonic_ns()
    # Validate each call, not solely the final output; excluded from timing.
    for layer in ('backend','lower'):
        r,_,_,count=tables[role,layer]
        assert all(q['confirmed_pfn_pages']==count and not q['missing_kernel_results']
                   for q in r.query_statistics[-2:]) and len(r.query_statistics)>=2
    return dict(role=role,start=start,end=end,cpu_ns=elapsed_cpu,pages=n)


def main():
    global gate
    rows=list(csv.DictReader((HERE/'shapes.csv').open()))
    rng=np.random.default_rng(20261120)
    for role in range(1,8):
        for layer in ('backend','lower'):
            build(role,layer,[r for r in rows if int(r['role'])==role and r['layer']==layer],rng)
    ctx=mp.get_context('fork')
    for mode in ('threads','processes'):
        gate=threading.Barrier(7) if mode=='threads' else ctx.Barrier(7)
        pool=ThreadPoolExecutor(7) if mode=='threads' else ProcessPoolExecutor(7,mp_context=ctx)
        with pool:
            data=list(pool.map(work,range(1,8)))
        wall=(max(r['end'] for r in data)-min(r['start'] for r in data))/1e9
        cpu=sum(r['cpu_ns'] for r in data)/1e9
        n=sum(r['pages'] for r in data)
        assert n==645238
        print(json.dumps(dict(mode=mode,wall_s=wall,worker_cpu_s=cpu,parallelism=cpu/wall,
                              wall_us_per_page=wall*1e6/n,workers=data)),flush=True)
    for reader,mem,_,_ in tables.values():
        os.close(reader.mapfds['entries'])
        mem.close()


if __name__=='__main__':
    main()
