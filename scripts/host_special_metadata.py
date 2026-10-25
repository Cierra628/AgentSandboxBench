"""One-shot special mapping metadata; no process memory or backing payload reads."""
import ctypes
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

HEADER = re.compile(r'^([0-9a-f]+)-([0-9a-f]+)\s+([rwxps-]{4})\s+([0-9a-f]+)\s+([0-9a-f]+):([0-9a-f]+)\s+(\d+)(?:\s+(.*))?$')
SMAPS_BYTES = {'Rss','Pss','Anonymous','Shared_Clean','Shared_Dirty','Private_Clean','Private_Dirty',
               'Swap','Shared_Hugetlb','Private_Hugetlb','KernelPageSize','MMUPageSize'}


def mapping_line(line):
    m=HEADER.match(line)
    if not m:return None
    start,end,perms,offset,major,minor,inode,name=m.groups()
    return dict(start=int(start,16),end=int(end,16),permissions=perms,offset=int(offset,16),
                device=os.makedev(int(major,16),int(minor,16)),inode=int(inode),mapping_path=name or '')


def attach_smaps(root,regions):
    wanted={(r['start'],r['end']):r for r in regions};current=None
    for line in (root/'smaps').read_text().splitlines():
        header=mapping_line(line)
        if header is not None:
            current=wanted.get((header['start'],header['end']))
            if current is not None:
                if any(current[k]!=header[k] for k in header):
                    current['smaps_status']='MAPPING_CHANGED';current=None
                else:current.update(smaps_status='OBSERVED',smaps_bytes={})
        elif current is not None and ':' in line:
            key,value=line.split(':',1);fields=value.split()
            if key in SMAPS_BYTES:
                if len(fields)!=2 or fields[1]!='kB':raise ValueError('unexpected smaps unit')
                current['smaps_bytes'][key]=int(fields[0])*1024
            elif key=='VmFlags':
                current['vm_flags']=fields
                current['pss_interpretation']='SKIPPED_PFNMAP_NOT_RESIDENCY' if 'pf' in fields else 'MAPPING_PSS_NOT_BACKING_TOTAL'
    for r in regions:
        if r.get('smaps_status')=='OBSERVED' and ('Pss' not in r['smaps_bytes'] or 'vm_flags' not in r):
            r['smaps_status']='INCOMPLETE_FIELDS'


def descriptor_metadata(path,key):
    fd=os.open(path,os.O_PATH|os.O_CLOEXEC)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=key:
            raise ValueError('deleted descriptor identity differs')
        # Linux statfs fits this buffer; only its first native-long field (f_type) is used.
        fields=(ctypes.c_long*32)();libc=ctypes.CDLL(None,use_errno=True)
        if libc.fstatfs(fd,ctypes.byref(fields)):
            error=ctypes.get_errno();raise OSError(error,os.strerror(error))
        magic=fields[0]
        return dict(descriptor=path.name,device=info.st_dev,inode=info.st_ino,size=info.st_size,
                    filesystem_magic=magic,backing_type={0x01021994:'TMPFS',0x958458f6:'HUGETLBFS'}.get(magic,'OTHER'),
                    scope='Descriptor metadata only; size is not residency or PSS.')
    finally:os.close(fd)


def deleted_descriptors(root,keys):
    result={key:[] for key in keys}
    for path in (root/'fd').iterdir():
        try:
            info=path.stat();key=(info.st_dev,info.st_ino)
            if key in result:result[key].append(descriptor_metadata(path,key))
        except FileNotFoundError:continue  # Descriptor closed during enumeration.
    return result


def build_ublk_query(output):
    target=Path(output)/'host-ublk-info'
    with (Path(output)/'host-ublk-info-build.log').open('x') as log:
        subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(Path(__file__).with_name('host_ublk_info.c')),
                        '-luring','-o',str(target)],check=True,timeout=30,stdout=log,stderr=subprocess.STDOUT)
    return target.resolve()


def ublkc_metadata(path,entry,query):
    if query is None:return dict(status='NOT_REQUESTED',allocation_bytes=None)
    started=time.monotonic_ns()
    begin=path.stat();key=(entry['device'],entry['inode'],entry['rdev'])
    if (begin.st_dev,begin.st_ino,begin.st_rdev)!=key:raise ValueError('ublkc identity differs')
    dev_id=int(Path(entry['mapping_path']).name.removeprefix('ublkc'))
    run=subprocess.run([str(query),str(path),str(dev_id)],check=True,capture_output=True,text=True,timeout=4)
    info=json.loads(run.stdout);last=path.stat()
    if (last.st_dev,last.st_ino,last.st_rdev)!=key or tuple(info[k] for k in ('device','inode','rdev'))!=key:
        raise ValueError('ublkc identity changed')
    if info['dev_id']!=dev_id or info['ublksrv_pid'] not in entry['owners'] or info['state']!=1:
        raise ValueError('ublkc device/owner/live state differs')
    page=info['page_size'];depth=info['queue_depth'];count=info['nr_hw_queues'];desc=info['descriptor_bytes']
    if page!=os.sysconf('SC_PAGE_SIZE') or desc!=24 or not 0<depth<=info['max_queue_depth'] or count<=0:
        raise ValueError('ublkc queue metadata invalid')
    size=((depth*desc+page-1)//page)*page
    allocated=page << ((size//page)-1).bit_length()
    stride=((info['max_queue_depth']*desc+page-1)//page)*page
    queues=set()
    for r in entry['regions']:
        off=r['offset']-info['command_buffer_offset'];qid=off//stride
        if off<0 or off%stride or not 0<=qid<count or r['end']-r['start']!=size or r['permissions']!='r--s':
            raise ValueError('ublkc mapping does not match queue layout')
        if r.get('smaps_status')!='OBSERVED' or 'pf' not in r.get('vm_flags',[]):
            raise ValueError('ublkc PFN mapping not confirmed')
        r['queue_id']=qid;queues.add(qid)
    return dict(status='SOURCE_CALCULATED',info=info,mapped_queue_ids=sorted(queues),
                start_monotonic_ns=started,end_monotonic_ns=time.monotonic_ns(),
                queue_mapping_bytes=size,per_queue_allocation_bytes=allocated,allocation_bytes=count*allocated,
                scope='Live queue configuration and kernel allocation formula, once per device; not page cache, PSS or full kernel memory.')
