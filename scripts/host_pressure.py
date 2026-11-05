"""Explicit pressure experiments; the allocator stays outside each measurement slice."""
import csv
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from host_private_files import read,write

ROOT=Path(__file__).resolve().parents[1]
GIB=2**30
EXTRA_STATS=('workingset_refault_file','workingset_activate_file')


def plan():
    return [dict(task='prettier6604',count=10,mode=mode,condition=condition,pressure=pressure)
        for condition,mode,pressure in (('A','shared',False),('B','independent',False),('C','shared',True),('D','independent',True))]


def extension_plan():
    """Four curve points first, then two more N=10 pairs; never rerun A/B controls."""
    return [dict(task='prettier6604',count=n,mode=mode,condition=condition,pressure=True)
        for condition,n,mode in (('E',4,'shared'),('F',4,'independent'),('G',7,'independent'),('H',7,'shared'),
            ('I',10,'shared'),('J',10,'independent'),('K',10,'independent'),('L',10,'shared'))]


def host_snapshot(proc=Path('/proc')):
    mem={line.split(':',1)[0]:int(line.split()[1])*1024 for line in (proc/'meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:','SwapTotal:','SwapFree:'))}
    if set(mem)!=set(('MemTotal','MemAvailable','SwapTotal','SwapFree')):raise ValueError('host memory counters missing')
    vm=dict(line.split() for line in (proc/'vmstat').read_text().splitlines())
    vm={key:int(value) for key,value in vm.items() if key.startswith(('pgscan_','pgsteal_','pswpin','pswpout'))}
    return dict(monotonic_ns=time.monotonic_ns(),**mem,**vm)


def process_snapshot(pid,proc=Path('/proc')):
    root=proc/str(pid)
    fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in (root/'status').read_text().splitlines() if ':' in line}
    return dict(pid=pid,cgroup=next(line[3:] for line in (root/'cgroup').read_text().splitlines() if line.startswith('0::')),
        start_ticks=(root/'stat').read_text().rsplit(')',1)[1].split()[19],
        rss_bytes=int(fields['VmRSS'].split()[0])*1024,anon_bytes=int(fields['RssAnon'].split()[0])*1024,swap_bytes=int(fields['VmSwap'].split()[0])*1024)


def require_outside(pressure_cgroup,experiment_cgroup):
    worker=Path(pressure_cgroup);group=Path(experiment_cgroup)
    if not worker.is_absolute() or not group.is_absolute() or worker.is_relative_to(group):
        raise ValueError('pressure worker is inside the experiment cgroup')


def before_guests(value,out,group):
    """After copy/sync, before P0 and any guest; no VM pause or extra cache operation."""
    if 'pressure_context' not in value:return None
    directory=Path(value['pressure_context'])
    from host_file_monitor import counters
    before=counters(group,extra_keys=EXTRA_STATS)
    write(directory/'start-request.json',dict(status='PREPARED_BEFORE_GUESTS',cgroup='/'+str(group.relative_to('/sys/fs/cgroup')),
        monotonic_ns=time.monotonic_ns(),counters=before))
    deadline=time.monotonic()+660
    while not (directory/'ready.json').exists():
        if time.monotonic()>deadline:raise TimeoutError('external pressure controller did not reply')
        time.sleep(.1)
    receipt=read(directory/'ready.json')
    if receipt['status']!='READY':raise RuntimeError('external pressure setup failed: '+repr(receipt))
    if value['pressure_enabled']:
        actual=process_snapshot(receipt['worker']['pid'])
        assert (actual['start_ticks'],actual['cgroup'])==(receipt['worker']['start_ticks'],receipt['worker']['cgroup'])
        require_outside(actual['cgroup'],'/'+str(group.relative_to('/sys/fs/cgroup')))
    result=dict(before=before,after=counters(group,extra_keys=EXTRA_STATS),receipt=receipt)
    write(out/'pressure-before-guests.json',result)
    return result


class PressureRun:
    def __init__(self,member,enabled,binary):
        self.member=member;self.directory=member/'pressure';self.directory.mkdir()
        self.enabled=enabled;self.binary=binary;self.pid=None;self.unit=None;self.phase='NOT_STARTED';self.receipt={}

    def sample(self):
        pid=self.pid
        empty=None if self.enabled and self.phase=='RELEASING' else 0
        if pid is None:return dict(pressure_phase=self.phase,pressure_pid='',pressure_rss_bytes=empty,pressure_anon_bytes=empty,pressure_swap_bytes=empty)
        try:p=process_snapshot(pid)
        except FileNotFoundError:
            if self.phase not in ('RELEASING','RELEASED'):raise
            empty=None if self.phase=='RELEASING' else 0
            return dict(pressure_phase=self.phase,pressure_pid='',pressure_rss_bytes=empty,pressure_anon_bytes=empty,pressure_swap_bytes=empty)
        return dict(pressure_phase=self.phase,pressure_pid=pid,pressure_rss_bytes=p['rss_bytes'],pressure_anon_bytes=p['anon_bytes'],pressure_swap_bytes=p['swap_bytes'])

    def start(self,request):
        before=host_snapshot();self.receipt=dict(enabled=self.enabled,before=before,prepared=request)
        if self.enabled:
            target=before['MemTotal']//5;maximum=before['MemTotal']*4//5
            self.unit='asbpressure-'+str(os.getpid())+'-'+str(time.monotonic_ns())
            self.phase='ALLOCATING'
            subprocess.run(['systemd-run','--quiet','--collect','--unit='+self.unit,'--slice=asbpressure.slice',
                '--property=MemoryAccounting=yes','--property=OOMScoreAdjust=1000','--property=MemoryMax='+str(maximum+2*GIB),
                '--property=RuntimeMaxSec=1800','--property=TimeoutStopSec=20',str(self.binary),str(self.directory/'allocator.json'),str(target),str(maximum)],check=True,timeout=30)
            deadline=time.monotonic()+600
            while not (self.directory/'allocator.json').exists():
                if time.monotonic()>deadline:raise TimeoutError('anonymous pressure allocation timeout')
                state=subprocess.check_output(['systemctl','show',self.unit,'-p','ActiveState','-p','MainPID'],text=True,timeout=10)
                values=dict(line.split('=',1) for line in state.splitlines());pid=int(values.get('MainPID',0))
                if pid:self.pid=pid
                if values.get('ActiveState') in ('failed','inactive'):raise RuntimeError('pressure service exited during allocation')
                time.sleep(.2)
            allocator=read(self.directory/'allocator.json')
            if allocator['status']!='HOLDING':raise RuntimeError('pressure allocator did not hold memory')
            self.pid=allocator['pid'];worker=process_snapshot(self.pid);require_outside(worker['cgroup'],request['cgroup'])
            self.phase='HOLDING';self.receipt.update(allocator=allocator,worker=worker,at_ready=host_snapshot())
        else:self.receipt['at_ready']=host_snapshot()
        write(self.directory/'ready.json',dict(status='READY',**self.receipt))

    def stop(self):
        observation_error=None
        try:self.receipt['before_release']=dict(**host_snapshot(),**self.sample())
        except Exception as error:self.receipt['before_release_error']=repr(error);observation_error=error
        self.phase='RELEASING';self.pid=None
        try:
            if self.unit:subprocess.run(['systemctl','stop',self.unit],check=True,timeout=40)
        finally:
            self.phase='RELEASED';self.receipt['after_release']=host_snapshot();write(self.directory/'result.json',self.receipt)
        if observation_error:raise observation_error


class HostObserver:
    def __init__(self,member,pressure):
        self.path=member/'host-global.tsv';self.pressure=pressure;self.stop_event=threading.Event();self.error=None;self.last=None
        self.thread=threading.Thread(target=self.collect,name='host-pressure-monitor',daemon=True);self.thread.start()

    def collect(self):
        started=time.thread_time_ns();samples=0
        try:
            with self.path.open('x',buffering=1) as stream:
                writer=None
                while True:
                    start=time.monotonic_ns();row=dict(**host_snapshot(),**self.pressure.sample());self.last=row
                    if writer is None:writer=csv.DictWriter(stream,fieldnames=list(row),delimiter='\t');writer.writeheader()
                    writer.writerow(row);samples+=1
                    if self.stop_event.is_set():break
                    self.stop_event.wait(max(0,.1-(time.monotonic_ns()-start)/1e9))
        except BaseException as error:self.error=repr(error)
        finally:self.receipt=dict(status='FAIL' if self.error else 'PASS',error=self.error,samples=samples,thread_cpu_ns=time.thread_time_ns()-started,interval_seconds=.1)

    def stop(self):
        self.stop_event.set();self.thread.join(timeout=10)
        if self.thread.is_alive():raise TimeoutError('host pressure observer did not stop')
        write(self.path.with_suffix('.json'),self.receipt)
        if self.error:raise RuntimeError(self.error)


def run_group(argv,env,member,enabled,binary):
    pressure=PressureRun(member,enabled,binary);observer=HostObserver(member,pressure);process=None
    try:
        with (member/'group.log').open('x') as log:
            process=subprocess.Popen(argv,env=env,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+1800;request=pressure.directory/'start-request.json'
            while process.poll() is None:
                if observer.error:raise RuntimeError(observer.error)
                if observer.last and observer.last['MemAvailable']<32*GIB:raise RuntimeError('host available memory below 32 GiB safety reserve; abort this condition')
                if time.monotonic()>deadline:raise TimeoutError('pressure comparison group exceeded 1800 seconds')
                if request.exists() and not (pressure.directory/'ready.json').exists():pressure.start(read(request))
                if pressure.phase=='HOLDING':
                    current=process_snapshot(pressure.pid)
                    if current['start_ticks']!=pressure.receipt['worker']['start_ticks']:raise RuntimeError('pressure process identity changed')
                time.sleep(.1)
            if process.returncode==0 and not (pressure.directory/'ready.json').exists():raise RuntimeError('guest path skipped the before-creation pressure gate')
            return process.returncode
    finally:
        # Independent recovery command: systemctl stop <owned pressure unit>.
        try:pressure.stop()
        finally:
            try:observer.stop()
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
