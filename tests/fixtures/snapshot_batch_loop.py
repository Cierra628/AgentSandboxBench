"""Pre-array query fixture; source is loaded for pure tail replay, never run whole.

Copied from the completed private batch calibration implementation.
"""
import ctypes
import os
import numpy as np
from snapshot_cache_residency import account, PAGE, PRESENT


class SnapshotCache:
    def query(self, offsets):
        st = os.fstat(self.fd)
        assert (st.st_dev, st.st_ino, st.st_rdev) == self.identity
        assert self.tracer.poll() is None
        self.token += 1
        key, token = (ctypes.c_uint64(0), ctypes.c_uint64(self.token))
        assert self.lib.bpf_map_update_elem(self.mapfds['token'], ctypes.byref(key), ctypes.byref(token), 0) == 0
        ordered = np.unique(offsets[offsets != np.uint64((1 << 64) - 1)])
        residency = {}
        splits = np.flatnonzero(np.diff(ordered) != 1) + 1
        for run in np.split(ordered, splits):
            if not len(run):
                continue
            vector = (ctypes.c_ubyte * len(run))()
            assert self.libc.mincore(self.address + int(run[0]) * PAGE, len(run) * PAGE, vector) == 0
            residency.update(zip(map(int, run), vector))
        result = []
        missing = 0
        batch = self.batch_entries.read(ordered, self.token) if self.batch_entries is not None else None
        for offset in offsets:
            if int(offset) == (1 << 64) - 1:
                result.append(0)
                continue
            if batch is None:
                key = ctypes.c_uint64(int(offset))
                value = (ctypes.c_uint64 * 2)()
                ok = self.lib.bpf_map_lookup_elem(self.mapfds['entries'], ctypes.byref(key), value)
            else:
                pfn = batch.get(int(offset))
                ok, value = (0, (self.token, pfn)) if pfn is not None else (-1, (0, 0))
            missing += ok != 0 or value[0] != self.token
            entry = PRESENT | int(value[1]) if ok == 0 and value[0] == self.token and value[1] and residency[int(offset)] & 1 else 0
            result.append(entry)
        self.query_statistics.append(dict(query=self.token, requested_pages=len(ordered), missing_kernel_results=missing, mincore_resident_pages=sum((v & 1 for v in residency.values())), confirmed_pfn_pages=sum((bool(v & PRESENT) for v in result)), no_file_mapping_pages=int(np.count_nonzero(offsets == np.uint64((1 << 64) - 1)))))
        if self.batch_entries is not None:
            self.query_statistics[-1]['bpf_lookup'] = dict(self.batch_entries.last_statistics)
        return np.array(result, dtype='<u8')
