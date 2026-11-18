"""Run fixed Django host-only creation controls in one owned service and slice."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

from isolated_storage import GIB,available,capacity_budget
from run_c4_sampling_controls import source_state
from run_light_monitor_controls import SOURCE

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')


def inspect_result(storage):
    outer=json.loads((storage/'result.json').read_text());raw=storage/'external-creation'
    value=json.loads((raw/'result.json').read_text())
    assert outer['status']==value['status']=='PASS' and value['actions']==0
    assert [r['mode'] for r in value['runs']]==['off','on','on','off'] and len(set(value['guests']))==5
    assert value['guest_sampling']=='NOT_REQUESTED' and value['cleanup']['known_guests_deleted'] and value['cleanup']['service_sandbox_list_empty']
    assert all(not r['log_phases']['converted_layer_copy_started'] for r in value['runs'])
    assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    assert all(outer['checks'][k]=='PASS' for k in ('host_only_creation_controls','actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation'))
    assert outer['host_file_residency']['status']=='NOT_REQUESTED'
    assert not (storage/'external-workload').exists() and not (raw/'runtime-monitor.json').exists()
    from host_creation_controls import ORDER
    for i,mode in enumerate(ORDER,1):
        row=value['runs'][i-1];path=raw/f'run-{i}-{mode}/host'
        if mode=='off':assert row['collector']['status']=='NOT_REQUESTED' and not path.exists()
        else:assert row['collector']['status']=='PASS' and row['collector']['samples']>0 and (path/'memory.tsv').exists() and not (path/'pss.jsonl').exists()
    pairs=[]
    for number in (1,2):
        selected={r['mode']:r for r in value['runs'][(number-1)*2:number*2]}
        off=selected['off']['creation']['duration_ns'];on=selected['on']['creation']['duration_ns']
        pairs.append(dict(pair=number,off_ns=off,on_ns=on,delta_ns=on-off,change_percent=(on/off-1)*100))
    local_input={}
    if 'django_oci_input' in outer:
        assert outer['django_oci_input']['status']=='VALIDATED' and outer['django_oci_input']['read_only_bind_verified']
        local_input['django_oci_input']=outer['django_oci_input']
    return dict(status='PASS',scope='One preparation, then four prepared-image creation calls; host sampling only, no workload',
        task_id=value['task_id'],image=value['image'],actions=0,created_guests=5,pairs=pairs,raw=str(raw),storage=str(storage),
        preparation=value['preparation'],runs=value['runs'],cleanup=outer['cleanup'],**local_input)


def grouped(args):
    from host_file_monitor import measurement_group
    group,_=measurement_group()
    assert os.environ.get('ASB_HOST_FILE_SAMPLING')=='off'
    out=args.output;out.mkdir(parents=True,exist_ok=False);started=False
    state=dict(status='FAIL',stage='precheck',cgroup=str(group),actions=0)
    write(out/'plan.json',dict(preparation_guests=1,measured_guests=4,order=['off','on','on','off'],volume_gib=32,guest_memory_mib=8192,guest_cpu=2,guest_sampling=False,actions=0))
    try:
        budget=capacity_budget(available(ROOT),32*GIB);assert budget['ready'],budget
        memory=int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024
        assert memory>64*GIB
        write(out/'resource-check.json',dict(budget=budget,mem_available_bytes=memory,load_average=os.getloadavg()))
        state['source_before']=source_state();assert not state['source_before']['running']
        assert not subprocess.check_output(['docker','ps','-q'],text=True,timeout=10).strip()
        assert subprocess.run(['pgrep','-x','firecracker'],capture_output=True,timeout=10).returncode==1
        code=out/'code';code.mkdir()
        for name in ('run_host_creation_controls.py','host_creation_controls.py','validate_isolated_agentenv.py','host_file_monitor.py','agentenv_cold_start.py'):
            shutil.copy2(ROOT/'scripts'/name,code/name)
        if getattr(args,'oci_input',None):
            # Full content validation is owned by the native entry before volume allocation.
            from django_oci import owned,IMAGE as django_image
            prepared=owned(args.oci_input);info=json.loads((prepared/'result.json').read_text())
            if info['status']!='PASS' or info['image']!=django_image:raise ValueError('complete fixed OCI input required before source start')
        started=True
        with (out/'source-start.log').open('x') as log:subprocess.run(['docker','start',SOURCE],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=30)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+90
        while True:
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=3) as r:
                    if r.status in (200,204):break
            except OSError:pass
            if time.monotonic()>deadline:raise TimeoutError('source health')
            time.sleep(1)
        state['stage']='isolated-creation-controls';write(out/'result.json',state)
        command=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window',
            '--external-workload-config',str(ROOT/'configs/external-django16819-light.json'),
            '--external-workload-upstream',str(args.upstream.resolve()),'--external-workload-sampling','off',
            '--external-creation-controls','--host-file-monitor','--host-current-only']
        if getattr(args,'oci_input',None):command+=['--external-creation-oci-input',str(args.oci_input.resolve())]
        write(out/'command.json',command)
        with (out/'native.log').open('x') as log:
            result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,ASB_AGENTENV_SOURCE_SERVICE=SOURCE),stdout=log,stderr=subprocess.STDOUT,timeout=1050)
        found={Path(p) for p in re.findall(r'status=(?:PASS|FAIL|RUNNING) result_dir=(\S+)',(out/'native.log').read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(found)==1:state['storage']=str(found.pop())
        assert result.returncode==0,'isolated creation controls failed; retain fixture and inspect first boundary'
        accepted=inspect_result(Path(state['storage']));write(out/'summary.json',accepted)
        state.update(status='PASS',stage='complete',created_guests=5)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if started:
            try:
                source_state()
                with (out/'source-stop.log').open('x') as log:subprocess.run(['docker','stop','-t','10',SOURCE],check=True,stdout=log,stderr=subprocess.STDOUT,timeout=30)
            except Exception as error:state.update(status='FAIL',source_cleanup_error=repr(error))
        state['source_after']=source_state();state['available_after_bytes']=available(ROOT)
        if state['source_after']['running']:state['status']='FAIL'
        write(out/'result.json',state)
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--upstream',type=Path,required=True);p.add_argument('--inside-group',action='store_true');p.add_argument('--oci-input',type=Path,help='Complete pinned Django disk OCI input; only this explicit mode changes transport');args=p.parse_args()
    args.output=args.output.resolve()
    if not args.output.is_relative_to(ROOT/'.artifacts') or args.output.exists():raise ValueError('new artifacts output required')
    if args.inside_group:return grouped(args)
    if os.geteuid()!=0:raise RuntimeError('root required')
    unit='asbhost-'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid());slice_name=unit+'.slice'
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,'--slice='+slice_name,
        '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
        '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,'--setenv=ASB_HOST_FILE_SAMPLING=off']
    for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
        if key in os.environ:command+=['--setenv='+key]
    command+=[sys.executable,'-B',str(Path(__file__).resolve()),'--output',str(args.output),'--upstream',str(args.upstream.resolve()),'--inside-group']
    if args.oci_input:command+=['--oci-input',str(args.oci_input.resolve())]
    try:return subprocess.run(command,timeout=1200).returncode
    finally:subprocess.run(['systemctl','stop',unit+'.service',slice_name],capture_output=True,check=False,timeout=150)


if __name__=='__main__':raise SystemExit(main())
