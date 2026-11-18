"""Explicit sibling-VMM ownership checks; no global process scan."""
import json
import os
from pathlib import Path
import re
from host_sandbox_monitor import identity


def lower_vm_identity(receipt, ram_identity, sandbox_id):
    """Join the two existing receipt schemas without inventing identity fields."""
    bound = receipt['vmm_binding']
    if receipt['status'] != 'PASS':
        raise ValueError('VMM binding receipt did not pass')
    if not sandbox_id or ram_identity['sandbox_id'] != sandbox_id:
        raise ValueError('RAM sandbox identity differs from session')
    if (bound['pid'], str(bound['start_ticks']), receipt['actual_cgroup']) != (
            ram_identity['pid'], str(ram_identity['start_time_ticks']), ram_identity['cgroup']):
        raise ValueError('RAM identity differs from VMM binding receipt')
    if 'sandbox_id' in bound and bound['sandbox_id'] != sandbox_id:
        raise ValueError('receipt sandbox identity conflicts')
    return dict(bound, sandbox_id=sandbox_id, actual_cgroup=receipt['actual_cgroup'])


def select_config(log, sandbox_id, device_path, configs, env):
    """Require a saved daemon-device association for this exact restored sandbox."""
    if not re.fullmatch(r'/dev/ublkb[0-9]+',device_path):
        raise ValueError('unsupported memory device')
    matches=set()
    for line in log.splitlines():
        line=re.sub(r'\x1b\[[0-9;]*m','',line)
        fields=dict(re.findall(r'(key|path|sandbox_id)=([^\s]+)',line))
        if fields.get('sandbox_id')==sandbox_id and fields.get('path')==device_path and 'key' in fields:
            matches.add(fields['key'])
    allowed={('/workspace/env/'+str(p.relative_to(env))):p for p in configs}
    if len(matches)!=1 or not matches<=allowed.keys():
        raise ValueError('memory config is missing, ambiguous or differs from restored device')
    return allowed[matches.pop()]


def service_openers(binding, vm, path):
    """Only exact private-service members; check identity and FD before and after."""
    root=binding.proc_root;parent=binding.cgroup;service=binding.private_service_cgroup
    if service.parent!=parent or service==parent:
        raise ValueError('private service must be a direct child of experiment parent')
    vmproc=root/str(vm['pid'])
    before_vm=identity(vm['pid'],root)
    if (before_vm['start_ticks'],before_vm['boot_id'])!=(str(vm['start_ticks']),vm['boot_id']):
        raise ValueError('stale VMM identity')
    member=(vmproc/'cgroup').read_text().strip()
    if member!='0::'+vm['actual_cgroup'] or Path(vm['actual_cgroup']).parent!=Path(binding.cgroup_path):
        raise ValueError('VMM is outside expected child')
    expected_service=binding.cgroup_text.strip()
    group_inode=service.stat().st_ino
    target=path.stat();found=[]
    for pid in map(int,(service/'cgroup.procs').read_text().split()):
        proc=root/str(pid)
        try:
            before=identity(pid,root)
            if (proc/'cgroup').read_text().strip()!=expected_service:raise ValueError('provider escaped private service')
            if (proc/'exe').resolve().name!='uvm-ublk-daemon':continue
            for fd in (proc/'fd').iterdir():
                st=fd.stat()
                if (st.st_dev,st.st_ino)!=(target.st_dev,target.st_ino):continue
                after=identity(pid,root)
                again=fd.stat()
                if before!=after or (proc/'cgroup').read_text().strip()!=expected_service:
                    raise ValueError('provider identity changed')
                if (again.st_dev,again.st_ino)!=(target.st_dev,target.st_ino):raise ValueError('provider FD changed')
                found.append(dict(pid=pid,fd=fd.name,start_ticks=before['start_ticks'],boot_id=before['boot_id'],
                    cgroup=expected_service,device=st.st_dev,inode=st.st_ino))
        except (FileNotFoundError,ProcessLookupError):
            continue
    if not found or len({f['pid'] for f in found})!=1:
        raise ValueError('one private-service provider opening exact memory layer required')
    if identity(vm['pid'],root)!=before_vm or (vmproc/'cgroup').read_text().strip()!=member:
        raise ValueError('VMM identity changed during ownership query')
    if service.stat().st_ino!=group_inode:raise ValueError('service cgroup replaced')
    current=path.stat()
    if (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=(target.st_dev,target.st_ino,target.st_size,target.st_mtime_ns):
        raise ValueError('memory layer changed during ownership query')
    return found
