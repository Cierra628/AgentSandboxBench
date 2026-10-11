"""Diagnostic-only full replays: guest CPU times and identity-checked host vCPU samples."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import traceback
from experiment_disk_guard import writer_lock
from run_exp1_monitor_balanced import run_one

ROOT = Path(__file__).resolve().parents[1]


def proc_stat(text):
    fields = text[text.rindex(')')+2:].split()
    return dict(user_ticks=int(fields[11]), system_ticks=int(fields[12]),
                start_ticks=int(fields[19]), cpu=int(fields[36]))


def cgroup(pid):
    return next(line.split(':',2)[2] for line in (Path('/proc')/str(pid)/'cgroup').read_text().splitlines() if line.startswith('0::'))


def matching_vm(sid, service_group):
    matches=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            if (proc/'comm').read_text().strip()!='firecracker': continue
            group=cgroup(proc.name)
            if group!=service_group and not group.startswith(service_group.rstrip('/')+'/'): continue
            args=(proc/'cmdline').read_bytes().decode().split('\0')
            api=args[args.index('--api-sock')+1]
            connection=http.client.HTTPConnection('localhost',timeout=.3)
            connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            connection.sock.settimeout(.3)
            try:
                connection.sock.connect(str(proc/'root'/api.lstrip('/')))
                connection.request('GET','/mmds')
                response=connection.getresponse()
                # Never persist MMDS, which contains authentication metadata.
                metadata=json.loads(response.read(1024*1024))
                if response.status==200 and metadata.get('instanceID')==sid:
                    matches.append(int(proc.name))
            finally:
                connection.close()
        except (OSError,ValueError,http.client.HTTPException):
            continue
    assert len(matches)<=1, 'ambiguous VM identity'
    return matches[0] if matches else None


def observe(directory, service_group, stop, result):
    pid=None
    samples=[]
    started=time.monotonic()
    try:
        while not stop.is_set():
            if pid is None:
                ids=list((directory/'raw').glob('*/sandbox-id.txt'))
                if ids:
                    sid=ids[0].read_text().strip()
                    pid=matching_vm(sid,service_group)
                    if pid:
                        result.update(pid=pid,sandbox_id=sid,identity='MMDS instanceID and service cgroup match',
                                      start_ticks=proc_stat((Path('/proc')/str(pid)/'stat').read_text())['start_ticks'])
                stop.wait(.1)
                continue
            begin=time.monotonic_ns()
            threads=[]
            for task in (Path('/proc')/str(pid)/'task').glob('*'):
                try:
                    name=(task/'comm').read_text().strip()
                    if 'vcpu' not in name.lower(): continue
                    row=proc_stat((task/'stat').read_text())
                    row.update(tid=int(task.name),name=name,schedstat=(task/'schedstat').read_text().strip())
                    freq=Path(f'/sys/devices/system/cpu/cpu{row["cpu"]}/cpufreq/scaling_cur_freq')
                    row['reported_frequency_khz']=int(freq.read_text()) if freq.exists() else None
                    row['allowed_cpus']=sorted(os.sched_getaffinity(int(task.name)))
                    threads.append(row)
                except (OSError,ValueError):
                    continue
            samples.append(dict(host_realtime_ns=time.time_ns(),monotonic_ns=begin,threads=threads,
                                collection_ns=time.monotonic_ns()-begin))
            stop.wait(.05)
        result['status']='PASS' if pid and any(s['threads'] for s in samples) else 'FAIL'
    except BaseException:
        result.update(status='FAIL',error=traceback.format_exc())
    finally:
        result.update(samples=samples,observer_elapsed_seconds=time.monotonic()-started,
                      scheduler_wait_valid=False,frequency_scope='scaling_cur_freq on last observed CPU; not per-thread effective frequency')
        (directory/'host-cpu.json').write_text(json.dumps(result,indent=2)+'\n')


def instrument(text):
    old='''  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?'''
    new='''  # Diagnostic variant: original action, stdout/stderr and exit status preserved.
  TIMEFORMAT=$'%R\\t%U\\t%S'
  cat /proc/stat > "$out/cpu-$step-before.stat"
  date +%s%N > "$out/cpu-$step-start-ns.txt"
  { time (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action" \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"); } 2> "$out/cpu-$step-time.tsv"
  status=$?
  date +%s%N > "$out/cpu-$step-end-ns.txt"
  cat /proc/stat > "$out/cpu-$step-after.stat"'''
    assert text.count(old)==1, 'unsupported guest runner version'
    return text.replace(old,new)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=int,choices=(1,4),default=1)
    args=parser.parse_args()
    os.umask(0o077)
    out=ROOT/'.artifacts/exp1-cpu-diagnostic'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    shutil.copy2(__file__,out/'driver.py')
    result=dict(status='FAIL',runs=[],purpose='diagnostic only; no monitoring overhead acceptance',
                host_memory_sampling=False,guest_memory_sampling=False,diagnostic_cpu_observation=True,
                sched_schedstats=Path('/proc/sys/kernel/sched_schedstats').read_text().strip(),
                cache_policy='no cache clearing; fresh VM each replay',host_sample_interval_seconds=.05)
    (out/'protocol.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        with writer_lock():
            bundle=out/'workspace'
            with (out/'prepare.log').open('w') as log:
                subprocess.run(['python3',str(ROOT/'scripts/prepare_exp1_clean_directory.py'),str(bundle)],
                               stdout=log,stderr=subprocess.STDOUT,check=True,timeout=150)
            cfg=json.loads((bundle/'configs/exp1-agentenv.json').read_text())
            cfg['repository']=str(bundle/'IncrementalDAX_moti')
            runner=Path(cfg['repository'])/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
            original=runner.read_text()
            runner.write_text(instrument(original))
            subprocess.run(['bash','-n',str(runner)],check=True,timeout=10)
            (out/'guest-runner-original.sh').write_text(original)
            shutil.copy2(runner,out/'guest-runner-diagnostic.sh')
            cid=(Path(cfg['server_result'])/'container-id.txt').read_text().strip()
            service_pid=subprocess.check_output(['docker','inspect','--format','{{.State.Pid}}',cid],text=True,timeout=10).strip()
            service_group=cgroup(service_pid)
            for number in range(1,args.runs+1):
                directory=out/f'run-{number:02}-off'
                stop=threading.Event()
                observed={}
                worker=threading.Thread(target=observe,args=(directory,service_group,stop,observed))
                worker.start()
                try:
                    row=run_one(out,cfg,number,'off')
                finally:
                    stop.set()
                    worker.join(timeout=10)
                    assert not worker.is_alive()
                result['runs'].append(row)
                assert observed['status']=='PASS', 'host identity or vCPU observation failed'
                guest=Path(row['raw_dir'])/'replay'
                row['cpu_times']={step:dict(zip(('wall_seconds','user_seconds','system_seconds'),
                    map(float,(guest/f'cpu-{step}-time.tsv').read_text().split()))) for step in ('023','024')}
                row['host_observer_status']=observed['status']
                row['diagnostic_guest_runner_sha256']=hashlib.sha256(runner.read_bytes()).hexdigest()
                (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'status=REPLAY_PASS run={number}/{args.runs} result_dir={out}',flush=True)
            result['status']='PASS'
    except BaseException:
        (out/'failure.txt').write_text(traceback.format_exc())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
