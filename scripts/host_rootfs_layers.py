"""Associate live user-rootfs lowers using metadata only; never read layer data."""
import json
from pathlib import Path
import stat


def file_identity(path):
    info=path.stat()
    if not stat.S_ISREG(info.st_mode):raise ValueError('lower is not a regular file')
    return dict(device=info.st_dev,inode=info.st_ino,size=info.st_size,mtime_ns=info.st_mtime_ns)


def local_path(name,config,process):
    if '://' in name:raise ValueError('lower has no confirmed local path')
    path=Path(name)
    return process/'root'/str(path).lstrip('/') if path.is_absolute() else config.parent/path


def lower_file(lower,config,process):
    # Match OverlayBD: explicit file first, then commit, then legacy sealed name.
    if lower.get('targetFile') or lower.get('gzipIndex') or lower.get('targetDigest'):
        raise ValueError('auxiliary lower files are outside this query')
    if lower.get('file'):return local_path(lower['file'],config,process)
    if lower.get('dir'):
        directory=local_path(lower['dir'],config,process)
        for name in ('overlaybd.commit','overlaybd.sealed'):
            path=directory/name
            try:path.stat()
            except FileNotFoundError:continue
            return path
    raise ValueError('no local lower file; remote or unresolved lower')


def rootfs_inventory(bindings,inventory,opened,restored,*,proc_root=Path('/proc')):
    """Record ordered references and deduplicate by inode, not digest or path."""
    guests={};objects={}
    for role,vm in bindings.items():
        process=proc_root/str(vm['pid']);config=process/'cwd/overlaybd/image.json'
        guest=dict(pid=vm['pid'],image_config=str(config),template=restored.get(role),
            status='UNKNOWN',lowers=[],resident_bytes=None,
            upper=dict(status='NOT_REQUESTED',resident_bytes=None))
        guests[role]=guest
        try:
            drives=[r for r in inventory[role] if r['drive_id']=='user_rootfs']
            if len(drives)!=1 or not drives[0]['block_device']:raise ValueError('no unique user_rootfs block device')
            guest['drive']=dict(drives[0])
            before=file_identity(config)
            image=json.loads(config.read_text())
            if file_identity(config)!=before:raise ValueError('rootfs config changed during reading')
            guest['config_identity']=before
            if not isinstance(image,dict) or not isinstance(image.get('lowers'),list) or not image['lowers']:raise ValueError('no rootfs lowers')
            guest['upper'].update({k:image.get('upper',{}).get(k,'') for k in ('data','index')})
            for index,lower in enumerate(image['lowers']):
                if not isinstance(lower,dict):raise ValueError('invalid rootfs lower metadata')
                row=dict(index=index,status='UNKNOWN',resident_bytes=None,
                    reference={k:lower[k] for k in ('file','dir','digest','uuid','size','targetFile','gzipIndex','targetDigest') if k in lower})
                guest['lowers'].append(row)
                try:
                    path=lower_file(lower,config,process);info=file_identity(path)
                    descriptors=opened.get((info['device'],info['inode']),[])
                    if not descriptors:raise ValueError('lower is not open in the owned service')
                    row.update(info,path=str(path),status='IDENTIFIED',open_descriptors=descriptors)
                except (OSError,ValueError,TypeError) as error:row['error']=str(error)
            if file_identity(config)!=before:raise ValueError('rootfs config changed during inventory')
            guest['status']='IDENTIFIED' if all(r['status']=='IDENTIFIED' for r in guest['lowers']) else 'INCOMPLETE'
        except (OSError,ValueError,KeyError,TypeError) as error:
            guest['error']=str(error)
            # A changing configuration cannot establish any references for this guest.
            for row in guest['lowers']:row.update(status='UNKNOWN',error=str(error))
        for row in guest['lowers']:
            if row['status']!='IDENTIFIED':continue
            key=('regular',row['device'],row['inode'])
            if key not in objects:
                objects[key]=dict(path=row['path'],object_key=list(key),device=row['device'],inode=row['inode'],
                    size=row['size'],mtime_ns=row['mtime_ns'],rdev=0,block_device=False,users=[],templates=[],aliases=[],
                    origin='ACTIVE_ROOTFS_LOWER',resident_bytes=None)
            obj=objects[key]
            if (obj['size'],obj['mtime_ns'])!=(row['size'],row['mtime_ns']):
                obj['error']='lower metadata differs between references'
            if role not in obj['users']:obj['users'].append(role)
            if row['path'] not in obj['aliases']:obj['aliases'].append(row['path'])
            if restored.get(role) and restored[role] not in obj['templates']:obj['templates'].append(restored[role])
    for obj in objects.values():
        obj['status']='UNKNOWN' if 'error' in obj else 'SELECTED' if len(obj['users'])>=2 else 'NOT_REQUESTED_SINGLE_USER'
    complete=all(v['status']=='IDENTIFIED' for v in guests.values()) and all(v['status']!='UNKNOWN' for v in objects.values())
    return dict(status='IDENTIFIED' if complete else 'INCOMPLETE',guests=guests,objects=list(objects.values()),
        scope='Local immutable rootfs lowers referenced by live guests; only >=2 confirmed users selected. Upper, reserved slots and preparation copies are not queried.')


def validate_rootfs_inventory(value):
    """Recheck metadata after residency queries; no payload or process memory read."""
    try:
        for guest in value['guests'].values():
            if guest['status'] not in ('IDENTIFIED','INCOMPLETE'):continue
            if file_identity(Path(guest['image_config']))!=guest['config_identity']:
                raise ValueError('rootfs config changed during query')
            drive=guest['drive'];info=Path(drive['path']).stat()
            if not stat.S_ISBLK(info.st_mode) or (info.st_dev,info.st_ino,info.st_rdev)!=(drive['device'],drive['inode'],drive['rdev']):
                raise ValueError('user_rootfs device changed during query')
            for row in guest['lowers']:
                if row['status']=='IDENTIFIED' and file_identity(Path(row['path']))!={k:row[k] for k in ('device','inode','size','mtime_ns')}:
                    raise ValueError('rootfs lower changed during query')
    except (OSError,ValueError) as error:
        value.update(status='INCOMPLETE',validation_error=str(error))
        raise
