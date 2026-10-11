"""Guest-wide CPU and per-device block counters; no cache control or host I/O."""
from pathlib import Path

from guest_diskstats import parse,FIELDS

CPUFIELDS=['user','nice','system','idle','iowait','irq','softirq','steal']


def cpu(text):
    lines=text.splitlines();values=lines[0].split()
    assert values[0]=='cpu' and len(values)>=9,'missing aggregate CPU counters'
    ticks=[int(v) for v in values[1:9]]
    assert all(v>=0 for v in ticks)
    cores=sum(s.split()[0][3:].isdigit() for s in lines if s.startswith('cpu') and s.split()[0]!='cpu')
    assert cores>0,'missing per-core counters'
    # guest/guest_nice are already included in user/nice; never add them again.
    return [*ticks,cores]


def disks(text,sys_root=Path('/sys')):
    result=[]
    for name,row in parse(text).items():
        block=sys_root/'class/block'/name
        partition='unknown' if not block.exists() else '1' if (block/'partition').exists() else '0'
        result.append([name,row['major'],row['minor'],partition,*[row[k] for k in FIELDS]])
    return result
