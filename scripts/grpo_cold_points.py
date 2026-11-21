"""Per-action, cold VMM mapping only; coordinated guest exports, no background scans."""
import json
import os
import shlex
import struct
import threading
import time
from pathlib import Path
import numpy as np
from bpo_checkpoint_window import api
from fast_cache_metadata import decode_array, join_arrays
from guest_cache_exporter import REQUEST, STOP
from light_memory_round import check_vmm
from live_task_cache import LiveTaskCache
from task_physical_cache import TaskPhysicalCache

clock=time.monotonic_ns


class ColdPoints:
    def __init__(self,out,container,cli,binding):
        self.out,self.container,self.cli,self.binding=out,container,cli,binding
        self.root=out/'action-points';self.root.mkdir()
        self.capture=TaskPhysicalCache(out,container,cli,directory=self.root/'capture')
        self.sessions={};self.abort=threading.Event();self.seq={};self.paused_ns={}

    def prepare(self,role,*,restored):
        if restored or role in self.sessions:raise ValueError('expected new cold role')
        # Cold session evidence belongs under action-points; live-cache/<role> is owned by PSS.
        self.sessions[role]=LiveTaskCache(self.root,self.container,self.cli,session='parent',
            session_name=role,capture=self.capture)
        self.seq[role]=0;self.paused_ns[role]=0

    def register(self,role,vm):
        c=self.sessions[role];c.expected_binding=vm
        self.seq[role]=0

    def start_role(self,role):
        c=self.sessions[role]
        try:c.start(c.expected_binding['sandbox_id'],coordinated=True)
        except BaseException:self.abort.set();raise

    def point(self,role,sid,segment,step):
        c=self.sessions[role];vm=c.expected_binding
        assert sid==vm['sandbox_id']==c.sid
        token=f'{role}:{segment}:{step}';base='/workspace/artifacts/exp5/'+role+'/'+segment+'/'+step
        def guest(cmd):
            return self.cli('action-'+role+'-'+step,'exec',sid,'bash','-lc',cmd,timeout=65)
        if self.abort.is_set():raise RuntimeError('another role failed')
        n=self.seq[role]+1
        r=dict(role=role,sandbox_id=sid,segment=segment,step=step,action=n,pid=vm['pid'])
        try:
            ready=guest('for i in {1..1000}; do if test -f '+shlex.quote(base+'.ready')+'; then cat '+shlex.quote(base+'.ready')+'; exit; fi; sleep .05; done; exit 1').split()
            assert ready[0]==token and len(ready)==2
            r.update(tool_end_guest_ns=int(ready[1]),ready_seen_host_ns=clock())
            if self.abort.is_set():raise RuntimeError('another role failed')
            check_vmm(vm,self.binding.cgroup)
            before,_=c._snapshot();left=decode_array(before);pfns=left[1][left[1][:,1]!=0,0]
            r['pause_request_ns']=clock()
            try:
                api(vm,'PATCH','Paused');r['pause_complete_ns']=clock()
                assert api(vm,'GET')=='Paused'
                r['read_start_ns']=clock()
                h=np.column_stack((pfns,*(c._entries(pfns,c._bulk()) for _ in range(3))))
                r['read_end_ns']=clock()
            finally:
                if api(vm,'GET')=='Paused':
                    r['resume_request_ns']=clock();api(vm,'PATCH','Resumed');r['resume_complete_ns']=clock()
            assert api(vm,'GET')=='Running'
            after,_=c._snapshot();check_vmm(vm,self.binding.cgroup)
            m=join_arrays(left,decode_array(after),h)
            self.paused_ns[role]+=r['resume_request_ns']-r['pause_complete_ns']
            r.update(confirmed_pages=m['host_confirmed_cache_bytes']//4096,
                unconfirmed_guest_pages=m['unconfirmed_after_pages'],cumulative_pause_ns=self.paused_ns[role])
            if self.abort.is_set():raise RuntimeError('another role failed')
            r['release_request_ns']=clock()
            guest("printf '%s\\n' "+shlex.quote(token)+' > '+shlex.quote(base+'.tmp')+' && mv '+shlex.quote(base+'.tmp')+' '+shlex.quote(base+'.go'))
            r['release_return_ns']=clock();r['status']='OBSERVED';self.seq[role]=n
        except BaseException as e:
            self.abort.set();r.update(status='FAILED',error=repr(e));raise
        finally:
            with (self.root/(role+'.jsonl')).open('a') as f:f.write(json.dumps(r)+'\n')

    def stop(self):
        try:
            for c in self.sessions.values():
                if hasattr(c,'addresses') and hasattr(c,'memory') and not c.memory.closed:
                    os.pwrite(c.memory.fileno(),struct.pack('<Q',STOP),c.addresses[0]+REQUEST)
                for k in ('memory','pm'):
                    if getattr(c,k,None):getattr(c,k).close()
        finally:self.capture.close()
