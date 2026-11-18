"""Read an explicitly selected experiment slice; never create or move a cgroup."""
import json
import os
from pathlib import Path
import re
import threading
import time

HOST_SCOPE = 'experiment_slice_cgroup'
KEYS = ('current', 'file', 'anon', 'kernel')
FILE_CLOSURE_THRESHOLD_BYTES = 1024**2
CGROOT = Path('/sys/fs/cgroup')


def resolve_scope(observed_group, *, environ=None, proc_root=Path('/proc'), cgroup_root=CGROOT):
    """Use launch metadata AND controller membership, never a common-root guess."""
    env = os.environ if environ is None else environ
    value = dict(host_scope=HOST_SCOPE, status='UNAVAILABLE', parent=None,
                 observed_cgroup=str(observed_group), controller_cgroup=None)
    try:
        lines = [s[3:] for s in (proc_root/'self/cgroup').read_text().splitlines() if s.startswith('0::')]
        if len(lines) != 1:raise ValueError('controller cgroup v2 membership unavailable')
        controller = cgroup_root/lines[0].lstrip('/')
        value['controller_cgroup'] = str(controller)
        candidates = []
        if env.get('ASB_HOST_FILE_SLICE'):
            unit = env['ASB_HOST_FILE_SLICE']
            if not re.fullmatch(r'asbhost-[a-z0-9]+\.slice', unit):raise ValueError('invalid workflow slice')
            candidates = [p for p in controller.parents if p.name == unit]
            if len(candidates) != 1:raise ValueError('controller is not inside declared workflow slice')
        if env.get('ASB_VMM_CGROUP_PARENT'):
            parent = Path(env['ASB_VMM_CGROUP_PARENT'])
            if not re.fullmatch(r'asbvmm[a-z0-9]+\.slice', parent.name):raise ValueError('invalid VMM experiment slice')
            candidates.append(parent)
        if not candidates:raise ValueError('no dedicated experiment slice declared by launcher')
        if len(set(candidates)) != 1:raise ValueError('conflicting declared experiment parents')
        parent = candidates[0]
        if (not parent.is_relative_to(cgroup_root) or '..' in parent.parts or
                controller == parent or not controller.is_relative_to(parent)):
            raise ValueError('controller is outside declared experiment parent')
        observed = Path(observed_group)
        if not observed.is_relative_to(parent) or '..' in observed.parts:
            raise ValueError('observed service/group is outside declared experiment parent')
        if not (parent/'memory.stat').is_file() or not (parent/'memory.current').is_file():
            raise ValueError('declared experiment parent counters unavailable')
        value.update(status='AVAILABLE', parent=str(parent), declaration='explicit launcher environment + controller/service membership')
    except (OSError, ValueError) as error:
        value['reason'] = str(error)
    return value


def normalize_groups(parent, groups, *, inventory_before=None, inventory_after=None, errors=None):
    """Shared by live sampling and replay; only sum direct children, not descendants."""
    parent = Path(parent)
    recorded_children = {p for p in groups if Path(p).parent == parent}
    children = set(recorded_children)
    for inventory in (inventory_before, inventory_after):
        if inventory is not None:children.update(str(p) for p in inventory if Path(p).parent==parent)
    children = sorted(children)
    issues = list(errors or [])
    if inventory_before is not None and recorded_children != set(map(str, inventory_before)):
        issues.append('direct child counters missing from starting inventory')
    if inventory_after is not None and recorded_children != set(map(str, inventory_after)):
        issues.append('direct child inventory changed during read')
    def fields(path):
        row = {k: groups.get(str(path), {}).get(k) for k in KEYS}
        for k, v in row.items():
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                issues.append(f'{path}: missing or invalid {k}')
                row[k] = None
        return row
    parent_values = fields(parent)
    child_values = {p:fields(p) for p in children}
    sums = {k:sum(r[k] for r in child_values.values()) if all(r[k] is not None for r in child_values.values()) else None for k in KEYS}
    delta = (parent_values['file']-sums['file'] if parent_values['file'] is not None and sums['file'] is not None else None)
    closure = ('INCOMPLETE' if issues else 'MISMATCH' if abs(delta) > FILE_CLOSURE_THRESHOLD_BYTES else 'WITHIN_THRESHOLD')
    return dict(host_scope=HOST_SCOPE, status='INCOMPLETE' if issues else 'OBSERVED', parent=str(parent),
                parent_counters=parent_values, direct_children=child_values, direct_children_sum=sums,
                file_closure=dict(status=closure, parent_minus_children_bytes=delta,
                                  threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES), errors=issues,
                accounting='Parent is inclusive. Never add parent and children. Sequential reads, not an atomic snapshot.')


def cgroup_stat(parent):
    """Read-only diagnostic; dying counts cannot identify page charge owners."""
    row=dict(status='UNAVAILABLE',path=str(parent/'cgroup.stat'),counters=None,error=None)
    try:
        values={}
        for line in (parent/'cgroup.stat').read_text().splitlines():
            key,value=line.split();values[key]=int(value)
        if any(k not in values or values[k]<0 for k in ('nr_descendants','nr_dying_descendants')):
            raise ValueError('missing or invalid descendant counters')
        row.update(status='OBSERVED',counters=values)
    except (OSError,ValueError) as error:row['error']=repr(error)
    return row


def sample_groups(parent,*,diagnostics=False):
    # Import here so host_file_monitor can reuse this module without an import cycle.
    from host_file_monitor import counters
    started = time.monotonic_ns(); cpu = time.thread_time_ns(); groups = {}; errors = []
    before = []; after = [];counter_reads=[]
    stat_before=cgroup_stat(parent) if diagnostics else None
    try:
        before = sorted(p for p in parent.iterdir() if p.is_dir())
        for p in [parent, *before]:
            try:groups[str(p)] = counters(p,read_timings=counter_reads) if diagnostics else counters(p)
            except (OSError, ValueError) as error:errors.append(f'{p}: {error!r}')
        after = sorted(p for p in parent.iterdir() if p.is_dir())
    except OSError as error:errors.append(repr(error))
    value = normalize_groups(parent, groups, inventory_before=before, inventory_after=after, errors=errors)
    result=dict(monotonic_ns=started, groups=groups, experiment_memory=value)
    if diagnostics:
        stat_after=cgroup_stat(parent)
        result['cgroup_diagnostics']=dict(version='cgroup-membership-diagnostics-v1',
            status='OBSERVED' if stat_before['status']==stat_after['status']=='OBSERVED' else 'PARTIAL',
            parent_stat_before=stat_before,parent_stat_after=stat_after,
            counter_reads=counter_reads,
            visible_direct_children_before=list(map(str,before)),visible_direct_children_after=list(map(str,after)),
            interpretation='Current visible directories only; dying counts do not assign residual bytes to a cgroup.')
    result.update(duration_ns=time.monotonic_ns()-started,cpu_ns=time.thread_time_ns()-cpu)
    return result


def snapshot(context):
    if context['status'] != 'AVAILABLE':
        return dict(monotonic_ns=time.monotonic_ns(), groups=None, duration_ns=None, cpu_ns=None,
                    experiment_memory=dict(host_scope=HOST_SCOPE, status='UNAVAILABLE', parent=None,
                        parent_counters=None, direct_children=None, direct_children_sum=None,
                        file_closure=None, reason=context['reason']))
    return sample_groups(Path(context['parent']))


def observation_status(row):
    value = row.get('experiment_memory', row)
    if value['status'] != 'OBSERVED':return value['status']
    return value['file_closure']['status']


class GroupSampler:
    """Parent/direct-child counters at 0.1 s, with explicit closure anomalies."""
    def __init__(self, root, out,*,diagnostics=False):
        self.root, self.out = Path(root), Path(out); self.out.mkdir()
        self.stop_event = threading.Event(); self.error = None; self.count = 0; self.status_counts = {}
        self.diagnostics=diagnostics
        self.thread = threading.Thread(target=self.collect, name='experiment-cgroup-counters')
        self.thread.start()

    def collect(self):
        try:
            with (self.out/'memory.jsonl').open('x', buffering=1) as stream:
                previous=None
                while not self.stop_event.is_set():
                    row = sample_groups(self.root,diagnostics=True) if self.diagnostics else sample_groups(self.root)
                    if self.diagnostics:
                        current=set(row['experiment_memory']['direct_children'])
                        row['cgroup_diagnostics']['membership_since_previous']=(dict(status='INITIAL',added=[],removed=[])
                            if previous is None else dict(status='COMPARED',added=sorted(current-previous),removed=sorted(previous-current)))
                        previous=current
                    status = observation_status(row)
                    self.status_counts[status] = self.status_counts.get(status, 0)+1
                    stream.write(json.dumps(row)+'\n'); self.count += 1
                    self.stop_event.wait(max(0, .1-(time.monotonic_ns()-row['monotonic_ns'])/1e9))
        except BaseException as error:self.error = repr(error)

    def stop(self):
        self.stop_event.set(); self.thread.join(timeout=5)
        anomalies = any(k != 'WITHIN_THRESHOLD' and v for k,v in self.status_counts.items())
        value = dict(status='FAIL' if self.error or self.thread.is_alive() else 'PASS', error=self.error,
                     samples=self.count, interval_seconds=.1, parent=str(self.root), host_scope=HOST_SCOPE,
                     experiment_memory_status='ANOMALY' if anomalies else 'OBSERVED' if self.count else 'NOT_OBSERVED',
                     observation_status_counts=self.status_counts, file_closure_threshold_bytes=FILE_CLOSURE_THRESHOLD_BYTES)
        (self.out/'result.json').write_text(json.dumps(value, indent=2)+'\n')
        assert value['status']=='PASS', value
        return value
