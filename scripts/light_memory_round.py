"""One sequential, timestamped parent/PSS round. No page scan or cgroup creation."""
import json
import copy
from pathlib import Path
import threading
import time
from host_file_monitor import counters
from host_sandbox_monitor import identity,read


def check_vmm(vm,parent,proc=Path('/proc'),cgroot=Path('/sys/fs/cgroup')):
    if identity(vm['pid'],proc)!={k:vm[k] for k in ('pid','start_ticks','boot_id')}:
        raise ValueError('VMM identity changed')
    membership=(proc/str(vm['pid'])/'cgroup').read_text().strip()
    if membership!='0::'+vm['actual_cgroup']:raise ValueError('VMM membership changed')
    group=cgroot/membership[3:].lstrip('/')
    if group.parent!=parent or group.stat().st_ino!=vm['cgroup_id']:
        raise ValueError('VMM cgroup identity changed')
    if set(map(int,(group/'cgroup.procs').read_text().split()))!={vm['pid']}:
        raise ValueError('VMM child membership differs')


def sample(parent,parent_id,bindings):
    start=time.monotonic_ns();cpu=time.thread_time_ns()
    if parent.stat().st_ino!=parent_id:raise ValueError('parent cgroup replaced')
    for vm in bindings.values():check_vmm(vm,parent)
    times=[];values=counters(parent,read_timings=times)
    if any(type(values.get(k)) is not int for k in ('current','file','anon','kernel','shmem')):
        raise ValueError('parent counter missing')
    vmms={}
    for role,vm in bindings.items():
        before=time.monotonic_ns();v=read(vm['pid'],pss=True,pss_timestamps=True)
        if str(v['start_ticks'])!=str(vm['start_ticks']):raise ValueError('PSS process changed')
        check_vmm(vm,parent)
        vmms[role]=dict(sandbox_id=vm['sandbox_id'],pid=vm['pid'],start_monotonic_ns=before,
            end_monotonic_ns=time.monotonic_ns(),counters=v)
    for vm in bindings.values():check_vmm(vm,parent)
    if parent.stat().st_ino!=parent_id:raise ValueError('parent cgroup replaced during round')
    end=time.monotonic_ns()
    return dict(status='OBSERVED',start_monotonic_ns=start,end_monotonic_ns=end,cpu_ns=time.thread_time_ns()-cpu,
        parent=str(parent),parent_cgroup_id=parent_id,parent_counters=values,parent_reads=times,vmms=vmms,
        pss_sum_kib=sum(v['counters']['pss_kib'] for v in vmms.values()),denominator=len(vmms),
        interpretation='Sequential windows, not atomic. Parent inclusive; never add PSS. PSS covers mapped pages, not all file cache.')


class Sampler:
    def __init__(self,parent,bindings,out,*,lifecycle=False,expected_roles=None):
        self.parent=Path(parent);self.parent_id=self.parent.stat().st_ino;self.bindings=copy.deepcopy(bindings);self.out=out
        self.lock=threading.Lock();self.lifecycle=lifecycle;self.transitions={};self.registry_events=[]
        self.expected_roles=set(bindings) if expected_roles is None else set(expected_roles);self.generation=0
        if expected_roles is not None:
            if not lifecycle or not self.expected_roles or not set(bindings)<=self.expected_roles:raise ValueError('invalid planned lifecycle roles')
            self.transitions={r:dict(state='PENDING',previous_binding=None) for r in self.expected_roles-set(bindings)}
        if len({v['pid'] for v in bindings.values()})!=len(bindings) or (not bindings and expected_roles is None):raise ValueError('duplicate or empty VMM set')
        self.stop_event=threading.Event();self.ready=threading.Event();self.error=None;self.count=0
        self.thread=threading.Thread(target=self.collect,name='light-memory-round');self.thread.start()
        if not self.ready.wait(5):
            self.stop_event.set();self.thread.join(5);raise TimeoutError('light sampler readiness timeout')
        if self.error:raise RuntimeError(self.error)
    def collect(self):
        try:
            with (self.out/'light-memory.jsonl').open('x') as f:
                while not self.stop_event.is_set():
                    with self.lock:
                        row=sample(self.parent,self.parent_id,self.bindings);row['round']=self.count
                        if self.lifecycle:
                            row.update(registry_generation=self.generation,expected_roles=sorted(self.expected_roles),transition_roles=copy.deepcopy(self.transitions),
                                all_roles_pss_sum_kib=None if self.transitions else row['pss_sum_kib'],
                                all_roles_pss_status='UNAVAILABLE_TRANSITION' if self.transitions else 'OBSERVED_SEQUENTIAL')
                    f.write(json.dumps(row)+'\n');f.flush();self.count+=1;self.ready.set()
                    self.stop_event.wait(max(0,.1-(time.monotonic_ns()-row['start_monotonic_ns'])/1e9))
        except Exception as error:self.error=repr(error);self.ready.set()
    def transition(self,role,state):
        if not self.lifecycle:raise ValueError('lifecycle option required')
        with self.lock:
            if self.error:raise RuntimeError(self.error)
            if role not in self.expected_roles:raise ValueError('unknown role')
            old=self.bindings.pop(role,None)
            if old is None and role not in self.transitions:raise ValueError('role not bound')
            previous=self.transitions.get(role,{})
            self.transitions[role]=dict(state=state,previous_binding=old or previous['previous_binding'])
            self.generation+=1
            self.registry_events.append(dict(monotonic_ns=time.monotonic_ns(),generation=self.generation,role=role,
                state=state,previous_binding=copy.deepcopy(self.transitions[role]['previous_binding'])))
            self.save_registry()
    def activate(self,role,vm):
        if not self.lifecycle:raise ValueError('lifecycle option required')
        with self.lock:
            if self.error:raise RuntimeError(self.error)
            if role not in self.transitions:raise ValueError('role must enter transition first')
            if any(v['pid']==vm['pid'] or v['sandbox_id']==vm['sandbox_id'] for v in self.bindings.values()):raise ValueError('duplicate live binding')
            check_vmm(vm,self.parent)
            old=self.transitions[role]['previous_binding']
            if old is not None and ((old['pid'],old['start_ticks'])==(vm['pid'],vm['start_ticks']) or old['sandbox_id']==vm['sandbox_id']):
                raise ValueError('restore did not change host instance identity')
            self.bindings[role]=copy.deepcopy(vm);del self.transitions[role];self.generation+=1
            self.registry_events.append(dict(monotonic_ns=time.monotonic_ns(),generation=self.generation,role=role,state='ACTIVE',binding=copy.deepcopy(vm)))
            self.save_registry()
    def resume_same(self,role,vm):
        """Snapshot capture resumes the same source; this is not a new restore."""
        if not self.lifecycle:raise ValueError('lifecycle option required')
        with self.lock:
            if self.error:raise RuntimeError(self.error)
            old=self.transitions.get(role,{})
            if old.get('state')!='SAVING' or old.get('previous_binding')!=vm:
                raise ValueError('saved source identity changed')
            check_vmm(vm,self.parent)
            self.bindings[role]=copy.deepcopy(vm);del self.transitions[role];self.generation+=1
            self.registry_events.append(dict(monotonic_ns=time.monotonic_ns(),generation=self.generation,role=role,state='RESUMED_SOURCE',binding=copy.deepcopy(vm)))
            self.save_registry()

    def save_registry(self):
        (self.out/'identity-events.json').write_text(json.dumps(self.registry_events,indent=2)+'\n')

    def stop(self):
        self.stop_event.set();self.thread.join(5)
        value=dict(status='FAIL' if self.error or self.thread.is_alive() or not self.count else 'PASS',error=self.error,
            samples=self.count,interval_seconds=.1)
        (self.out/'light-sampler.json').write_text(json.dumps(value,indent=2)+'\n')
        if value['status']!='PASS':raise RuntimeError(str(value))
        return value
