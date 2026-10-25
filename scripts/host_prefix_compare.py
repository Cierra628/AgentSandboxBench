"""Metadata-only endpoint observations for identical cold-guest prefixes."""
import array
import csv
import fcntl
import json
import os
from pathlib import Path
import stat
import time


def prefix_paths(rollouts,count):
    if type(count) is not int or count not in (1,7):raise ValueError('prefix count must be 1 or 7')
    roles=['backbone'] if count==1 else list(rollouts)
    assert len(roles)==count
    paths={r:rollouts[r][:2] for r in roles}
    assert all(p==['read_repo','read_typescript'] for p in paths.values())
    return paths


def drives_for_vm(vm):
    from host_sandbox_monitor import UnixHTTP,identity
    assert identity(vm['pid'])=={k:vm[k] for k in ('pid','start_ticks','boot_id')}
    connection=UnixHTTP(vm['api_socket'])
    try:
        connection.request('GET','/vm/config');response=connection.getresponse()
        if response.status!=200:raise RuntimeError('GET /vm/config status '+str(response.status))
        # Never save the complete VM configuration or MMDS body.
        drives=json.loads(response.read(1024*1024))['drives']
    finally:connection.close()
    rows=[]
    for drive in drives:
        target=Path(drive['path_on_host'])
        path=Path('/proc')/str(vm['pid'])/('root' if target.is_absolute() else 'cwd')/str(target).lstrip('/')
        info=path.stat();block=stat.S_ISBLK(info.st_mode)
        assert block or stat.S_ISREG(info.st_mode)
        rows.append(dict(drive_id=drive['drive_id'],is_read_only=drive['is_read_only'],direct=drive.get('direct'),
            is_root_device=drive['is_root_device'],path=str(path),device=info.st_dev,
            inode=info.st_ino,rdev=info.st_rdev,block_device=block))
    assert identity(vm['pid'])=={k:vm[k] for k in ('pid','start_ticks','boot_id')}
    return rows


def shared_drives(inventory):
    """Return a drive only when all actual VMMs reference the same object."""
    by_drive={}
    for role,rows in inventory.items():
        for row in rows:by_drive.setdefault(row['drive_id'],[]).append(dict(row,role=role))
    return [rows for rows in by_drive.values() if len(rows)==len(inventory) and all(r['is_read_only'] for r in rows)
        and len({(r['device'],r['inode'],r['rdev']) for r in rows})==1]


def observe_prefix(args,out,phase,guests,*,file_focus=False):
    from host_file_monitor import measurement_group,counters,pss_snapshot,residency_check,mincore_file
    from host_sandbox_monitor import Binding
    group,_=measurement_group();started=time.monotonic_ns()
    entry=dict(start_monotonic_ns=started,counters_at_start=counters(group))
    roots=list(map(Path,json.loads(args.host_prefix_roots.read_text())))
    entry['pss']=dict(status='NOT_REQUESTED_FILE_FOCUS') if file_focus else pss_snapshot(group)
    entry['regular']=residency_check(group,roots,out/('prefix-'+phase+'-regular.json'),live=True)
    if entry['regular']['status']!='OBSERVED':raise ValueError('readonly layer inventory incomplete')
    entry['vms']=[];entry['drives']={};entry['shared_readonly_drives']=[]
    if guests:
        binding=Binding(args.container,out)
        for role,sid in guests.items():
            vm=binding.resolve(sid);entry['vms'].append(dict(role=role,sandbox_id=sid,**vm))
            entry['drives'][role]=drives_for_vm(vm)
        candidates=shared_drives(entry['drives'])
        if file_focus:
            unique={}
            for rows in candidates:
                row=rows[0]
                key=('block',row['rdev']) if row['block_device'] else ('regular',row['device'],row['inode'])
                unique.setdefault(key,[]).extend(rows)
            candidates=list(unique.values())
        for rows in candidates:
            row=rows[0];key=(row['device'],row['inode'],row['rdev']);before=time.monotonic_ns()
            options={}
            if row['block_device']:
                fd=os.open(row['path'],os.O_RDONLY|os.O_CLOEXEC)
                try:
                    capacity=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,capacity,True)
                finally:os.close(fd)
                options=dict(block_identity=key,block_length=capacity[0])
            value=mincore_file(row['path'],**options)
            entry['shared_readonly_drives'].append(dict(drive_id=row['drive_id'],is_root_device=row['is_root_device'],
                users=sorted({r['role'] for r in rows}) if file_focus else [r['role'] for r in rows],
                **(dict(drive_ids=sorted({r['drive_id'] for r in rows})) if file_focus else {}),
                start_monotonic_ns=before,end_monotonic_ns=time.monotonic_ns(),
                residency=value,initial_residency='NOT_OBSERVED_BEFORE_DEVICE_CREATION'))
    entry['counters_at_end']=counters(group);entry['end_monotonic_ns']=time.monotonic_ns()
    entry['main_counter']=entry['counters_at_end'] if phase=='before' else entry['counters_at_start']
    entry['main_counter_monotonic_ns']=entry['end_monotonic_ns'] if phase=='before' else started
    entry['scope']='Sequential endpoint observations; no content read or guest pause; file/current/PSS and object cache are not added'
    (out/('prefix-'+phase+'.json')).write_text(json.dumps(entry,indent=2)+'\n')
    return entry


def audit_prefix(raw):
    from run_prettier6604_grpo import verify_path
    raw=Path(raw);result=json.loads((raw/'result.json').read_text());n=result['host_prefix_count']
    assert result['status']=='PASS' and result['actual_action_count']==11*n
    assert result['official_evaluation']=='NOT_RUN_PREFIX_ONLY' and result['final_roles']==[]
    assert not list(raw.glob('checkpoint-*.json')) and result['cleanup']['service_sandbox_list_empty']
    cfg=json.loads((raw/'effective-config.json').read_text())
    paths=prefix_paths(cfg['paths'],n)
    provenance=json.loads((raw/'input-provenance.json').read_text())
    source=json.loads((raw/'frozen-input/agent-sampling.json').read_text())['segments']
    names=provenance['original_segment_names']
    common=None
    for role,path in paths.items():
        verified=verify_path(raw,role,path,raw/'frozen-input',source,names)
        assert all(result['paths'][role][k]==v for k,v in verified.items())
        commands=[]
        for segment in path:
            with (raw/'rollouts'/role/segment/'actions.tsv').open() as stream:
                commands += [r['command'] for r in csv.DictReader(stream,delimiter='\t')]
        if common is None:common=commands
        assert commands==common and len(commands)==11
    before=result['prefix_before'];after=result['prefix_after']
    assert len(after['vms'])==n and len({v['sandbox_id'] for v in after['vms']})==n
    assert before['end_monotonic_ns']<after['start_monotonic_ns']
    return dict(status='PASS',sandboxes=n,actual_actions=11*n,official_task_status='NOT_RUN_PREFIX_ONLY',
        baseline=before['main_counter'],endpoint=after['main_counter'],
        delta={key:after['main_counter'][key]-before['main_counter'][key] if before['main_counter'][key] is not None and after['main_counter'][key] is not None else None for key in before['main_counter']},
        before=before,after=after,scope='Creation plus original common 11-action prefix; no suffix, snapshot or official test')
