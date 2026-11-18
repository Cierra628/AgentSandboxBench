"""Compare old/new CPU work on synthetic mincore vectors, never file data."""
import argparse
import ctypes
import json
from pathlib import Path
import statistics
import time

from host_file_monitor import PAGE,count_resident

ROOT=Path(__file__).resolve().parents[1]


def benchmark():
    layers=json.loads((ROOT/'docs/release/manifests/host-full-private-scope-20261024.json').read_text())['base_manifest']
    pages=sum((r['size']+PAGE-1)//PAGE for r in layers)
    data=bytes(range(256))*256  # Includes undefined upper bits; only bit 0 counts.
    full=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    rows=[]
    for copies in (1,7):
        count=pages*copies;chunks=[full]*(count//len(data))
        if count%len(data):chunks.append((ctypes.c_ubyte*(count%len(data))).from_buffer_copy(data[:count%len(data)]))
        timings={};totals={}
        for name,fn in [('old',lambda v:sum(bool(x&1) for x in v)),('new',count_resident)]:
            durations=[]
            for _ in range(3):
                start=time.perf_counter_ns();cpu=time.thread_time_ns();total=sum(fn(v) for v in chunks)
                durations.append(dict(wall_ns=time.perf_counter_ns()-start,cpu_ns=time.thread_time_ns()-cpu))
            timings[name]=dict(runs=durations,median_wall_ns=statistics.median(r['wall_ns'] for r in durations));totals[name]=total
        assert totals['old']==totals['new']
        rows.append(dict(copies=copies,objects=19*copies,pages=count,equal_resident_counts=totals,**timings))
    return dict(status='PASS_SYNTHETIC_VECTOR_BENCHMARK',rows=rows,
        scope='Resident-bit counting only; synthetic bytes, not real residency or whole mincore query cost; no data pages read or faulted')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    assert not args.output.exists();value=benchmark();args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,indent=2)+'\n');print('status='+value['status']+' output='+str(args.output))
