"""Guest-only bounded diagnosis of find/proc errors and sampler process churn."""
import argparse
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time


def process_snapshot(proc=Path('/proc')):
    rows={}
    for directory in proc.iterdir():
        if not directory.name.isdigit():continue
        try:
            text=(directory/'stat').read_text()
            fields=text.rsplit(')',1)[1].split()
            rows[directory.name]=dict(state=fields[0],ppid=int(fields[1]),
                start_ticks=int(fields[19]),command=text.split('(',1)[1].rsplit(')',1)[0])
        except (FileNotFoundError,ProcessLookupError):continue
    return rows


def error_processes(lines,before,after):
    pids=set(re.findall(r'/proc/(\d+)', '\n'.join(lines)))
    return {pid:dict(before=before.get(pid),after=after.get(pid)) for pid in sorted(pids,key=int)}


def pipeline_footer(command):
    return command+'; captured=("$?" "${PIPESTATUS[@]}"); printf "ASB_PIPE_STATUS %s\\n" "${captured[*]}" >&2'


def pipeline_probe(runner,output):
    output.mkdir(exist_ok=False)
    with (runner.parent/'segments/trace_index_state/actions.tsv').open() as stream:
        command=next(r['command'] for r in csv.DictReader(stream,delimiter='\t') if r['step']=='002')
    readfd,writefd=os.pipe()
    try:capacity=fcntl.fcntl(writefd,fcntl.F_GETPIPE_SZ)
    finally:os.close(readfd);os.close(writefd)
    kernel={name:(Path('/proc/sys/fs')/name).read_text().strip()
            for name in ('pipe-max-size','pipe-user-pages-soft','pipe-user-pages-hard')}
    cases=[]
    for index in range(12):
        # Capture $? and PIPESTATUS in one assignment before a builtin resets them.
        probe=pipeline_footer(command)
        r=subprocess.run(['bash','-o','pipefail','-c',probe],capture_output=True,text=True,timeout=15)
        assert r.returncode==0 and r.stderr.startswith('ASB_PIPE_STATUS ')
        status=list(map(int,r.stderr.strip().split()[1:]));assert len(status)==3
        (output/(str(index)+'-stdout.log')).write_text(r.stdout)
        cases.append(dict(index=index,original_exit_code=status[0],cat_exit_code=status[1],head_exit_code=status[2],stdout_sha256=hashlib.sha256(r.stdout.encode()).hexdigest()))
    result=dict(status='RECORDED',probe='pipeline',cases=cases,pipe_capacity_bytes=capacity,
        kernel_release=os.uname().release,pipe_sysctls=kernel,command=command,
        command_sha256=hashlib.sha256(command.encode()).hexdigest(),
        scope='12 original pipeline command probes; diagnostic footer captures final pipeline statuses; not frozen replay acceptance')
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


def run(runner,output):
    output.mkdir(exist_ok=False)
    text=runner.read_text()
    memory=text[text.index('sample_memory() {'):text.index('\nsample_vmstat() {')]
    monitor=text[text.index('monitor_step() {'):text.index('\nsample_memory baseline')]
    script=output/'shell-sampler.sh'
    script.write_text('sandbox_label=probe; branch=probe; round=probe; sample_interval=0.1; out='+str(output)+'\n'+memory+'\n'+monitor+'\nmonitor_step 001\n')
    rows=[]
    # Run the non-spawning reader first: shell child exits can leave /proc
    # entries behind and contaminate every subsequent control in this VM.
    for index,mode in enumerate(['none','persistent']*3+['none','shell']*3):
        process=None;stop=threading.Event();thread=None
        def persistent():
            with (output/(str(index)+'-persistent-memory.tsv')).open('x') as stream:
                while not stop.is_set():
                    stream.write(str(time.time_ns())+'\t'+Path('/proc/meminfo').read_text().replace('\n',';')+'\n');stream.flush();stop.wait(.1)
        try:
            if mode=='shell':process=subprocess.Popen(['bash',str(script)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
            elif mode=='persistent':thread=threading.Thread(target=persistent);thread.start()
            time.sleep(.2)
            before=process_snapshot()
            begin=time.monotonic_ns()
            r=subprocess.run(['bash','-o','pipefail','-c','find / -maxdepth 5 -name "numpy" -type d | head'],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=15)
            (output/(str(index)+'-stdout.log')).write_text(r.stdout)
            (output/(str(index)+'-stderr.log')).write_text(r.stderr)
            assert r.returncode in (0,1) and len(r.stderr)<65536
            after=process_snapshot();errors=r.stderr.splitlines()
            rows.append(dict(index=index,mode=mode,exit_code=r.returncode,elapsed_ns=time.monotonic_ns()-begin,
                             error_lines=errors,stdout=r.stdout,processes_before=before,processes_after=after,
                             error_processes=error_processes(errors,before,after)))
        finally:
            if process:
                os.killpg(process.pid,signal.SIGTERM);process.communicate(timeout=5)
            if thread:stop.set();thread.join(timeout=5);assert not thread.is_alive()
            time.sleep(.2)
    result=dict(status='RECORDED',cases=rows,scope='12 short find probes; original sampling functions versus persistent reader; removes only stderr suppression to expose errno; not task replay')
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runner',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--probe',choices=['find','pipeline'],default='find')
    args=parser.parse_args()
    (pipeline_probe if args.probe=='pipeline' else run)(args.runner,args.output)
