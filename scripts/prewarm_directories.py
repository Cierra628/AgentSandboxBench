"""Read directory entries only; no regular file content, no symlink traversal."""
import hashlib
import json
import os
from pathlib import Path
import resource
import time


def prewarm(root,diskstats=False):
    root=Path(root)
    started=time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    disk_before=Path('/proc/diskstats').read_text() if diskstats else None
    before=resource.getrusage(resource.RUSAGE_SELF)
    stack=[root];directories=entries=0;names=[]
    while stack:
        path=stack.pop()
        directories+=1
        if directories>100000 or time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)-started>20*10**9:
            raise RuntimeError('directory prewarm bound exceeded')
        names.append(str(path.relative_to(root)))
        with os.scandir(path) as stream:
            for entry in stream:
                entries+=1
                if entry.is_dir(follow_symlinks=False):stack.append(Path(entry.path))
    after=resource.getrusage(resource.RUSAGE_SELF)
    disk_after=Path('/proc/diskstats').read_text() if diskstats else None
    result=dict(status='PASS',schema=1,root=str(root),directories=directories,entries=entries,
                directory_names_sha256=hashlib.sha256('\n'.join(sorted(names)).encode()).hexdigest(),
                monotonic_raw_ns=time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)-started,
                user_seconds=after.ru_utime-before.ru_utime,system_seconds=after.ru_stime-before.ru_stime,
                voluntary_switches=after.ru_nvcsw-before.ru_nvcsw,involuntary_switches=after.ru_nivcsw-before.ru_nivcsw,
                major_faults=after.ru_majflt-before.ru_majflt,minor_faults=after.ru_minflt-before.ru_minflt,
                scope='directory entries only; is_dir may query attributes if filesystem entry type is unknown; no regular file reads or symlink traversal')
    if diskstats:result.update(diskstats_before=disk_before,diskstats_after=disk_after)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--diskstats',action='store_true')
    args=parser.parse_args()
    value=prewarm(args.root,args.diskstats)
    args.output.write_text(json.dumps(value,indent=2)+'\n')
