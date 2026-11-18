"""Explicit Cython relay: one VMM-bound /32 rule per sandbox, outer-owned cleanup."""
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from external_guest_proxy import GuestProxy, relay_rule, rule_present, remove_guest_relay_rule
from host_sandbox_monitor import identity


def select_source(nat, routes, source_pool):
    """Read the actual VMM namespace SNAT target, then match its host /32 route."""
    if source_pool not in ('10.211.0.0/16','10.213.0.0/16','10.215.0.0/16'):
        raise ValueError('unknown private experiment pool')
    targets=set()
    for line in nat.splitlines():
        fields=shlex.split(line)
        if fields[:2]!=['-A','POSTROUTING'] or '-j' not in fields or fields[fields.index('-j')+1]!='SNAT':continue
        if '-o' not in fields or fields[fields.index('-o')+1]!='vpeer':continue
        key=next((k for k in ('--to-source','--to') if k in fields),None)
        if key:targets.add(str(ipaddress.IPv4Address(fields[fields.index(key)+1])))
    if len(targets)!=1:raise ValueError('VMM namespace has no unique vpeer SNAT source')
    source=targets.pop()
    if ipaddress.ip_address(source) not in ipaddress.ip_network(source_pool):
        raise ValueError('VMM namespace source outside experiment pool')
    matched=[r for r in routes if r.get('dst') in (source,source+'/32')]
    if len(matched)!=1 or not re.fullmatch(r'veth-[A-Za-z0-9_-]{1,10}',matched[0].get('dev','')):
        raise ValueError('VMM source does not match one actual host /32 route')
    return source,matched[0]['dev']


def allow(output,sandbox,binding,*,source_pool):
    if not re.fullmatch(r'[a-fA-F0-9-]{36}',sandbox):raise ValueError('invalid sandbox ID')
    output=Path(output);directory=output/'guests'/sandbox
    vm=binding.resolve(sandbox);pid=vm['pid']
    before=identity(pid);assert before=={k:vm[k] for k in before} and binding.owned(pid)
    # Hold the namespace fd while inspecting it, so PID reuse cannot change the target.
    fd=os.open(f'/proc/{pid}/ns/net',os.O_RDONLY)
    try:
        assert os.fstat(fd).st_ino!=Path('/proc/self/ns/net').stat().st_ino,'VMM unexpectedly in host network namespace'
        nat=subprocess.check_output(['nsenter','--net=/proc/self/fd/'+str(fd),'--','iptables-nft-save','-t','nat'],
            text=True,timeout=10,pass_fds=(fd,))
    finally:os.close(fd)
    routes=json.loads(subprocess.check_output(['ip','-j','-4','route','show'],text=True,timeout=10))
    source,interface=select_source(nat,routes,source_pool)
    assert identity(pid)==before and binding.owned(pid),'VMM identity changed during network binding'
    directory.mkdir(parents=True,exist_ok=False)
    ready=json.loads((output/'guest-proxy-ready.json').read_text())
    (directory/'guest-proxy-ready.json').write_text(json.dumps(ready)+'\n')
    receipt=dict(status='PLANNED',backend='nft',sandbox_id=sandbox,source_pool=source_pool,source=source,
        interface=interface,destination=ready['address'],port=ready['port'],vmm=vm,
        binding_source='owned cgroup + MMDS instanceID + VMM netns vpeer SNAT + host /32 route')
    rule=relay_rule(directory,receipt)
    if rule_present(rule):raise ValueError('refusing to adopt existing relay rule')
    path=directory/'guest-proxy-rule.json';path.write_text(json.dumps(receipt,indent=2)+'\n')
    subprocess.run(['iptables-nft','-w','5','-I','INPUT','1',*rule],check=True,capture_output=True,timeout=10)
    if not rule_present(rule):raise RuntimeError('per-instance relay rule insertion ineffective')
    receipt['status']='ADDED';path.write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def cleanup_rules(output):
    errors=[]
    for path in sorted((Path(output)/'guests').glob('*/guest-proxy-rule.json')):
        try:remove_guest_relay_rule(path.parent)
        except Exception as error:errors.append(dict(path=str(path),error=repr(error)))
    if errors:raise RuntimeError('owned Cython relay rule cleanup failed: '+json.dumps(errors))


class ScheduleProxy(GuestProxy):
    def start(self):
        # Match the native entry's explicit unused-pool mode without changing callers.
        key='ASB_ISOLATED_UNUSED_NETWORK_POOLS';old=os.environ.get(key)
        os.environ[key]='on'
        try:return super().start()
        finally:
            if old is None:os.environ.pop(key,None)
            else:os.environ[key]=old

    def stop(self):
        try:cleanup_rules(self.output)
        finally:super().stop()
