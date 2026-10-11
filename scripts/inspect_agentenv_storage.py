"""Read storage identity for one owned cold sandbox, then delete only that sandbox."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import traceback

from smoke_vm_pfn import ROOT, SERVER, ENV, CLI
from experiment_disk_guard import writer_lock


def main():
    os.umask(0o077)
    out=ROOT/'.artifacts/agentenv-storage-inspection'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}'
    out.mkdir(parents=True)
    sid=None; result=dict(status='FAIL',scope='one private VM storage identity; not replay or memory acceptance')
    def call(label,argv,timeout=30):
        started=time.monotonic_ns()
        p=subprocess.run(argv,env=ENV,cwd=ROOT,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(p.stdout);(out/(label+'.stderr')).write_bytes(p.stderr)
        (out/(label+'.json')).write_text(json.dumps(dict(exit_code=p.returncode,elapsed_ns=time.monotonic_ns()-started))+'\n')
        p.check_returncode();return p.stdout
    def aenv(label,*argv,timeout=30):return call(label,[CLI,*argv],timeout)
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        assert os.geteuid()==0 and shutil.disk_usage(out).free>32*2**30
        cid=(SERVER/'container-id.txt').read_text().strip()
        info=json.loads(call('server',['docker','inspect',cid]))[0]
        assert info['State']['Running'] and info['Config']['Labels']['agentenv.experiment']=='initial-smoke'
        cg=Path(f'/proc/{info["State"]["Pid"]}/cgroup').read_text()
        before={p.name for p in Path('/proc').iterdir() if p.name.isdigit()}
        result['api_before']=json.loads(aenv('list-before','list','--output','json'))
        image=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())['workload_image']
        sid=aenv('start','start','--cold',image,'--detach','--cpu','2','--memory','4096','--timeout','300',timeout=180).decode().strip()
        assert re.fullmatch('[a-f0-9-]{36}',sid)
        result['sandbox_id']=sid
        aenv('guest','exec',sid,'bash','-lc','getconf PAGESIZE; cat /proc/iomem; cat /proc/mounts')
        processes=[]
        for p in Path('/proc').iterdir():
            if not p.name.isdigit():continue
            try:
                if (p/'cgroup').read_text()!=cg:continue
                name=(p/'comm').read_text().strip()
                if name!='firecracker' and 'ublk' not in name:continue
                fds=[]
                for fd in (p/'fd').iterdir():
                    try:target=os.readlink(fd);s=fd.stat()
                    except OSError:continue
                    fds.append(dict(fd=fd.name,path=target,dev=s.st_dev,inode=s.st_ino,mode=s.st_mode,size=s.st_size))
                row=dict(pid=int(p.name),name=name,new_process=p.name not in before,
                    cmdline=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace'),fds=fds,
                    start_time_ticks=int((p/'stat').read_text().rsplit(')',1)[1].split()[19]))
                processes.append(row)
                if name=='firecracker' and row['new_process']:(out/(p.name+'-maps.txt')).write_text((p/'maps').read_text())
            except OSError:continue
        (out/'processes.json').write_text(json.dumps(processes,indent=2)+'\n')
        result.update(status='PASS',new_firecracker_count=sum(p['name']=='firecracker' and p['new_process'] for p in processes),service_group=cg)
        assert result['new_firecracker_count']==1
    except BaseException as exc:
        result.update(status='FAIL',error=repr(exc));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if sid:
            try:
                aenv('delete','delete',sid,timeout=90)
                after=json.loads(aenv('list-after','list','--output','json'))
                assert not any(r.get('sandboxID')==sid for r in after)
                assert after==result['api_before']
                result['cleanup']='PASS'
            except BaseException as exc:result.update(status='FAIL',cleanup='FAIL',cleanup_error=repr(exc))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    with writer_lock():raise SystemExit(main())
