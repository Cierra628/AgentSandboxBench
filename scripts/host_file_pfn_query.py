"""Opt-in, one pinned regular disk file and one bounded range; no payload reads.

Import/replay helpers do not attach probes. The CLI requires --live-query.
SnapshotCache and its mincore-resident-only contract are unchanged.
"""
import argparse
from collections import Counter
import ctypes
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time
import traceback

import numpy as np
from host_file_monitor import mincore_permission
from host_schedule_files import filesystem_magic, permission_evidence
from kernel_cache_metadata import MASK
from snapshot_cache_residency import BatchEntries, _query_clock, _query_cost
from vmm_page_evidence import kernel_identity

PAGE=4096
MAX_CHUNK_PAGES=131072
RAW_BUDGET=2**30
VERSION='host-file-pfn-query-v1'
FOLIO=1<<63
EXCEPTION=1<<62
INVALID=1<<61
FIELDS=('device','inode','rdev','mode','size','mtime_ns','ctime_ns')


def file_identity(info):
    return dict(zip(FIELDS,(info.st_dev,info.st_ino,info.st_rdev,info.st_mode,
        info.st_size,info.st_mtime_ns,info.st_ctime_ns)))


def chunks(first,pages,size):
    if (any(type(n) is not int for n in (first,pages,size)) or first<0 or pages<=0 or
            (first+pages)*PAGE>2**63-1 or first+pages>(size+PAGE-1)//PAGE):
        raise ValueError('explicit range must be positive and within the pinned file')
    if pages*32>=RAW_BUDGET:raise ValueError('range exceeds 1 GiB raw-record budget')
    for start in range(first,first+pages,MAX_CHUNK_PAGES):
        count=min(MAX_CHUNK_PAGES,first+pages-start)
        yield start,count,min(count*PAGE,size-start*PAGE)


def decode_rows(rows):
    """Rows: absolute file-page offset, mincore bit, return found, encoded result."""
    if rows.ndim!=2 or rows.shape[1]!=4 or len(rows)>MAX_CHUNK_PAGES:
        raise ValueError('invalid bounded host-file rows')
    counts=Counter();pfns=set()
    for offset,bit,found,code in map(lambda r:map(int,r),rows):
        if bit not in (0,1) or found not in (0,1):raise ValueError('invalid mincore/found bit')
        if not found:
            if code:raise ValueError('missing return cannot carry a value')
            state='MISSING_KERNEL_RETURN'
        elif code==0:state='NO_FOLIO_OBSERVED' if not bit else 'NULL_MINCORE_CONFLICT'
        elif code&~MASK==FOLIO:
            pfns.add(code&MASK)
            state='FOLIO_PRESENT_MINCORE_SET' if bit else 'FOLIO_PRESENT_MINCORE_CLEAR'
        elif code&~MASK==EXCEPTION:state='XARRAY_EXCEPTION'
        elif code==INVALID:state='INVALID_FOLIO'
        else:state='INVALID_ENCODING'
        counts[state]+=1
    known={'FOLIO_PRESENT_MINCORE_SET','FOLIO_PRESENT_MINCORE_CLEAR','NO_FOLIO_OBSERVED'}
    unknown=sum(v for k,v in counts.items() if k not in known)
    return dict(status='PARTIAL' if unknown else 'OBSERVED_NONATOMIC',pages=len(rows),
        state_counts=dict(counts),unknown_pages=unknown,mincore_set_pages=int(rows[:,1].sum()),
        confirmed_pfn_union_bytes=len(pfns)*PAGE,
        complete_listed_pfn_union_bytes=None if unknown else len(pfns)*PAGE,
        snapshot_atomic=False,scope='PFNs observed during this chunk, not a simultaneous whole-file or sandbox total')


def probe_program(pid):
    if type(pid) is not int or pid<=0:raise ValueError('invalid query PID')
    return r'''
BEGIN { @context[0]=((uint64)0,(uint64)0,(uint64)0,(uint64)0,(uint64)0,(uint64)0,(uint64)0); printf("READY\n"); }
tracepoint:syscalls:sys_enter_mincore /pid == TARGET/ {
 $c=@context[0];
 if ($c.0 && tid==$c.1 && args->start==$c.2 && args->len==$c.3) { @active[tid]=$c.0; }
}
tracepoint:syscalls:sys_exit_mincore /pid == TARGET/ { delete(@active[tid]); delete(@pending[tid]); }
kprobe:filemap_get_entry /@active[tid]/ {
 $c=@context[0]; $m=(struct address_space *)arg0;
 if ($m->host->i_ino==$c.6 && $m->host->i_sb->s_dev==$c.5 &&
     arg1 >= $c.4 && arg1 < $c.4+($c.3+(uint64)4095)/(uint64)4096) { @pending[tid]=arg1+(uint64)1; }
}
kretprobe:filemap_get_entry /@active[tid] && @pending[tid]/ {
 $c=@context[0]; $i=@pending[tid]-(uint64)1; $v=(uint64)retval; $encoded=(uint64)0;
 if ($v & 3) { $encoded=((uint64)1<<62)|($v&0x7fffffffffffff); }
 else if ($v) {
  $base=*(uint64*)kaddr("vmemmap_base"); $f=(struct folio *)$v;
  if ($v >= $base && ($v-$base)%sizeof(struct page)==0 && $i >= $f->index) {
   $pfn=($v-$base)/sizeof(struct page)+$i-$f->index;
   $encoded=$pfn<=0x7fffffffffffff ? (((uint64)1<<63)|$pfn) : ((uint64)1<<61);
  } else { $encoded=((uint64)1<<61); }
 }
 @entries[$i-$c.4]=(@active[tid],$encoded); delete(@pending[tid]);
}
interval:s:900 { exit(); }
END { clear(@active); clear(@pending); clear(@entries); clear(@context); }
'''.replace('TARGET',str(pid))


class HostFilePfnQuery:
    version = VERSION
    row_bytes = 32

    def make_program(self):
        return probe_program(self.pid)

    def make_reader(self, info):
        return BatchEntries(self.lib,self.mapfds['entries'],info)

    def make_rows(self,first,pages,vector,entries):
        return np.array([[first+i,int(vector[i])&1,int(i in entries),entries.get(i,0)] for i in range(pages)],dtype='<u8')

    def summarize_rows(self,rows):
        return decode_rows(rows)

    def __init__(self,path,directory,*,expected_identity):
        self.path=Path(path);self.directory=Path(directory);self.fd=None;self.tracer=None;self.mapfds={}
        self.token=0;self.raw_bytes=0;self.records=[];self.pid=os.getpid();self.tid=threading.get_native_id()
        self.lib=None;self.cleanup=None
        try:
            self.fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
            info=os.fstat(self.fd);self.initial=file_identity(info)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=tuple(expected_identity):
                raise ValueError('pinned regular file differs from explicit device/inode')
            self.magic=filesystem_magic(self.fd)
            if self.magic in (0x01021994,0x958458f6):raise ValueError('TMPFS/hugetlb are separate, unsupported scopes')
            if os.sysconf('SC_PAGE_SIZE')!=PAGE or os.uname().machine!='x86_64':
                raise ValueError('this query profile requires x86-64 and 4096-byte pages')
            self.permission=self._permission(info)
            self.kernel=kernel_identity(Path('/proc'),Path('/sys'))
            self.libc=ctypes.CDLL(None,use_errno=True)
            self.libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
            self.libc.mmap.restype=ctypes.c_void_p
            self.libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
            self.libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
            self.lib=ctypes.CDLL('libbpf.so.1',use_errno=True)
            self.lib.bpf_map_get_fd_by_id.argtypes=[ctypes.c_uint]
            self.lib.bpf_map_update_elem.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint64]
        except BaseException:
            if self.fd is not None:os.close(self.fd);self.fd=None
            raise

    def _permission(self,info):
        host=permission_evidence();thread=mincore_permission(self.fd,info)
        if thread['basis']!='HOST_CAP_FOWNER':raise PermissionError('query thread lacks host CAP_FOWNER')
        return dict(host=host,thread=thread)

    def _thread(self):
        if (os.getpid(),threading.get_native_id())!=(self.pid,self.tid):
            raise ValueError('query belongs to another process/thread')

    def attach(self):
        self._thread()
        if self.tracer is not None:raise ValueError('owned tracer already started')
        (self.directory/'query.bt').write_text(self.make_program())
        try:
            with (self.directory/'query.stdout').open('x') as out,(self.directory/'query.stderr').open('x') as err:
                self.tracer=subprocess.Popen(['bpftrace','-q','-B','line',self.directory/'query.bt'],stdout=out,stderr=err,
                    env=dict(os.environ,BPFTRACE_MAX_MAP_KEYS=str(MAX_CHUNK_PAGES)))
            deadline=time.monotonic()+20
            while 'READY' not in (self.directory/'query.stdout').read_text():
                if self.tracer.poll() is not None or time.monotonic()>=deadline:raise RuntimeError('owned host-file probe attach failed')
                time.sleep(.05)
            info={}
            for p in Path('/proc',str(self.tracer.pid),'fdinfo').iterdir():
                match=re.search(r'map_id:\s*(\d+)',p.read_text())
                if not match:continue
                detail=json.loads(subprocess.check_output(['bpftool','-j','map','show','id',match[1]],timeout=5))
                name=detail['name'].removeprefix('AT_')
                if name not in ('entries','context'):continue
                fd=self.lib.bpf_map_get_fd_by_id(int(match[1]))
                if fd<0:raise OSError('cannot open owned probe map')
                if name in self.mapfds:os.close(fd);raise ValueError('duplicate owned probe map name')
                self.mapfds[name]=fd;info[name]=detail
            if set(info)!={'entries','context'}:raise ValueError('owned probe maps missing')
            context=info['context']
            if (context.get('type'),context.get('bytes_key'),context.get('bytes_value'),context.get('flags'))!=('hash',8,56,0):
                raise ValueError('unsupported context map layout')
            self.batch=self.make_reader(info['entries'])
            self.map_info=info;self._context((0,)*7)
        except BaseException as error:
            try:self.close()
            except Exception as cleanup_error:error.add_note('owned probe cleanup also failed: '+repr(cleanup_error))
            raise

    def _context(self,value):
        key=ctypes.c_uint64(0);data=(ctypes.c_uint64*7)(*value)
        if self.lib.bpf_map_update_elem(self.mapfds['context'],ctypes.byref(key),data,0):
            raise RuntimeError('owned query context publication failed')

    def query_chunk(self,first,pages,*,before_write=None):
        self._thread();started=_query_clock();stages={}
        info=os.fstat(self.fd);before=file_identity(info)
        planned=list(chunks(first,pages,info.st_size))
        if len(planned)!=1:raise ValueError('one query chunk must be <=131072 pages')
        if before!=self.initial or file_identity(self.path.stat(follow_symlinks=False))!=before:
            raise ValueError('file/alias changed before query')
        if self.tracer is None or self.tracer.poll() is not None:raise RuntimeError('owned probe is not live')
        if self.raw_bytes+pages*self.row_bytes>=RAW_BUDGET:raise ValueError('query records exceed strict 1 GiB budget')
        permission=self._permission(info);first,pages,length=planned[0]
        address=self.libc.mmap(None,length,0,1,self.fd,first*PAGE)  # PROT_NONE, MAP_SHARED; never touch.
        if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'query mmap failed')
        self.token+=1;token=self.token;rows=None
        try:
            # Kernel dev_t uses 20 minor bits, unlike userspace st_dev encoding.
            kdev=(os.major(info.st_dev)<<20)|os.minor(info.st_dev)
            self._context((token,self.tid,address,length,first,kdev,info.st_ino))
            try:
                vector=(ctypes.c_ubyte*pages)();t=_query_clock()
                if self.libc.mincore(address,length,vector):raise OSError(ctypes.get_errno(),'query mincore failed')
                stages['mincore']=_query_cost(t)
            finally:self._context((0,)*7)  # Disarm before map reads and any other query.
            t=_query_clock();entries=self.batch.read(np.arange(pages,dtype='<u8'),token)
            stages['map_read']=_query_cost(t)
            rows=self.make_rows(first,pages,vector,entries)
            t=_query_clock();summary=self.summarize_rows(rows);stages['decode']=_query_cost(t)
        finally:
            if self.libc.munmap(address,length):raise OSError(ctypes.get_errno(),'query munmap failed')
        after=file_identity(os.fstat(self.fd));alias_error=None
        try:alias_after=file_identity(self.path.stat(follow_symlinks=False))
        except OSError as exc:alias_after=None;alias_error=repr(exc)
        stable=before==after==alias_after;alive=self.tracer.poll() is None
        index=len(self.records);binary=f'chunk-{index:04}.bin'
        record=dict(version=self.version,token=token,first_page=first,pages=pages,mapping_length=length,
            query_thread=self.tid,query_process=self.pid,identity_before=before,identity_after=after,alias_after=alias_after,
            object_stable=stable,alias_after_error=alias_error,probe_alive_after=alive,
            permission=permission,bpf_lookup=self.batch.last_statistics,
            costs=_query_cost(started),stages=stages,binary=binary,raw_summary=summary,
            status='UNKNOWN_OBJECT_CHANGED' if not stable else summary['status'] if alive else 'UNKNOWN_PROBE_EXITED',
            accepted_pfn_union_bytes=summary['confirmed_pfn_union_bytes'] if stable and alive else None)
        text=json.dumps(record,indent=2)+'\n';size=rows.nbytes+len(text.encode())
        if self.raw_bytes+size>=RAW_BUDGET:raise ValueError('query records exceed strict 1 GiB budget')
        if before_write is not None:before_write(size)
        with (self.directory/binary).open('xb') as f:rows.tofile(f)
        with (self.directory/f'chunk-{index:04}.json').open('x') as f:f.write(text)
        self.raw_bytes+=size;self.records.append(record)
        if not stable:raise ValueError('file changed during query; raw chunk retained, physical value unavailable')
        if not alive:raise RuntimeError('probe exited during query; raw chunk retained, physical value unavailable')
        return record

    def close(self):
        error=None
        try:
            if self.tracer is not None and self.tracer.poll() is None:
                try:
                    fields=(Path('/proc')/str(self.tracer.pid)/'stat').read_text().rsplit(')',1)[1].split()
                    self.tracer_cpu_ns=int((int(fields[11])+int(fields[12]))*1e9/os.sysconf('SC_CLK_TCK'))
                except (OSError,ValueError,IndexError):self.tracer_cpu_ns=None
                self.tracer.send_signal(signal.SIGINT)
                try:self.tracer.wait(timeout=10)
                except subprocess.TimeoutExpired:self.tracer.kill();self.tracer.wait(timeout=5)
            self.cleanup=dict(tracer_returncode=self.tracer.returncode if self.tracer is not None else None,
                owned_tracer_exited=self.tracer is None or self.tracer.poll() is not None,
                tracer_cpu_before_stop_ns=getattr(self,'tracer_cpu_ns',None))
        except BaseException as exc:error=exc
        finally:
            for fd in self.mapfds.values():os.close(fd)
            self.mapfds.clear()
            if self.fd is not None:os.close(self.fd);self.fd=None
        if error is not None:raise error


def observe(path,expected,first,pages,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);query=None;started=_query_clock()
    stage='pin_and_check_permission'
    result=dict(version=VERSION,status='FAIL',path=str(path),expected_identity=list(expected),
        first_page=first,pages=pages,page_size=PAGE,complete_file_physical_bytes=None,
        complete_sandbox_physical_bytes=None,snapshot_atomic=False,
        scope='one explicit ordinary disk file range; per-chunk PFNs, no cross-chunk instantaneous total',
        cross_sandbox_ownership='NOT_MEASURED',memory_charge_cgroup='NOT_MEASURED')
    try:
        query=HostFilePfnQuery(path,output,expected_identity=expected)
        stage='validate_range'
        planned=list(chunks(first,pages,query.initial['size']))  # Validate before attaching probes.
        result.update(identity=query.initial,kernel=query.kernel,permission=query.permission,filesystem_magic=query.magic)
        stage='attach_owned_probe';query.attach()
        for a,n,length in planned:
            stage='query_chunk_'+str(len(query.records));query.query_chunk(a,n)
        result.update(status='PARTIAL' if any(r['status']=='PARTIAL' for r in query.records) else 'OBSERVED_NONATOMIC')
    except Exception as exc:
        result.update(error=repr(exc),failed_stage=stage)
        (output/'failure.txt').write_text(traceback.format_exc())
    finally:
        if query is not None:
            try:query.close()
            except Exception as exc:result.update(status='FAIL',cleanup_error=repr(exc))
            result.update(chunks=query.records,raw_bytes=query.raw_bytes,cleanup=query.cleanup,
                map_info=getattr(query,'map_info',None),receive_buffer_bytes=getattr(getattr(query,'batch',None),'buffer_bytes',None))
        result['costs']=_query_cost(started)
        with (output/'result.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live-query',action='store_true');p.add_argument('--file',required=True,type=Path)
    p.add_argument('--expected-device',required=True,type=int);p.add_argument('--expected-inode',required=True,type=int)
    p.add_argument('--first-page',required=True,type=int);p.add_argument('--pages',required=True,type=int)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    if not a.live_query:p.error('explicit --live-query required; import/helpers do not collect')
    r=observe(a.file,(a.expected_device,a.expected_inode),a.first_page,a.pages,a.output)
    print('status='+r['status']+' result_dir='+str(a.output))
    return 1 if r['status']=='FAIL' else 0


if __name__=='__main__':raise SystemExit(main())
