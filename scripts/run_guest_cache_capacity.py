"""One private guest: capability check and continuous cache-target validation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback

from agentenv_cold_start import create
from live_task_cache import LiveTaskCache

ROOT=Path(__file__).resolve().parents[1]
KERNEL='6.1.175-asb-cache-v3-reclaim'


def main(args):
    out=args.output.resolve();assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=dict(os.environ,XDG_CONFIG_HOME=str(args.config_home))
    result=dict(status='FAIL',vm_pause=False,hard_limit=False,checks={},scope='one guest; periodic clean regular-file reclamation, 128/64/128 MiB targets, owned synthetic demand; VMM PFNs only')
    sid=None;capture=None;stage='setup';success=False
    def cli(label,*argv,timeout=60):
        proc=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(proc.stdout);(out/(label+'.stderr')).write_bytes(proc.stderr)
        proc.check_returncode();return proc.stdout.decode().strip()
    def collect():
        guest=out/'guest';guest.mkdir(exist_ok=True)
        for name in ('capabilities-before.json','result.json','samples.jsonl'):
            text=cli('collect-'+name,'exec',sid,'cat','/workspace/asb-cache-capacity/'+name)
            (guest/name).write_text(text+'\n')
    try:
        image=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())['workload_image']
        capture=LiveTaskCache(out,args.container,cli,expected_kernel=KERNEL)
        stage='boot';sid=create(args.config_home,args.service_url,image,request_timeout=90)
        (out/'sandbox-id.txt').write_text(sid+'\n')
        assert cli('kernel','exec',sid,'uname','-r')==KERNEL
        tools=out/'tools';tools.mkdir()
        for name in ('guest_cache_capacity.py','kernel_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,tools/name)
        cli('upload','upload',sid,tools,'/workspace/asb-capacity-tools')
        stage='sampler-start';capture.start(sid)
        stage='capacity-targets'
        cli('guest-capacity','exec',sid,'python3','-B','/workspace/asb-capacity-tools/guest_cache_capacity.py',
            '/workspace/asb-cache-capacity',timeout=120)
        collect();guest=json.loads((out/'guest/result.json').read_text())
        assert guest['status']=='PASS' and guest['content_checks']=='PASS'
        assert guest['damon_disabled']['kdamond_pid']==-1
        assert guest['damon_restored']['enabled']==guest['capabilities_after']['damon']['enabled']
        stage='sampler-stop';capture.close();capture=None
        result.update(status='PASS',guest_result=guest,kernel=KERNEL,
            checks=dict(guest_data_and_anonymous_shmem_guards='PASS',owned_damon_stopped_and_restored='PASS',
                        target_sequence_completed='PASS',cache_membership_and_live_host_sampling='PASS'))
        success=True
    except BaseException as exc:
        result.update(error=repr(exc),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
        if sid:
            try:collect()
            except Exception as error:result['collect_error']=repr(error)
            try:cli('guest-failure','exec',sid,'cat','/workspace/asb-cache-capacity/failure.txt')
            except Exception:pass
    finally:
        if capture:
            try:capture.close()
            except Exception as exc:success=False;result['sampler_cleanup_error']=repr(exc)
        if sid:
            try:cli('delete-owned','delete',sid,timeout=60);result['owned_guest_deleted']=True
            except Exception as exc:success=False;result['delete_error']=repr(exc)
        result['status']='PASS' if success else 'FAIL'
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('container','service-url'):parser.add_argument('--'+name,required=True)
    for name in ('config-home','output'):parser.add_argument('--'+name,type=Path,required=True)
    raise SystemExit(main(parser.parse_args()))
