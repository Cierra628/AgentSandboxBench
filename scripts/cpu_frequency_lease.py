"""Four authorized CPU policies: durable backup and independent bounded recovery."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CPUS=(2,4,66,68)
FIELDS=('scaling_min_freq','scaling_max_freq','scaling_governor')


def identity(pid):
    try:
        s=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
        return s[19] if s[0]!='Z' else None
    except OSError:return None


def save(path,value):
    temporary=path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    with temporary.open('w') as f:
        json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)


@contextmanager
def locked(directory):
    with (directory/'policy.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX);yield


def read_policy(path):
    return {key:(path/key).read_text().strip() for key in FIELDS}


def write(path,value):
    path.write_text(str(value)+'\n')
    deadline=time.monotonic()+1
    while path.read_text().strip()!=str(value):
        if time.monotonic()>=deadline:raise AssertionError(f'readback mismatch: {path}')
        time.sleep(.02)


def restore(directory,reason):
    with locked(directory):
        receipt=directory/'restored.json'
        if receipt.exists():
            previous=json.loads(receipt.read_text())
            if previous['status']=='PASS':return previous
            save(directory/f'restore-failure-{time.time_ns()}.json',previous)
        backup=json.loads((directory/'backup.json').read_text())
        errors=[];after={}
        for cpu in CPUS:
            p=Path(backup['policies'][str(cpu)]['path']);old=backup['policies'][str(cpu)]['settings']
            for key in ('scaling_min_freq','scaling_max_freq'):
                try:write(p/key,old[key])
                except Exception as exc:errors.append(dict(cpu=cpu,field=key,error=repr(exc)))
            try:
                after[str(cpu)]=read_policy(p)
                assert after[str(cpu)]==old,'policy differs after restoration'
            except Exception as exc:errors.append(dict(cpu=cpu,error=repr(exc)))
        result=dict(status='FAIL' if errors else 'PASS',reason=reason,after=after,errors=errors)
        save(receipt,result);return result


def watch(directory):
    backup=json.loads((directory/'backup.json').read_text())
    save(directory/'watch-ready.json',dict(pid=os.getpid(),deadline=backup['deadline']))
    while True:
        receipt=directory/'restored.json'
        failed=False
        if receipt.exists():
            if json.loads(receipt.read_text())['status']=='PASS':return
            failed=True
        dead=identity(backup['owner_pid'])!=backup['owner_start_ticks']
        expired=time.monotonic()>=backup['deadline']
        if dead or expired or failed:
            for attempt in range(3):
                result=restore(directory,'failed_restore' if failed else ('owner_gone' if dead else 'deadline'))
                if result['status']=='PASS':break
                time.sleep(.25)
            save(directory/'watch-recovery.json',result)
            # Recovery is limited to the sandbox recorded by its creating controller.
            context=backup.get('sandbox')
            if context:
                env=dict(os.environ,PATH=context['runtime']+'/bin:'+os.environ['PATH'],XDG_CONFIG_HOME=context['runtime']+'/config')
                with (directory/'watch-sandbox-cleanup.log').open('w') as log:
                    try:
                        p=subprocess.run(['aenv','delete',context['id']],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=90)
                        save(directory/'watch-sandbox-cleanup.json',dict(exit_code=p.returncode,status='PASS' if p.returncode==0 else 'FAIL'))
                    except Exception as exc:save(directory/'watch-sandbox-cleanup.json',dict(status='FAIL',error=repr(exc)))
            return
        time.sleep(.25)


class PolicyLease:
    def __init__(self,directory,variant,*,cpu_root=Path('/sys/devices/system/cpu'),sandbox=None,timeout=180):
        assert variant in ('A','B')
        self.directory=Path(directory);self.directory.mkdir()
        policies={}
        for cpu in CPUS:
            path=(cpu_root/f'cpu{cpu}'/'cpufreq').resolve()
            settings=read_policy(path)
            assert settings==dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil'),'current policy differs from approved plan'
            assert '2000000' in (path/'scaling_available_frequencies').read_text().split()
            policies[str(cpu)]=dict(path=str(path),settings=settings)
        backup=dict(owner_pid=os.getpid(),owner_start_ticks=identity(os.getpid()),deadline=time.monotonic()+timeout,
                    variant=variant,policies=policies,sandbox=sandbox)
        save(self.directory/'backup.json',backup)
        self.log=(self.directory/'watch.log').open('w')
        self.worker=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--watch',str(self.directory)],
                                     stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not (self.directory/'watch-ready.json').exists():
                assert self.worker.poll() is None and time.monotonic()<deadline,'recovery watcher failed to start'
                time.sleep(.02)
            with locked(self.directory):
                for cpu in CPUS:
                    path=Path(policies[str(cpu)]['path'])
                    assert read_policy(path)==policies[str(cpu)]['settings']
                    if variant=='B':
                        write(path/'scaling_max_freq',2000000)
                        write(path/'scaling_min_freq',2000000)
                save(self.directory/'applied.json',dict(status='PASS',variant=variant,policies={str(c):read_policy(Path(policies[str(c)]['path'])) for c in CPUS}))
        except BaseException:
            self.close();raise

    def verify(self):
        assert self.worker.poll() is None, 'recovery watcher no longer running'
        assert not (self.directory/'restored.json').exists(), 'policy lease already restored'
        applied=json.loads((self.directory/'applied.json').read_text())
        backup=json.loads((self.directory/'backup.json').read_text())
        assert time.monotonic()<backup['deadline'], 'policy lease deadline reached'
        for cpu in CPUS:
            assert read_policy(Path(backup['policies'][str(cpu)]['path']))==applied['policies'][str(cpu)], 'policy changed during run'

    def close(self):
        result=restore(self.directory,'controller_finally')
        self.worker.wait(timeout=35);self.log.close()
        assert result['status']=='PASS' and result['reason']=='controller_finally',f'policy restoration failed or watchdog expired: {self.directory}'
        return result


if __name__=='__main__':
    assert len(sys.argv)==3 and sys.argv[1]=='--watch'
    watch(Path(sys.argv[2]))
