"""Explicit two-service file-copy control; the old shared-object filter is unchanged."""
import json
import os
from pathlib import Path
import time

from host_file_monitor import counters, mincore_file
from host_rootfs_layers import file_identity, rootfs_inventory, validate_rootfs_inventory

ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def relative_file(reference):
    parts=Path(reference['file']).parts
    index=next(i for i in range(len(parts)-1) if parts[i:i+2]==('image-cache','commits'))
    result=Path(*parts[index+2:])
    if not result.parts or result.is_absolute() or '..' in result.parts:raise ValueError('invalid target lower path')
    return str(result)


def targets():
    value=read(ROOT/'docs/records/evidence/host-private-control-offline-20261024.json')
    rows=[dict(relative=relative_file(row['reference']),size=row['size']) for row in value['ordered_rootfs_manifest']]
    if len(rows)!=19 or len({r['relative'] for r in rows})!=19:raise ValueError('expected 19 unique pinned lower files')
    return rows


def member():
    name=os.environ.get('ASB_HOST_PRIVATE_MEMBER')
    if not name:return None
    path=Path(name).resolve()
    if not path.is_relative_to(ROOT/'.artifacts/host-private-files'):raise ValueError('private-control member outside owned output')
    value=read(path)
    if value['mode'] not in ('shared','private') or value['slot'] not in (0,1):raise ValueError('invalid private-control member')
    value['directory']=str(path.parent)
    return value


def signal_member(value,event,payload=None):
    write(Path(value['directory'])/(event+'.json'),dict(monotonic_ns=time.monotonic_ns(),**(payload or {})))


def wait_release(value,event,timeout=600):
    path=Path(value['directory'])/(event+'.release');start=time.monotonic()
    while not path.exists():
        if time.monotonic()-start>timeout:raise TimeoutError('private-control release timeout: '+event)
        time.sleep(.1)
    if path.read_text().strip()!='continue':raise RuntimeError('private-control aborted at '+event)


def gate(event,payload=None):
    value=member()
    if value:
        signal_member(value,event,payload);wait_release(value,event)


def copy_commits(source,dest,value,call):
    """Skip only the shared member's target payloads; retain all other entries."""
    dest.mkdir()
    expected={r['relative']:r['size'] for r in targets()}
    reuse=value.get('reuse_targets_from');rows=[]
    for path in sorted(source.rglob('*')):
        rel=str(path.relative_to(source));target=dest/rel
        if path.is_dir():target.mkdir(exist_ok=True);continue
        if path.is_symlink() or not path.is_file():raise ValueError('unexpected commit entry')
        if rel in expected and reuse:
            original=Path(reuse)/rel
            if file_identity(original)['size']!=expected[rel]:raise ValueError('shared target size mismatch')
            target.touch();rows.append(dict(relative=rel,action='READONLY_BIND',source=str(original)))
        else:
            call('copy-commit-'+str(len(rows)),['cp','-a','--reflink=never','--sparse=always','--',path,target],120)
            if target.stat().st_size!=path.stat().st_size:raise ValueError('copied commit size mismatch')
            rows.append(dict(relative=rel,action='COPIED',size=target.stat().st_size))
    for rel,size in expected.items():
        if not (source/rel).is_file() or (source/rel).stat().st_size!=size:raise ValueError('pinned target absent from seed')
    return dict(status='PREPARED',entries=rows,scope='Copy completion and sizes; shared targets are placeholders until verified read-only binds, not claimed copied payloads')


def target_mounts(value,mount):
    own=Path(mount)/'env/image-cache/commits';source=Path(value.get('reuse_targets_from') or own)
    return [dict(source=str(source/r['relative']),destination='/workspace/env/image-cache/commits/'+r['relative']) for r in targets()]


def verify_mounts(info,rows):
    actual={row['Destination']:row for row in info['Mounts']}
    for row in rows:
        mounted=actual[row['destination']]
        if mounted['Type']!='bind' or mounted['RW'] or mounted['Source']!=row['source']:raise ValueError('target is not the requested read-only bind')


def prepared_objects(services,mode):
    objects={}
    for slot,service in enumerate(services):
        for target in targets():
            source=Path(services[0 if mode=='shared' else slot]['mount'])/'env/image-cache/commits'/target['relative']
            info=file_identity(source)
            if info['size']!=target['size']:raise ValueError('prepared target size changed')
            key=(info['device'],info['inode'])
            row=objects.setdefault(key,dict(path=str(source),relative=target['relative'],identity=info,users=[]))
            if row['relative']!=target['relative']:raise ValueError('two targets alias one inode')
            row['users'].append(str(slot))
    expected=19 if mode=='shared' else 38
    if len(objects)!=expected or any(len(r['users'])!=(2 if mode=='shared' else 1) for r in objects.values()):
        raise ValueError('wrong shared/private object identities')
    return list(objects.values())


def query_objects(objects,group,label,*,measure_residency=True):
    start=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(group);rows=[]
    for obj in objects:
        begin=time.monotonic_ns();thread=time.thread_time_ns()
        residency=mincore_file(Path(obj['path'])) if measure_residency else dict(status='NOT_MEASURED',resident_bytes=None)
        if (measure_residency and not residency['stable']) or file_identity(Path(obj['path']))!=obj['identity']:raise ValueError('target identity or size changed')
        rows.append(dict(obj,residency=residency,query_elapsed_ns=time.monotonic_ns()-begin,query_cpu_ns=time.thread_time_ns()-thread))
    after=counters(group);end=time.monotonic_ns()
    return dict(status='OBSERVED' if measure_residency else 'NOT_MEASURED',label=label,start_monotonic_ns=start,end_monotonic_ns=end,
        elapsed_ns=end-start,cpu_ns=time.thread_time_ns()-cpu,
        counters_before=before,counters_after=after,objects=rows,
        resident_bytes=sum(r['residency']['resident_bytes'] for r in rows) if measure_residency else None,
        scope='Unique pinned regular-file cache objects; sequential global residency, not cgroup charge or total RAM' if measure_residency else 'File identity checks only; residency not measured')


def verify_live(services,objects,group,out):
    from host_sandbox_monitor import Binding
    from host_prefix_compare import drives_for_vm
    from host_rollout_shared import opened_regular_files
    bindings={};inventory={};resolvers={}
    for slot,service in enumerate(services):
        ready=read(Path(service['directory'])/'guest-ready.json')
        resolver=Binding(service['container'],Path(service['mount'])/'controller/prettier6604')
        vm=resolver.resolve(ready['sandbox_id']);bindings[str(slot)]=vm
        resolvers[str(slot)]=(resolver,ready['sandbox_id']);inventory[str(slot)]=drives_for_vm(vm)
    roots=rootfs_inventory(bindings,inventory,opened_regular_files(group),{})
    if roots['status']!='IDENTIFIED':raise ValueError('live lower inventory incomplete')
    expected_order=[r['relative'] for r in targets()]
    expected={(r['identity']['device'],r['identity']['inode']):set(r['users']) for r in objects}
    for role,guest in roots['guests'].items():
        if [relative_file(r['reference']) for r in guest['lowers']]!=expected_order:raise ValueError('live lower order changed')
    actual={(r['device'],r['inode']):set(r['users']) for r in roots['objects']}
    if actual!=expected:raise ValueError('live references do not match prepared shared/private targets')
    validate_rootfs_inventory(roots)
    write(Path(out)/'live-targets.json',dict(status='PASS',bindings=bindings,roots=roots))
    return roots,resolvers


def audit_prefix_task(raw):
    """Original prefix semantics, independent of the old broad-cache observations."""
    from host_prefix_compare import prefix_paths
    from run_prettier6604_grpo import verify_path
    raw=Path(raw);task=read(raw/'result.json');cfg=read(raw/'effective-config.json')
    assert task['status']=='PASS' and task['host_prefix_count']==1 and task['actual_action_count']==11
    assert task['official_evaluation']=='NOT_RUN_PREFIX_ONLY' and task['cleanup']['service_sandbox_list_empty']
    assert not list(raw.glob('checkpoint-*.json'))
    samples=read(raw/'frozen-input/agent-sampling.json')['segments'];names=read(raw/'input-provenance.json')['original_segment_names']
    paths=prefix_paths(cfg['paths'],1)
    for role,path in paths.items():
        checked=verify_path(raw,role,path,raw/'frozen-input',samples,names)
        assert all(task['paths'][role][k]==value for k,value in checked.items())
    return dict(status='PASS',actual_actions=11,official_evaluation='NOT_RUN_PREFIX_ONLY',
        tool_ns=sum(row['guest_action_sum_ns'] for row in task['paths'].values()),
        scope='Original actions, order, expected exits, output and cleanup; P0/P1 cache observations are in the group coordinator, not old prefix_before/after fields')


def compare(shared,private):
    s0,s1=(shared[k]['resident_bytes'] for k in ('P0','P1'))
    u0,u1=(private[k]['resident_bytes'] for k in ('P0','P1'))
    return dict(preparation_difference_bytes=u0-s0,endpoint_difference_bytes=u1-s1,
        endpoint_reduction_fraction=(u1-s1)/u1 if u1 else None,
        shared_runtime_change_bytes=s1-s0,private_runtime_change_bytes=u1-u0,
        difference_change_bytes=(u1-u0)-(s1-s0),
        scope='Direct two-service/two-guest pinned-file comparison; preparation included; no linear extrapolation or whole-RAM claim')
