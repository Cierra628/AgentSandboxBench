"""Read only an owned service's VMMs; bind sandbox identity through Firecracker MMDS."""
import http.client
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import threading
import time

FIELDS=['timestamp_ns','monotonic_ns','elapsed_ns','sandbox_id','role','session_id','phase',
        'pid','start_ticks','user_ticks','system_ticks','rss_kib','read_bytes','write_bytes','rchar','wchar',
        'pss_kib','pss_anon_kib','pss_file_kib','pss_shmem_kib','scan_duration_ns','pss_scan_duration_ns']


def identity(pid,proc_root=Path('/proc')):
    values=(proc_root/str(pid)/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=pid,start_ticks=values[19],boot_id=(proc_root/'sys/kernel/random/boot_id').read_text().strip())


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path):super().__init__('localhost',timeout=.5);self.path=str(path)
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)


def sandbox_id(socket_path):
    connection=UnixHTTP(socket_path)
    try:
        connection.request('GET','/mmds');response=connection.getresponse()
        if response.status!=200:raise RuntimeError('MMDS HTTP status '+str(response.status))
        body=response.read(1024*1024)
        # MMDS also contains access-token hashes; never persist/log its complete body.
        return json.loads(body).get('instanceID')
    finally:connection.close()


class Binding:
    def __init__(self,container,raw,proc_root=Path('/proc'),cgroup_root=Path('/sys/fs/cgroup')):
        info=json.loads(subprocess.check_output(['docker','inspect',container],text=True,timeout=10))[0]
        assert info['State']['Running'] and info['Config']['Labels']['agentsandboxbench.run_id']==raw.parents[2].name
        assert info['Config']['Labels']['agentenv.experiment']=='isolated-storage-check'
        self.proc_root=proc_root
        self.cgroup_text=(proc_root/str(info['State']['Pid'])/'cgroup').read_text()
        lines=[s[3:] for s in self.cgroup_text.splitlines() if s.startswith('0::')];assert len(lines)==1 and lines[0]!='/'
        self.cgroup_path=lines[0];self.cgroup=cgroup_root/lines[0].lstrip('/')
        assert self.cgroup.is_dir()

    def owned(self,pid):
        lines=(self.proc_root/str(pid)/'cgroup').read_text().splitlines()
        return any(s=='0::'+self.cgroup_path or s.startswith('0::'+self.cgroup_path+'/') for s in lines)

    def resolve(self,sid):
        matches=[];pids=set()
        for f in self.cgroup.rglob('cgroup.procs'):pids.update(map(int,f.read_text().split()))
        for pid in sorted(pids):
            root=self.proc_root/str(pid)
            try:
                if (root/'comm').read_text().strip()!='firecracker' or not self.owned(pid):continue
                before=identity(pid,self.proc_root);args=(root/'cmdline').read_bytes().split(b'\0')
                if b'--api-sock' not in args:continue
                path=Path(args[args.index(b'--api-sock')+1].decode());assert path.is_absolute()
                host_path=root/'root'/str(path).lstrip('/')
                if sandbox_id(host_path)==sid:
                    assert identity(pid,self.proc_root)==before and self.owned(pid)
                    matches.append(dict(**before,api_socket=str(host_path),cgroup=self.cgroup_path,
                        binding_source='owned service cgroup + Firecracker GET /mmds instanceID'))
            except (FileNotFoundError,ProcessLookupError,ConnectionError,OSError):continue
        assert len(matches)==1,'sandbox/VMM binding is missing or ambiguous: '+sid
        return matches[0]


def read(pid,proc_root=Path('/proc'),pss=False):
    root=proc_root/str(pid);values=(root/'stat').read_text().rsplit(')',1)[1].split()
    status=dict(s.split(':',1) for s in (root/'status').read_text().splitlines() if ':' in s)
    io=dict(s.split(':',1) for s in (root/'io').read_text().splitlines())
    result=dict(start_ticks=values[19],user_ticks=int(values[11]),system_ticks=int(values[12]),rss_kib=int(status['VmRSS'].split()[0]),
                **{k:int(io[k]) for k in ('read_bytes','write_bytes','rchar','wchar')})
    begin=time.monotonic_ns()
    if pss:
        memory=dict(s.split(':',1) for s in (root/'smaps_rollup').read_text().splitlines() if ':' in s)
        assert 'Pss' in memory,'PSS is unavailable'
        result.update({target:int(memory[source].split()[0]) if source in memory else '' for source,target in
            [('Pss','pss_kib'),('Pss_Anon','pss_anon_kib'),('Pss_File','pss_file_kib'),('Pss_Shmem','pss_shmem_kib')]})
        result['pss_scan_duration_ns']=time.monotonic_ns()-begin
    return result


class HostMonitor:
    def __init__(self,binding,sid,role,token,directory,phase):
        self.binding=binding;self.sid=sid;self.role=role;self.token=token;self.directory=directory;self.phase=phase
        self.vmm=binding.resolve(sid);self.stop_event=threading.Event();self.ready=threading.Event();self.receipt=None
        directory.mkdir(parents=True,exist_ok=False)
        self.thread=threading.Thread(target=self.collect,name='vmm-'+sid[-8:],daemon=True);self.thread.start()
        if not self.ready.wait(5):
            self.stop_event.set();self.thread.join(timeout=10)
            raise TimeoutError('host VMM monitor readiness timeout')
        if self.receipt is not None and self.receipt['status']=='FAIL':raise RuntimeError('host VMM monitor failed at readiness')

    def collect(self):
        started=time.monotonic_ns();next_pss=started;count=0;pss_count=0;error=None;max_read=0
        try:
            with (self.directory/'host-vmm.tsv').open('x',buffering=1) as f:
                f.write('\t'.join(FIELDS)+'\n')
                while True:
                    now=time.monotonic_ns();pid=self.vmm['pid']
                    assert identity(pid)=={k:self.vmm[k] for k in ('pid','start_ticks','boot_id')} and self.binding.owned(pid),'VMM identity/cgroup changed'
                    value=read(pid,pss=now>=next_pss)
                    assert value['start_ticks']==self.vmm['start_ticks']
                    if now>=next_pss:pss_count+=1;next_pss=now+10**9
                    value.update(timestamp_ns=time.time_ns(),monotonic_ns=now,elapsed_ns=now-started,
                        sandbox_id=self.sid,role=self.role,session_id=self.token,phase=self.phase,pid=pid,
                        scan_duration_ns=time.monotonic_ns()-now)
                    max_read=max(max_read,value['scan_duration_ns']);f.write('\t'.join(str(value.get(k,'')) for k in FIELDS)+'\n');count+=1
                    self.ready.set()
                    if self.stop_event.is_set():break
                    if time.monotonic_ns()-started>1800*10**9:raise TimeoutError('host monitor lifetime exceeded')
                    self.stop_event.wait(max(0,.1-(time.monotonic_ns()-now)/1e9))
        except Exception as exc:error=repr(exc)
        usage=resource.getrusage(resource.RUSAGE_THREAD)
        self.receipt=dict(status='FAIL' if error else 'PASS',error=error,sandbox_id=self.sid,role=self.role,session_id=self.token,
            identity=self.vmm,samples=count,pss_samples=pss_count,interval_seconds=.1,pss_interval_seconds=1,
            clock_ticks=os.sysconf('SC_CLK_TCK'),cpu_user_seconds=usage.ru_utime,cpu_system_seconds=usage.ru_stime,
            max_scan_duration_ns=max_read,elapsed_ns=time.monotonic_ns()-started,
            scope='owned VMM process only; excludes shared service/block-provider processes; PSS is proportional residency, not unique physical file cache')
        (self.directory/'collector-status.json').write_text(json.dumps(self.receipt,indent=2)+'\n');self.ready.set()

    def stop(self):
        self.stop_event.set();self.thread.join(timeout=10)
        assert not self.thread.is_alive(),'host monitor did not stop'
        assert self.receipt and self.receipt['status']=='PASS',self.receipt
        return self.receipt
