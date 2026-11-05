"""Explicit identical-path density experiment; metadata and two residency queries only."""
import json
import fcntl
import os
from pathlib import Path
import stat
import struct
import time

from host_private_files import read,write,query_objects
from host_rootfs_layers import file_identity,rootfs_inventory,validate_rootfs_inventory
from host_full_rootfs_control import manifest_layers

ROOT=Path(__file__).resolve().parents[1]
COUNTS=(1,2,4,7,10)


def request(path):
    value=read(path)
    if value.get('profile')!='host-density-v1' or value.get('task') not in ('prettier6604','c4'):
        raise ValueError('density requires pinned Prettier or C4')
    if type(value.get('count')) is not int or value['count'] not in COUNTS or value.get('mode') not in ('shared','independent'):
        raise ValueError('unsupported density count or group')
    if value['task']=='c4' and value['count']!=7:raise ValueError('C4 density requires N=7')
    if 'pressure_context' in value:
        directory=Path(value['pressure_context']).resolve()
        allowed=(4,7,10) if value.get('pressure_extension') is True else (10,)
        if value['task']!='prettier6604' or value['count'] not in allowed or type(value.get('pressure_enabled')) is not bool or not directory.is_relative_to(ROOT/'.artifacts') or directory.name!='pressure':
            raise ValueError('pressure comparison requires an explicit owned Prettier context and supported count')
    daemon=Path(value['daemon']).resolve()
    with daemon.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF':raise ValueError('density daemon must be ELF')
    return value


def prepare(mount,value,call,*,layers=None):
    mount=Path(mount);env=mount/'env';root=env/'rootfs-copy-control';root.mkdir()
    layers=manifest_layers() if layers is None else layers
    copies=[];objects=[]
    for slot in range(value['count'] if value['mode']=='independent' else 1):
        selected=[]
        for index,layer in enumerate(layers):
            original=env/'image-cache/commits'/layer['relative']
            copy_needed=slot!=0 or value.get('task')=='c4'
            path=root/'copies'/str(slot)/layer['relative'] if copy_needed else original
            if copy_needed:
                path.parent.mkdir(parents=True,exist_ok=True)
                call(f'density-copy-{slot}-{index}',['cp','-a','--reflink=never','--sparse=always','--',original,path],120)
            path.chmod(0o444);identity=file_identity(path)
            if identity['size']!=layer['size'] or path.stat().st_nlink!=1:raise ValueError('density copy size/link count differs')
            objects.append(dict(path=str(path),relative=str(path.relative_to(env)),identity=identity,users=[]))
            selected.append(dict(digest=layer['source_digest'],file='/workspace/env/'+str(path.relative_to(env)),size=identity['size']))
        copies.append(selected)
    keys={(o['identity']['device'],o['identity']['inode']) for o in objects}
    if len(keys)!=len(layers)*len(copies):raise ValueError('density copies alias an inode')
    if call('density-filesystem',['findmnt','-n','-o','FSTYPE','-T',mount],10).strip()!='ext4':
        raise ValueError('independence evidence requires the existing non-reflink ext4 volume')
    manifest=dict(version=1,mode='private' if value['mode']=='independent' else 'shared',schedule='density',
        density_count=value['count'],initial_lower_count=len(layers),runtime_root='/workspace/env/firecracker-work',
        receipts='/workspace/env/rootfs-copy-control/receipts',copies=copies)
    write(root/'manifest.json',manifest)
    result=dict(status='PREPARED',request=value,layers=layers,objects=objects,copy_method='cp -a --reflink=never --sparse=always',
        filesystem='ext4',hard_links='ALL_NLINK_ONE',target_file_bind_mounts='NOT_USED')
    write(root/'preparation.json',result)
    return result


def context(path,args):
    if path is None:return None
    path=Path(path).resolve();out=args.output.resolve()
    if path.parent.name!='density' or path.parent.parent!=out.parent:raise ValueError('density context outside owned controller')
    value=read(path)
    if value['container']!=args.container or value['output']!=str(out) or value['task']!=getattr(args,'density_task','prettier6604'):
        raise ValueError('density child binding differs')
    import uuid
    if str(uuid.UUID(value['sandbox_id']))!=value['sandbox_id']:raise ValueError('invalid density sandbox ID')
    return dict(value,path=str(path))


def tools_finished(value,end_ns):
    if value is None:return
    root=Path(value['path']).parent
    write(root/(value['name']+'-tools.json'),dict(status='TOOLS_COMPLETE',end_monotonic_ns=end_ns))
    deadline=time.monotonic()+180
    while not (root/'paths.release').exists():
        if time.monotonic()>deadline:raise TimeoutError('density coordinator did not release final checks')
        time.sleep(.02)
    if (root/'paths.release').read_text().strip()!='continue':raise RuntimeError('density group aborted')


def evict(objects,group):
    start=time.monotonic_ns();cpu=time.thread_time_ns()
    for obj in objects:
        path=Path(obj['path'])
        if not path.resolve().is_relative_to(ROOT/'.artifacts/storage-isolated-service') or file_identity(path)!=obj['identity']:
            raise ValueError('density eviction target is not the prepared owned file')
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
        finally:os.close(fd)
    record=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        operation='POSIX_FADV_DONTNEED',files=len(objects))
    point=query_objects(objects,group,'P0-before-guest-creation')
    if point['resident_bytes']!=0:raise RuntimeError('density P0 is not zero; guests must not start')
    return record,point


def independent_extents(objects):
    """Read extent metadata after sync; verify links and absence of shared extents."""
    spans=[];records=[]
    for obj in objects:
        fd=os.open(obj['path'],os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if info.st_nlink!=1:raise ValueError('target has hard links')
            start=0;count=0
            while True:
                buffer=bytearray(32+56*256)
                struct.pack_into('=QQIIII',buffer,0,start,(1<<64)-1-start,0,0,256,0)
                fcntl.ioctl(fd,0xC020660B,buffer,True)  # FS_IOC_FIEMAP, no SYNC flag.
                mapped=struct.unpack_from('=I',buffer,20)[0]
                if not mapped:break
                for i in range(mapped):
                    logical,physical,length=struct.unpack_from('=QQQ',buffer,32+56*i)
                    flags=struct.unpack_from('=I',buffer,32+56*i+40)[0]
                    if flags & (0x2000|0x2|0x4):raise ValueError('shared or unresolved target extent')
                    spans.append((physical,physical+length,obj['path']));count+=1
                if flags & 1:break
                start=logical+length
            records.append(dict(path=obj['path'],extents=count,nlink=info.st_nlink))
        finally:os.close(fd)
    spans.sort()
    for a,b in zip(spans,spans[1:]):
        if a[1]>b[0]:raise ValueError('target files have overlapping physical extents')
    return dict(status='PASS',method='FIEMAP without SYNC, distinct extents on the one ext4 volume',files=records)


def live_objects(preparation,guests,binding):
    from host_prefix_compare import drives_for_vm
    from host_rollout_shared import opened_regular_files
    bindings={role:binding.resolve(sid) for role,sid in guests.items()}
    drives={role:drives_for_vm(vm) for role,vm in bindings.items()}
    inventory=rootfs_inventory(bindings,drives,opened_regular_files(binding.cgroup),{})
    if inventory['status']!='IDENTIFIED':raise ValueError('density rootfs identity chain incomplete')
    expected={(o['identity']['device'],o['identity']['inode']):o for o in preparation['objects']}
    actual={(o['device'],o['inode']):o for o in inventory['objects']}
    if set(expected)!=set(actual):raise ValueError('density live lower identities differ from prepared objects')
    layers=preparation.get('layers') or manifest_layers()
    n=preparation['request']['count'];shared=preparation['request']['mode']=='shared'
    for role,guest in inventory['guests'].items():
        if len(guest['lowers'])!=len(layers):raise ValueError('density guest has wrong layer count')
        if [r['reference']['digest'] for r in guest['lowers']]!=[r['source_digest'] for r in layers]:
            raise ValueError('density guest lower order/digests differ')
    result=[]
    for key,obj in actual.items():
        if len(obj['users'])!=(n if shared else 1):raise ValueError('density lower sharing differs from requested mode')
        result.append(dict(expected[key],users=obj['users']))
    validate_rootfs_inventory(inventory)
    return result,dict(bindings=bindings,drives=drives,inventory=inventory,status='PASS')
