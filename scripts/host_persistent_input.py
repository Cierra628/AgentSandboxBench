"""Explicit disk inputs for current-only tasks and the pair; old RAM seeds are unchanged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

from isolated_storage import GIB, available, capacity_budget, mount_identity
from experiment_disk_guard import writer_lock, guarded_run

ROOT=Path(__file__).resolve().parents[1]
PROFILE='host-persistent-input-v1'
PARTS=('deps',*('image-cache/'+p for p in ('configs','indexes','commits','remote-blocks','convert-blocks')))
BACKUP=ROOT/'.artifacts/storage-isolated-service/20261008T102942Z-3642885'
TASKS={
    'prettier6604':dict(task_id='prettier__prettier-6604',cache_root='prettier6604',source_gib=20,input_gib=8,backup='20261008T102942Z-3642885'),
    'sphinx7590':dict(task_id='sphinx-doc__sphinx-7590',cache_root='sphinx-backbone',source_gib=20,input_gib=10,backup='20261009T050703Z-3934019'),
    'xarray6992':dict(task_id='pydata__xarray-6992',cache_root='python-backbone',source_gib=24,input_gib=12,backup='20261008T165515Z-3834076')}
SCOPE='Fresh imported plain files, controller, private service and guests; archive restoration is outside this group and its scratch is removed before the baseline.'


def settings(task_id):
    for value in TASKS.values():
        if value['task_id']==task_id:return value
    raise ValueError('unsupported persistent input task: '+task_id)


def task_image(task_id):
    if task_id=='sphinx-doc__sphinx-7590':
        from sphinx7590_inputs import IMAGE
        return IMAGE
    if task_id=='pydata__xarray-6992':
        from xarray6992_inputs import IMAGE
        return IMAGE


def local_grpo_config(cfg):
    return (cfg.get('task_id')=='prettier__prettier-6604' and cfg.get('rollout')=='grpo' and
            bool(cfg.get('frozen_export')) and cfg.get('host_current_only') and cfg.get('host_file_monitor') and
            (not cfg.get('light_skip_boundary_vmstat') or cfg.get('light_monitor') and cfg.get('monitor_mode','continuous')=='continuous') and
            (not cfg.get('light_merge_boundary_meminfo') or cfg.get('light_skip_boundary_vmstat')) and
            cfg.get('checkpoint_after_step') is None and not any(cfg.get(k) for k in (
                'host_shared_files','host_prefix_count','shared_cache_measurement','root_count',
                'physical_cache_after_segments','live_cache_kernel','damon_observe','damon_disable',
                'grpo_create_before_setup')))


def single_config(cfg,*,local_no_proxy=False):
    if cfg.get('light_merge_boundary_meminfo') and not (local_no_proxy and local_grpo_config(cfg)):
        raise ValueError('merged boundary meminfo requires local seven-guest current GRPO')
    sampled=cfg.get('light_monitor') and cfg.get('monitor_mode','continuous')=='continuous'
    off=local_no_proxy and cfg.get('monitor_mode')=='off' and not cfg.get('light_monitor')
    if not (cfg.get('host_current_only') and cfg.get('host_file_monitor') and (sampled or off) and
            (cfg['rollout']=='single' or local_no_proxy and local_grpo_config(cfg)) and cfg['checkpoint_after_step'] is None and
            not (cfg.get('damon_observe') or cfg.get('damon_disable')) and cfg['task_id'] in {v['task_id'] for v in TASKS.values()}):
        raise ValueError('persistent input requires a complete sampled current-only task or local sampling-off control, with default reclaim')
    task=settings(cfg['task_id'])
    if task['cache_root']!='prettier6604' and Path(cfg.get('cache_backup_directory','')).resolve()!=ROOT/'.artifacts/storage-isolated-service'/task['backup']:
        raise ValueError('persistent input requires this task\'s fixed verified backup')


def read(p):return json.loads(Path(p).read_text())
def write(p,v):
    p=Path(p);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2)+'\n');tmp.replace(p)
def owned(out):
    out=Path(out).absolute()
    if out.resolve()!=out or out.parent not in {ROOT/'.artifacts'/v['cache_root'] for v in TASKS.values()} or not out.name.startswith('host-persistent-'):
        raise ValueError('persistent input must be an owned host-persistent directory')
    return out

def call(argv,timeout=60):
    return subprocess.check_output(list(map(str,argv)),stderr=subprocess.PIPE,text=True,timeout=timeout).strip()

def require_budget(capacity):
    value=capacity_budget(available(ROOT),capacity)
    if not value['ready']:raise RuntimeError('persistent input disk budget: '+json.dumps(value))
    return value


def native_option(args):
    value=getattr(args,'persistent_cache_seed',None)
    pair=getattr(args,'host_current_pair',False) and getattr(args,'host_scaling_mode',None)=='cold'
    from validate_isolated_agentenv import ordinary_current_only
    single=getattr(args,'host_current_only',False) and ordinary_current_only(args) and not getattr(args,'host_scaling_mode',None)
    off=(os.environ.get('ASB_LOCAL_NO_PROXY')=='on' and getattr(args,'host_current_only',False) and
        ordinary_current_only(args,monitor_mode='off') and
        not getattr(args,'light_monitor',False) and not getattr(args,'host_scaling_mode',None) and
        not getattr(args,'local_no_proxy_check',False))
    if value and not (((pair or single) and getattr(args,'light_monitor',False) or off)
            and getattr(args,'host_file_monitor',False)
            and not any(getattr(args,k,None) for k in ('immutable_cache_seed','damon_observe','damon_disable'))):
        raise ValueError('persistent cache requires a sampled current pair/full task or local sampling-off control, default reclaim and no RAM seed')
    return value


def release_source(out,*,remove=False):
    """Release only loops/mounts for this exact scratch file, including an interrupted attach."""
    out=owned(out);path=out/'source.json'
    if not path.exists():return
    value=read(path);assert value['profile']==PROFILE
    image=out/'source.ext4';mount=out/'readonly'
    devices=[line.split(':',1)[0] for line in call(['losetup','-j',image]).splitlines() if line]
    assert all(re.fullmatch(r'/dev/loop[0-9]+',d) for d in devices)
    if mount.is_mount():
        info=json.loads(call(['findmnt','-J','-M',mount,'-o','SOURCE,TARGET']))['filesystems'][0]
        assert info['source'] in devices and Path(info['target'])==mount
        call(['umount',mount],30)
    for device in devices:call(['losetup','-d',device],30)
    assert not call(['losetup','-j',image]) and not mount.is_mount()
    if value.get('released') and value.get('successful_scratch_removed') and not image.exists():return
    value.update(mounted=False,loop_device=None,released=True)
    if remove:
        assert value['status']=='PASS'
        image.unlink();value['successful_scratch_removed']=True
    else:value['failed_scratch_retained']=image.exists()
    value['release_end_monotonic_ns']=time.monotonic_ns();write(path,value)


def prepare(out,task_profile='prettier6604'):
    from validate_isolated_agentenv import IMAGE,immutable_manifest
    out=owned(out);assert not (out/'source.json').exists()
    task=TASKS[task_profile];assert out.parent==ROOT/'.artifacts'/task['cache_root']
    # Archive reads must not instantiate cache in the future measurement group.
    if os.environ.get('ASB_HOST_FILE_SLICE'):raise ValueError('restore scratch outside the measurement group')
    budget=require_budget((task['source_gib']+task['input_gib'])*GIB)
    mem=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
    assert mem>64*GIB
    original=BACKUP if task_profile=='prettier6604' else ROOT/'.artifacts/storage-isolated-service'/task['backup']
    if task_profile=='prettier6604':backup=read(original/'volume-backup.json')
    else:
        from prepare_prettier6604_cache import xarray_backup_metadata
        backup=xarray_backup_metadata(original,task_profile)
    archive=original/'volume.ext4.gz'
    assert backup['status']=='PASS' and backup['source_bytes']==task['source_gib']*GIB
    start=time.monotonic_ns();value=dict(status='FAIL',profile=PROFILE,budget=budget,mounted=False,
        loop_device=None,start_monotonic_ns=start,source_backup=str(archive),task_id=task['task_id'])
    write(out/'source.json',value);(out/'readonly').mkdir()
    try:
        with archive.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==backup['archive_sha256']
        image=out/'source.ext4'
        with image.open('xb') as f:os.posix_fallocate(f.fileno(),0,task['source_gib']*GIB);os.fsync(f.fileno())
        with image.open('r+b') as f,(out/'decompress.stderr').open('x') as error:
            subprocess.run(['pigz','-dc','-p','4',str(archive)],stdout=f,stderr=error,check=True,timeout=180)
            f.flush();os.fsync(f.fileno())
        info=image.stat();assert info.st_size==task['source_gib']*GIB and info.st_blocks*512>=task['source_gib']*GIB
        value.update(source_logical_bytes=info.st_size,source_allocated_bytes=info.st_blocks*512)
        with image.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==backup['source_sha256']
        header=call(['dumpe2fs','-h',image],30);assert 'Filesystem state:         clean' in header
        value['loop_device']=call(['losetup','--find','--show','--read-only',image],30);write(out/'source.json',value)
        call(['mount','-t','ext4','-o','ro,noload,nodev,nosuid,noexec',value['loop_device'],out/'readonly'],30)
        value['mounted']=True;write(out/'source.json',value)
        parts={p:immutable_manifest(out/'readonly/env'/p) for p in PARTS}
        assert all(row['type']=='file' for rows in parts.values() for row in rows.values())
        assert sum(row['size'] for rows in parts.values() for row in rows.values())<task['input_gib']*GIB
        value.update(status='PASS',parts=parts,server_image=IMAGE,
            backup_sha256=backup['archive_sha256'],uncompressed_sha256=backup['source_sha256'],end_monotonic_ns=time.monotonic_ns())
        if task_image(task['task_id']):value['task_image']=task_image(task['task_id'])
        write(out/'source.json',value)
    except BaseException as error:
        value.update(status='FAIL',error=repr(error));write(out/'source.json',value)
        raise


def identities(root,parts):
    values={}
    for part,entries in parts.items():
        for name in entries:
            p=root/part/name;s=p.lstat();assert stat.S_ISREG(s.st_mode) and p.resolve()==p
            values[part+'/'+name]=dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns)
    return values


def import_input(out):
    from host_file_monitor import measurement_group
    from validate_isolated_agentenv import immutable_manifest
    out=owned(out);group,_=measurement_group();source=read(out/'source.json')
    assert source['profile']==PROFILE and source['status']=='PASS' and source['mounted'] and not source.get('released')
    task=settings(source['task_id']);assert out.parent==ROOT/'.artifacts'/task['cache_root']
    mount_identity(out/'readonly',source['loop_device'])
    assert (Path('/sys/class/block')/Path(source['loop_device']).name/'ro').read_text().strip()=='1'
    require_budget(task['input_gib']*GIB)
    started=time.monotonic_ns();root=out/'input/env'
    (out/'input').mkdir()  # No existing directory or inode may silently be reused.
    value=dict(status='FAIL',profile=PROFILE,group=str(group),group_id=group.stat().st_ino,
        import_start_monotonic_ns=started,source_env=str(root),scope=SCOPE)
    write(out/'seed.json',value)
    try:
        for part in PARTS:
            src=out/'readonly/env'/part;dest=root/part;dest.parent.mkdir(parents=True,exist_ok=True)
            assert immutable_manifest(src)==source['parts'][part]
            call(['cp','-a','--reflink=never','--sparse=always','--',src,dest],120)
            assert immutable_manifest(dest)==source['parts'][part]==immutable_manifest(src)
        actual=identities(root,source['parts']);original=identities(out/'readonly/env',source['parts'])
        assert not ({(v['device'],v['inode']) for v in actual.values()} & {(v['device'],v['inode']) for v in original.values()})
        for name in actual:
            with (root/name).open('rb') as f:os.fsync(f.fileno())
        for directory in sorted([out/'input',root,*[p for p in root.rglob('*') if p.is_dir()]],key=lambda p:len(p.parts),reverse=True):
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        release_source(out,remove=True)
        value.update(status='PASS',parts=source['parts'],files=actual,task_id=source['task_id'],server_image=source['server_image'],
            source_backup=source['source_backup'],import_end_monotonic_ns=time.monotonic_ns(),scratch_removed_before_runtime=True)
        if 'task_image' in source:value['task_image']=source['task_image']
        write(out/'seed.json',value)
    except BaseException as error:
        value.update(status='FAIL',error=repr(error));write(out/'seed.json',value);raise


def validate_seed(out,*,task_id='prettier__prettier-6604'):
    from host_file_monitor import measurement_group
    from validate_isolated_agentenv import IMAGE
    out=owned(out);value=read(out/'seed.json');group,_=measurement_group()
    task=settings(task_id);assert out.parent==ROOT/'.artifacts'/task['cache_root']
    assert value['status']=='PASS' and value['profile']==PROFILE and value['server_image']==IMAGE and value['task_id']==task_id
    if task_image(task_id):assert value['task_image']==task_image(task_id)
    assert (value['group'],value['group_id'])==(str(group),group.stat().st_ino)
    root=out/'input/env';assert value['source_env']==str(root) and identities(root,value['parts'])==value['files']
    source=read(out/'source.json')
    assert source['task_id']==task_id
    assert value['scratch_removed_before_runtime'] and source['released'] and source['successful_scratch_removed']
    assert not (out/'source.ext4').exists() and not (out/'readonly').is_mount()
    assert source['release_end_monotonic_ns']<=value['import_end_monotonic_ns']
    return value,root


def observe_input(out,group,output,*,task_id='prettier__prettier-6604'):
    from host_file_monitor import counters,mincore_file
    start=time.monotonic_ns();cpu=time.thread_time_ns();files=[];seen=set()
    try:
        seed,root=validate_seed(out,task_id=task_id);before=counters(group)
        for name,identity in seed['files'].items():
            key=(identity['device'],identity['inode'])
            if key in seen:continue
            seen.add(key);row=mincore_file(root/name)
            assert row['stable'] and all(row[k]==identity[k] for k in ('device','inode','size','mtime_ns'))
            files.append(row)
        assert identities(root,seed['parts'])==seed['files']
        after=counters(group);resident=sum(r['resident_bytes'] for r in files)
        value=dict(status='OBSERVED',profile=PROFILE,files=files,path_count=len(seed['files']),unique_file_count=len(files),resident_bytes=resident,counters_before=before,counters_after=after,
            group=str(group),group_id=group.stat().st_ino,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
            thread_cpu_ns=time.thread_time_ns()-cpu,shortfall_bytes=max(0,resident-max(before['file'],after['file'])),
            ownership='NOT_QUERIED',scope='Imported input files only; sequential, not atomic; fresh-inode provenance plus residency cross-check, not whole-host coverage.')
    except Exception as error:
        value=dict(status='FAIL',profile=PROFILE,error=repr(error),start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
            thread_cpu_ns=time.thread_time_ns()-cpu,resident_bytes=None,shortfall_bytes=None,ownership='NOT_QUERIED')
    write(output,value);return value


def run_phase(action,out,timeout,*,task_profile='prettier6604'):
    out=owned(out)
    with (out/(action+'.log')).open('x') as log:
        argv=[sys.executable,'-B',str(ROOT/'scripts/host_persistent_input.py'),action,str(out)]
        if task_profile!='prettier6604':argv+=['--task-profile',task_profile]
        if action.startswith('release'):
            subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,timeout=timeout,check=True)
        else:guarded_run(argv,directory=out,log=log,timeout=timeout)


def release_input(out):
    out=owned(out);seed,_=validate_seed(out,task_id=read(out/'seed.json')['task_id'])
    shutil.rmtree(out/'input');seed['released']=True;write(out/'seed.json',seed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('prepare','import','release-input','release-source'));parser.add_argument('output',type=Path)
    parser.add_argument('--task-profile',choices=TASKS,default='prettier6604')
    args=parser.parse_args()
    os.umask(0o077)
    with writer_lock():
        if args.action=='prepare':prepare(args.output,args.task_profile)
        elif args.action=='import':import_input(args.output)
        elif args.action=='release-input':release_input(args.output)
        else:release_source(args.output)
    print('status=PASS action='+args.action+' result_dir='+str(args.output))
