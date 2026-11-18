"""Explicit private one-guest full-list PFN calibration; no retries or totals."""
import copy
import json
import os
from pathlib import Path
import time
import traceback

from host_backing_pfn_live import host_boot_id
from host_backing_pfn_plan import make_plan,offset_coverage,ENVELOPE_ALLOWANCE_BYTES,METADATA_ALLOWANCE_PER_CHUNK
from host_file_pfn_query import HostFilePfnQuery,file_identity,RAW_BUDGET,VERSION as QUERY_VERSION,PAGE,FIELDS
from host_backing_evidence import summarize
from host_backing_pfns import attach,load_query

VERSION='host-backing-full-pfn-v4'
CHARGE_VERSION='host-backing-full-charge-v1'
CHARGE_CONTEXT_VERSION='host-backing-object-charge-v1'
UPPER_VERSION='host-backing-full-pfn-v3'
PREVIOUS_VERSION='host-backing-full-pfn-v2'
LEGACY_VERSION='host-backing-full-pfn-v1'
CONTEXT_VERSION='host-backing-object-pfn-v4'
UPPER_CONTEXT_VERSION='host-backing-object-pfn-v3'
PREVIOUS_CONTEXT_VERSION='host-backing-object-pfn-v2'
OBJECT_FIELDS=('device','inode','rdev','mode')
CODE_FILES=('host_backing_pfn_batch.py','host_backing_pfn_plan.py','host_backing_pfn_live.py',
    'host_backing_pfns.py','host_file_pfn_query.py','snapshot_cache_residency.py','kernel_cache_metadata.py','vmm_page_evidence.py',
    'host_file_page_charge.py','host_existing_cache_owner.py','host_backing_charge_scope.py')


class RecordBudget:
    """Meter this new PFN directory only, separately from RAM evidence budgets."""
    def __init__(self,root,limit):self.root=Path(root);self.limit=limit
    def used(self):return sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
    def check(self,increment=0):
        if self.used()+increment+ENVELOPE_ALLOWANCE_BYTES>=self.limit:
            raise RuntimeError('full-list actual records exceed strict budget; no truncation/retry')
    def write(self,path,value,*,timing=None):
        if timing is not None:timing.update(path=str(path),start_monotonic_ns=time.monotonic_ns(),status='STARTED')
        try:
            text=json.dumps(value,indent=2)+'\n';self.check(len(text.encode()))
            if timing is not None:timing.update(bytes=len(text.encode()),write_start_monotonic_ns=time.monotonic_ns())
            with Path(path).open('x') as f:f.write(text)
            if timing is not None:timing.update(write_end_monotonic_ns=time.monotonic_ns(),status='WRITTEN')
        except Exception as error:
            if timing is not None:timing.update(status='FAIL',error=repr(error))
            raise
        finally:
            if timing is not None:timing['end_monotonic_ns']=time.monotonic_ns()


def mutable_log(target):
    categories=set(target['categories'])
    return 'owned_logs' in categories and categories <= {'owned_logs','open_regular_file'}


def upper_version_bindings(raw,plan):
    """Saved rootfs configuration + inode aliases, never filename-based permission."""
    from host_rootfs_layers import local_path
    result={}
    files={('regular',f['device'],f['inode']):f for f in raw['expanded_files']['files']}
    for i,target in enumerate(plan['targets']):
        kinds=[k for k in ('data','index') if 'rootfs_upper_'+k in target['categories']]
        if not kinds:continue
        if len(kinds)!=1 or len(target['references'])!=1:raise ValueError('ambiguous upper reference')
        kind=kinds[0];category='rootfs_upper_'+kind
        if not set(target['categories'])<={'owned_firecracker-work',category,'open_regular_file','mapped_file'}:
            raise ValueError('upper conflicts with immutable layer/other backing category')
        ref=target['references'][0];role=ref['role'];vm=raw['bindings'][role]
        guest=raw['rootfs_inventory']['guests'][role]
        if (raw['rootfs_inventory']['status']!='IDENTIFIED' or guest['status']!='IDENTIFIED' or
                guest['pid']!=vm['pid'] or raw['guests'][role]!=ref['sandbox_id']):
            raise ValueError('upper rootfs/VMM reference not identified')
        config=Path(guest['image_config']);drive=guest['drive']
        if (config!=Path('/proc')/str(vm['pid'])/'cwd/overlaybd/image.json' or
                not guest['config_identity'] or drive['drive_id']!='user_rootfs' or
                not drive['block_device'] or drive['is_read_only'] or
                [d for d in raw['drives'][role] if d['drive_id']=='user_rootfs']!=[drive]):
            raise ValueError('upper configuration/drive linkage conflict')
        path=str(local_path(guest['upper'][kind],config,Path('/proc')/str(vm['pid'])))
        row=files[tuple(target['object_key'])]
        if (row['users']!=[role] or not any(a['path']==path and a.get('source')==category for a in row['aliases']) or
                not any(a.get('source','').startswith('open_fd:') and type(a.get('process')) is int for a in row['aliases'])):
            raise ValueError('upper inode lacks matching config alias/open descriptor/reference')
        result[str(i)]=dict(kind=kind,object_key=target['object_key'],reference=ref,pid=vm['pid'],
            image_config=str(config),config_identity=guest['config_identity'],configured_path=path,drive=drive)
    return result


def versioned_targets(plan,bindings):
    return [dict(t,upper_version_binding=bindings[str(i)]) if str(i) in bindings else t
            for i,t in enumerate(plan['targets'])]


def mutable_version(target):
    return mutable_log(target) or bool(target.get('upper_version_binding'))


def version_policy(target):
    return ('LIVE_UPPER_QUERY_VERSION' if target.get('upper_version_binding') else
            'LIVE_LOG_QUERY_VERSION' if mutable_log(target) else 'STRICT_INVENTORY_VERSION')


def differences(before,after):
    return {k:dict(before=v,after=after.get(k)) for k,v in before.items() if after.get(k)!=v}


def identity_decision(target,expected,identity,alias,allow_log_version):
    # Keep the v2 keyword name; v3 also permits explicitly bound live uppers.
    if identity is None or alias is None:return ['IDENTITY_READ_FAILED']
    errors=[]
    if identity!=alias:errors.append('FD_ALIAS_MISMATCH')
    fields=OBJECT_FIELDS if allow_log_version and mutable_version(target) else tuple(expected)
    if any(identity.get(k)!=expected[k] for k in fields if k in expected):errors.append('EXPECTED_IDENTITY_OR_VERSION_CHANGED')
    saved=target['saved_identity']
    fields=OBJECT_FIELDS if mutable_version(target) else tuple(saved)
    if any(identity.get(k)!=saved[k] for k in fields if k in saved):errors.append('INVENTORY_IDENTITY_OR_VERSION_CHANGED')
    return errors


def inspect_file(query,target,expected,*,allow_log_version=False):
    row=dict(monotonic_ns=time.monotonic_ns(),identity=None,alias=None,read_error=None)
    try:
        if query.fd is None:raise OSError('owned query file descriptor already closed')
        row['identity']=file_identity(os.fstat(query.fd))
        row['alias']=file_identity(query.path.stat(follow_symlinks=False))
    except OSError as error:row['read_error']=repr(error)
    row['errors']=identity_decision(target,expected,row['identity'],row['alias'],allow_log_version)
    row['expected_differences']=differences(expected,row['identity'] or {})
    row['inventory_differences']=differences(target['saved_identity'],row['identity'] or {})
    row['status']='FAIL' if row['errors'] else 'PASS'
    return row


def require_identity(observation):
    if observation['status']!='PASS':
        raise ValueError('file identity/version check failed; see saved observation; no fallback')
    return observation['identity']


def query_target(target,identity,chunk_pages,*,record_budget=RAW_BUDGET,row_bytes=32):
    """Explicit derived version/ranges; never edit the original membership plan."""
    if row_bytes not in (32,80):raise ValueError('unsupported query row width')
    if identity_decision(target,identity,identity,identity,True):raise ValueError('query version conflicts with fixed object')
    value=copy.deepcopy(target);size=identity['size'];pages=(size+PAGE-1)//PAGE
    if size<0 or pages*row_bytes>=RAW_BUDGET:raise ValueError('query version exceeds existing raw budget')
    count=(pages+chunk_pages-1)//chunk_pages
    if pages*row_bytes+count*METADATA_ALLOWANCE_PER_CHUNK+ENVELOPE_ALLOWANCE_BYTES>=record_budget:
        raise ValueError('updated query ranges exceed record budget; no truncation/retry')
    value.update(saved_identity=copy.deepcopy(identity),missing_identity_fields=[],size_bytes=size,page_offsets=pages,
        chunk_count=(pages+chunk_pages-1)//chunk_pages,binary_row_bytes=pages*row_bytes,
        status='EMPTY_AT_QUERY_VERSION' if pages==0 else 'PLANNED_QUERY_VERSION',
        ranges=[dict(first_page=i,pages=min(chunk_pages,pages-i),mapping_length=min(chunk_pages*PAGE,size-i*PAGE))
            for i in range(0,pages,chunk_pages)])
    return value


def query_plan(plan,targets):
    value=copy.deepcopy(plan);value['targets']=copy.deepcopy(targets)
    binary=sum(t['binary_row_bytes'] for t in targets)
    allowance=sum(t['chunk_count'] for t in targets)*plan['budget']['metadata_allowance_per_chunk']+ENVELOPE_ALLOWANCE_BYTES
    value['budget'].update(binary_row_bytes=binary,metadata_allowance_bytes=allowance,
        planned_record_bytes=binary+allowance,strictly_within_limit=binary+allowance<plan['budget']['limit_bytes'])
    value.update(status='QUERY_VERSIONS_RECORDED',
        execution='ranges scheduled once at each recorded query version; no common endpoint snapshot',
        scope='per-object query versions, not a simultaneous full-list endpoint')
    return value



def observe_batch(raw,out,recheck,*,record_budget=RAW_BUDGET,charge_scope=None):
    """Fixed object membership, explicit per-query versions; no common snapshot."""
    charged=charge_scope is not None
    from host_file_page_charge import HostFileChargeQuery, VERSION as CHARGE_QUERY_VERSION
    from host_backing_charge_scope import validate as validate_scope, signature
    row_bytes=80 if charged else 32
    Query=HostFileChargeQuery if charged else HostFilePfnQuery
    root=Path(out)/'host-backing-full-pfn';root.mkdir(exist_ok=False)
    budget=RecordBudget(root,record_budget);started=time.monotonic_ns();cpu=time.thread_time_ns()
    value=dict(version=CHARGE_VERSION if charged else VERSION,status='FAIL',host_boot_id=host_boot_id(),process_id=os.getpid(),
        start_monotonic_ns=started,objects=[],query_invocations=0,record_limit_bytes=record_budget,
        version_policy='identified live uppers/logs: query version fixed after probe preparation; other files: original inventory version',
        acceptance_scope='fixed object membership and each saved query version; no common endpoint physical total',
        complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None,
        cross_object_pfn_union_bytes=None,cross_chunk_pfn_union_bytes=None)
    pinned=[];stage='plan';active=None
    try:
        if len(raw['guests'])!=1:raise ValueError('full-list calibration requires one private guest')
        plan=make_plan(dict(raw,status='COLLECTED'),record_budget=record_budget,**({'row_bytes':80} if charged else {}))
        budget.write(root/'plan.json',plan)
        if plan['status']!='PLANNED_NOT_EXECUTED':raise ValueError('full-list plan BLOCKED; no subset selected')
        value['upper_version_bindings']=upper_version_bindings(raw,plan)
        targets=versioned_targets(plan,value['upper_version_bindings'])
        value['bindings_before']=recheck()
        if value['bindings_before']!=raw['bindings'] or any(vm['boot_id']!=value['host_boot_id'] for vm in raw['bindings'].values()):
            raise ValueError('full-list sandbox/host binding conflict')
        if charged:
            value['charge_scope_before']=charge_scope.capture(raw)
            validate_scope(value['charge_scope_before'],raw,value['host_boot_id'])
        stage='pin_all_files'
        for i,target in enumerate(targets):
            active=dict(index=i,object_key=target['object_key'],file=target['candidate_path'])
            directory=root/f'object-{i:04}';directory.mkdir();qd=directory/'query';qd.mkdir()
            query=Query(target['candidate_path'],qd,expected_identity=target['object_key'][1:])
            pinned.append((target,query,directory))
            pin=dict(identity=query.initial,observation=inspect_file(query,target,query.initial,allow_log_version=True))
            budget.write(directory/'pin.json',pin)
            require_identity(pin['observation'])
        effective=copy.deepcopy(plan);effective['targets']=[]
        for i,(target,query,directory) in enumerate(pinned):
            stage='object_'+str(i);active=dict(index=i,object_key=target['object_key'],file=target['candidate_path'])
            pin=json.loads((directory/'pin.json').read_text())
            context=dict(version=CHARGE_CONTEXT_VERSION if charged else CONTEXT_VERSION,status='FAIL',object_key=target['object_key'],
                references=target['references'],file=str(query.path),inventory_identity=target['saved_identity'],
                pinned_identity=pin['identity'],version_policy=version_policy(target),
                host_boot_id=value['host_boot_id'],process_id=os.getpid(),query_invocations=0,
                start_monotonic_ns=time.monotonic_ns())
            value['objects'].append(dict(index=i,object_key=target['object_key'],context=f'object-{i:04}/context.json',status='STARTED'))
            qraw=dict(version=CHARGE_QUERY_VERSION if charged else QUERY_VERSION,status='FAIL',path=str(query.path),
                expected_identity=target['object_key'][1:],page_size=PAGE,first_page=0,
                kernel=query.kernel,permission=query.permission,filesystem_magic=query.magic,
                complete_file_physical_bytes=None,complete_sandbox_physical_bytes=None,snapshot_atomic=False)
            qs=time.monotonic_ns();qc=time.thread_time_ns();actual=None
            try:
                # MMDS checks can write logs. Finish them before defining the
                # query version; check its full version before the next MMDS.
                context['bindings_before']=recheck()
                if context['bindings_before']!=value['bindings_before']:raise ValueError('sandbox reference changed before query')
                if mutable_version(target):
                    # Prepare the expensive probe before freezing a live file's
                    # query version. Keep this observation; it is not the query.
                    context['before_probe_preparation']=inspect_file(query,target,pin['identity'],allow_log_version=True)
                    pre_identity=require_identity(context['before_probe_preparation'])
                    pre_target=query_target(target,pre_identity,plan['budget']['chunk_pages'],record_budget=record_budget,row_bytes=row_bytes)
                    projected=effective['targets']+[pre_target]+plan['targets'][i+1:]
                    estimate=sum(t['binary_row_bytes']+t['chunk_count']*plan['budget']['metadata_allowance_per_chunk'] for t in projected)+ENVELOPE_ALLOWANCE_BYTES
                    context['preparation_projected_record_bytes']=estimate
                    if estimate>=record_budget:raise RuntimeError('updated query ranges exceed record budget; no truncation/retry')
                    context['probe_preparation']=dict(start_monotonic_ns=time.monotonic_ns(),status='STARTED')
                    try:
                        query.attach();budget.check()
                        # MMDS can itself write a log. Finish revalidation before
                        # fixing the post-preparation query version as well.
                        context['bindings_after_probe_preparation']=recheck()
                        if (context['bindings_after_probe_preparation']!=value['bindings_before'] or
                                host_boot_id()!=value['host_boot_id']):
                            raise ValueError('sandbox reference or host boot changed during probe preparation')
                        context['probe_preparation']['status']='READY'
                    except Exception as error:
                        context['probe_preparation'].update(status='FAIL',error=repr(error));raise
                    finally:context['probe_preparation']['end_monotonic_ns']=time.monotonic_ns()
                if charged:
                    context['charge_scope_before']=charge_scope.capture(raw)
                    validate_scope(context['charge_scope_before'],raw,value['host_boot_id'])
                    if signature(context['charge_scope_before'])!=signature(value['charge_scope_before']):
                        raise ValueError('charge group identity changed before query')
                context['before_query']=inspect_file(query,target,pin['identity'],allow_log_version=True)
                identity=require_identity(context['before_query'])
                context['identity']=identity
                # Explicit rebaseline only after the recorded object/version checks.
                query.initial=identity
                actual=query_target(target,identity,plan['budget']['chunk_pages'],record_budget=record_budget,row_bytes=row_bytes)
                context['query_target']=actual;qraw.update(identity=identity,pages=actual['page_offsets'])
                effective['targets'].append(actual)
                # Growth changes ranges, never membership. Reject the projected
                # whole-list budget before attaching rather than truncate logs.
                projected=effective['targets']+plan['targets'][i+1:]
                estimate=sum(t['binary_row_bytes']+t['chunk_count']*plan['budget']['metadata_allowance_per_chunk'] for t in projected)+ENVELOPE_ALLOWANCE_BYTES
                context['projected_record_bytes']=estimate
                if estimate>=record_budget:raise RuntimeError('updated query ranges exceed record budget; no truncation/retry')
                if actual['page_offsets']:
                    context['query_invocations']=1;value['query_invocations']+=1
                    if not mutable_version(target):query.attach();budget.check()
                    for r in actual['ranges']:
                        block=query.query_chunk(r['first_page'],r['pages'],before_write=budget.check)
                        if block['status']!='OBSERVED_NONATOMIC':raise RuntimeError('chunk '+block['status']+'; no retry')
                        budget.check()
                    qraw['status']='OBSERVED_NONATOMIC'
                else:qraw['status']='EMPTY_REVALIDATED'
                if charged:
                    context['charge_scope_after']=charge_scope.capture(raw)
                    validate_scope(context['charge_scope_after'],raw,value['host_boot_id'])
                    if signature(context['charge_scope_after'])!=signature(context['charge_scope_before']):
                        raise ValueError('charge group identity changed during query')
                context['after_query']=inspect_file(query,target,identity)
                context['identity_after']=require_identity(context['after_query'])
                context['bindings_after']=recheck()
                if context['bindings_after']!=value['bindings_before'] or host_boot_id()!=value['host_boot_id']:
                    raise ValueError('sandbox reference or host boot changed')
                context['after_binding_check']=inspect_file(query,target,identity,allow_log_version=True)
                require_identity(context['after_binding_check'])
                context['status']='PASS'
            except Exception as error:
                qraw.update(status='FAIL',error=repr(error));context['error']=repr(error)
                context['failure_observation']=inspect_file(query,target,context.get('identity',pin['identity']))
                raise
            finally:
                try:query.close()
                except Exception as error:
                    context.update(status='FAIL',cleanup_error=repr(error));qraw.update(status='FAIL',cleanup_error=repr(error))
                qraw.update(chunks=query.records,raw_bytes=query.raw_bytes,cleanup=query.cleanup,
                    map_info=getattr(query,'map_info',None),receive_buffer_bytes=getattr(getattr(query,'batch',None),'buffer_bytes',None),
                    costs=dict(host_start_ns=qs,host_end_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-qc,
                        elapsed_ns=time.monotonic_ns()-qs))
                if (qraw.get('cleanup') or {}).get('owned_tracer_exited') is not True or ((context['query_invocations'] or context.get('probe_preparation')) and qraw['cleanup']['tracer_returncode']!=0):
                    context['status']='FAIL';qraw['status']='FAIL'
                context.update(query_status=qraw['status'],end_monotonic_ns=time.monotonic_ns())
                budget.write(directory/'query/result.json',qraw);budget.write(directory/'context.json',context)
                value['objects'][-1]['status']=context['status']
            if context['status']!='PASS':raise RuntimeError('object query/cleanup failed; no retry')
        stage='final_identity_check';active=None
        value['bindings_after']=recheck()
        if value['bindings_after']!=value['bindings_before'] or host_boot_id()!=value['host_boot_id']:
            raise ValueError('full-list ending reference changed')
        if charged:
            value['charge_scope_after']=charge_scope.capture(raw)
            validate_scope(value['charge_scope_after'],raw,value['host_boot_id'])
            if signature(value['charge_scope_after'])!=signature(value['charge_scope_before']):
                raise ValueError('ending charge group identity changed')
        value['endpoint_versions']=[]
        for i,(target,query,directory) in enumerate(pinned):
            active=dict(index=i,object_key=target['object_key'],file=target['candidate_path'])
            # FDs closed. Endpoint is only object continuity for mutable files, not an
            # assertion that their query-time contents remained unchanged.
            row=dict(index=i,monotonic_ns=time.monotonic_ns(),identity=None,read_error=None)
            try:row['identity']=file_identity(query.path.stat(follow_symlinks=False))
            except OSError as error:row['read_error']=repr(error)
            after=row['identity']
            row.update(query_version_differences=differences(query.initial,after or {}),
                errors=identity_decision(target,query.initial,after,after,True))
            value['endpoint_versions'].append(row)
            if row['errors']:raise ValueError('file changed at full-list endpoint; see endpoint_versions')
        budget.write(root/'query-plan.json',query_plan(plan,effective['targets']))
        value['status']='PASS'
    except Exception as error:
        value.update(error=repr(error),failed_stage=stage,failed_object=active)
        with (root/'failure.txt').open('x') as f:f.write(traceback.format_exc())
    finally:
        for target,query,directory in pinned:
            if query.fd is not None:
                try:query.close()
                except Exception as error:value.update(status='FAIL',cleanup_error=repr(error))
        if charged and charge_scope.history is not None:
            value['charge_identity_history']=copy.deepcopy(charge_scope.history.records)
        value.update(end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.thread_time_ns()-cpu,
            elapsed_ns=time.monotonic_ns()-started,record_bytes_before_receipt=budget.used())
        if budget.used()+len((json.dumps(value,indent=2)+'\n').encode())>=record_budget:
            value.update(status='FAIL',budget_error='actual record cap exceeded at final receipt')
        with (root/'result.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    value['actual_record_bytes']=budget.used()
    if value['actual_record_bytes']>=record_budget:value.update(status='FAIL',budget_error='actual record cap exceeded')
    return value


def replay_batch(raw,root):
    """Strict saved receipt/row replay. No live file/proc queries or physical sum."""
    root=Path(root);batch=json.loads((root/'result.json').read_text());plan=json.loads((root/'plan.json').read_text())
    if batch['status']!='PASS':raise ValueError('full-list failure retained; no successful batch to attach')
    charged=batch.get('version')==CHARGE_VERSION
    row_bytes=80 if charged else 32
    from host_backing_charge_scope import validate as validate_scope, signature, associate
    if charged:
        for key in ('charge_scope_before','charge_scope_after'):validate_scope(batch[key],raw,batch['host_boot_id'])
        if signature(batch['charge_scope_before'])!=signature(batch['charge_scope_after']):raise ValueError('batch charge identity conflict')
        history=batch.get('charge_identity_history',[])
        for group in batch['charge_scope_after']['groups']:
            if group not in history:raise ValueError('ending charge identity missing from retained history')
        if not batch['start_monotonic_ns']<=batch['charge_scope_before']['start_monotonic_ns']<=batch['charge_scope_before']['end_monotonic_ns']<=batch['charge_scope_after']['start_monotonic_ns']<=batch['charge_scope_after']['end_monotonic_ns']<=batch['end_monotonic_ns']:
            raise ValueError('batch charge identity time conflict')
    if batch.get('version') not in (LEGACY_VERSION,PREVIOUS_VERSION,UPPER_VERSION,VERSION,CHARGE_VERSION):raise ValueError('unsupported full-list version')
    versioned=batch['version']!=LEGACY_VERSION
    if (batch['bindings_before']!=raw['bindings'] or batch['bindings_after']!=raw['bindings'] or
            plan!=make_plan(dict(raw,status='COLLECTED'),record_budget=batch['record_limit_bytes'],**({'row_bytes':80} if charged else {}))):
        raise ValueError('full-list plan/VMM identity conflicts with saved inventory')
    targets=plan['targets']
    if batch['version'] in (UPPER_VERSION,VERSION,CHARGE_VERSION):
        bindings=upper_version_bindings(raw,plan)
        if batch.get('upper_version_bindings')!=bindings:raise ValueError('upper version binding receipt conflict')
        targets=versioned_targets(plan,bindings)
    inventory=summarize(raw);result=copy.deepcopy(inventory);result_by={tuple(o['object_key']):o for o in result['objects'] if o['kind']=='regular'}
    if len(batch['objects'])!=len(plan['targets']):raise ValueError('full-list receipt count differs from plan')
    queries=[];empty=0;actual_targets=[]
    if versioned and len(batch.get('endpoint_versions',[]))!=len(plan['targets']):raise ValueError('endpoint version receipts missing')
    for i,(entry,target) in enumerate(zip(batch['objects'],targets)):
        expected_path=f'object-{i:04}/context.json'
        if entry['context']!=expected_path or entry['status']!='PASS' or entry['index']!=i or entry['object_key']!=target['object_key']:
            raise ValueError('full-list object order/receipt conflict')
        context=json.loads((root/expected_path).read_text());identity=context['identity']
        if (context['status']!='PASS' or context['identity_after']!=identity or context['object_key']!=target['object_key'] or
                context['references']!=target['references'] or context['bindings_before']!=raw['bindings'] or context['bindings_after']!=raw['bindings'] or
                context['host_boot_id']!=batch['host_boot_id'] or any(v['boot_id']!=batch['host_boot_id'] for v in raw['bindings'].values()) or
                (not versioned and any(identity.get(k)!=v for k,v in target['saved_identity'].items())) or context['file']!=target['candidate_path'] or not batch['start_monotonic_ns']<=context['start_monotonic_ns']<context['end_monotonic_ns']<=batch['end_monotonic_ns']):
            raise ValueError('full-list object version/reference conflict')
        actual=target
        if versioned:
            pin=json.loads((root/f'object-{i:04}/pin.json').read_text())
            context_version={CHARGE_VERSION:CHARGE_CONTEXT_VERSION,VERSION:CONTEXT_VERSION,UPPER_VERSION:UPPER_CONTEXT_VERSION,PREVIOUS_VERSION:PREVIOUS_CONTEXT_VERSION}[batch['version']]
            if (context['version']!=context_version or context['inventory_identity']!=target['saved_identity'] or
                    context['pinned_identity']!=pin['identity'] or
                    context['version_policy']!=version_policy(target) or
                    any(type(pin['identity'].get(k)) is not int or type(identity.get(k)) is not int for k in FIELDS)):
                raise ValueError('explicit inventory/pin/query version receipt conflict')
            observations=[(pin['observation'],pin['identity'],True),
                    (context['before_query'],pin['identity'],True),
                    (context['after_query'],identity,False),(context['after_binding_check'],identity,True)]
            if batch['version'] in (VERSION,CHARGE_VERSION) and mutable_version(target):
                preparation=context['probe_preparation'];before=context['before_probe_preparation']
                if (preparation['status']!='READY' or context['bindings_after_probe_preparation']!=raw['bindings'] or
                        not context['start_monotonic_ns']<=before['monotonic_ns']<=preparation['start_monotonic_ns']<=preparation['end_monotonic_ns']<=context['before_query']['monotonic_ns'] or
                        preparation['end_monotonic_ns']>context['end_monotonic_ns']):
                    raise ValueError('probe preparation/reference timing conflict')
                observations.append((before,pin['identity'],True))
            for observation,expected,allowed in observations:
                observed=observation['identity']
                if (observation['status']!='PASS' or observation['errors'] or observation['read_error'] is not None or
                        any(type(observed.get(k)) is not int for k in FIELDS) or
                        identity_decision(target,expected,observed,observation['alias'],allowed) or
                        observation['expected_differences']!=differences(expected,observed) or
                        observation['inventory_differences']!=differences(target['saved_identity'],observed) or
                        not batch['start_monotonic_ns']<=observation['monotonic_ns']<=batch['end_monotonic_ns']):
                    raise ValueError('saved version observation conflicts with policy')
            if (context['before_query']['identity']!=identity or context['after_query']['identity']!=identity or
                    not context['start_monotonic_ns']<=context['before_query']['monotonic_ns']<=context['after_query']['monotonic_ns']<=context['after_binding_check']['monotonic_ns']<=context['end_monotonic_ns'] or
                    pin['observation']['monotonic_ns']>context['start_monotonic_ns']):
                raise ValueError('query identity/version window conflict')
            actual=query_target(target,identity,plan['budget']['chunk_pages'],record_budget=batch['record_limit_bytes'],row_bytes=row_bytes);actual_targets.append(actual)
            if context['query_target']!=actual:raise ValueError('query ranges/version differ from receipt')
            if batch['version'] in (VERSION,CHARGE_VERSION) and mutable_version(target):
                saved_query=json.loads((root/f'object-{i:04}/query/result.json').read_text())
                if (saved_query['identity']!=identity or saved_query['pages']!=actual['page_offsets'] or
                        saved_query['cleanup']['owned_tracer_exited'] is not True or saved_query['cleanup']['tracer_returncode']!=0 or
                        any(not context['before_query']['monotonic_ns']<=c['costs']['host_start_ns']<=c['costs']['host_end_ns']<=context['after_query']['monotonic_ns']
                            for c in saved_query['chunks'])):
                    raise ValueError('post-preparation query/cleanup window conflict')
            endpoint=batch['endpoint_versions'][i];after=endpoint['identity']
            if (endpoint['index']!=i or endpoint['errors'] or endpoint['read_error'] is not None or
                    any(type(after.get(k)) is not int for k in FIELDS) or
                    not context['end_monotonic_ns']<=endpoint['monotonic_ns']<=batch['end_monotonic_ns'] or
                    identity_decision(target,identity,after,after,True) or
                    endpoint['query_version_differences']!=differences(identity,after)):
                raise ValueError('endpoint object continuity conflict')
        if charged:
            for key in ('charge_scope_before','charge_scope_after'):validate_scope(context[key],raw,batch['host_boot_id'])
            if not (signature(context['charge_scope_before'])==signature(context['charge_scope_after'])==signature(batch['charge_scope_before'])):
                raise ValueError('object charge identity conflict')
            if not (context['start_monotonic_ns']<=context['charge_scope_before']['start_monotonic_ns']<=context['charge_scope_before']['end_monotonic_ns']<=context['before_query']['monotonic_ns']<=context['charge_scope_after']['start_monotonic_ns']<=context['charge_scope_after']['end_monotonic_ns']<=context['end_monotonic_ns']):
                raise ValueError('object charge scope timing conflict')
        obj=result_by[tuple(target['object_key'])]
        if versioned:
            obj['pfn_version_transition']=dict(policy=context['version_policy'],inventory=target['saved_identity'],
                pinned=pin['identity'],query=identity,after_binding_check=context['after_binding_check'],
                endpoint=batch['endpoint_versions'][i],query_target=actual,
                scope='Query covers its recorded version only. Later mutable-file changes are not queried or filled.')
        obj['pfn_context']=dict(identity=identity,host_boot_id=context['host_boot_id'],start_monotonic_ns=context['start_monotonic_ns'],
            end_monotonic_ns=context['end_monotonic_ns'],source=str(root/expected_path))
        if not actual['page_offsets']:
            if identity['size']!=0 or context['query_invocations']!=0 or context['query_status']!='EMPTY_REVALIDATED':
                raise ValueError('empty-file receipt not confirmed')
            obj['file_page_pfns']=dict(status='EMPTY_REVALIDATED',chunks=[],complete_object_physical_bytes=None,
                exclusive_physical_bytes=None,page_charge_owner='EMPTY_NO_PAGES' if charged else 'NOT_MEASURED');empty+=1
            continue
        query=load_query(root/f'object-{i:04}/query/result.json',root/expected_path)
        # Match only this object; the established attach() checks identity, boot, permissions and window.
        one=attach(dict(inventory,objects=[obj]),query)
        obj['file_page_pfns']=one['objects'][0]['file_page_pfns']
        if one['pfn_attachment']['available_objects']!=1:raise ValueError('full-list object PFNs rejected')
        if charged:
            if any('charge' not in c or not context['charge_scope_before']['end_monotonic_ns']<=c['start_monotonic_ns']<=c['end_monotonic_ns']<=context['charge_scope_after']['start_monotonic_ns'] for c in query['chunks']):
                raise ValueError('charge query missing or outside group identity window')
            obj['page_charge_attribution']=associate(query['chunks'],context['charge_scope_before'],context['charge_scope_after'],target['references'])
        queries.append(query['source'])
    for obj in result['objects']:
        if 'file_page_pfns' not in obj:obj['file_page_pfns']=dict(status='UNAVAILABLE',chunks=[],reasons=['OUTSIDE_BOUND_ORDINARY_PLAN'],complete_object_physical_bytes=None)
    result['pfn_attachment']=dict(version=batch['version'],status='PARTIAL',available_objects=len(queries),empty_revalidated_objects=empty,
        unavailable_objects=len(result['objects'])-len(queries)-empty,query_sources=queries,
        cross_object_pfn_union_bytes=None,cross_chunk_pfn_union_bytes=None,page_charge_owner='OBSERVED_PER_CHUNK' if charged else 'NOT_MEASURED',dedup_with_guest_ram='NOT_MEASURED')
    if charged:
        result['charge_scope']=dict(before=batch['charge_scope_before'],after=batch['charge_scope_after'],history=batch['charge_identity_history'])
        result['pfn_attachment']['charge_identity_status']='PARTIAL' if any(o.get('page_charge_attribution',{}).get('status')=='PARTIAL' for o in result['objects']) else 'OBSERVED_DECLARED_GROUPS'
    coverage_plan=plan
    if versioned:
        coverage_plan=query_plan(plan,actual_targets)
        if json.loads((root/'query-plan.json').read_text())!=coverage_plan:raise ValueError('saved query plan conflicts with object receipts')
        if not coverage_plan['budget']['strictly_within_limit']:raise ValueError('saved query ranges exceed budget')
    coverage=offset_coverage(coverage_plan,result)
    if versioned:
        coverage.update(coverage_plan='query-plan.json',inventory_plan='plan.json',
            inventory_planned_page_offsets=sum(t['page_offsets'] for t in plan['targets']),
            scope='Offsets of separate per-object query versions; not complete at the batch endpoint',
            batch_endpoint_physical_coverage='NOT_MEASURED',
            endpoint_changed_objects=sum(bool(r['query_version_differences']) for r in batch['endpoint_versions']))
    return result,coverage
