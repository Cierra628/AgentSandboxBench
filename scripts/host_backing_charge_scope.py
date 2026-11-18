"""Explicit cgroup/sandbox binding for ordinary-file charge queries.

Only capture() reads live proc/cgroup metadata. Validation and association are
saved-data-only; no missing owner is assigned to a parent or reference holder.
"""
import copy
import json
from pathlib import Path
import time

from host_file_page_charge import GroupIdentityHistory


def signature(receipt):
    return dict(host_boot_id=receipt['host_boot_id'],parent=receipt['parent'],
                groups=[{k:r[k] for k in ('path','role','inode','device')} for r in receipt['groups']],
                sandboxes=receipt['sandboxes'])


def validate(receipt,raw,boot):
    if receipt.get('version')!='host-backing-charge-scope-v1':raise ValueError('unsupported charge scope receipt')
    if receipt['host_boot_id']!=boot or any(vm['boot_id']!=boot for vm in raw['bindings'].values()):
        raise ValueError('charge scope host boot conflict')
    if not 0<receipt['start_monotonic_ns']<=receipt['end_monotonic_ns']:
        raise ValueError('charge scope observation time invalid')
    groups=receipt['groups'];by_path={r['path']:r for r in groups}
    expected_roles={'parent','controller','docker',*('vmm:'+role for role in raw['guests'])}
    if len(groups)!=len(expected_roles) or {r['role'] for r in groups}!=expected_roles:
        raise ValueError('charge group roles missing or duplicated')
    if (len(by_path)!=len(groups) or len({r['inode'] for r in groups})!=len(groups) or
            receipt['parent'] not in by_path or by_path[receipt['parent']]['role']!='parent'):
        raise ValueError('duplicate or absent charge group identity')
    for group in groups:
        if (group['host_boot_id']!=boot or group['id_kind']!='cgroupfs_st_ino' or
                type(group['inode']) is not int or group['inode']<=0 or group['first_absent_ns'] is not None or
                not group['first_seen_ns']<=group['last_seen_ns']<=receipt['end_monotonic_ns']):
            raise ValueError('invalid or absent live charge group identity')
        if group['path']!=receipt['parent'] and str(Path(group['path']).parent)!=receipt['parent']:
            raise ValueError('charge group outside explicit parent')
    if set(receipt['sandboxes'])!=set(raw['guests']):raise ValueError('charge sandbox inventory differs')
    paths=[]
    for role,binding in receipt['sandboxes'].items():
        vm=raw['bindings'][role];group=by_path.get(binding['cgroup'])
        if (binding['sandbox_id']!=raw['guests'][role] or binding['pid']!=vm['pid'] or
                str(binding['start_ticks'])!=str(vm['start_ticks']) or group is None or
                group['role']!='vmm:'+role or group['inode']!=binding['cgroup_id'] or
                binding['creation']!='clone3_CLONE_INTO_CGROUP'):
            raise ValueError('charge VMM/sandbox/birth identity conflict')
        paths.append(binding['cgroup'])
    if len(paths)!=len(set(paths)):raise ValueError('multiple VMMs share declared child group')


class ChargeScope:
    def __init__(self,parent,service_groups,vmm_birth_bindings,*,proc_root=Path('/proc'),cgroup_root=Path('/sys/fs/cgroup')):
        self.parent=Path(parent);self.services={role:Path(p) for role,p in service_groups.items()}
        self.birth=vmm_birth_bindings;self.proc=Path(proc_root);self.cgroot=Path(cgroup_root)
        if (not self.parent.is_relative_to(self.cgroot) or self.parent==self.cgroot or
                not self.parent.name.startswith('asbvmm') or self.parent.suffix!='.slice'):
            raise ValueError('explicit private VMM slice required; no fallback')
        if set(self.services)!={'controller','docker'}:raise ValueError('explicit controller and Docker groups required')
        self.history=None

    def capture(self,raw):
        start=time.monotonic_ns();boot=(self.proc/'sys/kernel/random/boot_id').read_text().strip()
        if self.history is None:self.history=GroupIdentityHistory(boot)
        if self.history.boot_id!=boot:raise ValueError('charge scope boot changed')
        groups=[]
        def remember(path,role):
            r=self.history.observe(path,role)
            if r is None:raise ValueError('declared charge group missing: '+str(path))
            groups.append(copy.deepcopy(r));return r
        remember(self.parent,'parent')
        for role,path in self.services.items():remember(path,role)
        sandboxes={}
        for role,vm in raw['bindings'].items():
            birth=self.birth[role];proc=self.proc/str(vm['pid'])
            ticks=proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]
            membership=proc.joinpath('cgroup').read_text().strip()
            if not membership.startswith('0::/') or '\n' in membership:raise ValueError('VMM cgroup membership unavailable')
            path=self.cgroot/membership[3:].lstrip('/')
            if (birth.get('status')!='PASS' or path.parent!=self.parent or birth['actual_cgroup']!=membership[3:] or
                    birth['cgroup_birth']['birth_cgroup']!=membership or
                    birth['cgroup_birth']['creation']!='clone3_CLONE_INTO_CGROUP' or
                    str(birth['registration_identity']['start_ticks'])!=ticks or str(vm['start_ticks'])!=ticks or
                    birth['vmm_binding']['pid']!=vm['pid'] or birth['vmm_binding']['boot_id']!=boot):
                raise ValueError('VMM birth/start/membership conflict')
            if set(map(int,(path/'cgroup.procs').read_text().split()))!={vm['pid']}:
                raise ValueError('VMM child members differ')
            group=remember(path,'vmm:'+role)
            if group['inode']!=birth['registration_identity']['cgroup_id']:raise ValueError('VMM group ID reused')
            # Detect membership/start changes during the observation too.
            if proc.joinpath('cgroup').read_text().strip()!=membership or proc.joinpath('stat').read_text().rsplit(')',1)[1].split()[19]!=ticks:
                raise ValueError('VMM changed during charge identity observation')
            sandboxes[role]=dict(sandbox_id=raw['guests'][role],pid=vm['pid'],start_ticks=ticks,
                cgroup=str(path),cgroup_id=group['inode'],creation=birth['cgroup_birth']['creation'])
        receipt=dict(version='host-backing-charge-scope-v1',host_boot_id=boot,parent=str(self.parent),
                     groups=groups,sandboxes=sandboxes,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns())
        validate(receipt,raw,boot);return receipt


def associate(chunks,before,after,references):
    """Annotate each observed owner; never sum across chunks or assign by reference."""
    if signature(before)!=signature(after):raise ValueError('charge group identity changed across object query')
    by_id={r['inode']:r for r in before['groups']}
    result=[]
    for chunk in chunks:
        owners=[]
        for owner in chunk['charge']['owners']:
            row=copy.deepcopy(owner);group=by_id.get(owner['raw_cgroup_id'])
            row.update(group_path=group['path'] if group else None,group_role=group['role'] if group else None,
                       identity_status='MATCHED_DECLARED_GROUP' if group else 'UNKNOWN_UNLISTED_GROUP',
                       sandbox_id=None)
            if group and group['role'].startswith('vmm:'):
                row['sandbox_id']=before['sandboxes'][group['role'][4:]]['sandbox_id']
            owners.append(row)
        result.append(dict(first_page=chunk['first_page'],pages=chunk['pages'],
                           start_monotonic_ns=chunk['start_monotonic_ns'],end_monotonic_ns=chunk['end_monotonic_ns'],
                           owners=owners,unknown_page_observations=chunk['charge']['unknown_page_observations'],
                           reference_sandboxes=copy.deepcopy(references),exclusive_usage='NOT_MEASURED'))
    return dict(status='PARTIAL' if any(c['unknown_page_observations'] or any(o['identity_status']!='MATCHED_DECLARED_GROUP' for o in c['owners']) for c in result) else 'OBSERVED_PER_CHUNK',
                chunks=result,cross_chunk_physical_bytes=None,
                scope='Charge attribution is separate from file references; no shared-page division')


def private_scope(out,measurement_parent,*,proc_root=Path('/proc'),cgroup_root=Path('/sys/fs/cgroup')):
    """Use this run's setup/birth receipts; setup.control denotes Docker."""
    setup=json.loads((out.parents[2]/'vmm-cgroup-setup.json').read_text())
    birth=json.loads((out/'live-cache/vmm-child-binding.json').read_text())
    membership=(proc_root/'self/cgroup').read_text().strip()
    if not membership.startswith('0::/') or '\n' in membership:
        raise ValueError('controller cgroup membership unavailable')
    controller=cgroup_root/membership[3:].lstrip('/')
    parent=Path(measurement_parent);docker=Path(setup['control'])
    if setup.get('status')!='PASS' or Path(setup['parent'])!=parent or controller.parent!=parent or docker.parent!=parent:
        raise ValueError('private charge scope setup/controller parent conflict')
    return ChargeScope(parent,dict(controller=controller,docker=docker),{'private-a':birth},
                       proc_root=proc_root,cgroup_root=cgroup_root)
