"""Owned command CPU usage and optional process-tree observations; diagnostic only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time


def process_tree(pid):
    pending=[pid]; seen=set(); rows=[]
    inet={}
    for protocol in ('tcp','tcp6','udp','udp6'):
        try:lines=(Path('/proc/net')/protocol).read_text().splitlines()[1:]
        except FileNotFoundError:continue
        for line in lines:
            fields=line.split();inet[fields[9]]=protocol+':'+fields[3]
    while pending:
        current=pending.pop()
        if current in seen:continue
        seen.add(current); root=Path('/proc')/str(current)
        try:
            stat=(root/'stat').read_text().rsplit(')',1)[1].split()
            command=(root/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').strip()
            children=(root/'task'/str(current)/'children').read_text().split()
            pending.extend(map(int,children))
            io=dict(line.split(':',1) for line in (root/'io').read_text().splitlines())
            sockets=0;inet_states=[]
            for fd in (root/'fd').iterdir():
                try:
                    target=os.readlink(fd);sockets+=target.startswith('socket:[')
                    if target.startswith('socket:[') and target[8:-1] in inet:inet_states.append(inet[target[8:-1]])
                except FileNotFoundError:pass
            rows.append(dict(pid=current,start_ticks=stat[19],state=stat[0],
                command=command,user_ticks=int(stat[11]),system_ticks=int(stat[12]),
                wchan=(root/'wchan').read_text().strip(),socket_fds=sockets,inet_socket_states=inet_states,
                read_bytes=int(io['read_bytes']),write_bytes=int(io['write_bytes']),
                rchar=int(io['rchar']),wchar=int(io['wchar'])))
        except (FileNotFoundError,ProcessLookupError):pass
    return rows


def capture(command,output,profile=False,timeout=30,interval=.025,cwd='/testbed',disable_pip_version_check=False):
    if not 0<timeout<=120 or not .01<=interval<=1:raise ValueError('invalid diagnostic budget')
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic_ns(); before=resource.getrusage(resource.RUSAGE_CHILDREN)
    self_before=resource.getrusage(resource.RUSAGE_SELF); proc=None; timed_out=False; scans=0
    try:
        with (output/'stdout').open('xb') as stdout,(output/'stderr').open('xb') as stderr,(output/'processes.jsonl').open('x') as trace:
            env=dict(os.environ)
            if disable_pip_version_check:env['PIP_DISABLE_PIP_VERSION_CHECK']='1'
            proc=subprocess.Popen(['bash','-o','pipefail','-c',command],cwd=cwd,env=env,
                stdout=stdout,stderr=stderr,start_new_session=True)
            if profile:
                while proc.poll() is None:
                    begin=time.monotonic_ns()
                    trace.write(json.dumps(dict(monotonic_ns=begin,elapsed_ns=begin-started,
                        processes=process_tree(proc.pid)))+'\n');scans+=1
                    if (time.monotonic_ns()-started)/1e9>=timeout:raise subprocess.TimeoutExpired(command,timeout)
                    time.sleep(interval)
            else:proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid,signal.SIGTERM)
            try:proc.wait(timeout=2)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=2)
    after=resource.getrusage(resource.RUSAGE_CHILDREN);self_after=resource.getrusage(resource.RUSAGE_SELF)
    result=dict(status='TIMEOUT' if timed_out else 'CAPTURED',command_sha256=hashlib.sha256(command.encode()).hexdigest(),
        exit_code=proc.returncode if proc else None,elapsed_ns=time.monotonic_ns()-started,
        child_user_seconds=after.ru_utime-before.ru_utime,child_system_seconds=after.ru_stime-before.ru_stime,
        child_minor_faults=after.ru_minflt-before.ru_minflt,child_major_faults=after.ru_majflt-before.ru_majflt,
        child_voluntary_switches=after.ru_nvcsw-before.ru_nvcsw,child_involuntary_switches=after.ru_nivcsw-before.ru_nivcsw,
        observer_cpu_seconds=self_after.ru_utime+self_after.ru_stime-self_before.ru_utime-self_before.ru_stime,
        profile=profile,scans=scans,interval_seconds=interval if profile else None,clock_ticks=os.sysconf('SC_CLK_TCK'),
        pip_version_check_disabled=disable_pip_version_check,inherited_pip_disable_version_check=os.environ.get('PIP_DISABLE_PIP_VERSION_CHECK'),
        stdout_sha256=hashlib.sha256((output/'stdout').read_bytes()).hexdigest(),
        stderr_sha256=hashlib.sha256((output/'stderr').read_bytes()).hexdigest(),
        scope='owned shell and waited descendants; process observations may miss short-lived processes and threads; observed sockets do not prove network delay')
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--command',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--profile',action='store_true');p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--disable-pip-version-check',action='store_true',help='Diagnostic child environment only; frozen command bytes stay identical')
    p.add_argument('--cwd',default='/testbed')
    args=p.parse_args();result=capture(args.command,args.output,args.profile,args.timeout,cwd=args.cwd,disable_pip_version_check=args.disable_pip_version_check)
    print(json.dumps(result))
    # Frozen exploratory commands may return nonzero; capture success is separate.
    raise SystemExit(0 if result['status']=='CAPTURED' else 1)
