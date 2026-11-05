"""Explicit two-guest calibration: owned-file eviction before creation, sparse residency observations."""
import os
from pathlib import Path
import time

from host_private_files import ROOT,write,query_objects
from host_rootfs_layers import file_identity


class ColdFileRead:
    def __init__(self,objects,services,group,out,*,interval=2):
        self.objects=objects;self.services=services;self.group=group;self.out=Path(out)
        self.interval_ns=int(interval*1e9);self.next_ns=0
        self.state=dict(status='RUNNING',interval_seconds=interval,points=[],events=[],
            scope='Owned initial lower files only; cold before guest creation, no runtime reclaim or VM pause; periodic queries are sequential snapshots')

    def save(self):write(self.out/'cold-read.json',self.state)

    def mark(self,label):
        self.state['events'].append(dict(label=label,monotonic_ns=time.monotonic_ns()));self.save()

    def roots(self):
        return [(Path(s['mount'])/'env/image-cache/commits').resolve() for s in self.services]

    def initial(self):
        roots=self.roots()
        if any(not r.is_relative_to(ROOT/'.artifacts/storage-isolated-service') for r in roots):
            raise ValueError('cold eviction requires owned isolated-service files')
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        for obj in self.objects:
            path=Path(obj['path']).resolve()
            if not any(path.is_relative_to(r) for r in roots) or file_identity(path)!=obj['identity']:
                raise ValueError('cold eviction target outside owned roots or identity changed')
            fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try:
                s=os.fstat(fd)
                if dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns)!=obj['identity']:
                    raise ValueError('cold eviction opened a different file')
                os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
            finally:os.close(fd)
        self.state['eviction']=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
            files=len(self.objects),operation='POSIX_FADV_DONTNEED after coordinator filesystem sync; no global drop_caches')
        initial=self.capture('P0')
        if initial['resident_bytes']!=0:
            self.state.update(status='FAIL',error='Initial target residency is not zero; do not label it cold or start guests');self.save()
            raise RuntimeError(self.state['error'])
        self.state['initial_residency']='CONFIRMED_ZERO';self.save();return initial

    def capture(self,label):
        point=query_objects(self.objects,self.group,label)
        self.state['points'].append(point);self.next_ns=point['end_monotonic_ns']+self.interval_ns
        self.save();return point

    def tick(self):
        if time.monotonic_ns()>=self.next_ns:self.capture('periodic')

    def complete(self):
        self.state['status']='PASS';self.state['query_total_ns']=sum(p['elapsed_ns'] for p in self.state['points'])
        self.state['query_cpu_ns']=sum(p['cpu_ns'] for p in self.state['points']);self.save()
