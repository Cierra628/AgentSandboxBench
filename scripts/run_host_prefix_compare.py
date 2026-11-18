"""One cold guest versus seven; same 11-action prefix, light counters and object checks."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

from platform_tasks import ROOT,configuration,preflight
from isolated_storage import available,capacity_budget,GIB
from run_light_monitor_controls import SOURCE,SOURCE_ID


def read(path):return json.loads(Path(path).read_text())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2)+'\n')


def signed_reference(single,multiple,n=7):
    if single is None or multiple is None:return dict(status='NOT_MEASURED',reference_bytes=None,reduction_bytes=None,reduction_fraction=None)
    reference=n*single
    return dict(status='OBSERVED',reference_bytes=reference,reduction_bytes=reference-multiple,
        reduction_fraction=(reference-multiple)/reference if reference>0 else None,
        meaning='Incremental accounting versus N times one guest; not measured no-sharing physical savings')


def plot_shared_objects(objects,out):
    if not objects:return None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,len(objects),figsize=(8*len(objects),5),squeeze=False,constrained_layout=True)
    for ax,obj in zip(axes[0],objects):
        a=obj['single']['residency']['resident_bytes']/2**20;b=obj['multiple']['residency']['resident_bytes']/2**20
        ax.plot([1,7],[a,b],'o-',color='#0f766e',linewidth=2,label='Observed cache residency (one shared object)')
        ax.plot([1,7],[a,7*a],'o--',color='#94a3b8',label='N x single-guest residency (hypothetical)')
        ax.annotate(f'{a:.3f} MiB',(1,a),xytext=(8,10),textcoords='offset points')
        ax.annotate(f'{b:.3f} MiB',(7,b),xytext=(-8,10),ha='right',textcoords='offset points')
        ax.annotate(f'{7*a:.3f} MiB',(7,7*a),xytext=(-8,-16),ha='right',textcoords='offset points')
        ax.set(title='Shared read-only drive: '+obj['drive_id'],xlabel='Number of guests',ylabel='Host resident cache / MiB',xticks=[1,7],ylim=(0,max(7*a,b)*1.15))
        ax.grid(alpha=.2);ax.legend(loc='center left',fontsize=9)
    fig.suptitle('Specified backing cache only; not all host RAM or guest page cache')
    fig.savefig(Path(out)/'shared-object.png',dpi=140);fig.savefig(Path(out)/'shared-object.svg');plt.close(fig)
    return 'shared-object.png'


def plot_timeline(runs,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,constrained_layout=True)
    for r in runs:
        base=r['endpoint']['baseline'];origin=r['endpoint']['before']['main_counter_monotonic_ns'];rows=r['task_window']
        times=[0,*[(p['monotonic_ns']-origin)/1e9 for p in rows],r['elapsed_ns']/1e9]
        for ax,key in zip(axes,('file','current')):
            values=[0,*[(p[key]-base[key])/2**20 for p in rows],r['endpoint']['delta'][key]/2**20]
            ax.plot(times,values,label='N='+str(r['sandboxes']))
            ax.plot(times[-1],values[-1],'o',color=ax.lines[-1].get_color(),markersize=4)
            ax.set(ylabel='MiB change',title='Host '+key+' from before guest creation');ax.grid(alpha=.2);ax.legend()
    axes[-1].set_xlabel('Host monotonic seconds from each pre-creation baseline')
    fig.savefig(Path(out)/'timeline.png',dpi=140);fig.savefig(Path(out)/'timeline.svg');plt.close(fig)


def collect(workflow,n):
    work=read(workflow/'result.json');assert work['status']=='PASS' and work['cache_released']
    storage=Path(work['storage_result_dir']);outer=read(storage/'result.json');raw=storage/'prettier6604'
    assert outer['status']=='PASS'
    assert all(outer['cleanup'][key] for key in ('owned_container_removed','mount_and_loop_removed','successful_fixture_removed','original_service_unchanged','original_firecracker_identities_unchanged'))
    from host_prefix_compare import audit_prefix
    audit=audit_prefix(raw);assert audit['sandboxes']==n
    task=read(raw/'result.json');monitor=read(raw/'runtime-monitor.json')
    assert monitor['profile']=='light' and not monitor['host_vmm_requested']
    assert len(monitor['sessions'])==n
    for session in monitor['sessions']:
        assert session['status']=='PASS' and session['collector_reaped'] and session['receipt']['profile']=='light'
        path=raw/session['raw_directory']
        assert not any((path/name).exists() for name in ('guest-vmstat.tsv','guest-cpu.tsv','guest-diskstats.tsv'))
    assert not (raw/'host/memory-samples.tsv').exists() and not (raw/'host-vmm').exists()
    with (workflow/'host-file/memory.tsv').open() as stream:
        rows=[{k:int(v) if v else None for k,v in r.items()} for r in csv.DictReader(stream,delimiter='\t')]
    before=audit['before'];after=audit['after']
    window=[r for r in rows if before['main_counter_monotonic_ns']<=r['monotonic_ns']<=after['main_counter_monotonic_ns']]
    assert window and rows[0]['monotonic_ns']<before['main_counter_monotonic_ns']<after['main_counter_monotonic_ns']<rows[-1]['monotonic_ns']
    return dict(status='PASS',sandboxes=n,workflow=str(workflow),storage=str(storage),report=work['report_dir'],
        actual_actions=task['actual_action_count'],tool_sum_ns=sum(p['guest_action_sum_ns'] for p in task['paths'].values()),
        guest_samples=sum(s['receipt']['samples'] for s in monitor['sessions']),host_samples=len(rows),
        elapsed_ns=after['main_counter_monotonic_ns']-before['main_counter_monotonic_ns'],
        endpoint=audit,task_window=window,host_collector=work['host_file_monitor'])


def summarize(out,runs):
    a,b=runs;assert [r['sandboxes'] for r in runs]==[1,7]
    comparisons={k:signed_reference(a['endpoint']['delta'][k],b['endpoint']['delta'][k]) for k in ('file','current','anon','shmem','kernel')}
    objects=[]
    for row in a['endpoint']['after']['shared_readonly_drives']:
        matches=[v for v in b['endpoint']['after']['shared_readonly_drives'] if v['drive_id']==row['drive_id']]
        if not matches:continue
        other=matches[0]
        assert row['residency']['mapping_length']==other['residency']['mapping_length']
        assert row['residency']['stable'] and other['residency']['stable']
        object_reference=signed_reference(row['residency']['resident_bytes'],other['residency']['resident_bytes'])
        object_reference['meaning']='Hypothetical N copies of the single-guest object cache; not measured no-sharing or whole-host physical savings'
        objects.append(dict(drive_id=row['drive_id'],single=row,multiple=other,
            endpoint_linear_reference=object_reference,
            scope='Same drive role/capacity; all actual VMMs share one object within each group. Endpoint cache observation, initial cache not queried; not whole physical memory'))
    summary=dict(status='PASS_SHORT_PREFIX_COMPARISON',runs=runs,incremental_accounting_reference=comparisons,
        shared_drive_objects=objects,whole_physical_savings='NOT_MEASURED',
        scope='Fresh private services/volumes; same original prefix, 11 and 77 actual actions. Sequential N=1 then N=7, no cold-cache reset or no-sharing backend.')
    write(out/'summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(14,4.5),constrained_layout=True)
    names=('file','current','anon');colors=('#2563eb','#64748b','#d97706')
    for ax,key,color in zip(axes,names,colors):
        values=[r['endpoint']['delta'][key]/2**20 for r in runs];reference=[values[0],values[0]*7]
        ax.plot([1,7],values,'o-',label='Observed group increment',color=color)
        ax.plot([1,7],reference,'o--',label='N x single-guest increment',color='#94a3b8')
        ax.set(title='Host '+key,xlabel='Number of guests',ylabel='MiB above pre-creation baseline',xticks=[1,7]);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle('Same 11-action prefix: incremental accounting, not total physical savings')
    fig.savefig(out/'comparison.png',dpi=140);fig.savefig(out/'comparison.svg');plt.close(fig)
    plot_timeline(runs,out)
    plot_shared_objects(objects,out)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    out=args.output.resolve();assert out.is_relative_to(ROOT/'.artifacts') and not out.exists()
    os.umask(0o077);out.mkdir(parents=True);(out/'code').mkdir()
    for name in ('run_host_prefix_compare.py','host_prefix_compare.py','platform_tasks.py','run_prettier6604_grpo.py','validate_isolated_agentenv.py','report_task_overview.py'):
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    os.environ['ASB_AGENTENV_SOURCE_SERVICE']=SOURCE
    result=dict(status='FAIL',stage='precheck',runs=[]);started=False
    try:
        source=json.loads(subprocess.check_output(['docker','inspect',SOURCE],timeout=20))[0]
        assert source['Id']==SOURCE_ID and not source['State']['Running'] and source['HostConfig']['RestartPolicy']['Name']=='no'
        write(out/'source-before.json',source['State'])
        for n in (1,7):write(out/f'N{n}-preflight.json',preflight(configuration(ROOT/f'configs/platform-prettier6604-prefix-N{n}.json'),offline=True))
        subprocess.run(['docker','start',SOURCE],check=True,capture_output=True,timeout=30);started=True
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            try:
                with opener.open('http://127.0.0.1:39681/health',timeout=2) as response:
                    if response.status in (200,204):break
            except OSError:pass
            time.sleep(1)
        else:raise TimeoutError('source health timeout')
        for n in (1,7):
            result['stage']='N'+str(n);budget=capacity_budget(available(ROOT),16*GIB);assert budget['ready']
            write(out/f'N{n}-resources.json',dict(budget=budget,load=os.getloadavg()))
            write(out/'result.json',dict(result,status='RUNNING'));print('status=RUNNING group=N'+str(n)+' result_dir='+str(out),flush=True)
            with (out/f'N{n}.log').open('x') as log:
                proc=subprocess.run(['bash','scripts/run-host-file-monitor.sh',f'configs/platform-prettier6604-prefix-N{n}.json'],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=1700)
            assert proc.returncode==0,'N'+str(n)+' failed; diagnose before any further tasks'
            paths={Path(v) for v in re.findall(r'status=PASS result_dir=(\S+)',(out/f'N{n}.log').read_text()) if Path(v).parent==ROOT/'.artifacts/platform-tasks'}
            assert len(paths)==1
            result['runs'].append(collect(paths.pop(),n));write(out/'runs.json',result['runs'])
        summarize(out,result['runs']);result.update(status='PASS',stage='complete')
    except BaseException as error:
        import traceback
        (out/'failure.txt').write_text(traceback.format_exc());result['error']=repr(error)
    finally:
        if started:
            with (out/'source-stop.log').open('x') as log:
                stop=subprocess.run(['docker','stop','-t','30',SOURCE],stdout=log,stderr=subprocess.STDOUT,timeout=60)
            state=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',SOURCE],timeout=20));write(out/'source-after.json',state)
            result['source_returned_to_stopped']=stop.returncode==0 and not state['Running']
            if not result['source_returned_to_stopped']:result['status']='FAIL'
        write(out/'result.json',result);print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
