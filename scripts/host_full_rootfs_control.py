"""Complete GRPO/BPO: fixed initial-lower copies, generated layers kept separate."""
import os
from pathlib import Path
import time

from host_private_files import read,write,query_objects
from host_file_monitor import counters
from host_rootfs_layers import file_identity,rootfs_inventory,validate_rootfs_inventory

ROOT=Path(__file__).resolve().parents[1]
PROFILE='full-grpo-rootfs-copies-v1'


def validate_mode(args):
    mode=getattr(args,'host_full_rootfs_mode',None)
    if mode is None:return None
    if mode not in ('shared','private') or getattr(args,'prettier6604_schedule',getattr(args,'schedule','grpo')) not in ('grpo','bpo'):
        raise ValueError('full rootfs copy control requires complete GRPO or BPO')
    if any(getattr(args,key,None) for key in ('host_prefix_count','host_scaling_mode','host_shared_files',
        'host_restore_short','host_matched_task','host_independent_phase','host_rootfs_short',
        'prettier6604_role','role','damon_disable','damon_observe','snapshot_cache_pair')):
        raise ValueError('full rootfs copy control cannot combine with another experiment')
    return mode


def manifest_layers():
    rows=read(ROOT/'docs/records/evidence/host-full-private-scope-20261024.json')['base_manifest']
    assert len(rows)==19 and len({row['source_digest'] for row in rows})==19
    return rows


def prepare(mount,mode,call,*,schedule='grpo'):
    if mode not in ('shared','private'):raise ValueError('unknown full rootfs mode')
    if schedule not in ('grpo','bpo'):raise ValueError('unknown full rootfs schedule')
    mount=Path(mount);env=mount/'env';control=env/'rootfs-copy-control';control.mkdir()
    start=time.monotonic_ns();cpu=time.thread_time_ns();copies=[]
    for slot in range(7 if mode=='private' else 1):
        selected=[]
        for index,source in enumerate(manifest_layers()):
            original=env/'image-cache/commits'/source['relative']
            if file_identity(original)['size']!=source['size']:raise ValueError('initial layer size changed')
            path=original if slot==0 else control/'copies'/str(slot)/source['relative']
            if slot:
                path.parent.mkdir(parents=True,exist_ok=True)
                call(f'copy-rootfs-{slot}-{index}',['cp','-a','--reflink=never','--sparse=always','--',original,path],120)
            path.chmod(0o444);info=file_identity(path)
            if info['size']!=source['size']:raise ValueError('prepared layer copy size mismatch')
            selected.append(dict(path=str(path),relative=source['relative'],digest=source['source_digest'],identity=info))
        copies.append(selected)
    keys=[(row['identity']['device'],row['identity']['inode']) for rows in copies for row in rows]
    if len(set(keys))!=19*len(copies):raise ValueError('independent prepared layers alias an inode')
    manifest=dict(version=1,mode=mode,schedule=schedule,runtime_root='/workspace/env/firecracker-work',
        receipts='/workspace/env/rootfs-copy-control/receipts',copies=[[
            dict(digest=row['digest'],file='/workspace/env/'+str(Path(row['path']).relative_to(env)),size=row['identity']['size'])
            for row in rows] for rows in copies])
    write(control/'manifest.json',manifest)
    value=dict(profile=PROFILE if schedule=='grpo' else 'full-bpo-rootfs-copies-v1',schedule=schedule,status='PREPARED',mode=mode,copies=copies,
        start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
        scope='Slot 0 reuses the owned initial layers; extra sets are ordinary copies. No payload reread or forced residency.')
    write(control/'preparation.json',value)
    return value


def prepared_objects(preparation):
    mode=preparation['mode'];rows=[]
    for slot,selected in enumerate(preparation['copies']):
        for row in selected:
            if file_identity(Path(row['path']))!=row['identity']:raise ValueError('prepared file changed')
            users=[f'planned-{n}' for n in range(7)] if mode=='shared' else [f'planned-{slot}']
            rows.append(dict(path=row['path'],relative=row['relative'],identity=row['identity'],users=users))
    if len(rows)!=(19 if mode=='shared' else 133):raise ValueError('wrong full copy count')
    return rows


class FullRootfsControl:
    def __init__(self,mode,out,group,binding,*,schedule='grpo'):
        self.out=Path(out);self.group=group;self.binding=binding
        self.env=self.out.parent.parent/'env';self.control=self.env/'rootfs-copy-control'
        self.templates={};self.restored={}
        self.state=dict(profile=PROFILE if schedule=='grpo' else 'full-bpo-rootfs-copies-v1',mode=mode,status='PREPARED',pss='NOT_REQUESTED',schedule=schedule,
            scope='Initial 19 lower-file cache only; same complete seven-guest schedule, generated layers separate, not whole RAM')
        if schedule=='bpo':self.state.update(phases=[],generated_layers=dict(P0=dict(status='NOT_CREATED',resident_bytes=None)))

    def save(self):write(self.out/'host-full-rootfs.json',self.state)

    def before(self):
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        self.preparation=read(self.control/'preparation.json')
        if self.preparation['mode']!=self.state['mode']:raise ValueError('prepared mode differs from requested mode')
        if self.preparation.get('schedule','grpo')!=self.state['schedule']:raise ValueError('prepared schedule differs')
        self.state['preparation']=self.preparation
        self.objects=prepared_objects(self.preparation)
        self.state['P0_metadata']=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu)
        self.state['P0']=query_objects(self.objects,self.group,'prepared-before-guests')
        self.state['P0']['scope']='Prepared planned copies, not yet confirmed live guest references'
        self.save()

    def phase(self,label,guests,*,template=None,step=None,restored=None):
        start=time.monotonic_ns();cpu=time.thread_time_ns();before=counters(self.group)
        if template:self.templates[template]=step
        if restored:self.restored.update(restored)
        bindings={role:self.binding.resolve(sid) for role,sid in guests.items()}
        after=counters(self.group)
        self.state['phases'].append(dict(label=label,start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
            counters_before=before,counters_after=after,bindings=bindings,templates=dict(self.templates),restored=dict(self.restored),residency='NOT_QUERIED'))
        self.save()

    def after(self,guests,paths_end_ns):
        from host_prefix_compare import drives_for_vm
        from host_rollout_shared import opened_regular_files
        self.state['paths_end_monotonic_ns']=paths_end_ns
        start=time.monotonic_ns();cpu=time.thread_time_ns()
        try:
            if len(guests)!=7:raise ValueError('complete copy control requires seven live guests')
            bindings={role:self.binding.resolve(sid) for role,sid in guests.items()}
            drives={role:drives_for_vm(vm) for role,vm in bindings.items()}
            roots=rootfs_inventory(bindings,drives,opened_regular_files(self.binding.cgroup),self.restored)
            expected={(r['identity']['device'],r['identity']['inode']):r for r in self.objects}
            if roots['status']!='IDENTIFIED':raise ValueError('actual rootfs inventory incomplete')
            generated=self.generated_objects(roots) if self.state['schedule']=='bpo' else []
            generated_keys={(r['identity']['device'],r['identity']['inode']) for r in generated}
            if set(expected)&generated_keys or {(r['device'],r['inode']) for r in roots['objects']}!=set(expected)|generated_keys:
                raise ValueError('actual lower inventory differs from prepared target identities')
            wanted=[r['source_digest'] for r in manifest_layers()];slots=[];receipts={}
            for role,guest in roots['guests'].items():
                if [r['reference']['digest'] for r in guest['lowers'][:19]]!=wanted:raise ValueError('actual initial layer order changed')
                if self.state['schedule']=='grpo' and len(guest['lowers'])!=19:raise ValueError('unexpected generated GRPO layers')
                name=Path(os.readlink('/proc/'+str(bindings[role]['pid'])+'/cwd')).name
                receipt=read(self.control/'receipts'/(name+'.json'));receipts[role]=receipt;slots.append(receipt['slot'])
                if receipt['mode']!=self.state['mode']:raise ValueError('daemon assignment mode differs')
                if receipt.get('schedule','grpo')!=self.state['schedule']:raise ValueError('daemon assignment schedule differs')
                actual=[(r['device'],r['inode'],r['size']) for r in guest['lowers'][:19]]
                if actual!=[(r['device'],r['inode'],r['size']) for r in receipt['lowers']]:raise ValueError('actual opened files differ from daemon assignment')
            if sorted(slots)!=([0]*7 if self.state['mode']=='shared' else list(range(7))):raise ValueError('copy sets were reused or left unconsumed')
            active=[]
            for row in roots['objects']:
                key=(row['device'],row['inode'])
                if key in generated_keys:continue
                source=expected[key]
                if len(row['users'])!=(7 if self.state['mode']=='shared' else 1):raise ValueError('unexpected live users per copy')
                row['status']='SELECTED'
                active.append(dict(source,users=row['users']))
            roots['scope']='Explicit initial-lower copy control: all prepared identities confirmed live, including single-user copies; no upper or other devices'
            self.state.update(bindings=bindings,roots=roots,assignments=receipts,
                P1_metadata=dict(start_monotonic_ns=start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu))
            self.state['P1']=query_objects(active,self.group,'all-original-paths-finished')
            if generated:self.state['generated_layers']['P1']=query_objects(generated,self.group,'generated-layers-separate-not-main-savings')
            post_start=time.monotonic_ns();post_cpu=time.thread_time_ns()
            validate_rootfs_inventory(roots)
            if any(self.binding.resolve(guests[role])!=vm for role,vm in bindings.items()):raise ValueError('VMM identity changed during query')
            self.state['P1_postcheck']=dict(start_monotonic_ns=post_start,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-post_cpu)
            self.state['status']='OBSERVED'
        except Exception as error:
            self.state.update(status='FAIL',error=repr(error));raise
        finally:self.save()
        return self.state

    def generated_objects(self,roots):
        roles={'backbone',*[f'{kind}_{i}' for kind in ('early','late') for i in range(1,4)]}
        if set(roots['guests'])!=roles or sorted(self.templates.values())!=[11,24]:raise ValueError('BPO roles or two checkpoint sources incomplete')
        expected_restored=roles-{'backbone'}
        if set(self.restored)!=expected_restored or any(self.templates.get(t)!=(11 if r.startswith('early') else 24) for r,t in self.restored.items()):
            raise ValueError('BPO restored roles do not match checkpoint steps')
        result=[]
        for index,step in [(19,11),(20,24)]:
            users=sorted(roles if step==11 else {r for r in roles if not r.startswith('early')})
            references=[]
            for role,guest in roots['guests'].items():
                if len(guest['lowers'])!=(20 if role.startswith('early') else 21):raise ValueError('wrong BPO generated layer count')
                if role in users:references.append(guest['lowers'][index])
            keys={(r['device'],r['inode']) for r in references}
            if len(keys)!=1:raise ValueError('BPO generated layer lost original sharing')
            key=keys.pop();obj=next(r for r in roots['objects'] if (r['device'],r['inode'])==key)
            if sorted(obj['users'])!=users:raise ValueError('wrong generated layer users')
            result.append(dict(path=obj['path'],relative='generated-step-'+str(step),step=step,identity={k:obj[k] for k in ('device','inode','size','mtime_ns')},users=users,
                references=[r['reference'] for r in references],scope='Generated within this run; unchanged sharing, not included in initial-layer savings'))
        if len({(r['identity']['device'],r['identity']['inode']) for r in result})!=2:raise ValueError('generated checkpoint layers alias')
        return result
