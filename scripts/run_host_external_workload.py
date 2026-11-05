"""Lightweight host/guest measurement for pinned external Django/C4/Cython replays."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

from external_workload import C4,CYTHON,budgets,load
from external_guest_proxy import GuestProxy
from isolated_storage import GIB,available,capacity_budget

ROOT=Path(__file__).resolve().parents[1]


def guest_plot(raw,output,*,title='Django #16819'):
    manifest=json.loads((raw/'runtime-monitor.json').read_text());session=manifest['sessions'][0]
    assert manifest['profile']=='light' and len(manifest['sessions'])==1 and session['collector_reaped']
    with (raw/session['raw_directory']/'guest-memory.tsv').open() as f:memory=list(csv.DictReader(f,delimiter='\t'))
    with (raw/'tools.tsv').open() as f:tools=list(csv.DictReader(f,delimiter='\t'))
    assert len(memory)==session['receipt']['samples'] and session['receipt']['interval_seconds']==.1
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(11,9),constrained_layout=True)
    axes[0].bar([int(r['step']) for r in tools],[int(r['duration_ns'])/1e9 for r in tools]);axes[0].set(title=title+': tool latency',xlabel='Tool call',ylabel='Seconds')
    zero=int(memory[0]['monotonic_ns']);times=[(int(r['monotonic_ns'])-zero)/1e9 for r in memory]
    for ax,key,title in [(axes[1],'used','Guest memory: MemTotal - MemAvailable'),(axes[2],'Cached_kib','Guest Cached (includes Shmem)')]:
        values=[(int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 if key=='used' else int(r[key])/1024 for r in memory]
        ax.plot(times,values);ax.set(title=title,xlabel='Guest monotonic elapsed seconds',ylabel='MiB');ax.grid(alpha=.2)
    for suffix in ('png','svg'):fig.savefig(output/('guest.'+suffix),dpi=140)
    plt.close(fig)
    return dict(samples=len(memory),receipt=session['receipt'],guest_used_peak_mib=max((int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 for r in memory),
        guest_cached_peak_mib=max(int(r['Cached_kib'])/1024 for r in memory))


def grouped(config,upstream,sampling='on'):
    cfg,_,_=load(config,upstream)
    if sampling not in ('on','off') or sampling=='off' and cfg['task_id'] not in (C4,CYTHON,'django__django-16819'):raise ValueError('sampling off is limited to the explicit C4/Django/Cython controls')
    if os.environ.get('ASB_HOST_FILE_SAMPLING','on')!=sampling:raise ValueError('host and guest sampling modes differ')
    budget=capacity_budget(available(ROOT),32*GIB)
    if not budget['ready']:raise RuntimeError('external workload requires >58 GiB available')
    from host_file_monitor import measurement_group,HostFileMonitor
    group,_=measurement_group()
    out=ROOT/'.artifacts/host-external-workload'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid()))
    out.mkdir(parents=True);state=dict(status='FAIL',task_id=cfg['task_id'],budget=budget,sampling=sampling)
    code=out/'code';code.mkdir()
    for name in ('run_host_external_workload.py','run_external_workload.py','external_workload.py','external_guest_proxy.py','agentenv_cold_start.py','validate_isolated_agentenv.py','host_file_monitor.py','report_host_file.py'):
        shutil.copy2(ROOT/'scripts'/name,code/name)
    shutil.copy2(config,out/'config.json')
    collector=None;proxy=None
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        if sampling=='on':collector=HostFileMonitor(out/'host-file',group,include_pss=False)
        else:state.update(memory='NOT_MEASURED',collector=dict(status='NOT_REQUESTED',reason='explicit memory sampling off'))
        extra={}
        if cfg['task_id']==CYTHON:
            proxy=GuestProxy(out,upstream)
            extra['env']=dict(os.environ,ASB_EXTERNAL_GUEST_RELAY_URL=proxy.start(),ASB_EXTERNAL_GUEST_RELAY_STATE=str(out))
        command=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window',
            '--external-workload-config',str(Path(config).resolve()),'--external-workload-upstream',str(Path(upstream).resolve()),
            '--host-file-monitor','--host-current-only']
        command+=['--light-monitor'] if sampling=='on' else ['--external-workload-sampling','off']
        (out/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        with (out/'native.log').open('x') as log:p=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=budgets(cfg['task_id'])['native'],**extra)
        children={Path(p) for p in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)',(out/'native.log').read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(children)==1:state['storage']=str(children.pop())
        assert p.returncode==0,'native external workload failed; inspect first boundary'
        storage=Path(state['storage']);outer=json.loads((storage/'result.json').read_text());raw=storage/'external-workload'
        task=json.loads((raw/'result.json').read_text())
        assert outer['status']==task['status']=='PASS' and task['actions']==cfg['actions'] and task['task_id']==cfg['task_id']
        if cfg['task_id']==C4:assert task['roundtrip_validation']=='PASS'
        if cfg['task_id']==CYTHON:
            assert task['functional_validation']=='PASS'
            proxy.check()
        assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
        assert all(outer['checks'][k]=='PASS' for k in ('actual_writable_files_inside_volume','readonly_root_rejects_write','fixed_backing_allocation'))
        assert outer['host_file_residency']['status']=='NOT_REQUESTED'
        if sampling=='off':
            assert task['sampling']=='off' and task['memory']=='NOT_MEASURED' and task['cleanup']['memory_sampling_disabled']
            assert not (raw/'runtime-monitor.json').exists() and not (out/'host-file').exists()
        state.update(status='PASS',actions=task['actions'],raw=str(raw),task=task)
    except BaseException as error:
        state.update(status='FAIL',error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if proxy:
            try:proxy.stop();state['owned_guest_proxy_stopped']=True
            except Exception as error:state.update(status='FAIL',guest_proxy_cleanup_error=repr(error))
        if collector:
            state['collector']=collector.stop()
            if state['collector']['status']!='PASS':state['status']='FAIL'
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    if state['status']=='PASS' and sampling=='on':
        try:
            from report_host_file import report
            dest=out/'report';dest.mkdir()
            state['host']=report(out/'host-file',None,dest/'host',creation_path=storage/'result.json')
            state['guest']=guest_plot(raw,dest,title={C4:'C4 resharding',CYTHON:'Cython extensions'}.get(cfg['task_id'],'Django #16819'))
            state['report']=str(dest)
        except Exception as error:
            state.update(status='FAIL',report_error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);p.add_argument('upstream',type=Path);p.add_argument('--inside-group',action='store_true')
    p.add_argument('--sampling',choices=('on','off'),default='on',help='C4/Django/Cython controls: both host and guest memory sampling')
    args=p.parse_args();cfg,_,_=load(args.config,args.upstream)
    if args.sampling=='off' and cfg['task_id'] not in (C4,CYTHON,'django__django-16819'):raise ValueError('sampling off is limited to the explicit C4/Django/Cython controls')
    if args.inside_group:return grouped(args.config,args.upstream,args.sampling)
    if os.geteuid()!=0:raise RuntimeError('root required for the established isolated service')
    name='asbhost-'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid());slice_name=name+'.slice'
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+name,'--slice='+slice_name,
        '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
        '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,'--setenv=ASB_HOST_FILE_SAMPLING='+args.sampling]
    for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
        if key in os.environ:command+=['--setenv='+key]
    if os.environ.get('ASB_AGENTENV_SOURCE_SERVICE'):command+=['--setenv=ASB_AGENTENV_SOURCE_SERVICE='+os.environ['ASB_AGENTENV_SOURCE_SERVICE']]
    command+=[sys.executable,'-B',str(Path(__file__).resolve()),str(args.config.resolve()),str(args.upstream.resolve()),'--inside-group']
    if args.sampling=='off':command+=['--sampling','off']
    try:return subprocess.run(command,timeout=budgets(cfg['task_id'])['workflow']).returncode
    finally:subprocess.run(['systemctl','stop',name+'.service',slice_name],timeout=150,check=False,capture_output=True)


if __name__=='__main__':raise SystemExit(main())
