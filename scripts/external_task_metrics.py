"""One live external guest: reuse VMM sampling and metadata-only lower queries."""
import csv
import json
from pathlib import Path
import time

from host_file_monitor import counters, measurement_group, mincore_file
from host_prefix_compare import drives_for_vm
from host_rootfs_layers import rootfs_inventory, validate_rootfs_inventory
from host_rollout_shared import opened_regular_files
from host_sandbox_monitor import Binding, HostMonitor, read, validate_pss_interval

SNAPSHOT_FILES=('external_task_metrics.py','host_sandbox_monitor.py','host_rootfs_layers.py',
    'host_prefix_compare.py','host_rollout_shared.py','host_scaling.py','run_host_file_restore.py',
    'sandbox_runtime_monitor.py','guest_memory_collector.py')


def validate_result(task,*,pss_interval_seconds=None):
    """Require real endpoint values and a stopped, successful periodic collector."""
    assert task['functional_validation']=='PASS'
    value=task['endpoint_metrics'];pss=value['pss'];p1=value['p1']
    assert value['status']=='OBSERVED' and value['guest_count']==1
    assert len(pss['processes'])==1 and pss['total_bytes']==pss['per_sandbox_bytes']
    assert pss['total_bytes']>0 and p1['files'] and p1['unique_files']==len(p1['files'])
    assert len({(r['device'],r['inode']) for r in p1['files']})==len(p1['files'])
    assert all(r['stable'] for r in p1['files'])
    assert p1['resident_bytes']==sum(r['resident_bytes'] for r in p1['files'])
    assert task['host_vmm_collector']['status']=='PASS' and task['host_vmm_collector']['pss_samples']>0
    if pss_interval_seconds is not None:
        assert task['host_vmm_collector']['pss_interval_seconds']==pss_interval_seconds
        assert task['host_vmm_collector']['interval_seconds']==.1
    for row in (pss,p1):
        assert row['end_monotonic_ns']>=row['start_monotonic_ns'] and row['cpu_ns']>=0
    assert task['schedule_elapsed_ns']>0 and task['actions_download_elapsed_ns']>0


class ExternalMetrics:
    def __init__(self,container,out,sid,*,pss_interval_seconds=1):
        pss_interval_seconds=validate_pss_interval(pss_interval_seconds)
        started=time.monotonic_ns();cpu=time.thread_time_ns()
        self.out=Path(out);self.sid=sid;self.receipt=None
        self.group,_=measurement_group()
        self.binding=Binding(container,self.out)
        if not self.binding.cgroup.is_relative_to(self.group):raise ValueError('VMM service is outside the experiment group')
        self.vm=self.binding.resolve(sid)
        self.drives=drives_for_vm(self.vm)
        self.roots=rootfs_inventory({'backbone':self.vm},{'backbone':self.drives},opened_regular_files(self.binding.cgroup),{})
        if self.roots['status']!='IDENTIFIED' or not self.roots['objects']:raise ValueError('initial rootfs lower identity incomplete')
        self.roots['scope']='Initial rootfs lowers of one live guest, deduplicated by device:inode; single-user reference, not measured shared savings'
        (self.out/'external-initial-files.json').write_text(json.dumps(dict(binding=self.vm,drives=self.drives,rootfs_inventory=self.roots),indent=2)+'\n')
        self.host=HostMonitor(self.binding,sid,'backbone','external',self.out/'host-vmm/external','tools',pss_interval_seconds=pss_interval_seconds)
        self.initialization=dict(start_monotonic_ns=started,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)

    def observe(self):
        self.host.phase='endpoint'
        started=time.monotonic_ns();cpu=time.thread_time_ns()
        value=dict(status='FAIL',guest_count=1,sandbox_id=self.sid,binding=self.vm,
                   drives=self.drives,rootfs_inventory=self.roots,cgroup=str(self.group),
                   cgroup_scope='Outer experiment slice: preparation, controller, private service and descendants',
                   start_monotonic_ns=started,initialization=self.initialization)
        try:
            if self.binding.resolve(self.sid)!=self.vm:raise ValueError('VMM changed before endpoint query')
            pss_start=time.monotonic_ns();pss_cpu=time.thread_time_ns()
            memory=read(self.vm['pid'],pss=True)
            if memory['start_ticks']!=self.vm['start_ticks'] or memory.get('pss_kib') is None:
                raise ValueError('VMM PSS missing or process changed')
            if (Path('/proc')/str(self.vm['pid'])).stat().st_uid!=0:raise ValueError('expected root VMM')
            value['pss']=dict(processes=[dict(identity=self.vm,**memory)],total_bytes=memory['pss_kib']*1024,
                per_sandbox_bytes=memory['pss_kib']*1024,start_monotonic_ns=pss_start,
                end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-pss_cpu)
            begin=time.monotonic_ns();query_cpu=time.thread_time_ns();before=counters(self.group);files=[]
            for obj in self.roots['objects']:
                row=mincore_file(Path(obj['path']))
                if not row['stable'] or any(row[k]!=obj[k] for k in ('device','inode','size','mtime_ns')):
                    raise ValueError('initial lower changed during mincore')
                files.append(row)
            after=counters(self.group);resident=sum(r['resident_bytes'] for r in files)
            shortfall=max(0,resident-max(before['file'],after['file']))
            value['p1']=dict(files=files,unique_files=len(files),resident_bytes=resident,
                counters_before=before,counters_after=after,net_shortfall_bytes=shortfall,
                accounting='SHORTFALL_REQUIRES_RESOLUTION' if shortfall else 'NO_NET_SHORTFALL_DETECTED',
                outside_charge_bytes=None,start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),
                cpu_ns=time.thread_time_ns()-query_cpu)
            validate_rootfs_inventory(self.roots)
            if self.binding.resolve(self.sid)!=self.vm:raise ValueError('VMM changed during endpoint query')
            value.update(status='OBSERVED',scope='One VMM PSS and initial immutable rootfs lower residency only; no P0, upper or whole-cache attribution')
            return value
        except Exception as error:
            value['error']=repr(error)
            raise
        finally:
            value.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
            (self.out/'external-metrics.json').write_text(json.dumps(value,indent=2)+'\n')

    def stop(self):
        if self.receipt is None:
            self.receipt=dict(self.host.stop())
            with (self.out/'host-vmm/external/host-vmm.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
            self.receipt['maximum_sampling_gap_ns']=max((int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in zip(rows,rows[1:])),default=None)
        return self.receipt
