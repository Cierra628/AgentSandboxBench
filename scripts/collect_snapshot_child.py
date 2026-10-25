"""One owned sampler process per child, keeping BPF query maps independent."""
import argparse,json,os,signal,subprocess,time,traceback
from pathlib import Path
from live_task_cache import LiveTaskCache
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--container',required=True)
    p.add_argument('--config-home',type=Path,required=True);p.add_argument('--side',choices=['child-a','child-b'],required=True)
    p.add_argument('--provider-cache',action='store_true')
    p.add_argument('--batch-lookup',action='store_true')
    p.add_argument('--expected-guest-kernel',choices=('6.1.175-asb-cache-v3','6.1.175-asb-cache-v3-reclaim'))
    a=p.parse_args();d=a.output/'live-cache'/a.side
    if a.batch_lookup and not a.provider_cache:p.error('batch lookup requires provider collection')
    env=dict(os.environ,XDG_CONFIG_HOME=str(a.config_home));live=None;success=False
    def cli(label,*argv,timeout=60):
        v=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (d/(label+'.stdout')).write_bytes(v.stdout);(d/(label+'.stderr')).write_bytes(v.stderr)
        v.check_returncode();return v.stdout.decode().strip()
    def interrupted(signum,frame):raise RuntimeError('sampler interrupted: '+str(signum))
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    try:
        live=LiveTaskCache(a.output,a.container,cli,session=a.side,lower_cache=True,
            **(dict(expected_kernel=a.expected_guest_kernel) if a.expected_guest_kernel else {}),
            **(dict(provider_cache=True) if a.provider_cache else {}),
            **(dict(batch_lookup=True) if a.batch_lookup else {}))
        (d/'capture-ready').touch();deadline=time.monotonic()+120
        while not (d/'sandbox-id.txt').exists():
            assert not (d/'stop').exists(),'sampler stopped before sandbox assignment'
            assert time.monotonic()<deadline
            time.sleep(.05)
        live.start((d/'sandbox-id.txt').read_text().strip());(d/'sampling-ready').touch()
        while not (d/'stop').exists():
            assert time.monotonic()<deadline
            assert not live.error,live.error
            time.sleep(.05)
        success=True
    except BaseException:
        d.mkdir(parents=True,exist_ok=True);(d/'helper-failure.txt').write_text(traceback.format_exc())
    finally:
        if live:
            try:live.close()
            except BaseException:
                success=False;(d/'close-failure.txt').write_text(traceback.format_exc())
    print('status='+('PASS' if success else 'FAIL')+' result_dir='+str(d),flush=True)
    return 0 if success else 1

if __name__=='__main__':raise SystemExit(main())
