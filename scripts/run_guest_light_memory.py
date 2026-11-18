"""Two or seven private VMMs, bounded local writes; no guest or host page probes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from agentenv_cold_start import create
from host_sandbox_monitor import Binding,identity
from light_memory_round import Sampler,check_vmm
from prettier_no_proxy import IMAGE,clean_environment
ROOT=Path(__file__).resolve().parents[1]
def roles_for(guests):
    return tuple('private-'+chr(ord('a')+i) for i in range(guests))


def bind_vmm(binding,sid,out,role):
    vm=binding.resolve(sid);pid=vm['pid'];proc=Path('/proc')/str(pid)
    membership=(proc/'cgroup').read_text().strip();group=Path('/sys/fs/cgroup')/membership[3:].lstrip('/')
    birth=json.loads((out.parent.parent/'env/logs/vmm-cgroups'/group.name).with_suffix('.json').read_text())
    ns_pid=int(next(l for l in (proc/'status').read_text().splitlines() if l.startswith('NSpid:')).split()[-1])
    if birth['birth_cgroup']!=membership or birth['namespace_pid']!=ns_pid or birth['creation']!='clone3_CLONE_INTO_CGROUP':
        raise ValueError('VMM birth identity conflict')
    uids=list(map(int,next(l for l in (proc/'status').read_text().splitlines() if l.startswith('Uid:')).split()[1:]))
    if uids!=[0,0,0,0]:raise ValueError('expected root VMM')
    vm.update(sandbox_id=sid,actual_cgroup=membership[3:],cgroup_id=group.stat().st_ino,uids=uids)
    check_vmm(vm,binding.cgroup)
    d=out/'live-cache'/role;d.mkdir(parents=True)
    (d/'vmm-child-binding.json').write_text(json.dumps(dict(status='PASS',vmm_binding=vm,cgroup_birth=birth,
        note='MMDS, PID/start/boot and launcher birth; no RAM registration or page collector'),indent=2)+'\n')
    return vm


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('container','service-url'):p.add_argument('--'+name,required=True)
    for name in ('config-home','output'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--guests',type=int,choices=(2,7),default=2)
    p.add_argument('--restore-one',action='store_true')
    a=p.parse_args()
    if a.restore_one and a.guests!=7:raise ValueError('restore-one requires seven light guests')
    roles=roles_for(a.guests);out=a.output.resolve();out.mkdir()
    env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=str(a.config_home)))
    sids={};bindings={};events=[];sampler=None;snapshot=None;stage='binding';ok=False
    result=dict(status='FAIL',guests={},scope='private VMMs; light parent counters and timestamped PSS, not page ownership or business rollout',guest_count=a.guests,heavy_page_scans=False,restore_one=a.restore_one)
    def cli(role,label,*args,timeout=90):
        t=time.monotonic_ns();r=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,args)],env=env,capture_output=True,timeout=timeout)
        (out/(role+'-'+label+'.stdout')).write_bytes(r.stdout);(out/(role+'-'+label+'.stderr')).write_bytes(r.stderr)
        events.append(dict(role=role,stage=label,start_ns=t,duration_ns=time.monotonic_ns()-t,returncode=r.returncode))
        r.check_returncode();return r.stdout.decode().strip()
    try:
        binding=Binding(a.container,out)
        for role in roles:
            stage='create-'+role;sids[role]=create(a.config_home,a.service_url,IMAGE,request_timeout=90,local_no_proxy=True)
            result['guests'][role]=dict(sandbox_id=sids[role]);cli(role,'ready','exec',sids[role],'true')
            bindings[role]=bind_vmm(binding,sids[role],out,role)
            stage='prepare-directory-'+role
            cli(role,'prepare-directory','exec',sids[role],'mkdir','-p','/workspace')
        stage='start_light_sampler';sampler=Sampler(binding.cgroup,bindings,out,**(dict(lifecycle=True) if a.restore_one else {}));time.sleep(2)
        for role in roles:
            stage='write-'+role
            cli(role,'write-read','exec',sids[role],'python3','-c',"import os; p='/workspace/asb-light-cache.bin'; f=open(p,'xb'); f.write(b'A'*(16*2**20)); f.flush(); os.fsync(f.fileno()); f.close(); assert open(p,'rb').read()==b'A'*(16*2**20)")
            time.sleep(3)
        if a.restore_one:
            role=roles[0];old=bindings[role].copy();old_sid=sids[role]
            result['restore_source']=old
            sampler.transition(role,'SAVING')
            snapshot='light-restore-'+out.parents[2].name
            stage='checkpoint';cli(role,'checkpoint','snapshot','create',old_sid,'--name',snapshot,timeout=150)
            sampler.transition(role,'DELETING')
            stage='delete-source';cli(role,'delete-source','delete',old_sid);del sids[role]
            if Path('/proc',str(old['pid'])).exists() and identity(old['pid'])['start_ticks']==old['start_ticks']:
                raise RuntimeError('source VMM still alive after delete')
            sampler.transition(role,'RESTORING')
            stage='restore';sids[role]=cli(role,'restore','start',snapshot,'--detach','--timeout','1800',timeout=150)
            if not sids[role] or sids[role]==old_sid:raise ValueError('restored sandbox ID invalid')
            cli(role,'restore-ready','exec',sids[role],'true')
            stage='bind-restored';bindings[role]=bind_vmm(binding,sids[role],out,role+'-restored')
            sampler.activate(role,bindings[role]);result['restore_target']=bindings[role].copy()
            result['guests'][role].update(sandbox_id=sids[role],original_sandbox_id=old_sid,source_removed=True)
            stage='verify-restored-file'
            cli(role,'verify-restored-file','exec',sids[role],'python3','-c',"assert open('/workspace/asb-light-cache.bin','rb').read()==b'A'*(16*2**20)")
            time.sleep(3)
        for role in roles:
            stage='evict-'+role
            cli(role,'evict','exec',sids[role],'python3','-c',"import os; f=open('/workspace/asb-light-cache.bin','rb'); os.posix_fadvise(f.fileno(),0,16*2**20,os.POSIX_FADV_DONTNEED); f.close()")
            time.sleep(3)
        stage='stop_light_sampler';result['sampling']=sampler.stop();sampler=None
        for role in roles:cli(role,'file-cleanup','exec',sids[role],'rm','/workspace/asb-light-cache.bin')
        ok=True
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if sampler:
            try:result['sampling']=sampler.stop()
            except Exception as error:ok=False;result['sampler_cleanup_error']=repr(error)
        for role,sid in sids.items():
            try:cli(role,'delete','delete',sid);result['guests'][role]['vm_removed']=True
            except Exception as error:ok=False;result['guests'][role]['cleanup_error']=repr(error)
        if snapshot and ok:
            try:cli('private-a','delete-snapshot','template','delete',snapshot);result['snapshot_removed']=True
            except Exception as error:ok=False;result['snapshot_cleanup_error']=repr(error)
        result.update(status='PASS' if ok else 'FAIL',bindings=bindings)
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');(out/'events.json').write_text(json.dumps(events,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out));return 0 if ok else 1
if __name__=='__main__':raise SystemExit(main())
