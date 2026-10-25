"""One fresh service/cgroup, cumulative 1/2/4/7 guests; no measurement pause."""
import asyncio
import json
import os
import time
from pathlib import Path

SIZES=(1,2,4,7)
FILE_PROFILE='shared-files-v1'
ROOTFS_PROFILE='shared-rootfs-reference-v1'
CATEGORIES=('shared_readonly_backing','prepared_input','snapshot_memory_device',
    'snapshot_memory_layer','other_owned_backing')
REFERENCE_CATEGORIES=('shared_readonly_backing','snapshot_memory_device','snapshot_memory_layer')


def object_key(row):
    # Distinct block-device nodes with one rdev use the same block cache.
    return ['block',row['rdev']] if row['block_device'] else ['regular',row['device'],row['inode']]


def classify_objects(observation,mode,roles,drive_inventory=None):
    objects={}
    def add(row,category,users,aliases):
        if not (row['stable'] or (row.get('identity_stable') and row.get('size_stable'))):
            raise ValueError('shared object identity or size changed during query')
        key=tuple(object_key(row))
        item=dict(object_key=list(key),category=category,users=sorted(set(users)),aliases=sorted(set(aliases)),
            resident_bytes=row['resident_bytes'],mapping_length=row['mapping_length'],
            query_start_monotonic_ns=row.get('start_monotonic_ns'),query_end_monotonic_ns=row.get('end_monotonic_ns'))
        if key in objects:
            old=objects[key]
            if old['category']!=category or old['mapping_length']!=item['mapping_length']:
                raise ValueError('one cache object has conflicting classifications')
            old['users']=sorted(set(old['users']+item['users']))
            old['aliases']=sorted(set(old['aliases']+item['aliases']))
        else:objects[key]=item
    if mode=='cold':
        if observation['regular']['status']!='OBSERVED':raise ValueError('prepared input query incomplete')
        for row in observation['regular']['files']:add(row,'prepared_input',[],[row['path']])
        for drive in observation['shared_readonly_drives']:
            if set(drive['users'])!=set(roles):raise ValueError('shared drive does not cover every active guest')
            row=dict(drive['residency'],start_monotonic_ns=drive['start_monotonic_ns'],end_monotonic_ns=drive['end_monotonic_ns'])
            add(row,'shared_readonly_backing',drive['users'],drive.get('drive_ids',[drive['drive_id']]))
    else:
        from host_prefix_compare import shared_drives
        readonly={rows[0]['rdev']:rows for rows in shared_drives(drive_inventory)}
        if observation['status']!='OBSERVED':raise ValueError('restored backing query incomplete')
        for row in observation['files']:
            kind=row['kind']
            if kind=='memory-device':category='snapshot_memory_device';users=roles
            elif kind=='raw-memory-layer':category='snapshot_memory_layer';users=roles
            elif row['rdev'] in readonly:category='shared_readonly_backing';users=roles
            else:category='other_owned_backing';users=[]
            add(row,category,users,[row.get('target',row['path'])])
    result=list(objects.values())
    if not any(v['category']==('shared_readonly_backing' if mode=='cold' else 'snapshot_memory_device') for v in result):
        raise ValueError('no target shared cache object observed')
    return result


def observe_files(args,out,guests,group,binding,saved):
    if os.environ.get('ASB_HOST_ROOTFS_REFERENCE')=='on':
        from host_scaling_reference import observe_reference
        return observe_reference(out,guests,group,binding,saved)
    from host_file_monitor import counters
    begin=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(group)
    mode=options(args)
    if mode=='cold':
        from host_prefix_compare import observe_prefix
        observation=observe_prefix(args,out,'scaling-N'+str(len(guests)),guests,file_focus=True)
        inventory=observation['drives']
    else:
        from run_host_file_restore import live_residency
        from host_prefix_compare import drives_for_vm,shared_drives
        live=[dict(role=r,sandbox_id=sid,binding=binding.resolve(sid)) for r,sid in guests.items()]
        inventory={v['role']:drives_for_vm(v['binding']) for v in live}
        readonly=[rows[0] for rows in shared_drives(inventory)]
        observation=live_residency(group,binding,live,saved['template'],out,shared_readonly_drives=readonly)
    objects=classify_objects(observation,mode,list(guests),inventory)
    if mode=='cold':
        assert all(binding.resolve(v['sandbox_id'])=={k:v[k] for k in ('pid','start_ticks','boot_id','api_socket','cgroup','binding_source')} for v in observation['vms'])
    after=counters(group);end=time.monotonic_ns()
    return dict(status='OBSERVED',profile=FILE_PROFILE,sandboxes=len(guests),
        start_monotonic_ns=begin,end_monotonic_ns=end,elapsed_ns=end-begin,cpu_ns=time.thread_time_ns()-cpu,
        counters_before=before,counters_after=after,objects=objects,observation=observation,drive_inventory=inventory,
        scope='Unique cache objects, global residency regardless of charge group; categories kept separate. Sequential queries, not an atomic snapshot or exact guest Cached attribution.')


def file_reference(first,last):
    assert first['sandboxes']==1 and last['sandboxes']==7
    results=[]
    for category in REFERENCE_CATEGORIES:
        a={tuple(o['object_key']):o for o in first['objects'] if o['category']==category}
        b={tuple(o['object_key']):o for o in last['objects'] if o['category']==category}
        if a.keys()!=b.keys():raise ValueError('target cache objects changed between N=1 and N=7: '+category)
        for key,row in a.items():
            other=b[key]
            assert row['mapping_length']==other['mapping_length'] and len(row['users'])==1 and len(other['users'])==7
        if not a:continue
        single=sum(o['resident_bytes'] for o in a.values());multiple=sum(o['resident_bytes'] for o in b.values())
        reference=7*single
        results.append(dict(category=category,object_count=len(a),single_bytes=single,multiple_bytes=multiple,
            reference_bytes=reference,reduction_bytes=reference-multiple,
            reduction_fraction=(reference-multiple)/reference if reference else None))
    return results



def options(args):
    mode=getattr(args,'host_scaling_mode',None)
    if mode is None:return None
    if mode not in ('cold','restore'):raise ValueError('unknown host scaling mode')
    if getattr(args,'host_independent_phase',None) or getattr(args,'host_matched_task',False) or getattr(args,'host_restore_short',False) or getattr(args,'host_prefix_count',None):
        raise ValueError('scaling cannot combine with other host experiments')
    bundle=getattr(args,'host_checkpoint_bundle',None);source=getattr(args,'host_checkpoint_source',None)
    if (mode=='restore' and not (bundle and source)) or (mode=='cold' and (bundle or source)):
        raise ValueError('restore scaling requires one existing bundle and prefix evidence; cold has neither')
    return mode


def arguments(mode,bundle=None,source=None):
    return ['--host-scaling-mode',mode,*(['--host-checkpoint-bundle',str(bundle),
        '--host-checkpoint-source',str(source)] if mode=='restore' else [])]


def paths(rollouts,mode):
    if mode not in ('cold','restore') or len(rollouts)!=7:raise ValueError('scaling requires the seven pinned paths')
    assert all(p[:2]==['read_repo','read_typescript'] for p in rollouts.values())
    return {role:list(path[:2] if mode=='cold' else rollouts['early_1'][2:]) for role,path in rollouts.items()}


def meminfo(text):
    values={line.split(':',1)[0]:int(line.split()[1])*1024 for line in text.splitlines()
        if len(line.split())==3 and line.split()[2]=='kB'}
    return {key:values[key] for key in ('MemTotal','MemAvailable','Cached','Shmem')}


def sync_preparation(call,mount,*,timeout=60):
    begin=time.monotonic_ns()
    call('sync-scaling-preparation',['sync','-f',mount],timeout)
    return dict(status='PASS',start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),
        scope='Owned filesystem after preparation/import, before private service or guest creation; no global sync or cache drop')


async def checkpoint(group,binding,guests,cli,label):
    from host_file_monitor import counters
    from run_prettier6604_grpo import joined
    begin=time.monotonic_ns();main=counters(group);counter_end=time.monotonic_ns()
    identities={r:binding.resolve(sid) for r,sid in guests.items()}
    assert len({(v['pid'],v['start_ticks']) for v in identities.values()})==len(guests)
    async def guest_row(role,sid):
        start=time.monotonic_ns()
        value=await asyncio.to_thread(cli,'scaling-meminfo-'+label+'-'+role,'exec',sid,'cat','/proc/meminfo')
        return dict(role=role,sandbox_id=sid,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),**meminfo(value))
    memory=await joined(guest_row(r,sid) for r,sid in guests.items())
    assert all(binding.resolve(guests[r])==identity for r,identity in identities.items())
    return dict(label=label,group=str(group),group_id=group.stat().st_ino,
        start_monotonic_ns=begin,counter_end_monotonic_ns=counter_end,counters=main,
        pss=dict(status='NOT_REQUESTED_FILE_FOCUS'),bindings=identities,guest_meminfo=memory,
        counters_after=counters(group),end_monotonic_ns=time.monotonic_ns(),
        scope='Direct phase counters, identity bindings and guest meminfo; sequential, not atomic. Fixed checkpoints stay enabled when periodic host sampling is off.')


async def observe(controller,args,out,cli,executed,verify_instance,*,saved=None):
    from host_file_monitor import measurement_group
    from host_sandbox_monitor import Binding
    from run_prettier6604_grpo import joined
    group,_=measurement_group();binding=Binding(args.container,out)
    guests={};roles=list(executed);mode=options(args)
    profile=ROOTFS_PROFILE if os.environ.get('ASB_HOST_ROOTFS_REFERENCE')=='on' else FILE_PROFILE
    state=dict(status='RUNNING',profile=profile,mode=mode,sizes=list(SIZES),group=str(group),group_id=group.stat().st_ino,
        periodic_host_sampling=os.environ.get('ASB_HOST_FILE_SAMPLING','on'),stages=[],
        whole_physical_savings='NOT_MEASURED')
    def save(): (out/'host-scaling.json').write_text(json.dumps(state,indent=2)+'\n')
    try:
        state['baseline']=await checkpoint(group,binding,guests,cli,'baseline');save()
        for n in SIZES:
            stage=dict(sandboxes=n,status='RUNNING',new_roles=roles[len(guests):n])
            state['stages'].append(stage)
            stage['before_batch']=await checkpoint(group,binding,guests,cli,'before-'+str(n));save()
            new=await joined(controller.start(role,**(dict(template=saved['template'],parent=saved['parent']) if saved else {}))
                for role in stage['new_roles'])
            guests.update(zip(stage['new_roles'],new,strict=True));assert len(set(guests.values()))==n
            stage['batch_ready_monotonic_ns']=time.monotonic_ns();save()
            await joined(controller.run_path(guests[role],role,executed[role]) for role in stage['new_roles'])
            stage['task_end_monotonic_ns']=time.monotonic_ns()
            stage['endpoint']=await checkpoint(group,binding,guests,cli,'after-'+str(n))
            assert stage['endpoint']['group_id']==state['group_id']
            stage['verified_action_counts']={role:verify_instance(role)['action_count'] for role in stage['new_roles']}
            assert all(count==(11 if mode=='cold' else 10) for count in stage['verified_action_counts'].values())
            if n in (1,7):
                stage['file_residency']=await asyncio.to_thread(observe_files,args,out,guests,group,binding,saved)
                if n==7:
                    compare=file_reference
                    if profile==ROOTFS_PROFILE:
                        from host_scaling_reference import reference
                        compare=reference
                    state['shared_file_reference']=compare(state['stages'][0]['file_residency'],stage['file_residency'])
            stage['status']='PASS';save()
        state['task_end_monotonic_ns']=state['stages'][-1]['task_end_monotonic_ns']
        last=state['stages'][-1]['file_residency']
        state['endpoint_residency']=last['observation']
        state['endpoint_residency_query_ns']=last['elapsed_ns']
        state['file_residency_query_total_ns']=sum(s.get('file_residency',{}).get('elapsed_ns',0) for s in state['stages'])
        state['status']='PASS';save()
        return guests,state
    except BaseException as error:
        if state['stages'] and state['stages'][-1]['status']=='RUNNING':state['stages'][-1]['status']='FAIL'
        state.update(status='FAIL',error=repr(error));save()
        # The existing controller owns all successfully created guests, including
        # any created before a peer failed. Its original finally performs cleanup.
        raise


def audit(raw):
    from run_prettier6604_grpo import verify_path
    raw=Path(raw);task=json.loads((raw/'result.json').read_text())
    cfg=json.loads((raw/'effective-config.json').read_text());state=task['host_scaling']
    assert task['status']==state['status']=='PASS'
    mode=state['mode'];executed=paths(cfg['paths'],mode)
    expected_actions=77 if mode=='cold' else 70
    assert task['actual_action_count']==expected_actions and task['reused_action_count']==0
    assert task['host_scaling_mode']==mode and cfg['executed_paths']==executed
    assert task['cleanup']['service_sandbox_list_empty']
    assert [s['sandboxes'] for s in state['stages']]==list(SIZES)
    samples=json.loads((raw/'frozen-input/agent-sampling.json').read_text())['segments']
    names=json.loads((raw/'input-provenance.json').read_text())['original_segment_names']
    total=0
    for role,path in executed.items():
        value=verify_path(raw,role,path,raw/'frozen-input',samples,names,sampled_role='early_1' if mode=='restore' else None)
        assert all(task['paths'][role][key]==value[key] for key in value)
        assert value['action_count']==(11 if mode=='cold' else 10)
        total+=value['guest_action_sum_ns']
    if mode=='cold':assert task['official_evaluation']=='NOT_RUN_PREFIX_ONLY' and task['final_roles']==[]
    else:
        assert task['checks']['restored_files_progress_and_process']=='PASS'
        assert task['cleanup']['snapshots_removed']==[task['imported_snapshot']]
        for role in executed:
            restored=json.loads((raw/('restored-'+role+'.json')).read_text())
            assert restored['status']=='PASS' and restored['template']==task['imported_snapshot']
            assert task['paths'][role]['source_patch_check']==task['paths'][role]['independent_check']=='PASS'
            assert task['paths'][role]['official_passed']==0 and task['paths'][role]['official_failed']==2
            assert task['paths'][role]['official_task_status']=='FAIL'
    baseline=state['baseline'];previous=baseline;stages=[];seen={};roles=list(executed)
    assert baseline['bindings']=={} and baseline['guest_meminfo']==[]
    assert (state['group'],state['group_id'])==(baseline['group'],baseline['group_id'])
    for s in state['stages']:
        end=s['endpoint'];before=s['before_batch'];n=s['sandboxes']
        assert s['status']=='PASS' and s['new_roles']==roles[len(seen):n]
        assert s['verified_action_counts']=={r:11 if mode=='cold' else 10 for r in s['new_roles']}
        for point in (before,end):
            assert (point['group'],point['group_id'])==(baseline['group'],baseline['group_id'])
            assert point['start_monotonic_ns']<=point['counter_end_monotonic_ns']<=point['end_monotonic_ns']
        assert previous['end_monotonic_ns']<=before['start_monotonic_ns']<=s['batch_ready_monotonic_ns']<=s['task_end_monotonic_ns']<=end['start_monotonic_ns']
        assert set(before['bindings'])==set(seen) and list(end['bindings'])==roles[:n]
        assert all(end['bindings'][r]==identity for r,identity in seen.items())
        assert all(before['bindings'][r]==identity for r,identity in seen.items())
        cached_before={v['sandbox_id']:v['Cached'] for v in before['guest_meminfo']}
        cached_after={v['sandbox_id']:v['Cached'] for v in end['guest_meminfo']}
        old_cache={sid:cached_after[sid]-value for sid,value in cached_before.items()}
        delta=lambda base:{k:end['counters'][k]-base['counters'][k]
            if end['counters'][k] is not None and base['counters'][k] is not None else None for k in end['counters']}
        stages.append(dict(sandboxes=n,new_roles=s['new_roles'],baseline_delta=delta(baseline),
            batch_delta=delta(before),since_previous_endpoint_delta=delta(previous),
            old_guest_cached_changes_bytes=old_cache,checkpoint=end,
            duration_ns=s['task_end_monotonic_ns']-before['start_monotonic_ns']))
        seen=end['bindings'];previous=end
    assert len({v['pid'] for v in seen.values()})==7
    file_result={}
    if state.get('profile') in (FILE_PROFILE,ROOTFS_PROFILE):
        observed=[s for s in state['stages'] if 'file_residency' in s]
        assert [s['sandboxes'] for s in observed]==[1,7]
        for s in observed:
            q=s['file_residency']
            assert q['status']=='OBSERVED' and q['profile']==state['profile']
            if state['profile']==ROOTFS_PROFILE:
                from host_scaling_reference import audit_observation
                audit_observation(q,s['endpoint']['bindings'])
            else:assert q['objects']==classify_objects(q['observation'],mode,list(s['endpoint']['bindings']),q['drive_inventory'])
            assert s['endpoint']['end_monotonic_ns']<=q['start_monotonic_ns']<=q['end_monotonic_ns']
            assert q['elapsed_ns']==q['end_monotonic_ns']-q['start_monotonic_ns'] and q['cpu_ns']>=0
            assert len({tuple(o['object_key']) for o in q['objects']})==len(q['objects'])
            assert all(o['category'] in (*CATEGORIES,'shared_rootfs_layer') for o in q['objects'])
            for o in q['objects']:
                assert q['start_monotonic_ns']<=o['query_start_monotonic_ns']<=o['query_end_monotonic_ns']<=q['end_monotonic_ns']
            i=state['stages'].index(s)
            if i<3:assert q['end_monotonic_ns']<=state['stages'][i+1]['before_batch']['start_monotonic_ns']
        compare=file_reference
        if state['profile']==ROOTFS_PROFILE:
            from host_scaling_reference import reference
            compare=reference
        refs=compare(observed[0]['file_residency'],observed[1]['file_residency'])
        assert state['shared_file_reference']==refs
        cost=sum(s['file_residency']['elapsed_ns'] for s in observed)
        assert state['file_residency_query_total_ns']==cost
        assert all(p['pss']['status']=='NOT_REQUESTED_FILE_FOCUS' for p in [baseline,*[v for s in state['stages'] for v in (s['before_batch'],s['endpoint'])]])
        file_result=dict(profile=state['profile'],shared_file_reference=refs,
            object_observations=[s['file_residency'] for s in observed],file_residency_query_total_ns=cost,
            intermediate_query_ns=observed[0]['file_residency']['elapsed_ns'])
    reference={}
    for key in ('anon','current','file'):
        single=stages[0]['baseline_delta'][key]
        reference[key]=[dict(sandboxes=s['sandboxes'],reference_bytes=s['sandboxes']*single,
            reduction_bytes=s['sandboxes']*single-s['baseline_delta'][key]) for s in stages]
    return dict(status='PASS_SCALING_AUDIT',**file_result,mode=mode,actual_actions=expected_actions,tool_ns=total,
        baseline=baseline,stages=stages,linear_accounting_reference=reference,
        periodic_host_sampling=state['periodic_host_sampling'],
        controller_elapsed_ns=task.get('controller_elapsed_ns'),
        task_elapsed_ns=state['task_end_monotonic_ns']-baseline['end_monotonic_ns'],
        endpoint_residency_query_ns=state['endpoint_residency_query_ns'],
        whole_physical_savings='NOT_MEASURED',
        scope='Same service/cgroup and initial baseline; older guests remain running; N=1 query is included in elapsed task sequence, final query is separate. Accounting references, not a measured no-sharing backend or simultaneous physical union.')


def file_plot(summary,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    refs=summary['shared_file_reference']
    fig,axes=plt.subplots(1,1+len(refs),figsize=(6*(1+len(refs)),4),squeeze=False,constrained_layout=True)
    ax=axes[0,0]
    ax.plot(SIZES,[s['baseline_delta']['file']/2**20 for s in summary['stages']],'o-',color='#2563eb')
    ax.set(title='Host group file accounting',xlabel='Live sandboxes',ylabel='MiB above one baseline',xticks=SIZES)
    ax.grid(alpha=.2)
    for ax,row in zip(axes[0,1:],refs):
        ax.plot([1,7],[row['single_bytes']/2**20,row['multiple_bytes']/2**20],'o-',color='#0f766e',label='Observed object cache')
        if row['category']!='snapshot_memory_layer':
            ax.plot([1,7],[row['single_bytes']/2**20,row['reference_bytes']/2**20],'--',color='#94a3b8',label='N x single (hypothetical)')
        ax.set(title=row['category'],xlabel='Live sandboxes',ylabel='Global resident cache / MiB',xticks=[1,7]);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle('Specified file cache; default reclaim; not whole host RAM')
    fig.savefig(output/'scaling.png',dpi=130);fig.savefig(output/'scaling.svg');plt.close(fig)
    lines=['# 共享文件缓存：同组递增短实验','',
        '| 沙箱数 | 整组file增量／MiB |','| --- | ---: |']
    for stage in summary['stages']:lines.append(f"| {stage['sandboxes']} | {stage['baseline_delta']['file']/2**20:.3f} |")
    lines+=['','| 对象类别 | N=1驻留／MiB | N=7驻留／MiB | 相对七份单沙箱参考少占／MiB |',
        '| --- | ---: | ---: | ---: |']
    for row in refs:
        values=[f"{row[k]/2**20:.3f}" for k in ('single_bytes','multiple_bytes')]
        values.append('—（公共导入层）' if row['category']=='snapshot_memory_layer' else f"{row['reduction_bytes']/2**20:.3f}")
        lines.append('| '+row['category']+' | '+' | '.join(values)+' |')
    lines+=['','共享只读后备、公共准备输入、快照内存设备／原始层、其他独立后备分别分类；公共准备不乘七，各类别不合成整体物理量。N=2／4未查询目标对象驻留。',
        *(['', '快照原始层是公共导入对象，只列驻留，不乘七算本轮新增收益。原JSON中的线性算术值保持，展示时不用于该层的节省结论。'] if any(row['category']=='snapshot_memory_layer' for row in refs) else []),
        '',f"工具累计{summary['tool_ns']/1e9:.3f}秒，创建至最后原动作结束{summary['task_elapsed_ns']/1e9:.3f}秒。N=1查询{summary['intermediate_query_ns']/1e9:.3f}秒，包含在递增总时长中；N=7查询{summary['endpoint_residency_query_ns']/1e9:.3f}秒，在最后原动作结束之后。逐对象时段、线程CPU及前后计数见JSON。",
        '', '保持默认回收，不暂停、不预读、不锁页。PSS未请求，anon只保留原始计数。对象驻留不与整组file相加；线性参考不是实测不共享后端。',
        '', '![整组趋势及指定对象驻留](scaling.png)']
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')


def report(raw,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False);summary=audit(raw)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if summary.get('profile') in (FILE_PROFILE,ROOTFS_PROFILE):
        file_plot(summary,output);return summary
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(13,4),constrained_layout=True)
    for ax,key in zip(axes,('anon','current','file')):
        ax.plot(SIZES,[s['baseline_delta'][key]/2**20 for s in summary['stages']],'o-',label='Observed above one baseline')
        ax.plot(SIZES,[s['reference_bytes']/2**20 for s in summary['linear_accounting_reference'][key]],'--',label='N x first increment')
        ax.set(title='Host '+key,xlabel='Live sandboxes',ylabel='MiB',xticks=SIZES);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.suptitle(summary['mode']+': cumulative guests, accounting not whole physical savings')
    fig.savefig(output/'scaling.png',dpi=130);fig.savefig(output/'scaling.svg');plt.close(fig)
    lines=['# 同一统计组内的沙箱数量与内存变化','',
        '| 沙箱数 | anon增量／MiB | current增量／MiB | file增量／MiB |', '| --- | ---: | ---: | ---: |']
    for s in summary['stages']:
        lines.append('| '+str(s['sandboxes'])+' | '+' | '.join(f"{s['baseline_delta'][k]/2**20:.3f}" for k in ('anon','current','file'))+' |')
    lines.extend(['','以同一次无沙箱基线计算；每批前后另存计数、全组PSS及每个guest的meminfo。旧guest缓存变化和读取时段见JSON，不把所有增量都归因于新guest。',
        '', '关闭周期host采样时仍保留固定检查点；指定对象驻留只在最后查询，成本单列，不计入任务墙钟。PSS和驻留不加到current。',
        '', '整体host物理节省未测得。','', '![规模与内存记账](scaling.png)'])
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return summary


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='Rebuild a scaling report from saved metadata only; no services or live queries')
    p.add_argument('--report',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.resolve().is_relative_to(args.report.resolve()):p.error('report output must be outside raw data')
    value=report(args.report,args.output)
    print('status='+value['status']+' result_dir='+str(args.output))
