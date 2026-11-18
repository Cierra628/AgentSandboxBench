"""One small buffered loop diagnostic, no mount or guest; exact-inode owner query."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback

from experiment_memory import GroupSampler,sample_groups
from host_file_monitor import mincore_file
from host_existing_cache_owner import query_bounded,layout
from prettier_no_proxy import clean_environment
from platform_tasks import ROOT


def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def run(command,timeout=30):
    return subprocess.run(list(map(str,command)),check=True,capture_output=True,text=True,timeout=timeout)


def group_state(parent):
    paths=[parent,*sorted(p for p in parent.iterdir() if p.is_dir())]
    return {str(p):dict(inode=p.stat().st_ino,**{name:(p/name).read_text() if (p/name).exists() else None
        for name in ['cgroup.controllers','cgroup.subtree_control','cgroup.type','cgroup.procs','cgroup.stat','io.stat','memory.stat']}) for p in paths}


def probe_program(number,offsets):
    text=r'''
BEGIN { printf("READY\n"); }
kprobe:loop_process_work {
 $lo=(uint64)arg2;
 if (*(int32 *)($lo+LOOP_DEVICE_LO_NUMBER)==LOOP_NUMBER) {
  $head=(uint64)arg1; $cmd=*(uint64 *)$head;
  if ($cmd && $cmd!=$head) {
   $blk=*(uint64 *)($cmd+LOOP_CMD_BLKCG_CSS);
   $mem=*(uint64 *)($cmd+LOOP_CMD_MEMCG_CSS);
   $bid=0; $mid=0;
   if ($blk) { $cg=*(uint64 *)($blk+CGROUP_SUBSYS_STATE_CGROUP); $kn=*(uint64 *)($cg+CGROUP_KN); $bid=*(uint64 *)($kn+KERNFS_NODE_ID); }
   if ($mem) { $cg=*(uint64 *)($mem+CGROUP_SUBSYS_STATE_CGROUP); $kn=*(uint64 *)($cg+CGROUP_KN); $mid=*(uint64 *)($kn+KERNFS_NODE_ID); }
   @contexts[$bid,$mid]=count();
  }
 }
}
interval:s:45 { printf("DEADLINE\n"); exit(); }
END { print(@contexts); clear(@contexts); printf("DONE\n"); }
'''
    values=dict(offsets,LOOP_NUMBER=number)
    for key in sorted(values,key=len,reverse=True):text=text.replace(key,str(values[key]))
    return text


def offsets():
    values=layout()
    types=json.loads(run(['bpftool','-j','btf','dump','file','/sys/kernel/btf/vmlinux','format','raw'],30).stdout)['types']
    for struct,field in [('loop_device','lo_number'),('loop_cmd','blkcg_css'),('loop_cmd','memcg_css'),('loop_cmd','list_entry')]:
        t=next(t for t in types if t.get('kind')=='STRUCT' and t.get('name')==struct)
        m=next(m for m in t['members'] if m['name']==field)
        assert m['bits_offset']%8==0
        values[(struct+'_'+field).upper()]=m['bits_offset']//8
    assert values['LOOP_CMD_LIST_ENTRY']==0
    return values


def parse_contexts(text,error):
    assert 'READY\n' in text and 'DONE\n' in text and 'DEADLINE' not in text
    assert not re.search(r'lost\s+[1-9]|ERROR',text+error,re.I)
    values=[dict(blkcg_id=int(a),memcg_id=int(b),batches=int(n)) for a,b,n in
            re.findall(r'@contexts\[(\d+),\s*(\d+)\]:\s*(\d+)',text)]
    assert values,'no targeted loop work observed'
    return values


def inside(out,unit,io_accounting=False):
    membership=Path('/proc/self/cgroup').read_text().strip()
    control=Path('/sys/fs/cgroup')/membership[3:].lstrip('/');parent=control.parent
    assert parent.name==unit+'.slice' and membership.startswith('0::/')
    assert os.uname().release=='6.19.9+'
    assert __import__('shutil').disk_usage(out).free>2**30+128*2**20
    value=dict(status='RUNNING',parent=str(parent),controller=str(control),kernel=os.uname().release,
        image_capacity_bytes=128*2**20,write_bytes=64*2**20,guests_started=0,
        io_accounting_requested=io_accounting)
    device=None;probe=None;sampler=None;image=out/'loop.img'
    try:
        value['group_state_before']=group_state(parent)
        value['systemd_accounting']=run(['systemctl','show',unit+'control.service','-p','IOAccounting','-p','MemoryAccounting']).stdout
        if io_accounting:
            assert 'IOAccounting=yes' in value['systemd_accounting']
            assert 'io' in value['group_state_before'][str(parent)]['cgroup.subtree_control'].split()
            assert 'io' in value['group_state_before'][str(control)]['cgroup.controllers'].split()
        sampler=GroupSampler(parent,out/'memory')
        with image.open('xb') as f:os.posix_fallocate(f.fileno(),0,128*2**20)
        value['file_identity']=dict(device=image.stat().st_dev,inode=image.stat().st_ino)
        value['residency_initial']=mincore_file(image)
        device=run(['losetup','--find','--show',str(image)]).stdout.strip()
        assert re.fullmatch(r'/dev/loop\d+',device)
        number=int(device.removeprefix('/dev/loop'))
        value['device']=device
        value['loop_configuration']=run(['losetup','--json','--output','NAME,BACK-FILE,DIO',device]).stdout
        defs=offsets();write(out/'layout.json',defs)
        (out/'loop-context.bt').write_text(probe_program(number,defs))
        with (out/'loop-context.log').open('x') as stdout,(out/'loop-context.stderr').open('x') as stderr:
            probe=subprocess.Popen(['bpftrace','-q','-B','line',str(out/'loop-context.bt')],stdout=stdout,stderr=stderr)
            deadline=time.monotonic()+30
            while 'READY\n' not in (out/'loop-context.log').read_text():
                if probe.poll() is not None:raise RuntimeError('context probe failed: '+(out/'loop-context.stderr').read_text())
                if time.monotonic()>deadline:raise TimeoutError('loop context probe startup')
                time.sleep(.05)
            value['before_write']=sample_groups(parent)
            value['write_start_monotonic_ns']=time.monotonic_ns()
            write_cmd=['dd','if=/dev/zero','of='+device,'bs=1M','count=64','oflag=direct','conv=fsync','status=none']
            completed=run(write_cmd,30)
            value['write_end_monotonic_ns']=time.monotonic_ns()
            (out/'write.stderr').write_text(completed.stderr)
            value['after_write']=sample_groups(parent)
            probe.send_signal(signal.SIGINT);probe.wait(timeout=15)
            assert probe.returncode==0
        value['loop_contexts']=parse_contexts((out/'loop-context.log').read_text(),(out/'loop-context.stderr').read_text())
        time.sleep(.3)
        value['settled']=sample_groups(parent)
        value['owners']=query_bounded(image,parent,out/'owners',timeout=90,grace=15)
        assert value['owners']['status']=='OBSERVED',value['owners']
        value['after_owner_query']=sample_groups(parent)
        value['group_state_after']=group_state(parent)
        known={str(p.stat().st_ino):str(p) for p in [Path('/sys/fs/cgroup'),parent,control]}
        value['known_group_ids']=known
        value['status']='PASS_DIAGNOSTIC'
    except BaseException as error:
        value.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if probe is not None and probe.poll() is None:
            probe.send_signal(signal.SIGINT)
            try:probe.wait(timeout=10)
            except subprocess.TimeoutExpired:probe.kill();probe.wait(timeout=5)
        value['context_probe_reaped']=probe is not None and probe.poll() is not None
        if device:
            try:
                info=json.loads(run(['losetup','--json','--output','NAME,BACK-FILE',device]).stdout)['loopdevices'][0]
                assert Path(info['back-file']).resolve()==image.resolve()
                run(['losetup','--detach',device]);value['loop_detached']=True
            except Exception as error:value.update(status='FAIL',cleanup_error=repr(error))
        if sampler:
            try:value['sampler']=sampler.stop()
            except Exception as error:value.update(status='FAIL',sampler_error=repr(error))
        value['image_retained']=image.exists();write(out/'result.json',value)
    return 0 if value['status']=='PASS_DIAGNOSTIC' else 1


def launch_command(out,unit,io_accounting=False):
    return ['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit+'control','--slice='+unit+'.slice',
         '--property=WorkingDirectory='+str(ROOT),'--property=MemoryAccounting=yes',
         *(['--property=IOAccounting=yes'] if io_accounting else []),sys.executable,'-B',str(Path(__file__).resolve()),
         '--output',str(out),'--inside','--unit',unit,*(['--io-accounting'] if io_accounting else [])]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--inside',action='store_true');p.add_argument('--unit')
    p.add_argument('--io-accounting',action='store_true',help='Enable IOAccounting on this transient service only')
    args=p.parse_args();out=args.output.resolve();assert out.is_relative_to(ROOT/'.artifacts')
    if args.inside:return inside(out,args.unit,args.io_accounting)
    assert not out.exists();out.mkdir();(out/'code').mkdir()
    import shutil
    for name in ['run_loop_memcg_diagnostic.py','host_existing_cache_owner.py','host_file_monitor.py','experiment_memory.py']:
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    unit='asbloop'+datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%Sz').lower()
    cmd=launch_command(out,unit,args.io_accounting)
    write(out/'launch.json',dict(command=cmd))
    with (out/'launch.log').open('x') as stream:
        result=subprocess.run(cmd,env=clean_environment(os.environ),stdout=stream,stderr=subprocess.STDOUT,timeout=220)
    print('status='+('PASS' if result.returncode==0 else 'FAIL')+' result_dir='+str(out),flush=True)
    return result.returncode

if __name__=='__main__':raise SystemExit(main())
