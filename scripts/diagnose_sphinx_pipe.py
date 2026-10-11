"""Read archived Sphinx inputs and demonstrate grep/head exit differences locally."""
import argparse
import array
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import termios
import time


def run(raw,reference,output):
    assert not output.is_relative_to(raw) and not output.is_relative_to(reference),'output must not modify archived inputs'
    output.mkdir(parents=True,exist_ok=False)
    base=raw/'prepared-input/base-files'
    with (raw/'frozen-input/segments/read_repo/actions.tsv').open() as stream:
        row=next(csv.DictReader(stream,delimiter='\t'))
    assert hashlib.sha256(row['command'].encode()).hexdigest()==row['sha256']
    tokens=shlex.split(row['command']);index=tokens.index('grep')
    argv=tokens[index:tokens.index('|')]
    assert argv[:2]==['grep','-n'] and len(argv)==4 and argv[-1]=='sphinx/domains/cpp.py' and tokens[-3:]==['|','head','-80']
    complete=subprocess.check_output(argv,cwd=base,timeout=3)
    expected=b''.join(complete.splitlines(keepends=True)[:80])
    archived=(reference/'rollouts/backbone/read_repo/stdout/001.log').read_bytes()
    assert b''.join(archived.splitlines(keepends=True)[-80:])==expected
    assert len(complete)<65536,'producer-first case needs all output to fit in its private pipe'
    cases=[]
    for name,capacity,wait_first in [('producer_finishes_first',65536,True),('reader_closes_first',4096,False)]:
        read_fd,write_fd=os.pipe()
        fcntl.fcntl(read_fd,fcntl.F_SETPIPE_SZ,capacity)
        stream=os.fdopen(read_fd,'rb')
        child=subprocess.Popen(argv,cwd=base,stdout=write_fd,stderr=subprocess.PIPE)
        os.close(write_fd)
        try:
            if wait_first:child.wait(timeout=3)
            else:
                assert len(expected)<=capacity<len(complete)
                end=time.monotonic()+3
                while True:
                    buffered=array.array('i',[0]);fcntl.ioctl(stream,termios.FIONREAD,buffered,True)
                    if buffered[0]>=len(expected):break
                    assert time.monotonic()<end and child.poll() is None
                    time.sleep(.001)
                # Freeze this owned producer while head consumes the available
                # first 80 lines; only resume after the reader has closed.
                child.send_signal(signal.SIGSTOP)
                while Path(f'/proc/{child.pid}/stat').read_text().rsplit(')',1)[1].split()[0]!='T':
                    assert time.monotonic()<end;time.sleep(.001)
            head=subprocess.run(['head','-80'],stdin=stream,capture_output=True,timeout=3)
            stream.close()
            if not wait_first:child.send_signal(signal.SIGCONT)
            code=child.wait(timeout=3);err=child.stderr.read()
            assert head.returncode==0 and head.stdout==expected and not err
            cases.append(dict(case=name,private_pipe_capacity_bytes=capacity,producer_returncode=code,
                head_returncode=head.returncode,shell_pipefail_equivalent_exit=128-code if code<0 else code,
                stdout_sha256=hashlib.sha256(head.stdout).hexdigest()))
        finally:
            stream.close()
            if child.poll() is None:child.kill();child.wait(timeout=3)
    (output/'observations.json').write_text(json.dumps(cases,indent=2)+'\n')
    assert [r['producer_returncode'] for r in cases]==[0,-signal.SIGPIPE]
    report=dict(status='PASS',scope='owned host subprocesses, original pinned source and grep/head pattern; pipe capacity and start order intentionally differ; not a guest replay or monitoring comparison',
        action_sha256=row['sha256'],source_sha256=hashlib.sha256((base/'sphinx/domains/cpp.py').read_bytes()).hexdigest(),
        grep_argv=argv,grep_output_bytes=len(complete),head_output_bytes=len(expected),head_stdout_matches_archived_action=True,cases=cases,
        limits=['Guest PIPESTATUS was not recorded in the failing task. This demonstrates a mechanism producing identical output with 0/141, not the exact guest schedule or attribution to monitoring.',
            'Frozen commands, expected exits, global cache, sysctl and running services were not changed.'])
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print('status=PASS same_output=true producer_exits=0,-13 shell_pipefail_exits=0,141 result_dir='+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('raw','reference','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();run(args.raw.resolve(),args.reference.resolve(),args.output.resolve())
