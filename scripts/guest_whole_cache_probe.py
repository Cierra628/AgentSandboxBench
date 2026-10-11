"""Export all guest RAM page flags at two boundaries; output goes to tmpfs."""
import json
import os
from pathlib import Path
import re
import struct
import sys
import time

PAGE=4096


def ram_ranges(iomem):
    ranges=[((int(a,16)+PAGE-1)//PAGE,(int(b,16)+1)//PAGE)
            for a,b in re.findall(r'^\s*([0-9a-f]+)-([0-9a-f]+) : System RAM$',iomem,re.M)]
    assert ranges and all(a<b for a,b in ranges),'missing guest RAM ranges'
    assert all(b<=c for (_,b),(c,_) in zip(sorted(ranges),sorted(ranges)[1:]))
    return sorted(ranges)


def selected_flags(raw,ranges):
    result=[];index=0
    for start,end in ranges:
        for pfn in range(start,end):
            flag=struct.unpack_from('<Q',raw,index*8)[0];index+=1
            if flag&(1<<5) and not flag&((1<<12)|(1<<14)):
                result.append(pfn)
    assert index*8==len(raw)
    return result


def snapshot(out,label,ranges):
    started=time.monotonic_ns();raw=[]
    meminfo=Path('/proc/meminfo').read_text()
    with open('/proc/kpageflags','rb',buffering=0) as stream:
        for a,b in ranges:
            value=os.pread(stream.fileno(),(b-a)*8,a*8)
            assert len(value)==(b-a)*8,'short kpageflags read'
            raw.append(value)
    raw=b''.join(raw);selected=selected_flags(raw,ranges)
    (out/f'{label}-flags.bin').write_bytes(raw)
    result=dict(status='PASS',label=label,page_size=PAGE,ram_ranges_pfns=ranges,
        selected_file_lru_pages=len(selected),timestamp_ns=time.time_ns(),
        elapsed_ns=time.monotonic_ns()-started,meminfo=meminfo,
        scope='all System RAM: LRU set, ANON/SWAPBACKED clear; may include filesystem metadata',
        probe_output='tmpfs; adds anonymous/shmem memory, not an output file page cache')
    (out/f'{label}.json').write_text(json.dumps(result,indent=2)+'\n')


def wait(path):
    seconds=float(os.environ.get('ASB_CACHE_WAIT_SECONDS','90'));assert 0<seconds<=300
    deadline=time.monotonic()+seconds
    while not path.exists():
        if time.monotonic()>deadline:raise TimeoutError('host scan deadline')
        time.sleep(.05)


def main():
    out=Path(sys.argv[1]);assert out.parent==Path('/dev/shm') and out.name.startswith('asb-cache-')
    assert os.sysconf('SC_PAGE_SIZE')==PAGE
    out.mkdir(exist_ok=False);iomem=Path('/proc/iomem').read_text()
    (out/'iomem.txt').write_text(iomem);ranges=ram_ranges(iomem)
    try:
        snapshot(out,'before',ranges);(out/'ready').touch()
        wait(out/'capture-after');snapshot(out,'after',ranges);(out/'after-ready').touch()
        wait(out/'release');(out/'done').touch()
    except BaseException:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());raise


if __name__=='__main__':main()
