"""Bounded bpftrace capture for two caller-verified owned threads."""
import json
import os
import re
from pathlib import Path
import selectors
import signal
import subprocess
import time

SCRIPT=Path(__file__).with_name('trace_owned_vcpu_wait.bt')


def host_wait_bound(raw,frequency):
    wait=json.loads((raw/'fixed-frequency/host-wait.json').read_text())
    assert wait==frequency['host_wait'] and wait['status']=='PASS'
    start=int((raw/'host-wait-task-start-monotonic-ns.txt').read_text())
    end=int((raw/'host-wait-task-end-monotonic-ns.txt').read_text())
    assert wait['ready_monotonic_ns']<=start<end<=wait['stop_requested_monotonic_ns']
    tids={x['tid'] for x in frequency['binding']}
    assert {x['tid'] for x in wait['threads']}==tids
    proof=frequency['host_wait_initial_state']
    assert {x['tid'] for x in proof}==tids
    assert all(x['state'] in ('S','D') and wait['ready_monotonic_ns']<=x['observed_monotonic_ns']<=start for x in proof)
    assert all(x['runnable_wait_ns']>=0 and x['observed_switchins']>=0 for x in wait['threads'])
    return dict(task_window_inclusive_ns=end-start,threads=wait['threads'],
                summed_runnable_wait_upper_bound_ns=sum(x['runnable_wait_ns'] for x in wait['threads']),
                initial_interval_check='each vCPU observed non-runnable after probe READY and before task start',
                scope='sum over both owned vCPUs and the full capture including preparation/stop; upper bound for step 025 runnable wait only, not its measured wait',
                limits='excludes sleeping, host I/O threads, guest locks and guest I/O; two-thread wait sum is not task elapsed time')


class WaitTrace:
    def __init__(self,tids,path,*,max_seconds=None,kvm_faults=False):
        assert len(tids)==2 and len(set(tids))==2
        self.tids=tids;self.path=Path(path);self.kvm_faults=kvm_faults
        self.log=self.path.with_suffix('.stderr').open('w')
        script=SCRIPT
        if kvm_faults:
            script=self.path.with_suffix('.bt')
            script.write_text(combined_wait_script(SCRIPT.read_text(),SCRIPT.with_name('trace_owned_vcpu_faults.bt').read_text()))
        command=['bpftrace','-q','-B','line',str(script),*map(str,tids)]
        if max_seconds is not None:
            assert 1 <= max_seconds <= 180
            command=['timeout','--signal=INT','--kill-after=5s',str(max_seconds),*command]
        self.process=subprocess.Popen(command,
            stdout=subprocess.PIPE,stderr=self.log,text=True,start_new_session=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout,selectors.EVENT_READ)
                assert selector.select(15),'trace ready timeout'
            assert self.process.stdout.readline().strip()=='READY','trace setup failed'
            self.begin=time.monotonic_ns()
        except BaseException:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.process.wait(timeout=5);self.log.close();raise

    def stop(self):
        requested=time.monotonic_ns()
        self.process.send_signal(signal.SIGINT)
        try:
            output,_=self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.process.wait(timeout=5);self.log.close();raise
        self.log.close()
        self.path.with_suffix('.stdout').write_text(output)
        assert self.process.returncode==0,'trace exited with failure'
        rows=[]
        for line in output.splitlines():
            if line.startswith('WAIT '):
                _,tid,wait,count=line.split()
                rows.append(dict(tid=int(tid),runnable_wait_ns=int(wait),observed_switchins=int(count)))
        assert {r['tid'] for r in rows}==set(self.tids) and len(rows)==2
        result=dict(status='PASS',threads=rows,ready_monotonic_ns=self.begin,stop_requested_monotonic_ns=requested,elapsed_through_detach_ns=time.monotonic_ns()-self.begin,
            scope='runnable queue wait only, excludes sleeping; window includes counter startup/teardown',
            limitation='initial already-queued interval before first observed event is not reconstructed; END may follow stop request')
        if getattr(self,'kvm_faults',False):
            result['kvm_faults']=parse_kvm_faults(output,self.tids)
        self.path.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
        return result


def parse_kvm_faults(output,tids):
    rows=[]
    for line in output.splitlines():
        if line.startswith('KVM '):
            _,tid,tries,setups,returns,success,pending=line.split()
            row=dict(tid=int(tid),try_async_get_page=int(tries),setup_calls=int(setups),
                     setup_returns=int(returns),successful_async_setup=int(success),pending_setup=int(pending))
            assert all(type(v) is int and v>=0 for v in row.values())
            assert row['setup_calls']==row['setup_returns']+row['pending_setup']
            assert row['pending_setup'] in (0,1) and row['successful_async_setup']<=row['setup_returns']
            rows.append(row)
    assert len(rows)==2 and {r['tid'] for r in rows}==set(tids)
    return dict(threads=rows,scope='owned vCPU TIDs only; full capture including preparation and stop',
                limits='TRY is not successful async setup; no worker completion or fault latency; zero does not exclude synchronous faults; positive fixture validation reported separately')


def combined_wait_script(wait,faults):
    # bpftrace permits a single BEGIN and END; initialize before READY.
    parts={}
    for name in ('BEGIN','END'):
        matches=list(re.finditer(r'\b'+name+r'\s*\{([^{}]*)\}',faults))
        assert len(matches)==1
        parts[name]=matches[0].group(1)
        faults=faults[:matches[0].start()]+faults[matches[0].end():]
        assert wait.count(name+' {')==1
        wait=wait.replace(name+' {',name+' {'+parts[name],1)
    return wait+'\n'+faults
