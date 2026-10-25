"""One-shot file-cache intervention inside an owned diagnostic guest only."""
import argparse
import json
import os
from pathlib import Path
import time

from guest_diskstats import parse
from kernel_cache_metadata import decode_header


def snapshot():
    started = time.monotonic_ns()
    # This diagnostic-kernel interface is also the guard against host use.
    header = decode_header(Path('/proc/asb_pagecache').read_bytes())
    memory = {v[0].rstrip(':'): int(v[1]) for line in Path('/proc/meminfo').read_text().splitlines()
              if len(v := line.split()) == 3 and v[2] == 'kB'}
    vmstat = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
    result = dict(start_monotonic_ns=started,
                timestamp_ns=time.time_ns(), kernel=os.uname().release,
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                metadata=header, guest_cache_bytes=header['records'] * header['page_size'],
                meminfo_kib=memory,
                vmstat={k: int(vmstat[k]) for k in ('pgfault', 'pgmajfault', 'pgpgin', 'pgpgout')},
                disks=parse(Path('/proc/diskstats').read_text()))
    result['disk_is_partition'] = {name: (Path('/sys/class/block') / name / 'partition').exists()
                                   for name in result['disks']}
    result['end_monotonic_ns'] = time.monotonic_ns()
    return result


def control(policy):
    assert policy in ('preserve', 'drop-clean', 'observe')
    before = snapshot()
    if policy == 'observe':
        return dict(status='PASS', policy=policy, observation=before)
    begin = time.monotonic_ns()
    os.sync()
    sync_ns = time.monotonic_ns() - begin
    synced = snapshot()
    begin = time.monotonic_ns()
    if policy == 'drop-clean':
        Path('/proc/sys/vm/drop_caches').write_text('1\n')
    control_ns = time.monotonic_ns() - begin
    after = snapshot()
    assert before['boot_id'] == synced['boot_id'] == after['boot_id']
    return dict(status='PASS', policy=policy, before_sync=before, after_sync=synced,
                after_control=after, sync_ns=sync_ns, control_ns=control_ns,
                guest_cache_reduction_bytes=synced['guest_cache_bytes'] - after['guest_cache_bytes'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('policy', choices=('preserve', 'drop-clean', 'observe'))
    args = parser.parse_args()
    print(json.dumps(control(args.policy)))
