"""Own a bounded frequency lease around an existing Exp1 task window."""
import argparse
import json
import os
from pathlib import Path
import signal
import time
from cpu_frequency_lease import PolicyLease, save
from diagnose_exp1_cpu_variance import matching_vm, proc_stat


def interrupted(signum,frame):
    raise RuntimeError('frequency window interrupted')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sandbox_id')
    parser.add_argument('directory',type=Path)
    parser.add_argument('runtime',type=Path)
    parser.add_argument('service_group')
    parser.add_argument('--host-wait',action='store_true')
    parser.add_argument('--host-kvm-faults',action='store_true')
    args=parser.parse_args()
    assert not args.host_kvm_faults or args.host_wait
    args.directory.mkdir()
    result=dict(status='FAIL',sandbox_id=args.sandbox_id,binding=[])
    lease=None
    tracer=None
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        pid=matching_vm(args.sandbox_id,args.service_group)
        assert pid is not None,'owned VM not found'
        start=proc_stat((Path('/proc')/str(pid)/'stat').read_text())['start_ticks']
        for name,cpu in [('fc_vcpu 0',2),('fc_vcpu 1',4)]:
            matches=[p for p in (Path('/proc')/str(pid)/'task').iterdir() if (p/'comm').read_text().strip()==name]
            assert len(matches)==1
            tid=int(matches[0].name)
            old=sorted(os.sched_getaffinity(tid));os.sched_setaffinity(tid,{cpu})
            assert os.sched_getaffinity(tid)=={cpu}
            thread_start=proc_stat((matches[0]/'stat').read_text())['start_ticks']
            result['binding'].append(dict(tid=tid,cpu=cpu,original_affinity=old,name=name,start_ticks=thread_start))
        result.update(pid=pid,start_ticks=start,identity='service cgroup and MMDS instanceID')
        lease=PolicyLease(args.directory/'frequency-policy','B',sandbox=dict(id=args.sandbox_id,runtime=str(args.runtime)),timeout=180)
        time.sleep(10)
        lease.verify()
        if args.host_wait:
            from owned_thread_wait import WaitTrace
            tracer=WaitTrace([x['tid'] for x in result['binding']],args.directory/'host-wait',max_seconds=140,kvm_faults=args.host_kvm_faults)
            # Before allowing the task to start, end the unknown initial queue
            # interval by observing each owned vCPU in a non-runnable state.
            proof=[]
            for x in result['binding']:
                deadline=time.monotonic()+2
                task=Path('/proc')/str(pid)/'task'/str(x['tid'])
                while True:
                    observed=time.monotonic_ns()
                    text=(task/'stat').read_text()
                    state=text[text.rindex(')')+2:].split()[0]
                    if state in ('S','D'):
                        proof.append(dict(tid=x['tid'],state=state,observed_monotonic_ns=observed));break
                    assert time.monotonic()<deadline,'initial runnable-wait interval not bounded'
                    time.sleep(.005)
            result['host_wait_initial_state']=proof
        save(args.directory/'ready.json',dict(status='PASS',pid=pid,start_ticks=start,binding=result['binding'],host_wait=args.host_wait))
        deadline=time.monotonic()+120
        while not (args.directory/'stop').exists():
            if time.monotonic()>=deadline:raise TimeoutError('task window exceeded 120 seconds')
            time.sleep(.1)
        lease.verify()
        assert matching_vm(args.sandbox_id,args.service_group)==pid
        assert proc_stat((Path('/proc')/str(pid)/'stat').read_text())['start_ticks']==start
        assert all(os.sched_getaffinity(x['tid'])=={x['cpu']} for x in result['binding'])
        for x in result['binding']:
            task=Path('/proc')/str(pid)/'task'/str(x['tid'])
            assert (task/'comm').read_text().strip()==x['name']
            assert proc_stat((task/'stat').read_text())['start_ticks']==x['start_ticks']
        if tracer:
            owned=tracer;tracer=None
            result['host_wait']=owned.stop()
        result['status']='PASS'
    except Exception as exc:
        result['error']=repr(exc)
    finally:
        if tracer:
            try:result['host_wait']=tracer.stop()
            except Exception as exc:result.update(status='FAIL',host_wait_error=repr(exc))
        if lease:
            try:result['restoration']=lease.close()
            except Exception as exc:result.update(status='FAIL',restoration_error=repr(exc))
        save(args.directory/'result.json',result)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
