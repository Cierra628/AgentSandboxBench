"""Explicit identical-suffix host comparison; existing idle restore mode is unchanged."""
import asyncio
import csv
import json
import os
from pathlib import Path
import time

from run_host_file_restore import bound_pss, live_residency, ram_backend, read, write

INSTANCES=('N1-a','N2-a','N2-b')


def source_role(role):
    return 'early_1' if role in INSTANCES else role


def matched_paths(rollouts, *, dual_only=False):
    assert rollouts['early_1'][:2]==rollouts['backbone'][:2]
    return {'backbone':rollouts['backbone'][:2],**{role:list(rollouts['early_1'][2:]) for role in (INSTANCES[1:] if dual_only else INSTANCES)}}


async def observe_matched(controller,template,parent,runtime,args,out,cli,executed,verify_instance,final_check):
    from host_file_monitor import measurement_group,counters,pids,pss_snapshot,residency_check
    from host_sandbox_monitor import Binding
    group,_=measurement_group();binding=Binding(args.container,out)
    result=dict(status='INCOMPLETE',template=template,groups=[],original_role='early_1',suffix_actions=20 if getattr(args,'host_trace_dual',False) else 30)
    sampling=os.environ.get('ASB_HOST_FILE_SAMPLING','on')
    if sampling not in ('on','off') or sampling=='off' and getattr(args,'host_trace_dual',False):
        raise ValueError('host sampling off is not a tracing mode')
    if sampling=='off':result.update(host_sampling='off',host_measurements='NOT_REQUESTED')
    roots_file=getattr(args,'host_file_roots',None)
    roots=[Path(value) for value in read(roots_file)] if roots_file is not None else None
    if sampling=='off' and roots is not None:raise ValueError('host sampling off must not request a file inventory')
    if roots is not None:
        if not roots:raise ValueError('live regular-file inventory requires nonempty roots')
        result['ordinary_residency_requested']=True
    state=out/'host-matched.json'
    def no_vmm():
        assert not [pid for pid in pids(group) if Path('/proc',str(pid),'comm').read_text().strip()=='firecracker']
    independent_phase=getattr(args,'host_independent_phase',None)
    rootfs_short=getattr(args,'host_rootfs_short',False)
    if rootfs_short and not (independent_phase=='dual' and sampling=='on' and roots is None):
        raise ValueError('rootfs short requires imported dual restores, no broad file inventory')
    detail=os.environ.get('ASB_HOST_MEMORY_DETAIL','off')=='on'
    if detail and (not independent_phase or roots is None or sampling!='on'):
        raise ValueError('host memory detail requires sampled independent restores with file roots')
    if independent_phase:
        from host_independent_task import PHASE_ROLES
        phases=(PHASE_ROLES[independent_phase],)
        result.update(independent_phase=independent_phase,suffix_actions=10*len(phases[0]))
    else:phases=(INSTANCES[1:],) if getattr(args,'host_trace_dual',False) else (INSTANCES[:1],INSTANCES[1:])
    for roles in phases:
        no_vmm()
        initial=None
        if independent_phase and not rootfs_short:
            assert roots is not None and sampling=='on'
            initial=await asyncio.to_thread(residency_check,group,roots,out/'before-restore-regular.json',live=True)
            assert initial['status']=='OBSERVED'
        phase=dict(sandboxes=len(roles),baseline_start_ns=time.monotonic_ns(),baseline=counters(group) if sampling=='on' else None,guests=[])
        if initial is not None:phase['before_restore_ordinary']=initial
        phase['baseline_end_ns']=time.monotonic_ns()
        result['groups'].append(phase);write(state,result)
        for role in roles:
            begin=time.monotonic_ns();sid=await controller.start(role,template=template,parent=parent)
            phase['guests'].append(dict(role=role,source_role=source_role(role),sandbox_id=sid,
                binding=binding.resolve(sid),restore_begin_ns=begin,restore_end_ns=time.monotonic_ns(),
                restored=read(out/('restored-'+role+'.json'))))
            write(state,result)
        for g in phase['guests']:await asyncio.to_thread(runtime.mark,g['sandbox_id'],'matched-task')
        phase['start_monotonic_ns']=time.monotonic_ns();write(state,result)
        async def run_one(g):
            g['task_start_ns']=time.monotonic_ns()
            await controller.run_path(g['sandbox_id'],g['role'],executed[g['role']])
            g['task_end_ns']=time.monotonic_ns()
        values=await asyncio.gather(*(run_one(g) for g in phase['guests']),return_exceptions=True)
        phase['end_monotonic_ns']=time.monotonic_ns();write(state,result)
        errors=[v for v in values if isinstance(v,BaseException)]
        if errors:raise RuntimeError('matched suffix failed: '+repr(errors[0]))
        # These heavier scans are outside task timing, before extra functional/official tests.
        if rootfs_short:
            from host_rollout_shared import SharedRollout
            shared=SharedRollout(group,binding,out,'short-restore')
            shared.restored={g['role']:template for g in phase['guests']};shared.templates={template:11}
            # Preserve the original functional/official checks even when this added query fails.
            try:phase['shared_files']=await asyncio.to_thread(shared.query,{g['role']:g['sandbox_id'] for g in phase['guests']},phase['end_monotonic_ns'])
            except Exception as error:phase['measurement_error']=repr(error)
            phase['memory_backends']=[ram_backend(g['binding']) for g in phase['guests']]
            write(state,result)
        elif sampling=='on':
            phase['endpoint_pss']=bound_pss(await asyncio.to_thread(pss_snapshot,group),phase['guests'])
            detail_options=dict(regular_roots=roots) if detail else {}
            phase['residency']=await asyncio.to_thread(live_residency,group,binding,phase['guests'],template,out,capture_memory_pss=True,**detail_options)
            write(state,result);assert phase['residency']['status']=='OBSERVED'
        else:
            phase['memory_backends']=[ram_backend(g['binding']) for g in phase['guests']]
            assert len({(b['device'],b['inode'],b['rdev']) for b in phase['memory_backends']})==1
            phase['host_measurements']='NOT_REQUESTED'
        if roots is not None:
            if detail:phase['ordinary_residency']=phase['residency']['compact_inventory']['ordinary']
            else:phase['ordinary_residency']=await asyncio.to_thread(residency_check,group,roots,
                    out/('live-regular-N'+str(len(roles))+'.json'),live=True)
            write(state,result);assert phase['ordinary_residency']['status']=='OBSERVED'
        if detail:
            from host_existing_cache_owner import query_bounded
            bundle=getattr(args,'host_checkpoint_bundle',None)
            phase['existing_cache_owner']=await asyncio.to_thread(query_bounded,
                Path(bundle)/'repository.tar.gz' if bundle is not None else None,
                group,out/'existing-cache-owner')
            # Separate later observation: do not extend or reinterpret the original PSS/device window.
            write(state,result)
        for g in phase['guests']:
            assert binding.resolve(g['sandbox_id'])==g['binding']
            g['verification']=verify_instance(g['role'])
            assert g['verification']['action_count']==10
        values=await asyncio.gather(*(final_check(g['role'],g['sandbox_id']) for g in phase['guests']),return_exceptions=True)
        errors=[v for v in values if isinstance(v,BaseException)]
        if errors:raise RuntimeError('matched final check failed: '+repr(errors[0]))
        phase['functional_checks']='PASS';write(state,result)
        rdev=(phase['residency']['backends'] if sampling=='on' and not rootfs_short else phase['memory_backends'])[0]['rdev']
        device=Path('/sys/dev/block')/(str(os.major(rdev))+':'+str(os.minor(rdev)))
        for g in reversed(phase['guests']):
            await asyncio.to_thread(runtime.stop,g['sandbox_id'],'matched-task-finished')
            await asyncio.to_thread(cli,'delete-matched-'+g['role'],'delete',g['sandbox_id'],timeout=60)
            controller.live.pop(g['sandbox_id'])
        deadline=time.monotonic()+15
        while device.exists():
            assert time.monotonic()<deadline,'owned memory device not released'
            await asyncio.sleep(.05)
        phase['device_released_ns']=time.monotonic_ns();phase['status']='PASS'
        no_vmm();write(state,result)
        if rootfs_short and 'measurement_error' in phase:raise RuntimeError(phase['measurement_error'])
    result['status']='PASS';write(state,result)
    return result


def report_matched(source,raw,output):
    """Use saved data only; device reference is separate from group file accounting."""
    from host_file_monitor import linear_reference
    from report_sandbox_runtime import samples
    source,raw,output=map(Path,(source,raw,output))
    result=read(raw/'result.json');trial=read(raw/'host-matched.json')
    assert result['status']==trial['status']=='PASS'
    dual_only=result.get('host_trace_dual',False)
    independent_phase=result.get('host_independent_phase')
    assert result['action_count']==({'single':10,'dual':20}[independent_phase] if independent_phase else 31 if dual_only else 41)
    assert [g['sandboxes'] for g in trial['groups']]==([1 if independent_phase=='single' else 2] if independent_phase else [2] if dual_only else [1,2])
    assert result['checks']['final_source_properties_and_complete_official_outcomes']=='PASS'
    runtime=read(raw/'runtime-monitor.json');assert runtime['status']=='PASS'
    curves,_=samples(raw)
    with (source/'memory.tsv').open() as stream:
        rows=[{k:int(v) if v else None for k,v in row.items()} for row in csv.DictReader(stream,delimiter='\t')]
    pss=[json.loads(line) for line in (source/'pss.jsonl').read_text().splitlines()]
    final=read(raw.parent/'host-file-residency.json');assert final['status']=='OBSERVED'
    output.mkdir(parents=True,exist_ok=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(4,len(trial['groups']),figsize=(6*len(trial['groups']),11),squeeze=False)
    phases=[]
    for col,phase in enumerate(trial['groups']):
        assert phase['status']=='PASS' and phase['functional_checks']=='PASS'
        start,end=phase['start_monotonic_ns'],phase['end_monotonic_ns']
        data=[r for r in rows if start<=r['monotonic_ns']<=end]
        assert data,'missing task host samples'
        endpoint=bound_pss(phase['endpoint_pss']['snapshot'],phase['guests'])
        complete=[]
        for row in pss:
            if start<=row['start_monotonic_ns']<=row['end_monotonic_ns']<=end:
                try:complete.append(dict(start_monotonic_ns=row['start_monotonic_ns'],**bound_pss(row,phase['guests'])))
                except ValueError:continue  # Raw incomplete scans remain in pss.jsonl, never filled with zero.
        devices=[f for f in phase['residency']['files'] if f['kind']=='memory-device']
        assert phase['residency']['status']=='OBSERVED' and len(devices)==1 and devices[0]['stable']
        assert devices[0]['mapping_length']==4*2**30
        ordinary=phase.get('ordinary_residency')
        if trial.get('ordinary_residency_requested') and ordinary is None:
            raise ValueError('requested live regular-file inventory is missing')
        if ordinary is not None:
            assert ordinary['status']=='OBSERVED' and ordinary['observation_mode']=='LIVE' and not ordinary['errors']
            assert end<=ordinary['start_monotonic_ns']<=ordinary['end_monotonic_ns']<phase['device_released_ns']
            keys=[(f['device'],f['inode']) for f in ordinary['files']]
            assert len(keys)==len(set(keys))
        x=[(r['monotonic_ns']-start)/1e9 for r in data];base=phase['baseline']
        axes[0,col].plot(x,[r['file']/2**30 for r in data],label='Host cgroup file',color='#2563eb')
        axes[0,col].axhline(base['file']/2**30,linestyle=':',color='#64748b',label='Before restore')
        axes[0,col].set_title(f"N={phase['sandboxes']}: identical early_1 suffix")
        for key in ('file','anon','current'):
            axes[1,col].plot(x,[(r[key]-base[key])/2**20 for r in data],label=key)
        axes[2,col].plot([(r['start_monotonic_ns']-start)/1e9 for r in complete],
                         [r['vmm_pss_bytes']/2**20 for r in complete],label='Task periodic VMM PSS',marker='.')
        axes[2,col].scatter([(endpoint['snapshot']['start_monotonic_ns']-start)/1e9],
                            [endpoint['vmm_pss_bytes']/2**20],label='Post-task VMM PSS',marker='x',color='#7c3aed')
        paired=phase['residency'].get('memory_device_pss')
        if paired is not None:
            from run_host_file_restore import paired_memory_pss
            checked=paired_memory_pss(paired['before']['snapshot'],devices[0],paired['after']['snapshot'],phase['guests'])
            if checked!=paired:raise ValueError('paired memory PSS summary does not match saved observations')
            observations=[paired['before'],paired['after']]
            axes[2,col].scatter([(o['snapshot']['start_monotonic_ns']-start)/1e9 for o in observations],
                [o['vmm_pss_bytes']/2**20 for o in observations],label='PSS around device query',marker='+',color='#0f766e')
        guests=[]
        for guest in phase['guests']:
            role=guest['role'];path=result['paths'][role]
            assert source_role(role)=='early_1' and path['action_count']==10
            assert path['source_patch_check']==path['independent_check']=='PASS'
            selected=[s for s in runtime['sessions'] if s['sandbox_id']==guest['sandbox_id']]
            assert len(selected)==1 and selected[0]['status']=='PASS'
            values=[r for chunk in curves[guest['sandbox_id']]['chunks'] for r in chunk];assert values
            zero=int(values[0]['monotonic_ns'])
            axes[3,col].plot([(int(r['monotonic_ns'])-zero)/1e9 for r in values],[int(r['Cached_kib'])/1024 for r in values],label=role)
            guests.append(dict(role=role,binding=guest['binding'],host_replay_ns=guest['task_end_ns']-guest['task_start_ns'],
                wait_after_replay_ns=end-guest['task_end_ns'],guest_samples=len(values),**path))
        phases.append(dict(sandboxes=phase['sandboxes'],duration_ns=end-start,baseline=base,host_samples=len(data),
            file_range_bytes=[min(r['file'] for r in data),max(r['file'] for r in data)],
            file_delta_range_bytes=[min(r['file']-base['file'] for r in data),max(r['file']-base['file'] for r in data)],
            endpoint_pss=endpoint,complete_task_pss_count=len(complete),memory_device=devices[0],
            memory_device_pss=paired,
            ordinary_residency=ordinary,before_restore_ordinary=phase.get('before_restore_ordinary'),
            raw_memory_layers=[f for f in phase['residency']['files'] if f['kind']=='raw-memory-layer'],
            residency=phase['residency'],guests=guests))
        if 'compact_inventory' in phase['residency']:
            from host_memory_detail_report import memory_detail
            phases[-1]['memory_detail']=memory_detail(phase['residency'],existing_cache_owner=phase.get('existing_cache_owner'))
        for row,label in enumerate(('Host file / GiB','Delta before restore / MiB','Mapped residency / MiB','Guest Cached / MiB')):
            axes[row,col].set_ylabel(label);axes[row,col].grid(alpha=.2);axes[row,col].legend(fontsize=8)
        for row in range(3):axes[row,col].set_xlabel('Host seconds since suffix release')
        axes[3,col].set_xlabel('Guest sampler lifetime / s (not host-aligned; includes checks)')
    for row in range(4):
        ranges=[ax.get_ylim() for ax in axes[row]]
        for ax in axes[row]:ax.set_ylim(min(v[0] for v in ranges),max(v[1] for v in ranges))
    fig.suptitle('Imported original snapshot; independent writable service\nHost file is not guest Cached' if independent_phase else 'One snapshot; traced N=2 only; host file is not guest Cached' if dual_only else 'One snapshot; N=1 then N=2; host file is not guest Cached')
    fig.tight_layout();fig.savefig(output/'matched.png',dpi=150);fig.savefig(output/'matched.svg');plt.close(fig)
    reference=None
    if not dual_only and not independent_phase:
        a,b=(g['memory_device']['resident_bytes'] for g in phases)
        reference=linear_reference(a,b,2) if a>0 else None
    summary=dict(status='PASS',functional_validation='PASS',action_count=result['action_count'],groups=phases,
        independent_phase=independent_phase,
        baseline_file_difference_bytes=phases[1]['baseline']['file']-phases[0]['baseline']['file'] if len(phases)==2 else None,
        device_linear_reference=reference,device_reference_scope='Only the shared memory-device cache, at post-suffix scan windows; fixed order with different host cache states. Hypothetical separate devices, not a measured no-sharing backend or causal saving.',
        final_inventory=final,accounting_validation=final['accounting'],
        whole_host_physical_savings='NOT_MEASURED',guest_cache_physical_attribution='NOT_MEASURED',
        scope='Identical early_1 suffixes; common prefix once; group file curves and global device residency are separate quantities, never added. Raw layers and final regular files are different objects/windows.')
    write(output/'summary.json',summary)
    return summary


def report_matched_off(raw,output):
    """Functional and timing report for explicitly unrequested host measurements."""
    raw,output=map(Path,(raw,output))
    task=read(raw/'result.json');trial=read(raw/'host-matched.json');runtime=read(raw/'runtime-monitor.json')
    assert task['status']==trial['status']==runtime['status']=='PASS'
    assert trial['host_sampling']=='off' and trial['host_measurements']=='NOT_REQUESTED'
    assert task['action_count']==41 and task['logical_action_count']==63
    assert set(task['paths'])=={'backbone',*INSTANCES} and task['paths']['backbone']['action_count']==11
    assert all(value=='PASS' for value in task['checks'].values())
    assert not runtime['host_vmm_requested'] and all(s['status']=='PASS' for s in runtime['sessions'])
    assert [g['sandboxes'] for g in trial['groups']]==[1,2]
    groups=[]
    for phase,roles in zip(trial['groups'],(INSTANCES[:1],INSTANCES[1:])):
        assert phase['status']=='PASS' and phase['functional_checks']=='PASS'
        assert phase['baseline'] is None and phase['host_measurements']=='NOT_REQUESTED'
        assert not {'residency','endpoint_pss','ordinary_residency'} & phase.keys()
        assert [g['role'] for g in phase['guests']]==list(roles)
        assert len({(b['device'],b['inode'],b['rdev']) for b in phase['memory_backends']})==1
        paths=[]
        for g in phase['guests']:
            path=task['paths'][g['role']]
            assert path['action_count']==10 and path['source_patch_check']==path['independent_check']=='PASS'
            assert path['official_passed']==0 and path['official_failed']==2
            paths.append(dict(role=g['role'],tool_ns=path['guest_action_sum_ns'],
                host_replay_ns=g['task_end_ns']-g['task_start_ns']))
        groups.append(dict(sandboxes=phase['sandboxes'],duration_ns=phase['end_monotonic_ns']-phase['start_monotonic_ns'],paths=paths))
    summary=dict(status='PASS_TASK_TIMING_CONTROL',host_sampling='off',host_measurements='NOT_REQUESTED',
        actual_actions=41,logical_actions=63,groups=groups,controller_elapsed_ns=task['controller_elapsed_ns'],
        actual_tool_ns=sum(p['guest_action_sum_ns'] for p in task['paths'].values()),
        guest_profile=runtime['profile'],guest_interval_seconds=runtime['interval_seconds'],
        scope='Same task, guest sampling, accounting and resource/identity checks. No host counter/PSS/mincore sampling; missing host quantities are not zero.')
    output.mkdir(parents=True,exist_ok=False);write(output/'summary.json',summary)
    return summary
