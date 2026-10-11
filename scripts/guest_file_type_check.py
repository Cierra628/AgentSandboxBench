"""Recheck only exported guest PFNs; output raw flags and boundary meminfo."""
import array
import json
import os
from pathlib import Path
import struct
import sys
import time


def is_file_lru(flags):
    return bool(flags & (1<<5)) and not flags & ((1<<12)|(1<<14))


def main():
    sid,label=sys.argv[1:];assert label in ('before','after')
    root=Path('/dev/shm')/f'mixfs-exp5-{sid}'
    selected=array.array('I');selected.frombytes((root/'file-pfns.bin').read_bytes())
    assert os.sysconf('SC_PAGE_SIZE')==4096 and sys.byteorder=='little'
    start=time.monotonic_ns();meminfo=Path('/proc/meminfo').read_text();timestamp=time.time_ns()
    limit=0x140000000//4096
    with Path('/proc/kpageflags').open('rb',buffering=0) as stream:
        raw=os.pread(stream.fileno(),limit*8,0)
    assert len(raw)==limit*8 and all(pfn<limit for pfn in selected)
    flags=[struct.unpack_from('<Q',raw,pfn*8)[0] for pfn in selected]
    damon=Path('/sys/module/damon_reclaim/parameters')
    policy={name:(damon/name).read_text().strip() if (damon/name).exists() else None
            for name in ('enabled','kdamond_pid')}
    (root/f'flags-{label}.bin').write_bytes(b''.join(struct.pack('<Q',flag) for flag in flags))
    (root/f'flags-{label}.json').write_text(json.dumps(dict(status='PASS',sandbox_id=sid,
        label=label,timestamp_ns=timestamp,elapsed_ns=time.monotonic_ns()-start,
        selected_pages=len(selected),still_file_lru_pages=sum(is_file_lru(flag) for flag in flags),
        damon_observation=policy,
        meminfo_before_scan=meminfo,scope='guest classification check; extra Python process and flags read'),indent=2)+'\n')
    print('status=PASS label='+label)


if __name__=='__main__':main()
