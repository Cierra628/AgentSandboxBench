"""End-of-path residency of owned regular backings; never scan guest files or pause."""
import os
from pathlib import Path
import stat
import time

from host_file_monitor import mincore_file
from host_rootfs_layers import local_path


def observe_owned_files(env, rootfs, previous, *, opened=None):
    """Deduplicate against earlier lower queries; mutable/disappearing files stay partial."""
    env=Path(env);start=time.monotonic_ns();cpu=time.thread_time_ns();owned_device=env.stat().st_dev
    prior={tuple(o['object_key']) for o in previous if o['object_key'][0]=='regular'}
    annotations={};unresolved=[];files=[];seen=set(prior)
    for role,guest in rootfs['guests'].items():
        config=Path(guest['image_config']);process=Path('/proc')/str(guest['pid'])
        for kind in ('data','index'):
            name=guest.get('upper',{}).get(kind)
            if not name:
                unresolved.append(dict(role=role,category='rootfs_upper_'+kind,status='NOT_IDENTIFIED',resident_bytes=None))
                continue
            try:
                path=local_path(name,config,process);s=path.stat()
                if not stat.S_ISREG(s.st_mode):raise ValueError('upper is not a regular file')
                key=('regular',s.st_dev,s.st_ino)
                a=annotations.setdefault(key,dict(category='rootfs_upper_'+kind,users=[],declared_paths=[]))
                if a['category']!='rootfs_upper_'+kind:raise ValueError('upper categories alias the same inode')
                a['users'].append(role);a['declared_paths'].append(str(path))
            except (OSError,ValueError) as error:
                unresolved.append(dict(role=role,category='rootfs_upper_'+kind,status='UNRESOLVED',resident_bytes=None,error=str(error)))
    referenced=set()
    for parent,dirs,names in os.walk(env,followlinks=False):
        dirs[:]=[n for n in dirs if not (Path(parent)/n).is_symlink()]
        for name in sorted(names):
            path=Path(parent)/name
            try:
                s=path.lstat()
                if not stat.S_ISREG(s.st_mode):continue
                if s.st_dev!=owned_device:raise ValueError('file is outside owned filesystem')
                key=('regular',s.st_dev,s.st_ino)
                if key in annotations:referenced.add(key)
                if key in seen:continue
                seen.add(key)
                info=annotations.get(key,{})
                relative=str(path.relative_to(env))
                category=info.get('category','other_owned_'+relative.split('/',1)[0])
                row=dict(object_key=list(key),path=str(path),relative_path=relative,category=category,
                    users=sorted(set(info.get('users',[]))),declared_paths=info.get('declared_paths',[]),
                    open_descriptors=(opened or {}).get((s.st_dev,s.st_ino),[]),logical_bytes=s.st_size)
                begin=time.monotonic_ns();thread_cpu=time.thread_time_ns()
                try:
                    value=mincore_file(path,live=True)
                    if (value['device'],value['inode'])!=(s.st_dev,s.st_ino):raise ValueError('inode changed since inventory')
                    row.update(status='OBSERVED' if value['stable'] else 'CHANGED_DURING_QUERY',residency=value,
                        resident_bytes=value['resident_bytes'])
                except (OSError,ValueError) as error:
                    row.update(status='QUERY_FAILED',resident_bytes=None,error=str(error))
                row.update(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),thread_cpu_ns=time.thread_time_ns()-thread_cpu)
                files.append(row)
            except (OSError,ValueError) as error:
                unresolved.append(dict(path=str(path),status='INVENTORY_FAILED',resident_bytes=None,error=str(error)))
    for key,info in annotations.items():
        if key not in referenced and key not in prior:
            unresolved.append(dict(object_key=list(key),**info,status='OUTSIDE_OWNED_TREE_NOT_QUERIED',resident_bytes=None))
    stable=[r for r in files if r['status']=='OBSERVED']
    return dict(status='OBSERVED' if len(stable)==len(files) and not unresolved else 'PARTIAL',
        scope='Regular files retained in the owned service env, including rootfs upper data/index, deps, cache metadata, logs and state; deduplicated with prior lower queries. Not guest RAM or all host allocations.',
        start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),thread_cpu_ns=time.thread_time_ns()-cpu,
        previous_regular_object_count=len(prior),new_unique_file_count=len(files),files=files,unresolved=unresolved,
        stable_new_resident_bytes=sum(r['resident_bytes'] for r in stable),
        categories={c:dict(files=sum(r['category']==c for r in files),
            stable_resident_bytes=sum(r['resident_bytes'] for r in stable if r['category']==c),
            unknown_or_changed=sum(r['category']==c and r['status']!='OBSERVED' for r in files))
            for c in sorted({r['category'] for r in files})},
        exclusions=['Writable ublk device cache','VMM anonymous guest RAM','Deleted/unlinked files outside the retained tree',
                    'Outer loop backing image (separate cache layer)','Files outside this private service env'],
        interpretation='Stable bytes are a partial observed sum when status is PARTIAL; unknown is not zero. Queries are sequential while guests and kernel reclaim remain runnable. Do not add device residency or memory.current.')
