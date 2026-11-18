"""Read saved schedule evidence only; never query proc, devices, caches or services."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import stat


def read(path):return json.loads(path.read_text())


def audit_run(run):
    raw=Path(run['raw']);metrics=read(raw/'schedule-smoke-metrics.json');e=metrics['expanded_files']
    outer=read(raw.parent/'result.json');roles_by_pid={v['pid']:r for r,v in metrics['bindings'].items()}
    drives={};mapped={};other=Counter()
    for entry in e['excluded_entries']:
        if 'drive' in entry:
            d=entry['drive'];key=(d['device'],d['inode'],d['rdev'])
            row=drives.setdefault(key,dict(identity=list(key),drive_id=d['drive_id'],read_only=d['is_read_only'],roles=[]))
            assert (row['drive_id'],row['read_only'])==(d['drive_id'],d['is_read_only'])
            row['roles'].append(entry['role'])
        elif entry.get('reason')=='MAPPED_DEVICE_NOT_FILE_CACHE':
            mode=entry['mode'];kind='block' if stat.S_ISBLK(mode) else 'char' if stat.S_ISCHR(mode) else 'anon_inode' if stat.S_IFMT(mode)==0 else 'other'
            key=(kind,entry['device'],entry['inode'])
            row=mapped.setdefault(key,dict(kind=kind,device=entry['device'],inode=entry['inode'],references=0,roles=[],sources=[]))
            row['references']+=1
            pid=int(Path(entry['path']).parts[2])
            if pid in roles_by_pid and roles_by_pid[pid] not in row['roles']:row['roles'].append(roles_by_pid[pid])
            if entry['source'] not in row['sources']:row['sources'].append(entry['source'])
        else:other[entry.get('reason','UNSPECIFIED')]+=1
    special=[]
    for row in e['files']:
        if row['status']!='SEPARATE_SHMEM_OR_HUGETLB':continue
        assert row['filesystem_magic']==0x01021994 and row['nlink']==0
        names=sorted({a['source'].removeprefix('mapping:') for a in row['aliases']})
        special.append(dict(device=row['device'],inode=row['inode'],size_at_inventory=row['size'],
                            filesystem='TMPFS',sources=names,alias_count=len(row['aliases']),resident_bytes=None))
    assert len({(r['device'],r['inode']) for r in special})==len(special)
    # Save only structured identity fields from the existing logs, not raw logs.
    ansi=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
    memory_logs=[];log=raw.parent/'service-logs/server.log'
    for number,line in enumerate(log.read_text().splitlines(),1):
        line=ansi.sub('',line)
        if 'created or acquired shared read-only ublk device' not in line:continue
        config=re.search(r'key=(\S+/memory/image\.json)\s',line)
        device=re.search(r'path=(/dev/ublkb\d+)\s',line)
        if config and device:memory_logs.append(dict(log=str(log),line=number,config=config[1],device_path=device[1]))
    ram=[r for r in mapped.values() if r['kind']=='block']
    drive_pairs={(key[0],key[1]) for key in drives}
    for row in ram:
        assert (row['device'],row['inode']) not in drive_pairs
        paths={s.removeprefix('mapping:') for s in row['sources']}
        row['memory_config_evidence']=[x for x in memory_logs if x['device_path'] in paths]
        assert row['memory_config_evidence'], 'mapped block has no saved RAM configuration evidence'
        assert len(row['roles'])==3
    changed=[r for r in e['files'] if r['status']=='CHANGED_DURING_QUERY']
    retained_mounts=outer['boundary']['write_mounts']
    loop=outer['mount_identity']['source']
    assert (raw.parent/'attach.stdout').read_text().strip()==loop
    loop_verify=(raw.parent/'verify-own-loop.stdout').read_text().strip()
    assert loop_verify.startswith(loop+':') and str(raw.parent/'volume.ext4') in loop_verify
    assert not any(a['path'].endswith('/volume.ext4') for r in e['files'] for a in r['aliases'])
    assert len(drives)==8 and sum(r['read_only'] for r in drives.values())==1
    assert sum(len(r['roles']) for r in drives.values())==14
    assert len(ram)==(0 if run['schedule']=='grpo' else 2)
    assert len(special)==14 and all(r['size_at_inventory']==4096 for r in special)
    shmem_types=Counter(name for r in special for name in r['sources'])
    assert shmem_types=={'/dev/zero (deleted)':7,'/memfd:iov_deque (deleted)':7}
    regular_drive_keys={(d['device'],d['inode']) for entries in metrics['drives'].values()
                        for d in entries if not d['block_device']}
    regular_drive_rows=[r for r in e['files'] if (r['device'],r['inode']) in regular_drive_keys]
    assert len(regular_drive_keys)==len(regular_drive_rows)==28
    assert all(r['status']=='OBSERVED' for r in regular_drive_rows)
    return dict(schedule=run['schedule'],raw=str(raw),raw_coverage_status=e['status'],
        regular_stable_resident_bytes=e['stable_regular_resident_bytes'],
        drive_block_references=sum(len(r['roles']) for r in drives.values()),drive_block_objects=list(drives.values()),
        ordinary_drive_objects_already_queried=len(regular_drive_rows),
        ordinary_drive_resident_bytes=sum(r['resident_bytes'] for r in regular_drive_rows),
        ram_block_objects=ram,ram_block_resident_bytes=None,
        mapped_nonregular_summary={kind:dict(unique=sum(r['kind']==kind for r in mapped.values()),
            references=sum(r['references'] for r in mapped.values() if r['kind']==kind))
            for kind in sorted({r['kind'] for r in mapped.values()})},
        nonregular_other_references=dict(other),tmpfs_objects=special,
        tmpfs_logical_size_sum=sum(r['size_at_inventory'] for r in special),tmpfs_resident_bytes=None,
        cgroup_shmem_before=e['counters_before']['shmem'],cgroup_shmem_after=e['counters_after']['shmem'],
        outer_loop=dict(device_path_at_run=loop,verify_log=str(raw.parent/'verify-own-loop.stdout'),
            backing_path=str(raw.parent/'volume.ext4'),capacity_bytes=outer['capacity_bytes'],
            resident_bytes=None,effective_direct_io=None),
        owned_write_mounts=retained_mounts,unscanned_retained_write_roots=[dict(source=p,destination=dst)
            for p,dst in retained_mounts.items() if dst!='/workspace/env'],
        upper_changed_objects=len(changed),upper_changed_readout_bytes=sum(r['resident_bytes'] for r in changed),
        upper_single_queries_stable=all(r['residency']['stable'] for r in changed),
        missing_live_fields=['RAM block rdev/capacity/full source maps row and cache query',
            'block-device cache query for rootfs and user_rootfs',
            'loop backing file endpoint mincore and effective direct-I/O state',
            'TMPFS whole-backing cache counters and swap-entry checks',
            'retained closed-file inventory under owned /run and /tmp'])


def collect(source):
    batch=read(source);assert batch['status']=='PASS' and len(batch['runs'])==3
    rows=[audit_run(r) for r in batch['runs']]
    assert {r['schedule'] for r in rows}=={'grpo','bpo','tvcache'}
    return dict(status='OFFLINE_AUDIT_COMPLETE',source=str(source),runs=rows,
        new_sandboxes=0,new_residency_queries=0,collector_changes=False,
        physical_total_bytes=None,unknown_cache_capacity_bytes=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=collect(args.source)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    print('status='+value['status']+' result_dir='+str(args.output))


if __name__=='__main__':main()
