"""Run only inside an owned diagnostic sandbox; no changes to shared services."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def timing(out):
    started=time.monotonic_ns()
    p=subprocess.run(['bash','/workspace/replay-actions/024.sh'],cwd='/testbed',capture_output=True,timeout=60)
    ended=time.monotonic_ns()
    sys.stdout.buffer.write(p.stdout)
    sys.stderr.buffer.write(p.stderr)
    out.write_text(json.dumps(dict(guest_execution_ns=ended-started,returncode=p.returncode,
        scope='subprocess launch, action execution and output capture; excludes timing-file write'))+'\n')
    return p.returncode


def freezer(out):
    group=Path('/sys/fs/cgroup')/('asb-closeout-'+str(os.getpid()))
    counter=out.with_suffix('.counter')
    child=None
    checks={}
    result={'status':'FAIL','checks':checks,'guest_kernel':os.uname().release}
    try:
        assert Path('/sys/fs/cgroup/cgroup.controllers').exists(), 'guest cgroup v2 unavailable'
        group.mkdir()
        assert (group/'cgroup.freeze').exists(), 'freezer unavailable'
        # Child moves itself before forking; setsid must not escape the cgroup.
        code='''import os,time,sys
from pathlib import Path
Path(sys.argv[1], 'cgroup.procs').write_text(str(os.getpid()))
pid=os.fork()
if pid: sys.exit(0)
os.setsid()
p=Path(sys.argv[2])
for n in range(1000):
 p.write_text(str(n)); time.sleep(.02)
'''
        child=subprocess.Popen([sys.executable,'-c',code,str(group),str(counter)],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        assert child.wait(timeout=5)==0
        deadline=time.monotonic()+5
        while not counter.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert counter.exists(), 'orphan worker never wrote'
        members=(group/'cgroup.procs').read_text().split()
        assert len(members)==1 and str(child.pid) not in members
        result['descendant_pid']=members[0]
        checks['descendant_survives_shell_and_setsid']='PASS'
        (group/'cgroup.freeze').write_text('1')
        deadline=time.monotonic()+5
        while 'frozen 1' not in (group/'cgroup.events').read_text():
            assert time.monotonic()<deadline, 'freeze acknowledgement timeout'
            time.sleep(.01)
        before=counter.read_text()
        time.sleep(.2)
        assert counter.read_text()==before
        checks['freeze_ack_and_stable_counter']='PASS'
        (group/'cgroup.freeze').write_text('0')
        deadline=time.monotonic()+5
        while counter.read_text()==before:
            assert time.monotonic()<deadline, 'worker did not resume'
            time.sleep(.02)
        checks['resume_progress']='PASS'
        result['status']='PASS'
    except Exception as error:
        result['error']=repr(error)
    finally:
        if group.exists():
            (group/'cgroup.freeze').write_text('0')
            if (group/'cgroup.kill').exists():
                (group/'cgroup.kill').write_text('1')
            else:
                for pid in (group/'cgroup.procs').read_text().split():
                    try: os.kill(int(pid),signal.SIGKILL)
                    except ProcessLookupError: pass
            deadline=time.monotonic()+5
            while (group/'cgroup.procs').read_text().strip() and time.monotonic()<deadline: time.sleep(.02)
            group.rmdir()
        checks['owned_group_cleanup']='PASS' if not group.exists() else 'FAIL'
        out.write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    mode,path=sys.argv[1:]
    raise SystemExit(timing(Path(path)) if mode=='timing' else freezer(Path(path)))
