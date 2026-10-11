"""Read DAMON settings/statistics in one owned VM; no parameter writes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from cpu_frequency_lease import save
from experiment_disk_guard import writer_lock

ROOT=Path(__file__).resolve().parents[1]
PROBE='''set -eu
printf 'kernel='; uname -r
printf 'cmdline='; cat /proc/cmdline
for phase in before after; do
  printf 'phase=%s\\n' "$phase"
  for path in /sys/module/damon_reclaim/parameters/*; do
    test -f "$path" || continue
    printf 'damon_%s=' "${path##*/}"
    cat "$path"
  done
  printf 'memory_available_kib='
  awk '$1=="MemAvailable:" {print $2}' /proc/meminfo
  if test "$phase" = before; then sleep 2; fi
done
'''


def main():
    os.umask(0o077)
    out=ROOT/'.artifacts/guest-damon-probe'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    shutil.copy2(__file__,out/'probe_guest_damon.py');(out/'guest-probe.sh').write_text(PROBE)
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],XDG_CONFIG_HOME=str(ROOT/'runtime/config'))
    result=dict(status='FAIL',cleanup='NOT_NEEDED',scope='read-only DAMON inspection in one project-owned VM; no Exp1 replay or causal intervention',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    sid=None
    def call(label,argv,timeout=30):
        p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=timeout)
        (out/(label+'.stdout')).write_text(p.stdout);(out/(label+'.stderr')).write_text(p.stderr)
        p.check_returncode();return p.stdout.strip()
    print(f'status=RUNNING result_dir={out}',flush=True)
    with writer_lock():
        try:
            assert shutil.disk_usage(ROOT).free>32*1024**3
            cfg=json.loads((ROOT/'configs/exp1-agentenv.json').read_text())
            image=json.loads((ROOT/'configs/exp1-monitor-calibration.json').read_text())['workload_image']
            result['image']=image
            assert json.loads(call('list-before',['aenv','list','--output','json']))==[]
            sid=call('create',['aenv','start','--cold',image,'--detach','--timeout','90','--cpu',str(cfg['sandbox_cpu']),'--memory',str(cfg['sandbox_memory_mib'])],90)
            result['sandbox_id']=sid
            raw=call('probe',['aenv','exec',sid,'bash','-lc',PROBE],20)
            phases={};phase=None
            for line in raw.splitlines():
                if '=' not in line:continue
                key,value=line.split('=',1)
                if key=='phase':phase=value;assert phase not in phases;phases[phase]={}
                elif key.startswith('damon_') or key=='memory_available_kib':
                    assert phase and key not in phases[phase];phases[phase][key]=value
                elif key in ('kernel','cmdline'):result[key]=value
            assert set(phases)=={'before','after'}
            result.update(status='PASS',observations=phases)
        except Exception as exc:result['error']=repr(exc)
        finally:
            if sid:
                try:
                    call('delete',['aenv','delete',sid],90)
                    assert not any(x['sandboxID']==sid for x in json.loads(call('list-after',['aenv','list','--output','json'])))
                    result['cleanup']='PASS'
                except Exception as exc:result.update(status='FAIL',cleanup='FAIL',cleanup_error=repr(exc))
            result['free_bytes_after']=shutil.disk_usage(ROOT).free
            save(out/'result.json',result)
    print(f"status={result['status']} cleanup={result['cleanup']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
