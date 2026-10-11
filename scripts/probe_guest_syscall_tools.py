"""Read-only tool/capability check in one newly created owned sandbox."""
import argparse
import hashlib
import shutil
import json
import os
from pathlib import Path
import subprocess
import time
from cpu_frequency_lease import save
from experiment_disk_guard import writer_lock

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--host-bundle',action='store_true');parser.add_argument('--kernel-tracing',action='store_true');parser.add_argument('--mount-tracefs',action='store_true',help='Test tracefs only in the newly created disposable guest');args=parser.parse_args()
    out=ROOT/'.artifacts/guest-syscall-tools'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}');out.mkdir(parents=True)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(ROOT/'runtime/config'))
    sid=None;result=dict(status='FAIL',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),arguments=vars(args),free_bytes_before=shutil.disk_usage(ROOT).free)
    (out/'probe_guest_syscall_tools.py').write_bytes(Path(__file__).read_bytes())
    def call(label,args,timeout=30):
        p=subprocess.run(args,env=env,capture_output=True,text=True,timeout=timeout)
        (out/(label+'.stdout')).write_text(p.stdout);(out/(label+'.stderr')).write_text(p.stderr)
        p.check_returncode();return p.stdout.strip()
    try:
        with writer_lock():
            assert shutil.disk_usage(ROOT).free > 32*1024**3, 'insufficient disk reserve'
            assert json.loads(call('list-before',['aenv','list','--output','json']))==[]
            cfg=json.loads((ROOT/'configs/exp1-monitor-calibration.json').read_text())
            sid=call('create',['aenv','start','--cold',cfg['workload_image'],'--detach','--timeout','120','--cpu','2','--memory','4096'],90)
            (out/'sandbox-id.txt').write_text(sid+'\n')
            for _ in range(30):
                try:call('ready',['aenv','exec',sid,'true'],5);break
                except subprocess.CalledProcessError:time.sleep(.2)
            script='''set -eu
printf 'kernel='; uname -r
printf 'git='; git --version
printf 'strace='; command -v strace || true
printf 'perf='; command -v perf || true
if command -v strace >/dev/null; then
  strace --version | head -1
  strace -qq -f -T -ttt -o /workspace/syscall-probe.log -e trace=openat,read,getdents64,newfstatat /bin/true
  printf 'ptrace_probe=PASS\n'
  cat /workspace/syscall-probe.log
else
  printf 'ptrace_probe=NOT_RUN\n'
fi'''
            if args.host_bundle:
                from guest_syscall_bundle import prepare
                result['bundle']=prepare(out/'syscall-tools')
                call('upload',['aenv','upload',sid,str(out/'syscall-tools'),'/workspace/syscall-tools'])
                call('chmod',['aenv','exec',sid,'chmod','+x','/workspace/syscall-tools/run-strace','/workspace/syscall-tools/ld-linux-x86-64.so.2','/workspace/syscall-tools/strace'])
                script=script.replace("command -v strace", "test -x /workspace/syscall-tools/run-strace").replace('strace --version','/workspace/syscall-tools/run-strace --version').replace('strace -qq','/workspace/syscall-tools/run-strace -qq')
            if args.kernel_tracing:
                script+='''
for path in /sys/kernel/tracing /sys/kernel/debug/tracing /sys/kernel/btf/vmlinux /proc/config.gz; do
  if test -e "$path"; then printf 'exists=%s\\n' "$path"; else printf 'missing=%s\\n' "$path"; fi
done
printf 'trace_mounts='; awk '$3 == "tracefs" || $3 == "debugfs" { print $2,$3 }' /proc/mounts
for path in /sys/kernel/tracing/events/syscalls/sys_enter_getdents64/id /sys/kernel/debug/tracing/events/syscalls/sys_enter_getdents64/id; do
  if test -r "$path"; then printf 'syscall_event='; cat "$path"; fi
done
printf 'perf_event_paranoid='; cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true
'''
            if args.mount_tracefs:
                script+='''
printf '\nregistered_filesystems:\n'; cat /proc/filesystems
printf 'identity='; id
mkdir -p /workspace/tracefs-probe
if mount -t tracefs tracefs /workspace/tracefs-probe; then
  trap 'umount /workspace/tracefs-probe' EXIT
  printf 'tracefs_mount=PASS\n'
  for event in syscalls/sys_enter_getdents64 syscalls/sys_exit_getdents64 syscalls/sys_enter_newfstatat syscalls/sys_exit_newfstatat sched/sched_switch; do
    if test -r "/workspace/tracefs-probe/events/$event/id"; then
      printf 'event=%s id=' "$event"; cat "/workspace/tracefs-probe/events/$event/id"
    else printf 'event_missing=%s\n' "$event"; fi
  done
  umount /workspace/tracefs-probe
  trap - EXIT
  printf 'tracefs_unmount=PASS\n'
else
  printf 'tracefs_mount=UNAVAILABLE\n'
fi
printf 'tracing_symbols:\n'
awk '$3 ~ /^(trace_sys_enter|trace_sys_exit|bpf_prog_load|perf_event_open|__x64_sys_bpf|__x64_sys_perf_event_open|tracing_init|event_trace_init)$/ {print $2,$3}' /proc/kallsyms
'''
            output=call('probe' ,['aenv','exec',sid,'bash','-lc',script])
            if args.mount_tracefs:
                result['tracefs_mount']='PASS' if 'tracefs_mount=PASS' in output else 'UNAVAILABLE'
                result['tracefs_registered']='nodev\ttracefs' in output
                result['event_collection']='NOT_VALIDATED'
            result.update(status='PASS',capability='AVAILABLE' if 'ptrace_probe=PASS' in output else 'MISSING',sandbox_id=sid)
    except Exception as exc:result['error']=repr(exc)
    finally:
        if sid:
            try:
                call('delete',['aenv','delete',sid],90)
                assert not any(x['sandboxID']==sid for x in json.loads(call('list-after',['aenv','list','--output','json'])))
                result['cleanup']='PASS'
            except Exception as exc:result.update(status='FAIL',cleanup_error=repr(exc))
        result['free_bytes_after']=shutil.disk_usage(ROOT).free
        save(out/'result.json',result)
    print(f"status={result['status']} capability={result.get('capability','UNKNOWN')} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
