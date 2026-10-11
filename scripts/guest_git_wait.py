"""Opt-in native Git events and owned guest process observations; diagnostic only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import signal
import tarfile
import threading
import time

FINAL_BLOCK = '''git -C /testbed add -A
git -C /testbed diff --binary --cached "$(cat "$out/baseline-tree.txt")" > "$out/patch.diff"
git -C /testbed reset --mixed >/dev/null
git -C /testbed status --porcelain=v1 > "$out/git-status.txt"'''
FINAL_TAGS = ('add','diff','reset','status','done')


def instrument(source):
    anchor = '  { time "$@" 2>&3; } 3>&2 2> "$timing_file"'
    assert source.count(anchor) == 1 and source.count(FINAL_BLOCK) == 1
    replacement = '''  local -x GIT_TRACE2_EVENT="${timing_file}.events.jsonl"
  local -x GIT_TRACE_PERFORMANCE="${timing_file}.performance.txt"
  local probe_pid= status=0 runner_pid=$BASHPID
  if [[ $timing_file == *-reset-time.tsv ]]; then
    python3 "${ASB_GIT_WAIT_HELPER:?}" --parent "$runner_pid" --output "${timing_file}.process.jsonl" --stop "${timing_file}.stop" &
    probe_pid=$!
  fi
  { time "$@" 2>&3; } 3>&2 2> "$timing_file" || status=$?
  if [[ -n $probe_pid ]]; then
    : > "${timing_file}.stop"
    wait "$probe_pid"
  fi
  return "$status"'''
    source = source.replace(anchor, replacement).replace('unset -f baseline_timed_git', ': # retain timed Git helper for final commands')
    lines = []
    for number,(tag,command) in enumerate(zip(FINAL_TAGS,FINAL_BLOCK.splitlines())):
        lines.append(f'printf \'{tag}\\t%s\\n\' "$EPOCHREALTIME" {">" if number==0 else ">>"} "$out/final-commands.tsv"')
        lines.append(f'baseline_timed_git "$out/final-{tag}-time.tsv" {command}')
    lines.append('printf \'done\\t%s\\n\' "$EPOCHREALTIME" >> "$out/final-commands.tsv"')
    return source.replace(FINAL_BLOCK, '\n'.join(lines))


def read(path):
    try:
        return path.read_text().strip()
    except OSError as exc:
        return dict(error=type(exc).__name__, errno=exc.errno)


def poll_descriptor(proc, syscall):
    if not isinstance(syscall,str):
        return None
    fields=syscall.split()
    if len(fields)<4 or fields[0]!='7':
        return None  # x86_64 poll only; do not guess another architecture
    result=dict(timeout_ms=int(fields[3],0))
    try:
        assert int(fields[2],0)==1
        with (proc/'mem').open('rb',buffering=0) as memory:
            fd,events,revents=struct.unpack('ihh',os.pread(memory.fileno(),8,int(fields[1],0)))
        target=os.readlink(proc/'fd'/str(fd))
        result.update(fd=fd,events=events,revents=revents,target=target)
        if target.startswith('socket:['):
            inode=target[8:-1]
            for name in ('udp','udp6','tcp','tcp6'):
                text=read(proc/'net'/name)
                if isinstance(text,str):
                    for line in text.splitlines()[1:]:
                        columns=line.split()
                        if len(columns)>9 and columns[9]==inode:
                            result.update(protocol=name,remote_address=columns[2],
                                          remote_port=int(columns[2].split(':')[1],16))
    except (OSError,ValueError,AssertionError,struct.error) as exc:
        result['read_error']=repr(exc)
    return result


def sample(parent, output, stop):
    started = time.monotonic_ns(); own = os.getpid()
    with output.open('x') as stream:
        while not stop.exists() and time.monotonic_ns()-started < 25*10**9:
            tick = time.monotonic_ns(); pending = [parent]; visited = {own}; observations = []
            while pending:
                pid = pending.pop()
                if pid in visited:
                    continue
                visited.add(pid); proc = Path('/proc')/str(pid)
                children = read(proc/'task'/str(pid)/'children')
                if isinstance(children,str):
                    pending += [int(v) for v in children.split()]
                if pid == parent:
                    continue
                threads = []
                for task in proc.glob('task/*'):
                    syscall=read(task/'syscall')
                    threads.append(dict(tid=int(task.name),wchan=read(task/'wchan'),
                                        syscall=syscall,stack=read(task/'stack'),
                                        poll_descriptor=poll_descriptor(proc,syscall)))
                try:
                    exe = os.readlink(proc/'exe')
                except OSError:
                    exe = None
                observations.append(dict(pid=pid,exe=exe,comm=read(proc/'comm'),
                                         cmdline=read(proc/'cmdline'),threads=threads))
            stream.write(json.dumps(dict(timestamp_monotonic_ns=tick,observations=observations,
                                         scan_ns=time.monotonic_ns()-tick))+'\n'); stream.flush()
            time.sleep(.1)
        stream.write(json.dumps(dict(status='STOPPED' if stop.exists() else 'TIMEOUT',elapsed_ns=time.monotonic_ns()-started))+'\n')


def validate_identity_record(base, record):
    prefix=base/record['prefix']
    clock=json.loads(Path(str(prefix)+'.clock.json').read_text())
    assert record['clock']==clock and clock['exit_code']==record['exit_code']
    assert clock['monotonic_raw_ns']>0 and abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<=2_000_000
    assert record['observer_error'] is None and record['observer_stopped'] and not record['timed_out']
    observations=[json.loads(s) for s in Path(str(prefix)+'.process.jsonl').read_text().splitlines()]
    assert observations and observations[-1]['status']=='STOPPED'
    for suffix in ('stdout','stderr'):
        assert hashlib.sha256(Path(str(prefix)+'.'+suffix).read_bytes()).hexdigest()==record[suffix+'_sha256']
    status=record['exit_code']
    if record['label']=='identity-default' and status==128:
        assert 'unable to auto-detect email address' in Path(str(prefix)+'.stderr').read_text()
        return 'IDENTITY_UNAVAILABLE'
    assert status==0, f"{record['label']}: unexpected exit {status}"
    return 'SUCCESS'


def audit_identity(base):
    result=json.loads((base/'result.json').read_text())
    assert result['status']=='PASS'
    assert [r['label'] for r in result['records']]==['default','explicit','explicit','default','identity-default','identity-explicit']
    before=json.loads((base/'state-before.json').read_text())
    for record in result['records']:
        assert record['state_after']==before
        assert validate_identity_record(base,record)==record['command_result']
    assert len({r['stdout_sha256'] for r in result['records'][:4]})==1
    return dict(status='PASS',measurements_complete=True,records=result['records'],
                state_preserved=True,scope=result['scope'])


async def download_identity_probe(sb, out, tool):
    """Always collect available guest files before propagating the original error."""
    primary=None
    try:
        await tool(sb,'identity-probe','python3 /opt/asb-exp1/guest_git_wait.py --identity-probe /opt/asb-exp1',timeout=100)
    except BaseException as exc:
        primary=exc
    receipt=dict(status='FAIL',probe_error=repr(primary) if primary else None)
    try:
        await tool(sb,'pack-identity-probe','tar -C /opt/asb-exp1/identity-probe -czf /dev/shm/asb-identity-probe.tar.gz .')
        archive=out/'identity-probe.tar.gz'
        archive.write_bytes(await sb.download_file('/dev/shm/asb-identity-probe.tar.gz',timeout=30))
        with tarfile.open(archive,'r:gz') as f:
            f.extractall(out/'identity-probe',filter='data')
        receipt.update(status='PASS',archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    except BaseException as exc:
        receipt['download_error']=repr(exc)
        (out/'identity-download.json').write_text(json.dumps(receipt,indent=2)+'\n')
        if primary:
            raise BaseExceptionGroup('identity probe and data download failed',[primary,exc])
        raise
    (out/'identity-download.json').write_text(json.dumps(receipt,indent=2)+'\n')
    if primary:
        raise primary


def identity_probe(inputs, repository='/testbed'):
    base=inputs/'identity-probe';base.mkdir()
    git=['git','-C',str(repository)]; explicit=['git','-c','user.name=asb-fixture','-c','user.email=asb-fixture@localhost','-C',str(repository)]
    def state():
        return {name:subprocess.run(git+argv,capture_output=True,check=True,timeout=15).stdout.decode()
                for name,argv in (('head',['rev-parse','HEAD']),('tree',['write-tree']),('working_diff',['diff','--binary','HEAD']))}
    result=dict(status='RUNNING',records=[],scope='ABBA reset plus identity-generation check; command-scoped user settings only; reflog metadata may differ')
    def save():
        pending=base/'result.json.tmp';pending.write_text(json.dumps(result,indent=2)+'\n');pending.replace(base/'result.json')
    before=state();(base/'state-before.json').write_text(json.dumps(before,indent=2)+'\n');save()
    try:
        run_identity_commands(inputs,base,git,explicit,state,before,result,save)
        result.update(status='PASS',state_preserved=True);save()
        audit_identity(base)
    except BaseException as exc:
        result.update(status='FAIL',error=repr(exc));save()
        raise


def run_identity_commands(inputs,base,git,explicit,state,before,result,save):
    for index,label in enumerate(('default','explicit','explicit','default','identity-default','identity-explicit')):
        command=(explicit if 'explicit' in label else git)+(['var','GIT_COMMITTER_IDENT'] if label.startswith('identity') else ['reset','--mixed'])
        prefix=base/f'{index}-{label}';stop=Path(str(prefix)+'.stop')
        env=dict(os.environ,GIT_TRACE2_EVENT=str(prefix)+'.events.jsonl')
        with Path(str(prefix)+'.stdout').open('wb') as stdout,Path(str(prefix)+'.stderr').open('wb') as stderr:
            proc=subprocess.Popen([str(inputs/'command-clock'),str(prefix)+'.clock.json']+command,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
            errors=[]
            def observe():
                try:sample(proc.pid,Path(str(prefix)+'.process.jsonl'),stop)
                except BaseException as exc:errors.append(repr(exc))
            observer=threading.Thread(target=observe);observer.start();timed_out=False
            try:
                status=proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                timed_out=True
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=5)
                stop.touch();observer.join(timeout=5)
        clock_path=Path(str(prefix)+'.clock.json')
        record=dict(label=label,prefix=prefix.name,command=command,exit_code=proc.returncode,
            clock=json.loads(clock_path.read_text()) if clock_path.exists() else None,
            observer_stopped=not observer.is_alive(),observer_error=errors or None,timed_out=timed_out,
            **{suffix+'_sha256':hashlib.sha256(Path(str(prefix)+'.'+suffix).read_bytes()).hexdigest() for suffix in ('stdout','stderr')})
        result['records'].append(record);save()  # retain this command even if validation fails
        record['state_after']=state();save()
        assert record['state_after']==before, 'HEAD, index tree or working diff changed'
        record['command_result']=validate_identity_record(base,record);save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=int)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--stop',type=Path)
    parser.add_argument('--identity-probe',type=Path)
    args = parser.parse_args()
    if args.identity_probe:
        identity_probe(args.identity_probe)
    else:
        if not all((args.parent,args.output,args.stop)):
            parser.error('process sampling requires --parent, --output and --stop')
        sample(args.parent,args.output,args.stop)
