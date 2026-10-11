"""One guest process samples meminfo/vmstat; never executes task actions."""
import argparse
import json
import math
import os
from pathlib import Path
import resource
import re
import signal
import threading
import time
import traceback

FIELDS='MemTotal MemFree MemAvailable Buffers Cached SReclaimable Shmem AnonPages Slab SUnreclaim KernelStack PageTables Percpu'.split()
VMFIELDS='pgfault pgmajfault pgpgin pgpgout'.split()


def phase_step(path):
    # A shell writer may briefly truncate the advisory marker. Never invent a step.
    parts=path.read_text().strip().split('\t')
    if len(parts)!=2 or parts[0] not in ('preparation','baseline','before','running','after','complete'):
        return 'unknown','NA'
    if not re.fullmatch(r'000|999|0(?:0[1-9]|1[0-9]|2[0-6])',parts[1]):return 'unknown','NA'
    return parts


def counters(path,fields,meminfo=False):
    values={}
    for line in path.read_text().splitlines():
        parts=line.split();key=parts[0].rstrip(':')
        if key in fields:
            if meminfo and (len(parts)!=3 or parts[2]!='kB'):raise ValueError('unexpected meminfo unit')
            values[key]=int(parts[1])
    if not set(fields)<=values.keys():raise ValueError('required counter missing')
    return [values[key] for key in fields]


def data_rows(path):
    if not path.exists():return 0
    with path.open() as stream:
        return sum(bool(line.strip()) and not line.startswith('timestamp_ns\t') for line in stream)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('interval',type=float)
    parser.add_argument('--proc-root',type=Path,default=Path('/proc'))
    args=parser.parse_args()
    if not math.isfinite(args.interval) or args.interval<.1:parser.error('interval must be finite and >= 0.1')
    out=args.directory;assert out.is_dir()
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set())
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    started=time.monotonic_ns();count=0;previous=0;status='FAIL';error=None
    try:
        previous=data_rows(out/'guest-memory.tsv')
        assert previous==data_rows(out/'guest-vmstat.tsv'), 'accumulated sample counts differ'
        with (out/'guest-memory.tsv').open('a',buffering=1) as memory,(out/'guest-vmstat.tsv').open('a',buffering=1) as vmstat:
            while True:
                timestamp=time.time_ns()
                phase,step=phase_step(out/'.memory-phase')
                mem=counters(args.proc_root/'meminfo',FIELDS,True)
                vm=counters(args.proc_root/'vmstat',VMFIELDS)
                memory.write('\t'.join(map(str,[timestamp,phase,step,*mem]))+'\n')
                vmstat.write('\t'.join(map(str,[timestamp,phase,step,*vm]))+'\n')
                count+=1
                if count==1:(out/'.memory-ready').write_text(str(os.getpid())+'\n')
                if stop.wait(args.interval):
                    # Explicit final sample, still asynchronous to each individual action.
                    timestamp=time.time_ns();phase,step=phase_step(out/'.memory-phase')
                    memory.write('\t'.join(map(str,[timestamp,phase,step,*counters(args.proc_root/'meminfo',FIELDS,True)]))+'\n')
                    vmstat.write('\t'.join(map(str,[timestamp,phase,step,*counters(args.proc_root/'vmstat',VMFIELDS)]))+'\n')
                    count+=1;break
        status='PASS'
    except Exception:error=traceback.format_exc()
    usage=resource.getrusage(resource.RUSAGE_SELF)
    receipt=json.dumps(dict(status=status,error=error,pid=os.getpid(),
        samples=count,samples_before=previous,samples_in_file=previous+count,
        interval_seconds=args.interval,elapsed_ns=time.monotonic_ns()-started,
        cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime,maxrss_kib=usage.ru_maxrss,
        scope='collector process resource usage; maxrss is not private memory'),indent=2)+'\n'
    segments=out/'collector-segments';segments.mkdir(exist_ok=True)
    (segments/f'{started}-{os.getpid()}.json').write_text(receipt)
    (out/'collector-status.json').write_text(receipt)
    return 0 if status=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
