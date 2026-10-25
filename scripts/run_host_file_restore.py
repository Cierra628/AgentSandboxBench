"""Light host measurements for one checkpoint, then one and two restored guests."""
import array
import asyncio
import csv
import fcntl
import json
import os
from pathlib import Path
import stat
import time

ROOT=Path(__file__).resolve().parents[1]
EXPORT=ROOT/'.artifacts/prettier6604/grpo-input-audit-20261008T120626Z'


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def read(path):return json.loads(path.read_text())


def restore_command(command, *, matched=False):
    command=[arg for arg in command if arg not in ('--prettier6604-readonly-replay','--light-monitor')]
    return [*command,'--prettier6604-export',str(EXPORT),'--prettier6604-schedule','bpo','--host-matched-task' if matched else '--host-restore-short']


def ram_backend(vm):
    """Use actual process mappings, not a historical device number."""
    proc=Path('/proc')/str(vm['pid'])
    from host_sandbox_monitor import identity
    assert identity(vm['pid'])=={k:vm[k] for k in ('pid','start_ticks','boot_id')}
    regions=[]
    for line in (proc/'maps').read_text().splitlines():
        fields=line.split(maxsplit=5)
        if len(fields)!=6 or not fields[5].startswith('/dev/ublkb'):continue
        if fields[1]!='rw-p':continue
        start,end=(int(value,16) for value in fields[0].split('-'))
        major,minor=(int(value,16) for value in fields[3].split(':'))
        regions.append(dict(start=start,end=end,offset=int(fields[2],16),device=os.makedev(major,minor),
                            inode=int(fields[4]),permissions=fields[1],path=fields[5]))
    assert regions and len({r['path'] for r in regions})==1, 'one private memory backing required'
    path=proc/'root'/regions[0]['path'].lstrip('/')
    info=path.stat()
    assert stat.S_ISBLK(info.st_mode)
    assert all((r['device'],r['inode'])==(info.st_dev,info.st_ino) for r in regions)
    return dict(path=str(path),device=info.st_dev,inode=info.st_ino,rdev=info.st_rdev,regions=regions)


def bound_pss(snapshot,guests):
    expected={(g['binding']['pid'],g['binding']['start_ticks']) for g in guests}
    rows=[p for p in snapshot['processes'] if p['comm']=='firecracker']
    if len(expected)!=len(guests) or len(rows)!=len(expected) or {(p['pid'],p['start_ticks']) for p in rows}!=expected or any(p['pss']<=0 for p in rows):
        raise ValueError('endpoint PSS does not cover the exact restored VMM identities')
    return dict(vmm_status='OBSERVED',vmm_pss_bytes=sum(p['pss'] for p in rows),snapshot=snapshot)


def paired_memory_pss(before,device,after,guests):
    observations=[bound_pss(value,guests) for value in (before,after)]
    if not (before['start_monotonic_ns']<=before['end_monotonic_ns']<=device['start_monotonic_ns']
            <=device['end_monotonic_ns']<=after['start_monotonic_ns']<=after['end_monotonic_ns']):
        raise ValueError('PSS observations must bracket the memory-device query')
    for observation in observations:
        rows=[p for p in observation['snapshot']['processes'] if p['comm']=='firecracker']
        for key in ('pss_anon','pss_file','pss_shmem'):
            observation['vmm_'+key+'_bytes']=sum(p[key] for p in rows) if all(p.get(key) is not None for p in rows) else None
    return dict(status='OBSERVED' if all(o['vmm_pss_anon_bytes'] is not None for o in observations) else 'MISSING_ANON_PSS',
        start_monotonic_ns=before['start_monotonic_ns'],end_monotonic_ns=after['end_monotonic_ns'],
        device_identity={key:device[key] for key in ('device','inode','rdev')},
        device_resident_bytes=device['resident_bytes'],before=observations[0],after=observations[1],
        scope='VMM PSS immediately before/after the target device query, not atomic. Before/after values are observations, not extrema or bounds. No physical total inferred.')


def device_residency(devices,memory_identity,group,guests,*,capture_memory_pss=False,after_memory_query=None):
    from host_file_monitor import mincore_file,pss_snapshot
    files=[];paired=None
    # Scan the target before unrelated 64 GiB devices delay its observation.
    for row in sorted(devices.values(),key=lambda row:row['identity']!=memory_identity):
        capture=capture_memory_pss and row['identity']==memory_identity
        if capture:pss_before=pss_snapshot(group)
        key=row['identity'];query_begin=time.monotonic_ns()
        fd=os.open(row['path'],os.O_RDONLY|os.O_CLOEXEC)
        try:
            current=os.fstat(fd);assert (current.st_dev,current.st_ino,current.st_rdev)==key
            capacity=array.array('Q',[0]);fcntl.ioctl(fd,0x80081272,capacity,True)
            # /proc/self/fd aliases refer to this verified open description.
            value=mincore_file(Path('/proc/self/fd')/str(fd),block_identity=key,block_length=capacity[0])
            value.update(start_monotonic_ns=query_begin,end_monotonic_ns=time.monotonic_ns(),target=row['target'],owner_pid=row['owner_pid'],kind='memory-device' if key==memory_identity else 'other-owned-block-device')
            files.append(value)
        finally:os.close(fd)
        if capture:paired=paired_memory_pss(pss_before,value,pss_snapshot(group),guests)
        if key==memory_identity and after_memory_query:after_memory_query(paired)
    return files,paired


def shared_device_selection(devices,memory_identity,readonly_drives):
    """Select actual shared objects before any block-device residency query."""
    expected={memory_identity[2]:{tuple(memory_identity)}}
    if not readonly_drives:raise ValueError('shared-file query requires an actual shared read-only drive')
    for row in readonly_drives:
        if not row['block_device']:raise ValueError('shared restore drive must be a block device')
        key=(row['device'],row['inode'],row['rdev'])
        expected.setdefault(key[2],set()).add(key)
    for rdev,identities in expected.items():
        if rdev not in devices or tuple(devices[rdev]['identity']) not in identities:
            raise ValueError('shared cache device is missing or its identity changed')
    selected={rdev:row for rdev,row in devices.items() if rdev in expected}
    excluded=[dict(identity=list(row['identity']),target=row['target'],owner_pid=row['owner_pid'],
        status='NOT_REQUESTED',resident_pages=None,resident_bytes=None,
        reason='Outside the mapped snapshot memory device and actual shared read-only drives')
        for rdev,row in devices.items() if rdev not in expected]
    return selected,excluded


def live_residency(group,binding,guests,template,out,*,capture_memory_pss=False,regular_roots=None,shared_readonly_drives=None):
    """Read residency while VMMs own their devices; close every probe before return."""
    from host_file_monitor import counters,mincore_file,pids
    begin=time.monotonic_ns();cpu=time.process_time_ns();before=counters(group)
    backends=[ram_backend(item['binding']) for item in guests]
    keys={(row['device'],row['inode'],row['rdev']) for row in backends}
    assert len(keys)==1,'restored guests do not share one memory backing'
    memory_identity=next(iter(keys));devices={};open_files={}
    # Inventory only processes in this owned service, not the entire host.
    for pid in pids(binding.cgroup):
        directory=Path('/proc')/str(pid)
        try:
            for fd in (directory/'fd').iterdir():
                try:
                    info=fd.stat()
                    if stat.S_ISBLK(info.st_mode):
                        devices.setdefault(info.st_rdev,dict(identity=(info.st_dev,info.st_ino,info.st_rdev),path=str(fd),target=os.readlink(fd),owner_pid=pid))
                    elif stat.S_ISREG(info.st_mode):
                        open_files.setdefault((info.st_dev,info.st_ino),[]).append(dict(pid=pid,fd=fd.name))
                except FileNotFoundError:continue
        except FileNotFoundError:continue
    # The VMM mapping is decisive even if it no longer holds a matching fd.
    devices[memory_identity[2]]=dict(identity=memory_identity,path=backends[0]['path'],target=backends[0]['regions'][0]['path'],owner_pid=guests[0]['binding']['pid'])
    selection={}
    if shared_readonly_drives is not None:
        devices,excluded=shared_device_selection(devices,memory_identity,shared_readonly_drives)
        selection=dict(residency_scope='SHARED_FILES_ONLY',selected_block_identities=[list(row['identity']) for row in devices.values()],
            excluded_devices=excluded)
    compact={}
    def capture_compact(pair):
        from host_file_monitor import residency_check,pss_snapshot
        from host_mapped_backing import mapped_backing_inventory
        ordinary=residency_check(group,regular_roots,out/('live-regular-N'+str(len(guests))+'.json'),live=True)
        query=os.environ.get('ASB_HOST_UBLK_INFO_QUERY')
        if not query:raise ValueError('host memory detail requires the prebuilt metadata query')
        mapped=mapped_backing_inventory(group,ordinary['files'],ublk_query=Path(query),query_deleted_cache=True)
        after=bound_pss(pss_snapshot(group),guests)['snapshot']
        compact.update(status='OBSERVED' if ordinary['status']==mapped['status']==after['status']=='OBSERVED' else 'INCOMPLETE',
            start_monotonic_ns=pair['before']['snapshot']['start_monotonic_ns'],end_monotonic_ns=after['end_monotonic_ns'],
            before_pss=pair['before']['snapshot'],after_pss=after,ordinary=ordinary,mapped_backing=mapped,
            cgroup_after=counters(group),scope='Target device, ordinary cache and mapped backings between PSS observations; secondary block devices queried later. Sequential, not atomic; no physical total inferred.')
        write(out/'compact-memory.json',compact)
    if regular_roots is not None and not capture_memory_pss:raise ValueError('compact inventory requires target PSS observations')
    options=dict(after_memory_query=capture_compact) if regular_roots is not None else {}
    files,paired=device_residency(devices,memory_identity,group,guests,capture_memory_pss=capture_memory_pss,**options)
    memory=next(row for row in files if row['kind']=='memory-device')
    assert memory['mapping_length']==4*2**30
    assert all(0<=r['offset'] and r['offset']+r['end']-r['start']<=memory['mapping_length'] for b in backends for r in b['regions'])
    env=out.parent.parent/'env'
    config=env/'snapshot-local-cache/runtime'/template/'memory/image.json'
    image=read(config);seen=set()
    for lower in image['lowers']:
        name=lower['file'];assert name.startswith('/workspace/env/'), 'memory lower outside owned env'
        path=env/name.removeprefix('/workspace/env/')
        info=path.stat();key=(info.st_dev,info.st_ino)
        assert key in open_files and info.st_size==lower['size'], 'memory lower not open in owned service'
        if key in seen:continue
        seen.add(key);query_begin=time.monotonic_ns();value=mincore_file(path)
        value.update(start_monotonic_ns=query_begin,end_monotonic_ns=time.monotonic_ns(),kind='raw-memory-layer',open_descriptors=open_files[key]);files.append(value)
    assert seen,'no memory lower inventoried'
    assert not image.get('upper',{}).get('data'),'immutable memory image required'
    for item,backend in zip(guests,backends):
        assert binding.resolve(item['sandbox_id'])==item['binding']
        again=ram_backend(item['binding'])
        assert all(again[k]==backend[k] for k in ('device','inode','rdev'))
    after=counters(group);resident=sum(row['resident_bytes'] for row in files)
    shortage=max(0,resident-max(before['file'],after['file']))
    result=dict(status='OBSERVED' if all(row['stable'] for row in files) else 'INCOMPLETE',
        start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.process_time_ns()-cpu,
        cgroup_before=before,cgroup_after=after,backends=backends,files=files,resident_bytes=resident,
        minimum_unaccounted_or_timing_bytes=shortage,
        accounting='SHORTFALL_REQUIRES_RESOLUTION' if shortage else 'NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING',
        scope='Live owned block devices and open immutable memory layers, unique device identities; not a simultaneous physical union or exact guest cache. Final ordinary-file inventory is a separate time window.')
    if capture_memory_pss:result['memory_device_pss']=paired
    if selection:
        result.update(selection,scope='Selected shared read-only block devices, mapped snapshot memory device and open immutable memory layers; excluded owned devices have no residency query. Not a simultaneous physical union or exact guest cache.')
    if regular_roots is not None:
        result['compact_inventory']=compact
        if compact['status']!='OBSERVED':result['status']='INCOMPLETE'
    write(out/('live-residency-N'+str(len(guests))+'.json'),result)
    return result


async def observe(controller,template,parent,runtime,args,out,cli):
    from host_file_monitor import measurement_group,counters,pids
    from host_sandbox_monitor import Binding
    group,_=measurement_group();binding=Binding(args.container,out)
    result=dict(status='INCOMPLETE',template=template,groups=[],suffix_actions=0,percentage_output=False)
    async def remove(sid):
        await asyncio.to_thread(runtime.stop,sid,'short-restore-finished')
        await asyncio.to_thread(cli,'delete-short-restored','delete',sid,timeout=60)
        controller.live.pop(sid)
    def no_vmm():
        assert not [pid for pid in pids(group) if Path('/proc',str(pid),'comm').read_text().strip()=='firecracker']
    for count in (1,2):
        no_vmm()
        phase=dict(sandboxes=count,baseline_start_ns=time.monotonic_ns(),baseline=counters(group),guests=[])
        phase['baseline_end_ns']=time.monotonic_ns()
        result['groups'].append(phase);write(out/'host-restore.json',result)
        for role in ('early_1','early_2')[:count]:
            sid=await controller.start(role,template=template,parent=parent)
            item=dict(role=role,sandbox_id=sid,binding=binding.resolve(sid),restored=read(out/('restored-'+role+'.json')))
            phase['guests'].append(item);write(out/'host-restore.json',result)
        phase['start_monotonic_ns']=time.monotonic_ns()
        await asyncio.sleep(5)
        phase['end_monotonic_ns']=time.monotonic_ns()
        for item in phase['guests']:assert binding.resolve(item['sandbox_id'])==item['binding']
        phase['residency']=await asyncio.to_thread(live_residency,group,binding,phase['guests'],template,out)
        write(out/'host-restore.json',result)
        assert phase['residency']['status']=='OBSERVED'
        # Capture the host device identity before the VMM exits. The sysfs name is global.
        sys_device=Path('/sys/dev/block')/(str(os.major(phase['residency']['backends'][0]['rdev']))+':'+str(os.minor(phase['residency']['backends'][0]['rdev'])))
        for item in reversed(phase['guests']):await remove(item['sandbox_id'])
        deadline=time.monotonic()+15
        while sys_device.exists():
            assert time.monotonic()<deadline,'owned memory device not released'
            await asyncio.sleep(.05)
        phase['device_released_ns']=time.monotonic_ns();phase['status']='PASS'
        no_vmm();write(out/'host-restore.json',result)
    result['status']='PASS';write(out/'host-restore.json',result)
    return result


def phase_pss(rows,guests,start,end):
    expected={(g['binding']['pid'],g['binding']['start_ticks']) for g in guests};found=[]
    assert len(expected)==len(guests)
    for row in rows:
        if not start<=row['start_monotonic_ns']<=row['end_monotonic_ns']<=end or row['status']!='OBSERVED':continue
        vm=[p for p in row['processes'] if p['comm']=='firecracker']
        if len(vm)!=len(expected) or {(p['pid'],p['start_ticks']) for p in vm}!=expected or any(p['pss']<=0 for p in vm):
            raise ValueError('phase PSS does not cover the exact restored VMM identities')
        if found and row['start_monotonic_ns']<=found[-1]['start_monotonic_ns']:raise ValueError('duplicate PSS time')
        found.append(dict(start_monotonic_ns=row['start_monotonic_ns'],end_monotonic_ns=row['end_monotonic_ns'],
                          vmm_pss_bytes=sum(p['pss'] for p in vm),processes=vm))
    if len(found)<2:raise ValueError('fewer than two complete phase PSS observations')
    return found


def report_restore(source,raw,output):
    source,raw,output=map(Path,(source,raw,output))
    result=read(raw/'result.json');trial=read(raw/'host-restore.json')
    assert result['status']==trial['status']=='PASS' and result['action_count']==11
    assert read(raw/'runtime-monitor.json')['status']=='PASS'
    with (source/'memory.tsv').open() as stream:rows=[{k:int(v) if v else None for k,v in r.items()} for r in csv.DictReader(stream,delimiter='\t')]
    pss=[json.loads(line) for line in (source/'pss.jsonl').read_text().splitlines()]
    # Session metadata includes host monotonic start/stop bounds; guest clock itself differs.
    sessions=read(raw/'runtime-monitor.json')['sessions']
    from report_sandbox_runtime import samples
    curves,_=samples(raw)
    final=read(raw.parent/'host-file-residency.json')
    assert final['status']=='OBSERVED'
    output.mkdir(parents=True,exist_ok=False)
    groups=[]
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(4,2,figsize=(12,11),sharex=False)
    for col,phase in enumerate(trial['groups']):
        start,end=phase['start_monotonic_ns'],phase['end_monotonic_ns']
        data=[r for r in rows if start<=r['monotonic_ns']<=end]
        if len(data)<2:raise ValueError('missing phase host counters')
        coverage=phase_pss(pss,phase['guests'],start,end)
        x=[(r['monotonic_ns']-start)/1e9 for r in data];base=phase['baseline']
        for key,label in [('file','file'),('anon','anon'),('current','current')]:
            axes[1,col].plot(x,[(r[key]-base[key])/2**20 for r in data],label=label)
        axes[0,col].plot(x,[r['file']/2**30 for r in data],label='Host file (includes shmem)',color='#2563eb')
        axes[0,col].axhline(base['file']/2**30,linestyle=':',color='#64748b',label='Before restore')
        axes[0,col].set_title(f"N={phase['sandboxes']}: after all restore checks")
        axes[2,col].plot([(r['start_monotonic_ns']-start)/1e9 for r in coverage],[r['vmm_pss_bytes']/2**20 for r in coverage],label='Sum of bound VMM PSS',color='#7c3aed',marker='.')
        guest_samples={}
        for guest in phase['guests']:
            selected=[s for s in sessions if s['sandbox_id']==guest['sandbox_id']]
            assert len(selected)==1 and selected[0]['status']=='PASS'
            # Use the established session reader for paths and sample validation.
            chunks=curves[guest['sandbox_id']]['chunks'];values=[r for chunk in chunks for r in chunk]
            guest_samples[guest['sandbox_id']]=len(values)
            # No guest/host clock alignment is assumed: guest curve starts at its own sampler.
            zero=int(values[0]['monotonic_ns'])
            axes[3,col].plot([(int(r['monotonic_ns'])-zero)/1e9 for r in values],[int(r['Cached_kib'])/1024 for r in values],label=guest['role'])
        groups.append(dict(sandboxes=phase['sandboxes'],baseline=base,host_samples=len(data),
            duration_ns=end-start,file_range_bytes=[min(r['file'] for r in data),max(r['file'] for r in data)],
            file_delta_range_bytes=[min(r['file']-base['file'] for r in data),max(r['file']-base['file'] for r in data)],
            pss=coverage,guest_samples=guest_samples,residency=phase['residency']))
        for row in range(4):axes[row,col].grid(alpha=.2);axes[row,col].legend(fontsize=8)
        axes[0,col].set_ylabel('Host file / GiB');axes[1,col].set_ylabel('Delta from before restore / MiB')
        axes[2,col].set_ylabel('Mapped residency / MiB');axes[3,col].set_ylabel('Guest Cached / MiB')
        axes[2,col].set_xlabel('Host seconds since restore checks completed')
        axes[3,col].set_xlabel('Guest sampler lifetime / s (not host-window aligned)')
    for row in range(4):
        limits=[ax.get_ylim() for ax in axes[row]]
        low=min(v[0] for v in limits) if row==0 else 0
        for ax in axes[row]:ax.set_ylim(low,max(v[1] for v in limits))
    fig.suptitle('One snapshot: N=1 then N=2; host file is not guest Cached',fontsize=13)
    fig.text(.5,.008,'Final file-inventory accounting shortfall: '+format(final['minimum_unaccounted_or_timing_bytes']/2**30,'.3f')+' GiB; no savings ratio',ha='center',fontsize=9)
    fig.tight_layout(rect=(0,.025,1,.97));fig.savefig(output/'restore.png',dpi=150);fig.savefig(output/'restore.svg');plt.close(fig)
    summary=dict(status='PASS',groups=groups,action_count=11,suffix_actions=0,percentage_output=False,
        baseline_file_difference_bytes=groups[1]['baseline']['file']-groups[0]['baseline']['file'],
        scope='Fixed-order idle restore validation; preparation and pre-restore baselines retained. No savings ratio or exact guest-cache physical attribution.')
    checks=[g['residency'] for g in trial['groups']]+[final]
    summary['accounting_validation']='SHORTFALL_REQUIRES_RESOLUTION' if any(c['minimum_unaccounted_or_timing_bytes'] for c in checks) else 'NO_SHORTFALL_DETECTED_NOT_PROOF_OF_COMPLETE_ACCOUNTING'
    summary['final_inventory']={k:final[k] for k in ('resident_bytes','cgroup_before','cgroup_after','minimum_unaccounted_or_timing_bytes','accounting','cpu_ns')}
    summary['functional_validation']='PASS'
    write(output/'summary.json',summary)
    return summary


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Recompute a host restore report from saved files only')
    parser.add_argument('host_samples',type=Path);parser.add_argument('raw',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=report_restore(args.host_samples,args.raw,args.output)
    print('status='+value['status']+' accounting='+value['accounting_validation']+' result_dir='+str(args.output))
