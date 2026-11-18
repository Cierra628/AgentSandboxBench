"""Host/guest measurement for pinned external single-path replays."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import traceback

from external_workload import C4,CYTHON,MATPLOTLIB,TEXT_EDIT,NEW_TASKS,budgets,load
from external_task_metrics import SNAPSHOT_FILES,validate_result
from host_sandbox_monitor import validate_pss_interval
from external_guest_proxy import GuestProxy,upstream_port
from isolated_storage import GIB,available,capacity_budget

ROOT=Path(__file__).resolve().parents[1]


def guest_plot(raw,output,*,title='Django #16819',explicit_axes=False):
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
    if explicit_axes:
        for ax,label in zip(axes,('Tool duration, seconds','Guest MemTotal - MemAvailable, MiB','Guest Cached (includes Shmem), MiB')):ax.set_ylabel(label)
    for suffix in ('png','svg'):fig.savefig(output/('guest.'+suffix),dpi=140)
    plt.close(fig)
    return dict(samples=len(memory),receipt=session['receipt'],guest_used_peak_mib=max((int(r['MemTotal_kib'])-int(r['MemAvailable_kib']))/1024 for r in memory),
        guest_cached_peak_mib=max(int(r['Cached_kib'])/1024 for r in memory),
        maximum_sampling_gap_ns=max((int(b['monotonic_ns'])-int(a['monotonic_ns']) for a,b in zip(memory,memory[1:])),default=None))


def grouped(config,upstream,sampling='on',oci_input=None,endpoint_metrics=False,guest_relay_proxy=None,sampling_control=False,guest_count=1,delete_trace=False,*,vmm_pss_interval_seconds=1):
    cfg,_,_=load(config,upstream)
    metrics_on=cfg['task_id'] in NEW_TASKS or endpoint_metrics
    pss_interval=validate_pss_interval(vmm_pss_interval_seconds)
    if pss_interval!=1 and not (metrics_on and sampling=='on'):raise ValueError('non-default PSS interval requires sampled endpoint metrics')
    if delete_trace and guest_count!=2:raise ValueError('deletion trace requires two C4 guests')
    if guest_count not in (1,2) or guest_count==2 and not (sampling_control and cfg['task_id']==C4):raise ValueError('two guests require explicit C4 sampling control')
    if sampling_control and cfg['task_id']!=C4:raise ValueError('sampling control requires C4')
    if guest_relay_proxy:
        if cfg['task_id']!=CYTHON or not endpoint_metrics:raise ValueError('explicit relay requires upgraded Cython')
        upstream_port(dict(HTTP_PROXY=guest_relay_proxy))
    if metrics_on and sampling!='on':raise ValueError('endpoint metrics require sampling on')
    if oci_input:
        from external_oci import validate
        validate(oci_input,cfg)
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
    if metrics_on:
        for name in SNAPSHOT_FILES:shutil.copy2(ROOT/'scripts'/name,code/name)
    if oci_input:shutil.copy2(ROOT/'scripts/external_oci.py',code/'external_oci.py')
    shutil.copy2(config,out/'config.json')
    collector=None;proxy=None
    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        if sampling=='on':collector=HostFileMonitor(out/'host-file',group,include_pss=False)
        else:state.update(memory='NOT_MEASURED',collector=dict(status='NOT_REQUESTED',reason='explicit memory sampling off'))
        extra={}
        if metrics_on or sampling_control:
            extra['env']=dict(os.environ,**{key:'' for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')})
        if cfg['task_id']==CYTHON and (not endpoint_metrics or guest_relay_proxy):
            proxy=GuestProxy(out,upstream,proxy_url=guest_relay_proxy) if guest_relay_proxy else GuestProxy(out,upstream)
            extra['env']=dict(extra.get('env',os.environ),ASB_EXTERNAL_GUEST_RELAY_URL=proxy.start(),ASB_EXTERNAL_GUEST_RELAY_STATE=str(out))
        command=['bash',str(ROOT/'scripts/validate-isolated-storage.sh'),'--automatic-window',
            '--external-workload-config',str(Path(config).resolve()),'--external-workload-upstream',str(Path(upstream).resolve()),
            '--host-file-monitor','--host-current-only']
        # The historical shell entry has a fixed 900-second timeout. New tasks
        # use the Python entry under the explicit native/workflow budgets above.
        if metrics_on:command=[sys.executable,'-B',str(ROOT/'scripts/validate_isolated_agentenv.py'),*command[2:]]
        command+=['--light-monitor'] if sampling=='on' else ['--external-workload-sampling','off']
        if endpoint_metrics:command+=['--task-endpoint-metrics']
        if pss_interval!=1:command+=['--vmm-pss-interval-seconds',str(pss_interval)]
        if sampling_control:command+=['--external-sampling-control']
        if guest_count==2:command+=['--external-guest-count','2']
        if delete_trace:command+=['--external-delete-trace']
        if guest_relay_proxy:command+=['--external-workload-relay']
        if oci_input:command+=['--external-workload-oci-input',str(Path(oci_input).resolve())]
        (out/'command.json').write_text(json.dumps(command,indent=2)+'\n')
        with (out/'native.log').open('x') as log:p=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=budgets(cfg['task_id'])['native'],**extra)
        children={Path(p) for p in re.findall(r'status=(?:PASS|FAIL) result_dir=(\S+)',(out/'native.log').read_text()) if Path(p).parent==ROOT/'.artifacts/storage-isolated-service'}
        if len(children)==1:state['storage']=str(children.pop())
        assert p.returncode==0,'native external workload failed; inspect first boundary'
        storage=Path(state['storage']);outer=json.loads((storage/'result.json').read_text());raw=storage/'external-workload'
        task=json.loads((raw/'result.json').read_text())
        assert outer['status']==task['status']=='PASS' and task['actions']==guest_count*cfg['actions'] and task['task_id']==cfg['task_id']
        if cfg['task_id']==C4:assert task['roundtrip_validation']=='PASS'
        if cfg['task_id']==CYTHON and (not endpoint_metrics or guest_relay_proxy):
            assert task['functional_validation']=='PASS'
            proxy.check()
        if guest_count==2:
            from run_c4_parallel import validate_parallel
            validate_parallel(task,sampling,pss_interval_seconds=pss_interval if metrics_on else None)
        elif metrics_on:validate_result(task,pss_interval_seconds=pss_interval)
        source_check='original_source_firecracker_identities_unchanged' if 'original_source_firecracker_identities_unchanged' in outer['cleanup'] else 'original_firecracker_identities_unchanged'
        assert all(outer['cleanup'][k] for k in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged',source_check))
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
    if state['status']=='PASS' and sampling=='on' and guest_count==1:
        try:
            from report_host_file import report
            dest=out/'report';dest.mkdir()
            state['host']=report(out/'host-file',None,dest/'host',creation_path=storage/'result.json',**(dict(explicit_axes=True) if metrics_on else {}))
            state['guest']=guest_plot(raw,dest,title={C4:'C4 resharding',CYTHON:'Cython extensions',MATPLOTLIB:'Matplotlib #24177',TEXT_EDIT:'Large-scale text editing'}.get(cfg['task_id'],'Django #16819'),**(dict(explicit_axes=True) if metrics_on else {}))
            if metrics_on:
                value=task['endpoint_metrics'];pss=value['pss'];p1=value['p1']
                with (out/'host-file/memory.tsv').open() as stream:
                    host_rows=list(csv.DictReader(stream,delimiter='\t'))
                active=[int(r['current']) for r in host_rows if task['schedule_start_monotonic_ns']<=int(r['monotonic_ns'])<=task['schedule_end_monotonic_ns']]
                if not active:raise ValueError('no host counters inside task window')
                state['workflow_current']=dict(observed_peak_bytes=max(int(r['current']) for r in host_rows),median_bytes=statistics.median(int(r['current']) for r in host_rows))
                state['task_window_current']=dict(samples=len(active),observed_peak_bytes=max(active),median_bytes=statistics.median(active),scope=task['time_scope'])
                shutil.copy2(raw/'tools.tsv',dest/'tools.tsv')
                (dest/'REPORT.md').write_text('\n'.join([
                    '# 单路径任务验证：'+cfg['task_id'],'',
                    f"动作数：{task['actions']}；原退出序列及结果检查：通过。",
                    f"任务窗口：{task['schedule_elapsed_ns']/1e9:.6f}秒；动作及下载窗口：{task['actions_download_elapsed_ns']/1e9:.6f}秒；工具累计：{task['tool_duration_sum_ns']/1e9:.6f}秒；控制程序整轮：{task['controller_elapsed_ns']/1e9:.6f}秒。",'',
                    f"完成点单VMM PSS/N（N=1）：{pss['per_sandbox_bytes']/2**20:.6f} MiB；初始下层{p1['unique_files']}文件mincore P1：{p1['resident_bytes']/2**20:.6f} MiB。",
                    f"任务窗口内memory.current观测峰值：{max(active)/2**20:.6f} MiB；中位数：{statistics.median(active)/2**20:.6f} MiB。",
                    f"PSS查询：{(pss['end_monotonic_ns']-pss['start_monotonic_ns'])/1e9:.6f}秒，CPU {pss['cpu_ns']/1e9:.6f}秒；mincore查询：{(p1['end_monotonic_ns']-p1['start_monotonic_ns'])/1e9:.6f}秒，CPU {p1['cpu_ns']/1e9:.6f}秒。",
                    f"P1前／后组file：{p1['counters_before']['file']/2**20:.6f}／{p1['counters_after']['file']/2**20:.6f} MiB；净不足：{p1['net_shortfall_bytes']/2**20:.6f} MiB，实际组外容量未知。",'',
                    'guest图为guest整体非可用内存估计和Cached；host图为含输入准备、控制程序、私有服务和guest的实验组记账。PSS仅含VMM映射；P1只含初始不可变下层，不含upper，不当整体物理内存或新读入量。各量不相加。',
                    f'周期普通计数0.1秒、PSS{pss_interval:g}秒。采集CPU和时长不是任务减速率；无采样开关对照。',
                    '任务窗口从创建前至动作和下载返回，整轮另含编译、查询、检查和清理；逐动作时长使用guest时钟，不与host时钟相减。','',
                    'host曲线及基线：`host/`；guest及工具曲线：`guest.png`、`guest.svg`。',
                    f"原始计时、PSS曲线及成本：`{raw}/tools.tsv`、`{raw}/host-vmm/external/host-vmm.tsv`、`{raw}/host-vmm/external/collector-status.json`；终点清单及成本：`{raw}/external-metrics.json`。",'']))
                compact=dict(status='PASS',task_id=cfg['task_id'],image=cfg['image'],actions=task['actions'],raw=str(raw),
                    timing={key:task[key] for key in ('schedule_elapsed_ns','actions_download_elapsed_ns','tool_duration_sum_ns','controller_elapsed_ns','time_scope')},
                    endpoint_metrics=value,host_vmm_collector=task['host_vmm_collector'],
                    workflow_current=state['workflow_current'],task_window_current=state['task_window_current'],guest=state['guest'],host=state['host'])
                (dest/'measurements.json').write_text(json.dumps(compact,indent=2)+'\n')
                published=ROOT/'results'/('external-'+cfg['task_id']+'-'+out.name)
                shutil.copytree(dest,published)
                state['published_report']=str(published)
            state['report']=str(dest)
        except Exception as error:
            state.update(status='FAIL',report_error=repr(error));(out/'report-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(state,indent=2)+'\n')
    print('status='+state['status']+' result_dir='+str(out),flush=True)
    return 0 if state['status']=='PASS' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);p.add_argument('upstream',type=Path);p.add_argument('--inside-group',action='store_true')
    p.add_argument('--delete-trace',action='store_true',help='Explicit private backend deletion diagnostics')
    p.add_argument('--guest-count',type=int,choices=(1,2),default=1,help='Explicit C4 sampling control only')
    p.add_argument('--sampling-control',action='store_true',help='C4 explicit paired comparison with common environment and timing')
    p.add_argument('--endpoint-metrics',action='store_true',help='Legacy single tasks: opt in to VMM PSS, lower P1 and current task timing')
    p.add_argument('--vmm-pss-interval-seconds',type=validate_pss_interval,default=1,help='Endpoint metrics only: periodic VMM PSS interval; default 1s')
    p.add_argument('--guest-relay-proxy',help='Explicit upgraded Cython only: approved loopback proxy for original Git/pip')
    p.add_argument('--oci-input',type=Path,help='Validated fixed local OCI input, no runtime registry access')
    p.add_argument('--sampling',choices=('on','off'),default='on',help='C4/Django/Cython controls: both host and guest memory sampling')
    args=p.parse_args();cfg,_,_=load(args.config,args.upstream)
    metrics_on=cfg['task_id'] in NEW_TASKS or args.endpoint_metrics
    if args.vmm_pss_interval_seconds!=1 and not (metrics_on and args.sampling=='on'):raise ValueError('non-default PSS interval requires sampled endpoint metrics')
    if args.sampling=='off' and cfg['task_id'] not in (C4,CYTHON,'django__django-16819'):raise ValueError('sampling off is limited to the explicit C4/Django/Cython controls')
    if args.guest_relay_proxy:
        if cfg['task_id']!=CYTHON or not args.endpoint_metrics:raise ValueError('explicit relay requires upgraded Cython')
        upstream_port(dict(HTTP_PROXY=args.guest_relay_proxy))
    if args.inside_group:return grouped(args.config,args.upstream,args.sampling,args.oci_input,args.endpoint_metrics,args.guest_relay_proxy,args.sampling_control,args.guest_count,args.delete_trace,vmm_pss_interval_seconds=args.vmm_pss_interval_seconds)
    if os.geteuid()!=0:raise RuntimeError('root required for the established isolated service')
    name='asbhost-'+time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid());slice_name=name+'.slice'
    command=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+name,'--slice='+slice_name,
        '--property=MemoryAccounting=yes','--property=IOAccounting=yes','--property=TimeoutStopSec=120',
        '--working-directory='+str(ROOT),'--setenv=ASB_HOST_FILE_SLICE='+slice_name,'--setenv=ASB_HOST_FILE_SAMPLING='+args.sampling]
    if metrics_on or args.sampling_control:
        command+=['--setenv='+key+'=' for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')]
    else:
        for key in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
            if key in os.environ:command+=['--setenv='+key]
    if os.environ.get('ASB_AGENTENV_SOURCE_SERVICE'):command+=['--setenv=ASB_AGENTENV_SOURCE_SERVICE='+os.environ['ASB_AGENTENV_SOURCE_SERVICE']]
    if os.environ.get('ASB_ISOLATED_UNUSED_NETWORK_POOLS')=='on':command+=['--setenv=ASB_ISOLATED_UNUSED_NETWORK_POOLS=on']
    command+=[sys.executable,'-B',str(Path(__file__).resolve()),str(args.config.resolve()),str(args.upstream.resolve()),'--inside-group']
    if args.sampling=='off':command+=['--sampling','off']
    if args.delete_trace:command+=['--delete-trace']
    if args.guest_count==2:command+=['--guest-count','2']
    if args.sampling_control:command+=['--sampling-control']
    if args.endpoint_metrics:command+=['--endpoint-metrics']
    if args.vmm_pss_interval_seconds!=1:command+=['--vmm-pss-interval-seconds',str(args.vmm_pss_interval_seconds)]
    if args.guest_relay_proxy:command+=['--guest-relay-proxy',args.guest_relay_proxy]
    if args.oci_input:command+=['--oci-input',str(args.oci_input.resolve())]
    try:return subprocess.run(command,timeout=budgets(cfg['task_id'])['workflow']).returncode
    finally:subprocess.run(['systemctl','stop',name+'.service',slice_name],timeout=150,check=False,capture_output=True)


if __name__=='__main__':raise SystemExit(main())
