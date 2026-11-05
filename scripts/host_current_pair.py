"""Two cold guests in one group; persistent input adds one post-window file check."""
import asyncio
import csv
import json
import os
from pathlib import Path
import statistics
import time

PROFILE='current-pair-v1'
ROLES=('backbone','early_1')
OBSERVATION_SECONDS=5


def enabled(args):
    value=getattr(args,'host_current_pair',False)
    if getattr(args,'host_persistent_seed',None) and not value:
        raise ValueError('persistent input requires the current pair')
    if value and (getattr(args,'host_scaling_mode',None)!='cold' or
            os.environ.get('ASB_HOST_ROOTFS_REFERENCE')=='on'):
        raise ValueError('current pair requires cold scaling without rootfs residency queries')
    return value


def paths(rollouts):
    from host_scaling import paths as scaling_paths
    plan=scaling_paths(rollouts,'cold')
    return {role:plan[role] for role in ROLES}


async def observe(controller,args,out,cli,executed,verify_instance):
    from host_file_monitor import measurement_group
    from host_sandbox_monitor import Binding
    from host_scaling import checkpoint
    assert enabled(args) and list(executed)==list(ROLES)
    group,_=measurement_group();binding=Binding(args.container,out);guests={}
    state=dict(status='RUNNING',profile=PROFILE,mode='cold',sizes=[1,2],group=str(group),group_id=group.stat().st_ino,
        observation_seconds=OBSERVATION_SECONDS,pss='NOT_REQUESTED',mincore='NOT_REQUESTED',stages=[])
    persistent=getattr(args,'host_persistent_seed',None)
    if persistent:
        from host_persistent_input import validate_seed,SCOPE
        seed,_=validate_seed(persistent)
        state.update(persistent_input=True,input_import={k:seed[k] for k in ('profile','group','group_id','import_end_monotonic_ns','scratch_removed_before_runtime')},measurement_scope=SCOPE)
    def save(): (out/'host-scaling.json').write_text(json.dumps(state,indent=2)+'\n')
    try:
        state['baseline']=await checkpoint(group,binding,guests,cli,'baseline');save()
        for role in ROLES:
            n=len(guests)+1;stage=dict(sandboxes=n,status='RUNNING',new_roles=[role]);state['stages'].append(stage)
            stage['before_batch']=await checkpoint(group,binding,guests,cli,'before-'+str(n));save()
            sid=await controller.start(role);guests[role]=sid
            assert len(set(guests.values()))==n
            stage['batch_ready_monotonic_ns']=time.monotonic_ns()
            await controller.run_path(sid,role,executed[role])
            stage['task_end_monotonic_ns']=time.monotonic_ns()
            assert verify_instance(role)['action_count']==11
            stage['verified_action_counts']={role:11}
            stage['observation_start']=await checkpoint(group,binding,guests,cli,'start-'+str(n))
            stage['window_start_monotonic_ns']=time.monotonic_ns();save()
            await asyncio.sleep(OBSERVATION_SECONDS)
            stage['window_end_monotonic_ns']=time.monotonic_ns()
            stage['endpoint']=await checkpoint(group,binding,guests,cli,'end-'+str(n))
            stage['status']='PASS';save()
        state['task_end_monotonic_ns']=state['stages'][-1]['task_end_monotonic_ns']
        if persistent:
            from host_persistent_input import observe_input
            state['input_file_check']=await asyncio.to_thread(observe_input,persistent,group,out/'input-file-residency.json')
            state['mincore']='INPUT_FILES_ONCE_AFTER_WINDOWS'
        state['status']='PASS';save()
        return guests,state
    except BaseException as error:
        if state['stages']:state['stages'][-1]['status']='FAIL'
        state.update(status='FAIL',error=repr(error));save()
        raise  # The existing controller finally owns every allocated guest.


def summarize(state,points):
    assert state['profile']==PROFILE and state['status']=='PASS' and state['sizes']==[1,2]
    assert len(state['stages'])==2
    baseline=state['baseline'];base=baseline['counters']['current'];previous=base;identities={};phases=[]
    assert baseline['bindings']=={} and baseline['guest_meminfo']==[]
    assert points[0]['monotonic_ns']<=baseline['start_monotonic_ns']
    assert points[-1]['monotonic_ns']>=state['stages'][-1]['endpoint']['end_monotonic_ns']
    for n,stage in enumerate(state['stages'],1):
        assert stage['status']=='PASS' and stage['sandboxes']==n and stage['new_roles']==[ROLES[n-1]]
        assert stage['verified_action_counts']=={ROLES[n-1]:11}
        before,start,end=(stage[k] for k in ('before_batch','observation_start','endpoint'))
        for point in (before,start,end):
            assert (point['group'],point['group_id'])==(state['group'],state['group_id'])==(baseline['group'],baseline['group_id'])
        assert all(before['bindings'][r]==start['bindings'][r]==end['bindings'][r]==v for r,v in identities.items())
        assert list(end['bindings'])==list(ROLES[:n]) and len({v['pid'] for v in end['bindings'].values()})==n
        assert start['bindings']==end['bindings']
        a,b=(stage[k] for k in ('window_start_monotonic_ns','window_end_monotonic_ns'))
        assert before['end_monotonic_ns']<=stage['batch_ready_monotonic_ns']<=stage['task_end_monotonic_ns']<=start['start_monotonic_ns']
        assert start['end_monotonic_ns']<=a<b<=end['start_monotonic_ns']
        assert b-a>=state['observation_seconds']*1e9
        window=[p for p in points if a<=p['monotonic_ns']<=b];assert len(window)>=2
        current=end['counters']['current'];values=[p['current'] for p in window]
        old={r['sandbox_id']:r['Cached'] for r in before['guest_meminfo']}
        phases.append(dict(sandboxes=n,window_start_monotonic_ns=a,window_end_monotonic_ns=b,samples=len(window),
            current_sample_mean_bytes=statistics.mean(values),current_sample_min_bytes=min(values),current_sample_peak_bytes=max(values),
            endpoint_counters=end['counters'],baseline_increase_bytes=current-base,since_previous_endpoint_bytes=current-previous,
            since_before_batch_bytes=current-before['counters']['current'],
            old_guest_cached_change_bytes={r['sandbox_id']:r['Cached']-old[r['sandbox_id']] for r in end['guest_meminfo'] if r['sandbox_id'] in old},
            guest_meminfo=end['guest_meminfo']))
        identities=end['bindings'];previous=current
    result=dict(status='PASS_CURRENT_PAIR',baseline_counters=baseline['counters'],phases=phases,
        second_phase_mean_increase_bytes=phases[1]['current_sample_mean_bytes']-phases[0]['current_sample_mean_bytes'],
        checkpoint_elapsed_ns=sum(p['end_monotonic_ns']-p['start_monotonic_ns'] for p in
            [baseline,*[s[k] for s in state['stages'] for k in ('before_batch','observation_start','endpoint')]]),
        pss='NOT_REQUESTED',mincore='NOT_REQUESTED',
        scope='One group and service; old guest stays running with normal reclaim. Differences include its changes and service work, not second-guest private RAM or physical sharing savings.')
    if state.get('persistent_input'):
        imported=state['input_import'];q=state['input_file_check']
        assert (imported['group'],imported['group_id'])==(state['group'],state['group_id'])
        assert imported['scratch_removed_before_runtime'] and imported['import_end_monotonic_ns']<baseline['start_monotonic_ns']
        assert state['stages'][-1]['endpoint']['end_monotonic_ns']<=q['start_monotonic_ns']<=q['end_monotonic_ns']
        if q['status']=='OBSERVED':
            assert (q['group'],q['group_id'])==(state['group'],state['group_id'])
            assert len({(r['device'],r['inode']) for r in q['files']})==len(q['files'])
            assert sum(r['resident_bytes'] for r in q['files'])==q['resident_bytes']
        result.update(mincore=state['mincore'],measurement_scope=state['measurement_scope'],input_file_check=q)
    return result


def plot(state,points,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    base=state['baseline'];t0=base['start_monotonic_ns'];last=state['stages'][-1]['endpoint']['end_monotonic_ns']
    data=[p for p in points if t0<=p['monotonic_ns']<=last]
    x=[(p['monotonic_ns']-t0)/1e9 for p in data]
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True,constrained_layout=True)
    for key in ('current','anon','file','kernel'):
        ax=axes[0] if key=='current' else axes[1]
        ax.plot(x,[(p[key]-base['counters'][key])/2**20 for p in data],label=key)
    for stage in state['stages']:
        for ax in axes:
            ax.axvspan((stage['window_start_monotonic_ns']-t0)/1e9,(stage['window_end_monotonic_ns']-t0)/1e9,
                alpha=.12,color='green',label='N='+str(stage['sandboxes'])+' observation')
    for ax in axes:
        ax.axhline(0,color='gray',linewidth=.6);ax.set_ylabel('Change from baseline (MiB)');ax.grid(alpha=.2);ax.legend()
    axes[0].set_title('Host cgroup memory: one guest, then two (same service and baseline)')
    axes[1].set_xlabel('Host monotonic seconds from prepared baseline')
    fig.savefig(Path(output)/'host-current.png',dpi=140);plt.close(fig)


def audit_task(raw):
    from run_prettier6604_grpo import verify_path
    raw=Path(raw);task=json.loads((raw/'result.json').read_text());state=task['host_scaling']
    assert task['status']=='PASS' and task['actual_action_count']==22 and task['reused_action_count']==0
    assert task['host_current_pair'] and task['host_scaling_mode']=='cold'
    assert bool(task.get('host_persistent_input'))==bool(state.get('persistent_input'))
    assert state['profile']==PROFILE and state['status']=='PASS' and state['sizes']==[1,2]
    assert task['final_roles']==[] and task['official_evaluation']=='NOT_RUN_PREFIX_ONLY'
    assert task['cleanup']['service_sandbox_list_empty']
    cfg=json.loads((raw/'effective-config.json').read_text());executed=paths(cfg['paths'])
    assert cfg['executed_paths']==executed and list(task['paths'])==list(ROLES)
    samples=json.loads((raw/'frozen-input/agent-sampling.json').read_text())['segments']
    names=json.loads((raw/'input-provenance.json').read_text())['original_segment_names']
    total=0
    for role,path in executed.items():
        value=verify_path(raw,role,path,raw/'frozen-input',samples,names)
        assert value['action_count']==11
        assert all(task['paths'][role][key]==value[key] for key in value)
        total+=value['guest_action_sum_ns']
    return dict(status='PASS',profile=PROFILE,actual_actions=22,guest_action_sum_ns=total,
        official_evaluation='NOT_RUN_PREFIX_ONLY')


def report(raw,host,output):
    raw=Path(raw);proof=audit_task(raw)
    task=json.loads((raw/'result.json').read_text());state=task['host_scaling']
    with (Path(host)/'memory.tsv').open() as f:points=[{k:int(v) if v else None for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
    value=summarize(state,points)
    output=Path(output);output.mkdir(exist_ok=False)
    value.update(actual_actions=22,task_audit=proof,host_samples=len(points),controller_elapsed_ns=task['controller_elapsed_ns'])
    (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    with (output/'phases.tsv').open('w') as f:
        columns=['sandboxes','samples','endpoint_current_mib','baseline_increase_mib','since_previous_endpoint_mib','sample_mean_mib','sample_peak_mib']
        w=csv.DictWriter(f,columns,delimiter='\t');w.writeheader()
        for s in value['phases']:
            w.writerow(dict(sandboxes=s['sandboxes'],samples=s['samples'],endpoint_current_mib=s['endpoint_counters']['current']/2**20,
                baseline_increase_mib=s['baseline_increase_bytes']/2**20,since_previous_endpoint_mib=s['since_previous_endpoint_bytes']/2**20,
                sample_mean_mib=s['current_sample_mean_bytes']/2**20,sample_peak_mib=s['current_sample_peak_bytes']/2**20))
    plot(state,points,output)
    return value
