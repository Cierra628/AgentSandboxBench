"""One running child VMM: cache metadata, host PFNs and explicit file eviction."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from agentenv_cold_start import create
from experiment_memory import sample_groups
from host_sandbox_monitor import read as read_vmm
from live_task_cache import LiveTaskCache
from measure_file_physical import identity
from prettier_no_proxy import IMAGE, clean_environment

ROOT=Path(__file__).resolve().parents[1]
# Explicit charge calibration only: measured reread-to-finish was 152.558 s.
CHARGE_FINISH_SECONDS=180
CHARGE_PROCESS_SECONDS=210


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',required=True);p.add_argument('--config-home',type=Path,required=True)
    p.add_argument('--service-url',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--dynamic-file',action='store_true')
    p.add_argument('--ram-page-evidence',action='store_true')
    p.add_argument('--vmm-page-evidence',action='store_true')
    p.add_argument('--io-uring-page-evidence',action='store_true')
    p.add_argument('--host-backing-evidence',action='store_true')
    p.add_argument('--host-backing-pfn-evidence',action='store_true')
    p.add_argument('--host-backing-full-pfn-evidence',action='store_true')
    p.add_argument('--host-backing-charge-evidence',action='store_true')
    args=p.parse_args();out=args.output.resolve()
    if args.host_backing_charge_evidence and not args.host_backing_full_pfn_evidence:
        raise ValueError('page charge evidence requires explicit full-list PFNs')
    if args.host_backing_full_pfn_evidence and (not args.host_backing_evidence or args.host_backing_pfn_evidence):
        raise ValueError('full-list PFNs require backing inventory and no single-range option')
    if args.host_backing_pfn_evidence and not args.host_backing_evidence:
        raise ValueError('host backing PFNs require explicit backing evidence')
    if args.io_uring_page_evidence and not args.vmm_page_evidence:
        raise ValueError('io_uring evidence requires explicit VMM and RAM evidence')
    if args.vmm_page_evidence and not args.ram_page_evidence:
        raise ValueError('VMM mapping evidence requires explicit RAM evidence')
    assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=clean_environment(dict(os.environ,XDG_CONFIG_HOME=str(args.config_home)))
    sid=live=None;success=False;stage='capture_before_boot';events=[]
    result=dict(status='FAIL',vm_pause=False,checkpoint=False,task_id='prettier6604-child-cache-calibration',
        image=IMAGE,phases=[],file_locked_bytes=0,scope='one synthetic file plus whole guest regular/shmem cache; not the 29-action task')
    result['fixture_timeouts']=dict(finish_seconds=CHARGE_FINISH_SECONDS if args.host_backing_charge_evidence else 120,
        process_seconds=CHARGE_PROCESS_SECONDS if args.host_backing_charge_evidence else 150,
        other_phase_seconds=120)
    if args.host_backing_full_pfn_evidence:
        result['measurement_endpoint']=dict(status='UNAVAILABLE',reason='successful backing query endpoint not reached')
    def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
    def cli(label,*argv,timeout=60):
        started=time.monotonic_ns()
        process=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(process.stdout);(out/(label+'.stderr')).write_bytes(process.stderr)
        events.append(dict(stage=label,start_ns=started,duration_ns=time.monotonic_ns()-started,returncode=process.returncode))
        process.check_returncode();return process.stdout.decode().strip()
    def wait(name):
        cli('wait-'+name,'exec',sid,'bash','-lc',f'for i in $(seq 1 100); do test -f {guest}/{name} && exit 0; sleep .1; done; cat /tmp/asb-child-fixture.log; exit 1',timeout=15)
    def observe(name):
        wait(name+'.json')
        value=json.loads(cli('state-'+name,'exec',sid,'cat',guest+'/'+name+'.json'))
        write(out/(name+'.json'),value)
        if args.dynamic_file:
            cli('download-'+name+'-file','download',sid,guest+'/'+name+'-file.bin',out/(name+'-file.bin'))
        left=time.time_ns();time.sleep(3);right=time.time_ns()
        if live.error:raise RuntimeError(live.error)
        identity(live.vm,Path('/proc'),Path('/sys/fs/cgroup'))
        phase=dict(name=name,start_ns=left,end_ns=right,
            groups=sample_groups(live.capture.cgpath),vmm=read_vmm(live.vm['pid'],pss=True))
        result['phases'].append(phase)
        return value
    try:
        live_options=dict(file_cache_path='/workspace/asb-child-cache.bin',expected_kernel='6.1.175-asb-file-query-v1') if args.dynamic_file else {}
        if args.ram_page_evidence:live_options['ram_page_evidence']=True
        if args.vmm_page_evidence:live_options['vmm_page_evidence']=True
        if args.io_uring_page_evidence:live_options['io_uring_page_evidence']=True
        live=LiveTaskCache(out,args.container,cli,**live_options)
        assert live.capture.child_scope, 'explicit child-scope capture required'
        stage='diagnostic_guest_boot'
        sid=create(args.config_home,args.service_url,IMAGE,request_timeout=90,local_no_proxy=True)
        (out/'sandbox-id.txt').write_text(sid+'\n')
        cli('ready','exec',sid,'true',timeout=15)
        stage='start_live_cache';live.start(sid)
        tools=out/'tools';tools.mkdir()
        for name in ('guest_cache_child_fixture.py','kernel_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,tools/name)
        if args.dynamic_file:
            for name in ('guest_file_cache_query.py','file_cache_metadata.py'):shutil.copy2(ROOT/'scripts'/name,tools/name)
        cli('fixture-upload','upload',sid,tools,'/workspace/asb-child-tools')
        guest='/dev/shm/asb-child-fixture'
        extra=' --dynamic-file' if args.dynamic_file else ''
        if args.host_backing_charge_evidence:extra+=f' --finish-timeout-seconds {CHARGE_FINISH_SECONDS}'
        process_seconds=result['fixture_timeouts']['process_seconds']
        cli('fixture-start','exec',sid,'bash','-lc',f'nohup timeout {process_seconds} python3 -B /workspace/asb-child-tools/guest_cache_child_fixture.py {guest}{extra} >/tmp/asb-child-fixture.log 2>&1 </dev/null &')
        stage='written';observe('written')
        if args.dynamic_file:
            cli('request-evict','exec',sid,'touch',guest+'/evict');stage='evict';observe('evicted')
            cli('request-reread','exec',sid,'touch',guest+'/reread');stage='reread';observe('reread')
        else:
            cli('request-read','exec',sid,'touch',guest+'/read');stage='read';observe('read-done')
            cli('request-evict','exec',sid,'touch',guest+'/evict');stage='evict';observe('evicted')
        stage='stop_live_cache';live.close();expected_vm=live.vm;measurement_parent=live.capture.cgpath;live=None
        if args.host_backing_evidence:
            from host_backing_evidence import observe as observe_backing
            stage='host_backing_evidence'
            charge_options={}
            if args.host_backing_charge_evidence:
                from host_backing_charge_scope import private_scope
                charge_options['charge_scope']=private_scope(out,measurement_parent)
            result['host_backing_evidence']=observe_backing(args.container,out,{'private-a':sid},{'private-a':expected_vm},
                **(dict(pfn_evidence=True) if args.host_backing_pfn_evidence else {}),
                **(dict(full_pfn_evidence=True) if args.host_backing_full_pfn_evidence else {}),**charge_options)
        if args.host_backing_full_pfn_evidence:
            stage='measurement_endpoint'
            identity(expected_vm,Path('/proc'),Path('/sys/fs/cgroup'))
            result['measurement_endpoint']=dict(status='OBSERVED',
                boundary='after_backing_queries_before_fixture_finish_and_guest_delete',
                vmm_identity=expected_vm,snapshot=sample_groups(measurement_parent,diagnostics=True))
            write(out/'measurement-endpoint.json',result['measurement_endpoint'])
        stage='fixture_finish_request'
        cli('request-finish','exec',sid,'touch',guest+'/finish')
        stage='fixture_finish_wait';wait('done')
        stage='fixture_result_check'
        fixture=json.loads(cli('fixture-result','exec',sid,'cat',guest+'/result.json'))
        assert fixture['status']=='PASS';result['fixture']=fixture
        if args.dynamic_file:
            from report_dynamic_file_calibration import check
            stage='dynamic_file_evidence_check';checked=check(out,result)
            write(out/'dynamic-file-check.json',checked)
            assert checked['status']=='PASS',checked['errors']
        success=True
    except BaseException as error:
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
        if sid:
            try:cli('fixture-failure','exec',sid,'cat','/tmp/asb-child-fixture.log',timeout=5)
            except Exception:pass
    finally:
        if args.host_backing_full_pfn_evidence:
            result['guest_cleanup_start_monotonic_ns']=time.monotonic_ns()
        if live:
            try:live.close()
            except BaseException as error:success=False;result['collector_cleanup_error']=repr(error)
        if sid:
            try:cli('delete','delete',sid,timeout=90);result['vm_removed']=True
            except BaseException as error:success=False;result['vm_cleanup_error']=repr(error)
        result['status']='PASS' if success else 'FAIL'
        write(out/'events.json',events);write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
