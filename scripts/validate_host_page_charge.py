"""One small two-cgroup online/offline calibration; no AgentENV/VM or old data writes."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

from host_file_page_charge import HostFileChargeQuery, GroupIdentityHistory, COLUMNS
from host_schedule_files import permission_evidence

CGROOT=Path('/sys/fs/cgroup')


def dump(path,value):
    Path(path).write_text(json.dumps(value,indent=2)+'\n')


def worker(role,out):
    path=out/(role+'.bin')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600)
    try:
        os.ftruncate(fd,8192)
        assert os.pwrite(fd,b'x'*4096,0)==4096
        os.fsync(fd)
    finally:os.close(fd)
    group=Path('/proc/self/cgroup').read_text().strip()
    assert group.startswith('0::/')
    dump(out/(role+'-ready.json'),dict(pid=os.getpid(),cgroup=group[3:],
         start_ticks=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19],
         written_bytes=4096))
    deadline=time.monotonic()+150
    while not (out/(role+'-release')).exists():
        if time.monotonic()>deadline:raise TimeoutError('own worker deadline')
        time.sleep(.02)


def wait_for(predicate,seconds,message):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline:raise TimeoutError(message)
        time.sleep(.02)


def run(out):
    out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime()).lower()
    name=f'asbpagecharge{stamp}{os.getpid()}'
    slice_name=name+'.slice';parent=CGROOT/slice_name
    result=dict(status='FAIL',scope='two new 8 KiB sparse files, each with one written 4 KiB page',
                actual_payload_written_bytes=8192,logical_file_bytes=16384,
                business_runs=0,sandbox_runs=0,retried=False,parent_file_closure_is_acceptance=False,
                raw_columns=COLUMNS,phases=[],queries=[],cleanup={},parent=str(parent))
    workers={};units={};streams=[];queries={};groups={};stage='preflight'
    history=GroupIdentityHistory(Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    started=time.monotonic_ns()
    try:
        result['permission']=permission_evidence()
        v=os.statvfs(out);result['free_disk_before_bytes']=v.f_bavail*v.f_frsize
        if result['free_disk_before_bytes']<=64*2**20:raise ValueError('requires >64 MiB for this small calibration')
        if parent.exists():raise ValueError('owned slice name already exists')
        for role in ('a','b'):
            stage='start_'+role;unit=name+role+'.service';units[role]=unit
            log=(out/(role+'.log')).open('x');streams.append(log)
            env={k:v for k,v in os.environ.items() if 'proxy' not in k.lower()}
            command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,
                     '--slice='+slice_name,'--property=MemoryAccounting=yes','--property=RuntimeMaxSec=160',
                     sys.executable,'-B',str(Path(__file__).resolve()),'--worker',role,'--output',str(out)]
            workers[role]=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,env=env)
            def ready():
                if workers[role].poll() is not None:raise RuntimeError('worker exited before ready: '+role)
                return (out/(role+'-ready.json')).exists()
            wait_for(ready,15,'worker ready timeout: '+role)
            receipt=json.loads((out/(role+'-ready.json')).read_text())
            group=CGROOT/receipt['cgroup'].lstrip('/')
            if group.parent!=parent:raise ValueError('worker outside declared parent')
            if set(map(int,(group/'cgroup.procs').read_text().split()))!={receipt['pid']}:
                raise ValueError('worker group members differ')
            groups[role]=group;history.observe(parent,'parent');history.observe(group,role)
        history.save(out/'identity-before.json')
        ids={role:groups[role].stat().st_ino for role in groups}
        parent_id=parent.stat().st_ino
        result.update(group_ids=ids,parent_id=parent_id,boot_id=history.boot_id)
        stage='attach'
        for role in ('a','b'):
            path=out/(role+'.bin');s=path.stat();directory=out/('query-'+role);directory.mkdir()
            q=HostFileChargeQuery(path,directory,expected_identity=(s.st_dev,s.st_ino));queries[role]=q
            q.attach()
        for phase in ('online','a_exited'):
            if phase=='a_exited':
                stage='stop_a';stop_start=time.monotonic_ns();(out/'a-release').touch()
                workers['a'].wait(timeout=10)
                if workers['a'].returncode!=0:raise RuntimeError('worker a failed')
                wait_for(lambda:not groups['a'].exists(),5,'a cgroup did not disappear')
                result['a_stop']=dict(start_ns=stop_start,end_ns=time.monotonic_ns(),returncode=0)
            stage='query_'+phase
            for role in ('parent','a','b'):
                history.observe(parent if role=='parent' else groups[role],role)
            history.save(out/('identity-'+phase+'.json'))
            records={}
            for role,q in queries.items():
                record=q.query_chunk(0,2);records[role]=record
            result['phases'].append(dict(name=phase,records=records,
                cgroup_stat=(parent/'cgroup.stat').read_text(),monotonic_ns=time.monotonic_ns()))
            dump(out/'progress.json',result)
            for role,r in records.items():
                charge=r['raw_summary']['charge']
                if r['status']!='OBSERVED_NONATOMIC' or charge['unknown_page_observations'] or charge['conflicting_pfn_count']:
                    raise ValueError('page/owner evidence incomplete: '+phase+'/'+role)
                if r['raw_summary']['state_counts']!={'FOLIO_PRESENT_MINCORE_SET':1,'NO_FOLIO_OBSERVED':1}:
                    raise ValueError('one written page and one hole expected: '+phase+'/'+role)
                expected=dict(raw_cgroup_id=ids[role],online_ancestor_id=parent_id if phase=='a_exited' and role=='a' else ids[role],
                              raw_online=not(phase=='a_exited' and role=='a'),observed_unique_pages=1,observed_bytes=4096)
                if charge['owners']!=[expected]:
                    raise ValueError('raw owner/online ancestor differs from calibration hypothesis: '+phase+'/'+role)
        result['status']='PASS'
    except BaseException as exc:
        result.update(status='FAIL',failed_stage=stage,error=repr(exc))
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for role,q in queries.items():
            try:q.close()
            except BaseException as exc:
                result.update(status='FAIL');result['cleanup'][role+'_query_error']=repr(exc)
            receipt=dict(version=q.version,kernel=q.kernel,permission=q.permission,
                         identity=q.initial,columns=COLUMNS,chunks=q.records,raw_bytes=q.raw_bytes,
                         cleanup=q.cleanup,map_info=getattr(q,'map_info',None))
            dump(out/('query-'+role)/'result.json',receipt)
            result['queries'].append(dict(role=role,path=str(out/('query-'+role)/'result.json'),
                                          raw_bytes=q.raw_bytes,cleanup=q.cleanup))
        for role,proc in workers.items():
            (out/(role+'-release')).touch()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    subprocess.run(['systemctl','stop',units[role]],check=True,timeout=15)
                    proc.wait(timeout=5)
                except BaseException as exc:result['cleanup'][role+'_worker_error']=repr(exc);result['status']='FAIL'
            result['cleanup'][role+'_worker_returncode']=proc.returncode
            if proc.returncode!=0:result['status']='FAIL'
        for stream in streams:stream.close()
        if units:
            try:
                subprocess.run(['systemctl','stop',slice_name],check=True,timeout=15,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            except BaseException as exc:result['cleanup']['slice_error']=repr(exc);result['status']='FAIL'
        for role,path in [('parent',parent),*groups.items()]:
            try:history.observe(path,role)
            except BaseException as exc:result['cleanup'][role+'_identity_error']=repr(exc);result['status']='FAIL'
        history.save(out/'identity-history.json')
        result['cleanup']['owned_slice_absent']=not parent.exists()
        if parent.exists():result['status']='FAIL'
        result['cleanup']['synthetic_files_retained']=True
        result['elapsed_ns']=time.monotonic_ns()-started
        dump(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--live-calibration',action='store_true')
    p.add_argument('--worker',choices=('a','b'));a=p.parse_args()
    if a.worker:worker(a.worker,a.output);return 0
    if not a.live_calibration:p.error('--live-calibration required')
    def deadline(signum,frame):raise TimeoutError('owned calibration deadline')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(220)
    try:return run(a.output)
    finally:signal.alarm(0)


if __name__=='__main__':raise SystemExit(main())
