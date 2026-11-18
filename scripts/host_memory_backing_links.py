"""Opt-in memory backing reference audit; does not change prior cache observations."""
import csv
import json
from pathlib import Path
import re
import time

from host_rootfs_layers import file_identity, lower_file
from host_schedule_files import permission_evidence, process_identity
from host_schedule_cache_layers import check_process
from host_file_monitor import mincore_file


def config_layers(config, process, opened):
    """Save configuration and stable local lower identities without reading payloads."""
    value=dict(status='UNKNOWN',config_path=str(config),lowers=[])
    try:
        before=file_identity(config)
        image=json.loads(config.read_text())
        value.update(config_identity_before=before,config_contents=image)
        if not isinstance(image,dict) or not isinstance(image.get('lowers'),list) or not image['lowers']:
            raise ValueError('memory config has no lowers')
        upper=image.get('upper')
        if upper is not None and (not isinstance(upper,dict) or any(upper.values())):
            raise ValueError('unexpected writable memory config')
        for index,reference in enumerate(image['lowers']):
            row=dict(index=index,reference=reference,status='UNKNOWN');value['lowers'].append(row)
            try:
                path=lower_file(reference,config,process);identity=file_identity(path)
                holders=opened.get((identity['device'],identity['inode']),[])
                if not holders:raise ValueError('memory lower not open in owned service')
                row.update(status='IDENTIFIED',path=str(path),**identity,open_descriptors=holders)
            except (OSError,ValueError,TypeError,AttributeError) as error:row['error']=repr(error)
        after=file_identity(config);value['config_identity_after']=after
        if before!=after:raise ValueError('memory config changed during inventory')
        value['status']='IDENTIFIED' if all(r['status']=='IDENTIFIED' for r in value['lowers']) else 'INCOMPLETE'
    except (OSError,ValueError,TypeError) as error:
        value.update(status='UNKNOWN',error=repr(error))
        for row in value['lowers']:row['status']='UNKNOWN'
    return value


def observe(container,out,guests,bindings,expanded,layers,opened,*,proc_root=Path('/proc')):
    start=time.monotonic_ns();value=dict(status='INCOMPLETE',start_monotonic_ns=start,devices=[],files=[])
    try:
        value['permission']=permission_evidence()
        with (Path(out)/'sandbox-ids.tsv').open() as stream:
            ids={r['role']:r for r in csv.DictReader(stream,delimiter='\t')}
        identities={int(k):v for k,v in expanded['process_identities'].items()}
        anchor=next(iter(bindings.values()))
        process=proc_root/str(anchor['pid'])
        expected=identities[anchor['pid']]
        if process_identity(process)!=expected or str(expected['start_ticks'])!=str(anchor['start_ticks']):
            raise ValueError('log anchor process identity changed')
        log_path=process/'root/workspace/env/logs/server.log'
        log_text=log_path.read_text()
        if process_identity(process)!=expected:raise ValueError('log anchor changed during read')
        value['creation_log_source']=str(log_path)
        lines=re.sub(r'\x1b\[[0-9;]*m','',log_text).splitlines()
        objects={}
        for b in layers['block_devices']['objects']:
            refs=[a for a in b['aliases'] if a.get('source')=='vmm_ram_mapping']
            if not refs:continue
            row=dict(rdev=b['rdev'],status='UNKNOWN',roles=sorted({a['role'] for a in refs}),
                     original_device_query_status=b['status'],configs=[])
            value['devices'].append(row)
            try:
                templates=set()
                for role in row['roles']:
                    if ids[role]['sandbox_id']!=guests[role]:raise ValueError('sandbox role identity conflict')
                    templates.add(ids[role]['created_from'])
                if len(templates)!=1 or not re.fullmatch(r'[a-fA-F0-9-]{36}',next(iter(templates))):
                    raise ValueError('missing or conflicting restored template')
                template=next(iter(templates));row['template']=template
                paths={a['region']['mapping_path'] for a in refs}
                evidence=[]
                for number,line in enumerate(lines,1):
                    if 'created or acquired shared read-only ublk device' not in line:continue
                    match=re.search(r' key=(\S+) dev_id=(\d+) path=(\S+).* sandbox_id=(\S+)',line)
                    if match and match[3] in paths and match[1].endswith('/memory/image.json'):
                        evidence.append(dict(line=number,config_path=match[1],device_path=match[3],sandbox_id=match[4]))
                if not evidence or any('/runtime/'+template+'/memory/image.json' not in e['config_path'] or
                    e['sandbox_id'] not in {guests[r] for r in row['roles']} for e in evidence):
                    raise ValueError('device/config log reference missing or conflicting')
                if len({e['config_path'] for e in evidence})!=1:raise ValueError('ambiguous memory config')
                row['creation_evidence']=evidence
                for ref in refs:check_process(ref,identities,proc_root)
                for role in row['roles']:
                    vm=bindings[role];process=proc_root/str(vm['pid'])
                    if str(identities[vm['pid']]['start_ticks'])!=str(vm['start_ticks']):raise ValueError('VMM start identity changed')
                    config=process/'root'/evidence[0]['config_path'].lstrip('/')
                    inv=config_layers(config,process,opened);inv['role']=role;row['configs'].append(inv)
                    if inv['status']!='IDENTIFIED':raise ValueError('memory lower inventory incomplete')
                    for lower in inv['lowers']:
                        key=(lower['device'],lower['inode'])
                        obj=objects.setdefault(key,dict(object_key=list(key),path=lower['path'],
                            identity={k:lower[k] for k in ('device','inode','size','mtime_ns')},users=[],rdevs=[],status='PENDING',resident_bytes=None))
                        if obj['identity']!={k:lower[k] for k in obj['identity']}:raise ValueError('lower metadata conflict')
                        if role not in obj['users']:obj['users'].append(role)
                        if b['rdev'] not in obj['rdevs']:obj['rdevs'].append(b['rdev'])
                if len({json.dumps(c['config_contents'],sort_keys=True) for c in row['configs']})!=1:
                    raise ValueError('configuration differs between roles')
                row['status']='IDENTIFIED'
            except (OSError,ValueError,KeyError,TypeError) as error:row['error']=repr(error)
        if not value['devices']:raise ValueError('no restored memory device; validation requires restore workload')
        for obj in objects.values():
            try:
                obj['start_monotonic_ns']=time.monotonic_ns()
                if file_identity(Path(obj['path']))!=obj['identity']:raise ValueError('lower changed before query')
                query=mincore_file(Path(obj['path']))
                obj['query']=query
                if not query['stable'] or any(query[k]!=v for k,v in obj['identity'].items()):
                    raise ValueError('lower changed during query')
                obj.update(status='OBSERVED',resident_bytes=query['resident_bytes'])
            except (OSError,ValueError) as error:obj.update(status='UNKNOWN',error=repr(error))
            finally:obj['end_monotonic_ns']=time.monotonic_ns()
        for row in value['devices']:
            try:
                for inv in row['configs']:
                    if inv['status']!='IDENTIFIED' or file_identity(Path(inv['config_path']))!=inv['config_identity_before']:
                        raise ValueError('memory config changed after query or incomplete')
                    for lower in inv['lowers']:
                        if file_identity(Path(lower['path']))!={k:lower[k] for k in ('device','inode','size','mtime_ns')}:
                            raise ValueError('memory lower changed after query')
                b=next(b for b in layers['block_devices']['objects'] if b['rdev']==row['rdev'])
                for ref in b['aliases']:
                    if ref.get('source')=='vmm_ram_mapping':check_process(ref,identities,proc_root)
            except (OSError,ValueError,KeyError) as error:
                row['status']='UNKNOWN'
                row['post_query_error']=repr(error)
                row.setdefault('error',repr(error))
        value['files']=list(objects.values())
        if all(d['status']=='IDENTIFIED' for d in value['devices']) and all(f['status']=='OBSERVED' for f in value['files']):
            value['status']='IDENTIFIED'
    except Exception as error:value['error']=repr(error)
    value['end_monotonic_ns']=time.monotonic_ns()
    value['scope']='Separate memory-lower query window; deduplicate by device:inode. Not additive with prior file/cache/PSS values; references do not prove exclusive physical use.'
    return value
