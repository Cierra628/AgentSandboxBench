"""First-use Sphinx environment command timings; no process scans or cache controls."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time

COMMAND='cd /testbed && ls /opt/miniconda3/envs 2>/dev/null; /opt/miniconda3/bin/conda env list 2>/dev/null; pip list 2>/dev/null | grep -i -E "docutils|sphinx|pytest"'
PARTS=(('ls','cd /testbed && ls /opt/miniconda3/envs 2>/dev/null'),
       ('conda','/opt/miniconda3/bin/conda env list 2>/dev/null'),
       ('pip','pip list 2>/dev/null | grep -i -E "docutils|sphinx|pytest"'))


def stop_owned(proc):
    # Only this probe's new session; KILL also handles descendants surviving TERM.
    for sig in (signal.SIGTERM,signal.SIGKILL):
        try:os.killpg(proc.pid,sig)
        except ProcessLookupError:pass
        try:proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if sig==signal.SIGKILL:raise


def capture_parts(parts,output,*,cwd,timeout):
    if not 0<timeout<=120:raise ValueError('diagnostic timeout must be 0..120 seconds')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic_ns();observer=resource.getrusage(resource.RUSAGE_SELF);records=[];timed_out=False
    for name,command in parts:
        remaining=timeout-(time.monotonic_ns()-started)/1e9
        if remaining<=0:timed_out=True;break
        directory=output/name;directory.mkdir()
        usage=resource.getrusage(resource.RUSAGE_CHILDREN);proc=None
        try:
            with (directory/'stdout').open('xb') as stdout,(directory/'stderr').open('xb') as stderr:
                begin=time.monotonic_ns()
                proc=subprocess.Popen(['bash','-o','pipefail','-c',command],cwd=cwd,
                    stdout=stdout,stderr=stderr,start_new_session=True)
                try:proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:timed_out=True;stop_owned(proc)
                end=time.monotonic_ns()
        finally:
            if proc is not None and proc.poll() is None:stop_owned(proc)
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        records.append(dict(name=name,command=command,exit_code=proc.returncode,elapsed_ns=end-begin,
            child_user_seconds=after.ru_utime-usage.ru_utime,child_system_seconds=after.ru_stime-usage.ru_stime,
            child_minor_faults=after.ru_minflt-usage.ru_minflt,child_major_faults=after.ru_majflt-usage.ru_majflt))
        if timed_out:break
    ended=time.monotonic_ns();usage=resource.getrusage(resource.RUSAGE_SELF)
    for kind in ('stdout','stderr'):
        with (output/kind).open('xb') as stream:
            for record in records:stream.write((output/record['name']/kind).read_bytes())
    part_sum=sum(r['elapsed_ns'] for r in records)
    result=dict(status='TIMEOUT' if timed_out else 'CAPTURED',parts=records,
        exit_code=records[-1]['exit_code'] if records else None,elapsed_ns=ended-started,
        parts_elapsed_sum_ns=part_sum,between_parts_ns=ended-started-part_sum,
        observer_cpu_seconds=usage.ru_utime+usage.ru_stime-observer.ru_utime-observer.ru_stime,
        shell_invocations=len(records),process_scans=False,
        scope='Diagnostic shell wall time and waited-child counters; split adds shell startups and is not frozen-command latency')
    assert result['between_parts_ns']>=0
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('whole','split'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=float,default=90)
    args=parser.parse_args()
    value=capture_parts((('whole',COMMAND),) if args.mode=='whole' else PARTS,args.output,cwd='/testbed',timeout=args.timeout)
    value.update(mode=args.mode,inherited_pip_disable_version_check=os.environ.get('PIP_DISABLE_PIP_VERSION_CHECK'))
    (args.output/'result.json').write_text(json.dumps(value,indent=2)+'\n')
    print(json.dumps(value))
    raise SystemExit(0 if value['status']=='CAPTURED' else 1)
