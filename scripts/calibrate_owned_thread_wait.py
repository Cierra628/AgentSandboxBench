"""Own-child calibration: compare sleeping with runnable competition on one CPU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from owned_thread_wait import WaitTrace

ROOT=Path(__file__).resolve().parents[1]
CHILD='''import os,sys,time
os.sched_setaffinity(0,{int(sys.argv[1])})
print("READY",flush=True)
mode=input()
end=time.monotonic()+.8
if mode=="busy":
 while time.monotonic()<end: pass
else: time.sleep(.8)
print("DONE",flush=True)
input()
'''


def main():
    out=ROOT/'.artifacts/vcpu-wait-calibration'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    cpu=min(os.sched_getaffinity(0));results=[]
    for mode in ('sleep','busy'):
        children=[subprocess.Popen([sys.executable,'-c',CHILD,str(cpu)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) for _ in range(2)]
        tracer=None
        try:
            for p in children:assert p.stdout.readline().strip()=='READY'
            tracer=WaitTrace([p.pid for p in children],out/mode)
            for p in children:p.stdin.write(mode+'\n');p.stdin.flush()
            for p in children:assert p.stdout.readline().strip()=='DONE'
            result=tracer.stop();tracer=None
            result['mode']=mode;results.append(result)
        finally:
            if tracer:tracer.stop()
            for p in children:
                if p.poll() is None:p.kill();p.wait(timeout=5)
    sleeping=sum(r['runnable_wait_ns'] for r in results[0]['threads'])/1e9
    competing=sum(r['runnable_wait_ns'] for r in results[1]['threads'])/1e9
    status='PASS' if sleeping<.2 and .2<competing<1.6 else 'FAIL'
    (out/'result.json').write_text(json.dumps(dict(status=status,cpu=cpu,sleep_wait_seconds=sleeping,competition_wait_seconds=competing,runs=results),indent=2)+'\n')
    print(f'status={status} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
