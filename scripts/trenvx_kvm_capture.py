"""Capture KVM registrations in the caller's private PID namespace and its child."""
import json
import hashlib
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from measure_file_physical import captured_slots, validate_slots


def pid_layout():
    """Read the running kernel's layout; bpftrace 0.20 drops flexible arrays."""
    raw=subprocess.check_output(['bpftool','-j','btf','dump','file','/sys/kernel/btf/vmlinux','format','raw'],timeout=20)
    types=json.loads(raw)['types'];by_id={row['id']:row for row in types}
    pid=next(row for row in types if row.get('kind')=='STRUCT' and row.get('name')=='pid')
    member=next(row for row in pid['members'] if row['name']=='numbers')
    array=by_id[member['type_id']];element=by_id[array['type_id']]
    assert array['kind']=='ARRAY' and element['kind']=='STRUCT' and element['name']=='upid'
    assert member['bits_offset']%8==0 and element['size']>0
    assert member['bits_offset']//8==pid['size'] and array['nr_elems']==0
    return dict(numbers_offset=member['bits_offset']//8,upid_size=element['size'],
                btf_sha256=hashlib.sha256(Path('/sys/kernel/btf/vmlinux').read_bytes()).hexdigest())


def program(namespace_inode,layout):
    assert isinstance(namespace_inode,int) and namespace_inode>0
    return r'''struct region { unsigned int slot; unsigned int flags; unsigned long guest; unsigned long size; unsigned long host; };
BEGIN { printf("READY\n"); }
tracepoint:syscalls:sys_enter_ioctl {
  $t=(struct task_struct *)curtask;
  if (args->cmd == 0x4020ae46) {
    $p=$t->group_leader->thread_pid;
    $u=(struct upid *)((uint64)$p + NUMBERS_OFFSET + $p->level * UPID_SIZE);
    if ($u->ns->ns.inum != NSINO && $p->level > 0) {
      $u=(struct upid *)((uint64)$p + NUMBERS_OFFSET + ($p->level - 1) * UPID_SIZE);
    }
    if ($u->ns->ns.inum == NSINO) {
    $r=(struct region *)uptr(args->arg);
    @local[tid]=$u->nr;
    @started[tid]=$t->group_leader->start_boottime;
    @active[tid]=1; @slot[tid]=$r->slot; @guest[tid]=$r->guest;
    @size[tid]=$r->size; @host[tid]=$r->host;
    }
  }
}
tracepoint:syscalls:sys_exit_ioctl /@active[tid]/ {
  printf("%d\t%lu\t%lx\t%lx\t%lx\t%ld\t%d\t%lu\t%lu\n", @local[tid], @slot[tid], @guest[tid], @size[tid], @host[tid], args->ret, pid, NSINO, @started[tid]);
  delete(@active[tid]); delete(@slot[tid]); delete(@guest[tid]); delete(@size[tid]); delete(@host[tid]); delete(@local[tid]); delete(@started[tid]);
}
interval:s:240 { exit(); }
END { clear(@active); clear(@slot); clear(@guest); clear(@size); clear(@host); clear(@local); clear(@started); }
'''.replace('NSINO',str(namespace_inode)).replace('NUMBERS_OFFSET',str(layout['numbers_offset'])).replace('UPID_SIZE',str(layout['upid_size']))


class Capture:
    def __init__(self,out):
        self.directory=out/'kvm-capture';self.directory.mkdir()
        self.namespace_inode=Path('/proc/self/ns/pid').stat().st_ino
        assert os.getpid()==1,'capture must run in owned private PID namespace'
        layout=pid_layout();(self.directory/'pid-layout.json').write_text(json.dumps(layout,indent=2)+'\n')
        self.path=self.directory/'owned-kvm.bt';self.path.write_text(program(self.namespace_inode,layout))
        self.process=None;self.stdout=None;self.stderr=None

    def start(self):
        self.stdout=(self.directory/'raw.tsv').open('w');self.stderr=(self.directory/'stderr.log').open('w')
        env=dict(os.environ,BPFTRACE_PERF_RB_PAGES='8')
        self.process=subprocess.Popen(['bpftrace','-B','none',str(self.path)],stdout=self.stdout,stderr=self.stderr,env=env)
        deadline=time.monotonic()+20
        while 'READY\n' not in (self.directory/'raw.tsv').read_text():
            if self.process.poll() is not None:raise RuntimeError('owned KVM tracer exited before readiness')
            if time.monotonic()>deadline:raise TimeoutError('owned KVM tracer readiness timeout')
            time.sleep(.1)

    def stop(self):
        if self.process is None:return
        try:
            if self.process.poll() is None:self.process.send_signal(signal.SIGINT)
            try:self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill();self.process.wait(timeout=5)
            error=(self.directory/'stderr.log').read_text()+(self.directory/'raw.tsv').read_text()
            status='PASS' if self.process.returncode==0 and not re.search(r'lost\s+[1-9]|ERROR',error,re.I) else 'FAIL'
            (self.directory/'stop.json').write_text(json.dumps(dict(status=status,exit_code=self.process.returncode,
                namespace_inode=self.namespace_inode,scope='owned namespace and direct child KVM capture; output PID in owning namespace; no monitoring overhead verdict'),indent=2)+'\n')
            if status!='PASS':raise RuntimeError('KVM tracer failed or lost events')
        finally:
            if self.stdout:self.stdout.close()
            if self.stderr:self.stderr.close()


def recount_capture(directory,vm,iomem):
    """Offline check: only guest System RAM, with every raw registration retained."""
    stop=json.loads((directory/'stop.json').read_text())
    if stop['status']!='PASS':raise RuntimeError('capture did not stop successfully')
    ns=stop['namespace_inode']
    candidates=[];normalized=[]
    for line in (directory/'raw.tsv').read_text().splitlines():
        fields=line.split('\t')
        if len(fields)!=9 or not fields[0].isdigit():continue
        if int(fields[0])!=vm['pid']:continue
        if int(fields[7])!=ns:raise RuntimeError('event outside owned namespace')
        ticks=int(fields[8])*os.sysconf('SC_CLK_TCK')//1_000_000_000
        if ticks!=vm['start_time_ticks']:raise RuntimeError('capture VMM start time mismatch')
        if int(fields[5])!=0 or int(fields[3],16)<=0:raise RuntimeError('failed registration or removed slot')
        normalized.append('\t'.join(fields[:6])+'\n')
        candidates.append(dict(slot=int(fields[1]),guest=int(fields[2],16),size=int(fields[3],16),host=int(fields[4],16)))
    if not candidates:raise RuntimeError('no successful owned VMM registrations captured')
    ranges=[(int(a,16),int(b,16)+1) for a,b in re.findall(r'^\s*([0-9a-f]+)-([0-9a-f]+) : System RAM$',iomem,re.M)]
    if not ranges or any(b<=a for a,b in ranges):raise RuntimeError('guest System RAM ranges missing')
    allowed=sorted((int(a,16),int(b,16)+1) for a,b in re.findall(
        r'^([0-9a-f]+)-([0-9a-f]+) : (?:System RAM|Reserved)$',iomem,re.M))
    latest={row['slot']:row for row in candidates}
    selected=[]
    for slot,row in latest.items():
        begin=row['guest'];end=begin+row['size']
        covered=[(max(begin,a),min(end,b)) for a,b in ranges if a<end and b>begin]
        if not covered:continue
        # RAM slots can include explicitly reserved low pages and BIOS holes.
        # Require coverage by actual RAM/Reserved ranges; never fill unknown gaps.
        cursor=begin
        for a,b in allowed:
            if a>cursor:break
            if b>cursor:cursor=b
            if cursor>=end:break
        if cursor<end:raise RuntimeError('RAM registration includes an unclassified iomem range')
        selected.append(slot)
    with tempfile.TemporaryDirectory(prefix='asb-kvm-recount-') as tmp:
        path=Path(tmp)/'ioctl.tsv';path.write_text(''.join(normalized))
        slots=captured_slots(path,vm['pid'],selected)
    validate_slots(slots)
    for begin,end in ranges:
        if not any(row['guest']<=begin and end<=row['guest']+row['size'] for row in slots):
            raise RuntimeError('observed RAM not covered by captured registrations')
    return dict(status='PASS',pid=vm['pid'],start_time_ticks=vm['start_time_ticks'],namespace_inode=ns,
        slots=slots,ram_slot_ids=selected,iomem=iomem,
        scope='successful owned KVM registrations selected against guest System RAM; no guessed RAM address')


def attest(directory,vm,iomem):
    ns=json.loads((directory/'stop.json').read_text())['namespace_inode']
    if Path('/proc/self/ns/pid').stat().st_ino!=ns:raise RuntimeError('capture namespace mismatch')
    return recount_capture(directory,vm,iomem)
