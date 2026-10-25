"""N=1/N=7 reference using the existing selected-object collector, not a new backend."""
import json
import time
from pathlib import Path

from host_file_monitor import counters
from host_rollout_shared import SharedRollout

PROFILE='shared-rootfs-reference-v1'
TARGETS=('shared_rootfs_layer','shared_readonly_backing','snapshot_memory_device')


def object_rows(observed):
    return [dict(object_key=o['object_key'],category=o['category'],users=o['users'],
        resident_bytes=o['residency']['resident_bytes'],mapping_length=o['residency']['mapping_length'],
        query_start_monotonic_ns=o['residency']['start_monotonic_ns'],
        query_end_monotonic_ns=o['residency']['end_monotonic_ns']) for o in observed['query']['objects']]


def observe_reference(out,guests,group,binding,saved):
    n=len(guests)
    if n not in (1,7):raise ValueError('reference observations require N=1 or N=7')
    start=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(group)
    collector=SharedRollout(group,binding,out,'short-restore' if saved else 'cold-reference',single_reference=n==1)
    if saved:
        collector.restored={role:saved['template'] for role in guests};collector.templates={saved['template']:11}
    observed=collector.query(guests,start)
    roots=observed['query']['rootfs_inventory']
    if roots['status']!='IDENTIFIED':raise ValueError('rootfs reference requires identified lower files')
    after=counters(group)
    value=dict(status='OBSERVED',profile=PROFILE,sandboxes=n,objects=object_rows(observed),observation=observed,
        start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        counters_before=before,counters_after=after,drive_inventory=observed['phases'][-1]['drive_inventory'],
        scope='N=1 rootfs files are explicit reference candidates, not confirmed sharing. N=7 confirms actual users. Imported memory layer is observed separately, never multiplied by seven.')
    value['elapsed_ns']=value['end_monotonic_ns']-start
    audit_observation(value,observed['phases'][-1]['bindings'])
    (Path(out)/('host-shared-reference-N'+str(n)+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    return value


def audit_observation(value,bindings):
    observed=value['observation'];query=observed['query'];n=value['sandboxes']
    assert observed['status']=='OBSERVED' and observed['pss']=='NOT_REQUESTED' and n==len(bindings)
    assert value['objects']==object_rows(observed)
    phase=observed['phases'][-1];assert phase['bindings']==bindings
    roots=query['rootfs_inventory'];assert roots['status']=='IDENTIFIED'
    assert value['start_monotonic_ns']<=phase['start_monotonic_ns']<=phase['end_monotonic_ns']<=query['start_monotonic_ns']<=query['end_monotonic_ns']<=value['end_monotonic_ns']
    users={}
    for role,guest in roots['guests'].items():
        assert guest['status']=='IDENTIFIED'
        for row in guest['lowers']:
            assert row['status']=='IDENTIFIED' and row['open_descriptors']
            users.setdefault(('regular',row['device'],row['inode']),set()).add(role)
    actual={tuple(o['object_key']):o for o in value['objects'] if o['category']=='shared_rootfs_layer'}
    assert actual and set(actual)==set(users)
    for key,obj in actual.items():assert set(obj['users'])==users[key]==set(bindings)
    for obj in query['objects']:
        row=obj['residency'];assert row['stable'] and row['resident_bytes']==row['resident_pages']*4096
    assert len({tuple(o['object_key']) for o in value['objects']})==len(value['objects'])


def reference(first,last):
    assert first['status']==last['status']=='OBSERVED' and first['sandboxes']==1 and last['sandboxes']==7
    rows=[]
    for category in TARGETS:
        a={tuple(o['object_key']):o for o in first['objects'] if o['category']==category}
        b={tuple(o['object_key']):o for o in last['objects'] if o['category']==category}
        if a.keys()!=b.keys():raise ValueError('reference object set changed: '+category)
        if not a:continue
        for key,obj in a.items():
            if obj['mapping_length']!=b[key]['mapping_length'] or len(obj['users'])!=1 or len(b[key]['users'])!=7:
                raise ValueError('reference capacity or users changed: '+category)
        single=sum(o['resident_bytes'] for o in a.values());multiple=sum(o['resident_bytes'] for o in b.values());base=7*single
        rows.append(dict(category=category,object_count=len(a),single_bytes=single,multiple_bytes=multiple,
            reference_bytes=base,reduction_bytes=base-multiple,reduction_fraction=(base-multiple)/base if base else None))
    if not any(r['category']=='shared_rootfs_layer' for r in rows):raise ValueError('missing rootfs reference')
    return rows
