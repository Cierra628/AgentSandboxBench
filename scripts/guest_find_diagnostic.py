"""Capture an unchanged shell action and its find/head errors separately."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile
import os
import threading
import time


def error_processes(errors,proc_root=Path('/proc')):
    rows=[]
    for pid in sorted(set(re.findall(r'/proc/(\d+)(?:/|\b)',errors)),key=int):
        record=dict(pid=int(pid))
        try:
            stat=(proc_root/pid/'stat').read_text();fields=stat[stat.rfind(')')+2:].split()
            record.update(comm=stat[stat.find('(')+1:stat.rfind(')')],state=fields[0],parent_pid=int(fields[1]),start_ticks=fields[19])
        except FileNotFoundError:record['status']='DISAPPEARED_AFTER_FIND'
        except (OSError,ValueError,IndexError) as error:record['read_error']=str(error)
        rows.append(record)
    return rows


def capture(command, cwd):
    tail='2>/dev/null | head'
    assert command.endswith(tail), 'expected the original find/head pipeline'
    with tempfile.TemporaryDirectory(prefix='asb-find-',dir=cwd) as temp:
        directory=Path(temp)
        original=subprocess.run(['bash','-o','pipefail','-c',command],cwd=cwd,
            capture_output=True,text=True,timeout=15)
        # Keep find arguments and head unchanged. Save PIPESTATUS immediately,
        # before another command overwrites it; diagnostics are not replay data.
        diagnostic=command[:-len(tail)]+'2>"$1/errors" | head; codes=("${PIPESTATUS[@]}"); printf "%s\\n" "${codes[@]}" > "$1/codes"'
        probe=subprocess.run(['bash','-o','pipefail','-c',diagnostic,'asb-find',temp],cwd=cwd,
            capture_output=True,text=True,timeout=15)
        codes=[int(value) for value in (directory/'codes').read_text().splitlines()]
        assert len(codes)==2 and probe.returncode==0
        return dict(status='CAPTURED',command_sha256=hashlib.sha256(command.encode()).hexdigest(),
            cwd=str(cwd),original_exit_code=original.returncode,original_stdout=original.stdout,
            original_stderr=original.stderr,diagnostic_stdout=probe.stdout,diagnostic_stderr=probe.stderr,
            pipeline_exit_codes=codes,find_stderr=(directory/'errors').read_text(),
            error_processes_after_find=error_processes((directory/'errors').read_text()),
            scope='two consecutive commands; diagnostic exposes suppressed errors, not a strict replay or timing comparison')


def transient_control(command,cwd):
    """Finite owned children with delayed wait; not a replay or timing result."""
    quiet_before=[capture(command,cwd) for _ in range(3)]
    stop=threading.Event();children=[];errors=[];lock=threading.Lock()
    def churn():
        try:
            while not stop.is_set():
                child=subprocess.Popen(['/bin/sleep','0.005'])
                with lock:children.append(child)
                try:
                    # Model a parent that does other work before wait(). Every
                    # child is still waited for, including on interruption.
                    stop.wait(0.025)
                finally:child.wait(timeout=5)
        except BaseException as error:
            errors.append(repr(error));stop.set()
    workers=[threading.Thread(target=churn) for _ in range(2)]
    for worker in workers:worker.start()
    try:
        active=[capture(command,cwd) for _ in range(8)]
    finally:
        stop.set()
        for worker in workers:worker.join(timeout=6)
    assert not errors and all(not worker.is_alive() for worker in workers),errors
    assert all(child.poll() is not None for child in children)
    pids={child.pid for child in children}
    assert all(not (Path('/proc')/str(pid)).exists() for pid in pids),'owned child not reaped'
    quiet_after=[capture(command,cwd) for _ in range(3)]
    for row in active:
        for record in row['error_processes_after_find']:record['owned_control_child']=record['pid'] in pids
    return dict(status='CAPTURED',command_sha256=hashlib.sha256(command.encode()).hexdigest(),
        controller_pid=os.getpid(),workers=2,child_command='/bin/sleep 0.005',wait_delay_seconds=0.025,
        quiet_before=quiet_before,active=active,quiet_after=quiet_after,
        owned_child_count=len(children),owned_children_reaped=True,
        scope='controlled process churn and delayed wait; tests find vulnerability, cannot identify the cause of an earlier unobserved failure')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command_file',type=Path)
    p.add_argument('--transient-control',action='store_true')
    args=p.parse_args()
    probe=transient_control if args.transient_control else capture
    print(json.dumps(probe(args.command_file.read_text(),Path('/testbed'))))
