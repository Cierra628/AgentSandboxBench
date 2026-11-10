"""One live sandbox: bound VMM PSS and its initial immutable rootfs lowers."""
import json
from pathlib import Path
import time

from host_file_monitor import counters, measurement_group, mincore_file
from host_prefix_compare import drives_for_vm
from host_rollout_shared import opened_regular_files
from host_rootfs_layers import rootfs_inventory, validate_rootfs_inventory
from host_sandbox_monitor import Binding, HostMonitor, read


class Endpoint:
    def __init__(self, container, output, sid, max_seconds):
        setup_start=time.monotonic_ns(); setup_cpu=time.thread_time_ns()
        self.output = Path(output)
        self.sid = sid
        self.binding = Binding(container, self.output)
        self.vm = self.binding.resolve(sid)
        self.group, _ = measurement_group()
        self.roots = rootfs_inventory({'single': self.vm}, {'single': drives_for_vm(self.vm)},
                                      opened_regular_files(self.binding.cgroup), {})
        if self.roots['status'] != 'IDENTIFIED' or not self.roots['objects']:
            raise ValueError('initial rootfs lowers unresolved')
        for obj in self.roots['objects']:obj['status']='SELECTED_SINGLE_GUEST'
        self.roots['scope']='All confirmed initial immutable user-rootfs lowers for this one guest; deduplicated by device/inode. Upper and other backing are not queried.'
        (self.output/'initial-lowers.json').write_text(json.dumps(self.roots, indent=2)+'\n')
        self.monitor = HostMonitor(self.binding, sid, 'single', 'single', self.output/'vmm',
                                   'task', max_seconds=max_seconds)
        setup=dict(status='PASS',start_monotonic_ns=setup_start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-setup_cpu,
                   scope='Control thread: VMM binding, initial lower identity inventory and periodic monitor launch; monitor thread CPU is separate')
        (self.output/'endpoint-setup.json').write_text(json.dumps(setup,indent=2)+'\n')

    def observe(self):
        self.observation=dict(status='INCOMPLETE')
        try:
            value=self.query()
            self.observation=value
            return value
        except Exception as error:
            self.observation.update(status='FAIL',error=repr(error))
            raise
        finally:
            (self.output/'endpoint.json').write_text(json.dumps(self.observation,indent=2)+'\n')

    def query(self):
        start = time.monotonic_ns(); cpu = time.thread_time_ns()
        if self.binding.resolve(self.sid) != self.vm:
            raise ValueError('VMM identity changed')
        value = read(self.vm['pid'], pss=True)
        if value['start_ticks'] != self.vm['start_ticks'] or self.binding.resolve(self.sid) != self.vm:
            raise ValueError('PSS identity changed')
        pss = dict(start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(),
                   cpu_ns=time.thread_time_ns()-cpu, denominator=1,
                   per_sandbox_bytes=value['pss_kib']*1024, process=value, identity=self.vm)
        self.observation['pss']=pss
        validate_rootfs_inventory(self.roots)
        start = time.monotonic_ns(); cpu = time.thread_time_ns(); before = counters(self.group)
        files = []
        self.observation['partial_mincore_files']=files
        for obj in self.roots['objects']:
            row = mincore_file(Path(obj['path']))
            if not row['stable'] or any(row[k] != obj[k] for k in ('device', 'inode', 'size', 'mtime_ns')):
                raise ValueError('lower changed during mincore')
            files.append(row)
        after = counters(self.group)
        p1 = dict(files=files, unique_files=len(files), resident_bytes=sum(r['resident_bytes'] for r in files),
                  start_monotonic_ns=start, end_monotonic_ns=time.monotonic_ns(), cpu_ns=time.thread_time_ns()-cpu,
                  counters_before=before, counters_after=after)
        validate_rootfs_inventory(self.roots)
        if self.binding.resolve(self.sid) != self.vm:
            raise ValueError('VMM identity changed during endpoint query')
        result = dict(status='OBSERVED', pss=pss, p1=p1, cgroup=str(self.group),
                      coverage_insufficient=p1['resident_bytes'] > after['file'],
                      scope='N=1; initial immutable rootfs lowers deduplicated by device/inode. Upper and other backing unknown. PSS/current/file/mincore/guest Cached are separate quantities; never sum or infer savings from differences.')
        (self.output/'endpoint.json').write_text(json.dumps(result, indent=2)+'\n')
        return result

    def stop(self):
        return self.monitor.stop()
