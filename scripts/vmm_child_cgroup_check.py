"""Explicit one-run cgroup validation helpers; never enabled by default."""
import argparse
import json
import os
from pathlib import Path
import time

from check_mincore_permissions import credentials, drop_caps, CAP_FOWNER
from host_file_monitor import mincore_file, counters
from prettier_no_proxy import check_cache
from experiment_memory import GroupSampler

CGROOT = Path('/sys/fs/cgroup')


def accounting_controller_state(group, *, require_io=False):
    value=dict(path=str(group),inode=group.stat().st_ino)
    for name in ('cgroup.controllers','cgroup.subtree_control','cgroup.type','cgroup.procs','io.stat'):
        value[name]=(group/name).read_text() if (group/name).exists() else None
    if require_io:
        assert {'memory','io'}<=set(value['cgroup.controllers'].split()), 'memory/io controller unavailable: '+str(group)
        assert value['io.stat'] is not None, 'io.stat unavailable: '+str(group)
    return value


def accounting_acceptance(metrics, samples):
    """Same segments_v3 rules, before successful-volume removal."""
    from report_experiment_schedule import periodic_closure, checked_peak
    periodic=periodic_closure(samples)
    endpoint=metrics.get('experiment_memory_p1',{}).get('experiment_memory',{})
    endpoint_ok=(endpoint.get('status')=='OBSERVED' and
                 endpoint.get('file_closure',{}).get('status')=='WITHIN_THRESHOLD')
    peaks={key:checked_peak(samples,list(range(len(samples))),key,periodic)
           for key in ('current','file')} if samples else {}
    complete=(periodic['status']=='PASS' and endpoint_ok and
              all(p['status']!='UNAVAILABLE' for p in peaks.values()))
    return dict(status='PASS' if complete else 'FAIL',periodic=periodic,
                endpoint=endpoint.get('file_closure'),peaks=peaks,
                reason=None if complete else 'endpoint, periodic closure or peak evidence failed')


def service_root(info):
    membership = Path('/proc', str(info['State']['Pid']), 'cgroup').read_text().strip()
    assert membership.startswith('0::/') and membership != '0::/'
    path = CGROOT/membership[3:].lstrip('/')
    if info['Config']['Labels'].get('asb.vmm-child-cgroup') == 'on':
        parent = Path(info['Config']['Labels']['asb.vmm-parent'])
        assert path.parent == parent and parent.name.startswith('asbvmm')
        path = parent
    return path


def setup_service(info, mount, out, *, sample=True):
    assert info['Config']['Labels'].get('asb.vmm-child-cgroup') == 'on'
    root = service_root(info)
    control = CGROOT/Path('/proc',str(info['State']['Pid']),'cgroup').read_text().strip()[3:].lstrip('/')
    assert not list(root.glob('vmm-*'))
    assert not (root/'cgroup.procs').read_text().strip(), 'service parent still has processes'
    assert 'memory' in (root/'cgroup.subtree_control').read_text().split()
    assert (control/'memory.stat').is_file()
    (mount/'env/logs/vmm-cgroups').mkdir()
    value = dict(status='PASS', parent=str(root), control=str(control), moved_service_processes=[],
                 no_vmm_migrated=True, before_guest_creation_monotonic_ns=time.monotonic_ns(),
                 parent_counters=counters(root), control_counters=counters(control))
    if os.environ.get('ASB_VMM_REQUIRE_IO')=='on':
        assert 'io' in (root/'cgroup.subtree_control').read_text().split()
        value['io_controller_states']=[accounting_controller_state(root,require_io=True),
            accounting_controller_state(control,require_io=True)]
    (out/'vmm-cgroup-setup.json').write_text(json.dumps(value, indent=2)+'\n')
    if not sample:return None
    external=os.environ.get('ASB_EXPERIMENT_MEMORY_SERIES')
    if external:
        with Path(external).open() as stream:first=json.loads(stream.readline())
        assert first['experiment_memory']['parent']==str(root), 'external sampler parent mismatch'
        value['external_series']=external
        (out/'vmm-cgroup-setup.json').write_text(json.dumps(value,indent=2)+'\n')
        return None
    return GroupSampler(root, out/'vmm-cgroup-samples')


def endpoint(binding, bindings, pss, env, out, initial, *, proc_root=Path('/proc'), query_owners=True):
    root = binding.cgroup; rows = []; used = set()
    records = {p.stem:json.loads(p.read_text()) for p in (env/'logs/vmm-cgroups').glob('*.json')}
    parent_before = counters(root)
    for role, vm in bindings.items():
        proc = proc_root/str(vm['pid'])
        membership = (proc/'cgroup').read_text().strip()
        group = CGROOT/membership[3:].lstrip('/')
        assert group.parent == root and group.name.startswith('vmm-') and group not in used
        assert set(map(int, (group/'cgroup.procs').read_text().split())) == {vm['pid']}
        birth = records[group.name]
        ns_pid = int(next(l for l in (proc/'status').read_text().splitlines() if l.startswith('NSpid:')).split()[-1])
        assert birth['birth_cgroup'] == membership and birth['namespace_pid'] == ns_pid
        assert birth['creation'] == 'clone3_CLONE_INTO_CGROUP'
        used.add(group)
        ps = next(r for r in pss['processes'] if r['identity']['pid']==vm['pid'])
        row=dict(role=role,pid=vm['pid'],cgroup=str(group),counters=counters(group),pss=ps,birth=birth)
        if os.environ.get('ASB_VMM_REQUIRE_IO')=='on':
            row['io_controller_state']=accounting_controller_state(group,require_io=True)
        rows.append(row)
    assert len(rows) == len(records) == 7
    value = dict(status='OBSERVED', parent=str(root), parent_before=parent_before, parent_after=counters(root),
                 other_children={str(p):counters(p) for p in root.iterdir() if p.is_dir() and p not in used}, children=rows,
                 child_sum={k:sum(r['counters'][k] for r in rows) for k in ('current','file','anon','kernel')},
                 scope='Sequential common endpoint, parent inclusive of control and seven VMM groups; no parent/child addition')
    if os.environ.get('ASB_VMM_REQUIRE_IO')=='on':
        value['io_controller_states']=[accounting_controller_state(p,require_io=True)
            for p in [root,*sorted(p for p in root.iterdir() if p.is_dir())]]
    (out/'vmm-cgroup-endpoint.json').write_text(json.dumps(value,indent=2)+'\n')
    if not query_owners:
        value['ownership_status']='NOT_REQUESTED_ACCOUNTING_ONLY'
        (out/'vmm-cgroup-endpoint.json').write_text(json.dumps(value,indent=2)+'\n')
        return value
    # A cgroup aggregate cannot locate a particular inode's charges. Reuse the
    # existing non-faulting page-owner query; never guess from similar totals.
    from host_existing_cache_owner import query_bounded
    ownership = []
    for i, f in enumerate(initial):
        result = query_bounded(Path(f['path']), root, out/'lower-owners'/f'{i:02d}', timeout=60, grace=15)
        ownership.append(result)
        assert result['status']=='OBSERVED', result
    value['ownership'] = ownership
    (out/'vmm-cgroup-endpoint.json').write_text(json.dumps(value,indent=2)+'\n')
    return value


def permission_comparison(env, output):
    paths = [env/name for name in check_cache(env)['local_files']]
    assert len(paths) == len({(p.stat().st_dev, p.stat().st_ino) for p in paths}) == 19
    results = []
    for label, dropped in [('with_fowner', []), ('without_fowner', [CAP_FOWNER])]:
        reader, writer = os.pipe(); pid = os.fork()
        if pid == 0:
            os.close(reader)
            try:
                drop_caps(dropped); start = time.monotonic_ns()
                rows = [mincore_file(p) for p in paths]
                value = dict(status='PASS', condition=label, credentials=credentials(), files=rows,
                             file_bytes=sum(r['size'] for r in rows), resident_bytes=sum(r['resident_bytes'] for r in rows),
                             wall_ns=time.monotonic_ns()-start)
                code = 0
            except BaseException as error:
                value = dict(status='FAIL', condition=label, error=repr(error)); code = 1
            with os.fdopen(writer, 'w') as stream:json.dump(value, stream)
            os._exit(code)
        os.close(writer)
        with os.fdopen(reader) as stream:value = json.load(stream)
        _, status = os.waitpid(pid, 0)
        results.append(value)
        assert os.waitstatus_to_exitcode(status) == 0, value
    keys = lambda q: [(r['device'], r['inode'], r['size'], r['mtime_ns']) for r in q['files']]
    assert keys(results[0]) == keys(results[1]) and all(r['stable'] for q in results for r in q['files'])
    value = dict(status='PASS', scope='New restored input inodes, not the deleted historical 19 objects', comparisons=results,
                 conclusion='CAP_FOWNER query is permission-established. Dropping FOWNER alone retains file ownership/write permission; equality does not imply a fake result.')
    with output.open('x') as stream:json.dump(value, stream, indent=2);stream.write('\n')
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query-env', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    q = permission_comparison(args.query_env, args.output)
    print('status='+q['status']+' comparisons='+str([(r['condition'], r['resident_bytes'], r['file_bytes']) for r in q['comparisons']]))
