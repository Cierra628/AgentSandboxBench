"""Mapped backing identities and additional global cache residency; no data reads."""
from pathlib import Path
import stat
import subprocess
import time

from host_file_monitor import mincore_file, pids
from host_special_metadata import mapping_line, attach_smaps, deleted_descriptors, ublkc_metadata


def mapped_backing_inventory(group,known_files,*,proc=Path('/proc'),ublk_query=None,query_deleted_cache=False):
    start=time.monotonic_ns();known={(f['device'],f['inode']) for f in known_files}
    objects={};errors=[];unresolved=[];processes=[];metadata_errors=[]
    for pid in pids(group):
        root=proc/str(pid)
        try:
            ticks=(root/'stat').read_text().rsplit(')',1)[1].split()[19]
            special_regions=[];deleted=set()
            for line in (root/'maps').read_text().splitlines():
                region=mapping_line(line)
                if region is None or not region['mapping_path'] or not region['inode']:continue
                # anon_inode keys can be shared by unrelated mappings; unresolved
                # entries are metadata groups, not unique physical memory objects.
                key=(region['device'],region['inode']);name=region['mapping_path']
                entry=objects.setdefault(key,dict(device=key[0],inode=key[1],mapping_path=name,owners=[],mappings=0))
                entry['mappings']+=1
                if pid not in entry['owners']:entry['owners'].append(pid)
                if not name.startswith('/') or name.endswith(' (deleted)') or name.startswith('/dev/ublk'):
                    region.update(pid=pid,start_ticks=ticks,smaps_status='NOT_OBSERVED')
                    entry.setdefault('regions',[]).append(region);special_regions.append(region)
                if name.endswith(' (deleted)'):deleted.add(key)
            if special_regions:
                try:attach_smaps(root,special_regions)
                except (OSError,ValueError) as error:metadata_errors.append(dict(pid=pid,stage='smaps',error=repr(error)))
            if deleted:
                try:
                    for key,descriptors in deleted_descriptors(root,deleted).items():
                        objects[key].setdefault('deleted_descriptors',[]).extend(dict(pid=pid,start_ticks=ticks,**d) for d in descriptors)
                except (OSError,ValueError) as error:metadata_errors.append(dict(pid=pid,stage='deleted-descriptors',error=repr(error)))
            if (root/'stat').read_text().rsplit(')',1)[1].split()[19]!=ticks:
                for r in special_regions:r['smaps_status']='PID_CHANGED'
                for key in deleted:objects[key]['deleted_descriptors']=[]
                raise ProcessLookupError('PID identity changed')
            processes.append(dict(pid=pid,start_ticks=ticks))
        except (OSError,ProcessLookupError) as error:errors.append(dict(pid=pid,error=repr(error)))
    files=[];overlap=[];special=[]
    for key,entry in objects.items():
        if key in known:
            overlap.append(entry);continue
        name=entry['mapping_path']
        if not name.startswith('/') or name.endswith(' (deleted)'):
            unresolved.append(dict(**entry,reason='mapping has no live absolute backing path'));continue
        path=proc/str(entry['owners'][0])/'root'/name.lstrip('/')
        try:
            info=path.stat()
            if (info.st_dev,info.st_ino)!=key:raise ValueError('mapped backing identity differs')
            if not stat.S_ISREG(info.st_mode):
                row=dict(**entry,mode=info.st_mode,rdev=info.st_rdev)
                if stat.S_ISCHR(info.st_mode) and Path(name).name.startswith('ublkc'):
                    try:
                        for r in entry['regions']:
                            if (proc/str(r['pid'])/'stat').read_text().rsplit(')',1)[1].split()[19]!=r['start_ticks']:
                                raise ProcessLookupError('ublkc owner identity changed')
                        row['command_buffer']=ublkc_metadata(path,row,ublk_query)
                        for r in entry['regions']:
                            if (proc/str(r['pid'])/'stat').read_text().rsplit(')',1)[1].split()[19]!=r['start_ticks']:
                                raise ProcessLookupError('ublkc owner identity changed')
                    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
                        row['command_buffer']=dict(status='UNKNOWN',allocation_bytes=None,error=repr(error))
                        metadata_errors.append(dict(mapping_path=name,stage='ublkc-info',error=repr(error)))
                special.append(row);continue
            before=time.monotonic_ns();value=mincore_file(path,live=True)
            if (value['device'],value['inode'])!=key or not (value['identity_stable'] and value['size_stable']):
                raise ValueError('backing changed during residency query')
            value.update(mapping_path=name,owners=entry['owners'],mappings=entry['mappings'],
                start_monotonic_ns=before,end_monotonic_ns=time.monotonic_ns())
            files.append(value)
        except (OSError,ValueError) as error:errors.append(dict(**entry,error=repr(error)))
    regions=[r for e in objects.values() for r in e.get('regions',[])]
    metadata_complete=not (errors or metadata_errors) and all(r.get('smaps_status')=='OBSERVED' for r in regions)
    metadata_complete=metadata_complete and all(e.get('deleted_descriptors') for e in unresolved if e['mapping_path'].endswith(' (deleted)'))
    command=[e['command_buffer'] for e in special if 'command_buffer' in e]
    metadata_complete=metadata_complete and all(c['status']=='SOURCE_CALCULATED' for c in command)
    result=dict(status='INCOMPLETE' if errors else 'OBSERVED',start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
        processes=processes,mapped_objects=len(objects),overlap_objects=overlap,special_objects=special,
        additional_files=files,additional_global_resident_bytes=sum(f['resident_bytes'] for f in files),
        unresolved=unresolved,errors=errors,coverage='INCOMPLETE' if errors or unresolved or special else 'ALL_ENUMERATED_MAPPINGS_CLASSIFIED',
        special_metadata_status='OBSERVED' if metadata_complete else 'INCOMPLETE',special_metadata_errors=metadata_errors,
        ublkc_command_allocation_bytes=sum(c['allocation_bytes'] for c in command) if command and all(c['status']=='SOURCE_CALCULATED' for c in command) else None,
        scope='Whole backing-file global cache, deduplicated against known objects, not only mapped ranges or sandbox-exclusive pages. Deleted/unresolved mappings remain unknown; no payload read.')
    if query_deleted_cache:
        from host_deleted_backing import query_deleted_backings_bounded
        deleted_regions=[r for e in unresolved if e['mapping_path'].endswith(' (deleted)') for r in e.get('regions',[])]
        result['deleted_backing_cache']=query_deleted_backings_bounded(deleted_regions)
        result['end_monotonic_ns']=time.monotonic_ns()
    return result
