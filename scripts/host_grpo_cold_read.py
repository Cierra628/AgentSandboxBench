"""Explicit full GRPO: cold owned initial files, one serial observer, no task barriers."""
from pathlib import Path
import threading
import time

from host_cold_file_read import ColdFileRead
from host_private_files import write


class GrpoColdRead(ColdFileRead):
    def __init__(self,objects,mount,mode,layers,group,out):
        super().__init__(objects,[dict(mount=str(mount))],group,out,interval=5)
        self.mount=Path(mount).resolve();self.mode=mode;self.layers=layers
        self.stop_event=threading.Event();self.thread=None;self.error=None;self.stop_receipt=None
        self.events=[];self.write_wall_ns=0;self.write_cpu_ns=0
        self.event_write_wall_ns=0;self.event_write_cpu_ns=0
        self.state.update(profile='full-grpo-initial-cold-v1',events_file='host-full-cold-events.json',
            scope='Fixed initial lower files only; P0 confirmed zero; default reclaim; sequential global residency, not whole RAM')

    def roots(self):
        base=self.mount/'env'
        roots=[base/'image-cache/commits']
        if self.mode=='private':roots += [base/'rootfs-copy-control/copies'/str(i) for i in range(1,7)]
        if self.mode not in ('shared','private') or len(self.layers)!=19:
            raise ValueError('full cold read requires the pinned 19-layer shared/private manifest')
        relatives=[Path(r['relative']) for r in self.layers]
        if len(set(relatives))!=19 or any(p.is_absolute() or '..' in p.parts for p in relatives):
            raise ValueError('invalid pinned cold-read relative paths')
        expected={root/rel for root in roots for rel in relatives}
        actual=[Path(r['path']).resolve() for r in self.objects]
        keys={(r['identity']['device'],r['identity']['inode']) for r in self.objects}
        if set(actual)!=expected or len(actual)!=len(expected) or len(keys)!=len(expected):
            raise ValueError('cold targets must exactly match owned initial copies, without aliases')
        return roots

    def save(self):
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        write(self.out/'host-full-cold-read.json',self.state)
        self.write_wall_ns+=time.monotonic_ns()-start;self.write_cpu_ns+=time.thread_time_ns()-cpu

    def mark(self,label,*,monotonic_ns=None,**details):
        # Only the controller writes events. The query worker owns point/state writes.
        self.events.append(dict(label=label,monotonic_ns=time.monotonic_ns() if monotonic_ns is None else monotonic_ns,**details))
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        write(self.out/'host-full-cold-events.json',dict(events=self.events))
        self.event_write_wall_ns+=time.monotonic_ns()-start;self.event_write_cpu_ns+=time.thread_time_ns()-cpu

    def start(self):
        if self.thread is not None or self.state.get('initial_residency')!='CONFIRMED_ZERO':
            raise RuntimeError('start one cold observer only after a verified zero P0')
        self.state['worker_started']=True;self.save()
        self.thread=threading.Thread(target=self.collect,name='host-grpo-cold-read',daemon=True)
        self.thread.start()

    def collect(self):
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        try:
            while not self.stop_event.wait(max(0,(self.next_ns-time.monotonic_ns())/1e9)):
                self.capture('periodic')
        except Exception as error:
            self.error=repr(error);self.state.update(status='FAIL',error=self.error);self.save()
        finally:
            self.state['worker_timing']=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
                cpu_ns=time.thread_time_ns()-cpu,scope='Worker lifetime including periodic queries, data writes and waits; overlaps query/write costs')

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self,timeout=30):
        if self.state.get('worker_reaped') and not self.is_alive():
            if self.error:raise RuntimeError('cold query worker failed: '+self.error)
            return
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        self.stop_event.set()
        # After a timeout, a second cleanup check does not repeat a long wait.
        if self.thread:self.thread.join(timeout=0 if self.stop_receipt else timeout)
        receipt=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),
            cpu_ns=time.thread_time_ns()-cpu,worker_started=self.thread is not None,reaped=not self.is_alive())
        if self.stop_receipt is None:self.stop_receipt=receipt
        else:
            self.stop_receipt['reaped']=receipt['reaped']
            self.stop_receipt['confirmed_reaped_monotonic_ns']=receipt['end_monotonic_ns']
        if not receipt['reaped']:
            # Do not write the worker's state or clean its targets while it is active.
            raise TimeoutError('cold query worker still active; do not delete guests in this process')
        self.state.update(worker_reaped=True,stop=self.stop_receipt)
        self.save()
        if self.error:raise RuntimeError('cold query worker failed: '+self.error)

    def complete(self,p1):
        if self.is_alive() or not self.state.get('worker_started') or not self.state.get('worker_reaped') or self.error:
            raise RuntimeError('cold query worker not successfully reaped')
        self.state['points'].append(p1)
        boundaries={r['label']:r['monotonic_ns'] for r in self.events if r['label'] in
            ('guest-creation-begin','guests-and-setup-ready','paths-finished')}
        if set(boundaries)!= {'guest-creation-begin','guests-and-setup-ready','paths-finished'}:
            raise ValueError('cold read phase boundaries incomplete')
        begin,ready,end=(boundaries[k] for k in ('guest-creation-begin','guests-and-setup-ready','paths-finished'))
        if not begin<=ready<=end:raise ValueError('cold read phase times out of order')
        for p in self.state['points']:
            a,b=p['start_monotonic_ns'],p['end_monotonic_ns']
            if p['label']=='P0':phase='before-guests'
            elif p is p1:phase='after-paths'
            elif begin<=a and b<=ready:phase='startup-and-setup'
            elif ready<=a and b<=end:phase='original-paths-and-downloads'
            else:phase='MIXED_PHASES'
            p['phase']=phase
        self.state.update(status='PASS',events=list(self.events),live_identity_checks='PASS',
            query_total_ns=sum(p['elapsed_ns'] for p in self.state['points']),
            query_cpu_ns=sum(p['cpu_ns'] for p in self.state['points']),
            periodic_gaps_ns=[b['start_monotonic_ns']-a['end_monotonic_ns'] for a,b in
                zip(self.state['points'],self.state['points'][1:]) if b['label']=='periodic'],
            write_cost=dict(data_wall_ns_before_final_receipt=self.write_wall_ns,
                data_cpu_ns_before_final_receipt=self.write_cpu_ns,
                event_wall_ns=self.event_write_wall_ns,event_cpu_ns=self.event_write_cpu_ns,
                scope='Actual prior data/event writes; excludes this final receipt write'))
        self.save()
