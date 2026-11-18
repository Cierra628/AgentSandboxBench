"""Opt-in deletion diagnostics on one actual private server binary and PID."""
import json,re,signal,subprocess,time
from pathlib import Path

LOG_FILTER='agentenv=info,agentenv::orchestrator=debug,agentenv::sandbox::firecracker=debug,agentenv::sandbox::ublk::device=debug,agentenv::sandbox::network::manager=debug,agentenv::image::cache=debug,uvm_ublk=info'

def program(pid,binary,network_symbol):
    clauses=['BEGIN { printf("READY\\n"); }']
    for kind,entry,leave,fd in [
        ('fsync','tracepoint:syscalls:sys_enter_fsync','tracepoint:syscalls:sys_exit_fsync','args->fd'),
        ('fdatasync','tracepoint:syscalls:sys_enter_fdatasync','tracepoint:syscalls:sys_exit_fdatasync','args->fd'),
        ('rocksdb_delete',f'uprobe:{binary}:rocksdb_delete',f'uretprobe:{binary}:rocksdb_delete','-1'),
        ('network_release',f'uprobe:{binary}:{network_symbol}',f'uretprobe:{binary}:{network_symbol}','-1')]:
        clauses += [f'{entry} /pid == {pid}/ {{ @start_{kind}[tid] = nsecs; printf("E {kind} %llu %u %d\\n", nsecs, tid, {fd}); }}',
          f'{leave} /pid == {pid} && @start_{kind}[tid]/ {{ $duration = nsecs - @start_{kind}[tid]; printf("X {kind} %llu %u %llu\\n", nsecs, tid, $duration); if ($duration > 100000000) {{ printf("STACK {kind} %u\\n",tid); print(ustack(12)); }} delete(@start_{kind}[tid]); }}']
    return '\n'.join(clauses)+'\n'

class DeleteTrace:
    def __init__(self,container,pid,out):
        self.out=Path(out);self.out.mkdir();self.process=None;self.receipt=None
        info=json.loads(subprocess.check_output(['docker','inspect',container],text=True,timeout=10))[0]
        assert info['State']['Pid']==pid and info['Config']['Labels'].get('agentenv.experiment')=='isolated-storage-check'
        binary=f'/proc/{pid}/root/server'
        symbols=subprocess.check_output(['nm',binary],text=True,timeout=20)
        names=[line.split()[-1] for line in symbols.splitlines() if len(line.split())==3 and line.split()[1] in ('t','T')]
        network=[n for n in names if n.endswith('14NetworkManager7release')];assert len(network)==1 and 'rocksdb_delete' in names
        (self.out/'program.bt').write_text(program(pid,binary,network[0]))
        self.log=(self.out/'events.log').open('x');self.error=(self.out/'stderr.log').open('x')
        self.identity=dict(container=container,pid=pid,image=info['Image'],binary=binary,network_symbol=network[0],start_monotonic_ns=time.monotonic_ns())
        self.process=subprocess.Popen(['bpftrace','-q','-B','line','-p',str(pid),str(self.out/'program.bt')],stdout=self.log,stderr=self.error)
        try:
            deadline=time.monotonic()+30
            while 'READY' not in (self.out/'events.log').read_text():
                if self.process.poll() is not None:raise RuntimeError('private BPF trace failed; see stderr.log')
                if time.monotonic()>deadline:raise TimeoutError('private BPF trace readiness')
                time.sleep(.1)
            (self.out/'identity.json').write_text(json.dumps(self.identity,indent=2)+'\n')
        except BaseException:
            self.stop();raise

    def stop(self):
        if self.receipt is not None:return self.receipt
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=10)
        self.log.close();self.error.close();code=self.process.returncode if self.process else None
        text=(self.out/'events.log').read_text();error=(self.out/'stderr.log').read_text()
        self.receipt=dict(self.identity,status='PASS' if code==0 and 'READY' in text and not re.search(r'lost [1-9]|ERROR',error,re.I) else 'FAIL',exit_code=code,end_monotonic_ns=time.monotonic_ns(),event_lines=sum(line.startswith(('E ','X ')) for line in text.splitlines()),scope='One private server PID only; BPF syscall and synchronous function timing; no guest memory sampling, ptrace or binary changes')
        (self.out/'result.json').write_text(json.dumps(self.receipt,indent=2)+'\n');return self.receipt
