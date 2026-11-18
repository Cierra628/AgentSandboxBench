"""Attach one saved ordinary-file PFN query to backing objects; offline only.

No live file, /proc, mincore or BPF queries. Existing inventory fields retain
their meaning. Each chunk remains a separate non-atomic physical observation.
"""
import argparse
import copy
import json
from pathlib import Path

import numpy as np

from host_file_pfn_query import PAGE, FIELDS, MAX_CHUNK_PAGES, VERSION as QUERY_VERSION
from host_file_pfn_query import FOLIO, MASK, chunks, decode_rows

VERSION='host-backing-pfn-attachment-v1'


def read_json(path):
    return json.loads(Path(path).read_text())


def load_query(source,context_source=None):
    """Read raw rows and recompute summaries; no target-file lookups."""
    source=Path(source).resolve();raw=read_json(source)
    from host_file_page_charge import VERSION as CHARGE_VERSION, COLUMNS, decode_charge_rows
    charged=raw.get('version')==CHARGE_VERSION
    if raw.get('version') not in (QUERY_VERSION,CHARGE_VERSION):raise ValueError('unsupported saved PFN query version')
    width=len(COLUMNS) if charged else 4
    identity=raw.get('identity');expected=raw.get('expected_identity')
    result=dict(source=str(source),identity=identity,key=expected,host_boot_id=raw.get('host_boot_id'),
        context_source=None,chunks=[],errors=[],simulated=raw.get('simulated',False))
    if raw.get('status')=='FAIL':
        result['errors']=['SAVED_QUERY_FAILED'];return result
    if raw.get('status') not in ('OBSERVED_NONATOMIC','PARTIAL') or not isinstance(identity,dict):
        raise ValueError('invalid saved PFN query status or identity')
    if any(type(identity.get(k)) is not int for k in FIELDS):raise ValueError('saved query identity incomplete')
    if expected!=[identity['device'],identity['inode']]:raise ValueError('saved query explicit identity conflicts')
    if context_source is not None:
        context_source=Path(context_source).resolve();context=read_json(context_source)
        joint=context.get('version') in ('host-backing-single-pfn-v1','host-backing-object-pfn-v1','host-backing-object-pfn-v2','host-backing-object-charge-v1')
        expected_source=context_source.parent/('host-backing-pfn-query/result.json' if context.get('version')=='host-backing-single-pfn-v1' else 'query/result.json')
        if (source!=expected_source or context.get('status')!='PASS' or
                context.get('query_invocations')!=1 or context.get('query_status')!=raw['status'] or
                context.get('identity')!=identity or context.get('file')!=raw.get('path') or
                not context.get('host_boot_id') or context.get('simulated',False)!=result['simulated']):
            raise ValueError('calibration context does not bind this saved query')
        if joint and (context.get('identity_after')!=identity or
                context.get('bindings_before')!=context.get('bindings_after') or
                not context.get('bindings_before')):
            raise ValueError('joint file/reference stability evidence missing')
        if result['host_boot_id'] not in (None,context['host_boot_id']):
            raise ValueError('query host boot conflicts with calibration context')
        result.update(host_boot_id=context['host_boot_id'],context_source=str(context_source))
    if not result['host_boot_id']:result['errors'].append('QUERY_HOST_BOOT_NOT_RECORDED')
    if raw.get('page_size')!=PAGE:raise ValueError('unsupported saved page size')
    planned=list(chunks(raw['first_page'],raw['pages'],identity['size']))
    if len(planned)!=len(raw.get('chunks',[])):raise ValueError('saved chunk range incomplete')
    if raw.get('cleanup',{}).get('owned_tracer_exited') is not True or raw['cleanup'].get('tracer_returncode')!=0:
        result['errors'].append('QUERY_CLEANUP_NOT_CONFIRMED')
    tokens=set()
    for row,(first,count,length) in zip(raw['chunks'],planned):
        if charged and row.get('version')!=CHARGE_VERSION:raise ValueError('mixed charge row version')
        if (row.get('first_page'),row.get('pages'),row.get('mapping_length'))!=(first,count,length):
            raise ValueError('saved chunk does not match explicit bounded range')
        token=row.get('token')
        if type(token) is not int or token<=0 or token in tokens:raise ValueError('invalid or repeated query token')
        tokens.add(token)
        if context_source is not None and row.get('query_process')!=context.get('process_id'):
            raise ValueError('query process differs from saved calibration context')
        if (row.get('object_stable') is not True or row.get('probe_alive_after') is not True or
                any(row.get(k)!=identity for k in ('identity_before','identity_after','alias_after'))):
            result['errors'].append('QUERY_OBJECT_OR_PROBE_UNSTABLE')
        permission=row.get('permission',{})
        if (permission.get('host',{}).get('host_root_cap_fowner') is not True or
                permission.get('thread',{}).get('basis')!='HOST_CAP_FOWNER'):
            result['errors'].append('QUERY_PERMISSION_NOT_CONFIRMED')
        binary=row['binary']
        if Path(binary).name!=binary or count>MAX_CHUNK_PAGES:raise ValueError('invalid saved chunk path or size')
        path=source.parent/binary
        if path.stat().st_size!=count*width*8:raise ValueError('saved raw row size mismatch')
        rows=np.fromfile(path,dtype='<u8').reshape(-1,width)
        if not np.array_equal(rows[:,0],np.arange(first,first+count,dtype='<u8')):
            raise ValueError('saved raw file offsets mismatch')
        summary=decode_charge_rows(rows) if charged else decode_rows(rows)
        if summary!=row.get('raw_summary') or summary['status']!=row.get('status'):
            raise ValueError('saved PFN summary differs from raw rows')
        if row.get('accepted_pfn_union_bytes')!=summary['confirmed_pfn_union_bytes']:
            raise ValueError('saved accepted PFN bytes differ from raw rows')
        start=row['costs']['host_start_ns'];end=row['costs']['host_end_ns']
        if any(type(n) is not int for n in (start,end)) or not 0<=start<end:
            raise ValueError('saved query timestamps invalid')
        if charged:
            window=row['stages']['mincore'];found=rows[:,2]==1
            if not start<=window['host_start_ns']<=window['host_end_ns']<=end or not (
                    np.all(rows[found,8]>=window['host_start_ns']) and np.all(rows[found,9]<=window['host_end_ns'])):
                raise ValueError('charge observation outside same mincore window')
        selected=(rows[:,2]==1)&((rows[:,3]&np.uint64(~MASK&((1<<64)-1)))==FOLIO)
        pfns=sorted(map(int,np.unique(rows[selected,3]&MASK)))
        result['chunks'].append(dict(first_page=first,token=token,
            start_monotonic_ns=start,end_monotonic_ns=end,raw_rows_source=str(path),
            raw_row_columns=list(COLUMNS) if charged else ['file_page','mincore_bit','return_found','encoded_return'],
            physical_pfns=pfns,**summary))
    return result


def calibration_inventory(source):
    """Normalize recorded calibration identity/window, without inventing guests."""
    source=Path(source).resolve();raw=read_json(source)
    if raw.get('version')!='host-file-pfn-positive-v1' or raw.get('status')!='PASS':
        raise ValueError('successful saved positive calibration required')
    identity=raw['identity']
    return dict(version='host-backing-calibration-object-v1',status='CALIBRATION_ONLY',
        source=str(source),simulated=raw.get('simulated',False),guests={},bindings={},objects=[dict(
            object_key=['regular',identity['device'],identity['inode']],kind='regular',
            categories=['synthetic_calibration_file'],references=[],reference_status='UNASSIGNED',
            aliases=[dict(path=raw['file'])],
            pfn_context=dict(identity=identity,host_boot_id=raw['host_boot_id'],
                start_monotonic_ns=raw['reference_before']['start_monotonic_ns'],
                end_monotonic_ns=raw['reference_after']['end_monotonic_ns'],
                source=str(source),basis='saved calibration identity and bracketing reference observations'))],
        complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None)


def attach(inventory,query=None,*,inventory_source=None):
    """New fields only; references are not ownership, and chunks are not added."""
    value=copy.deepcopy(inventory)
    if 'pfn_attachment' in value:raise ValueError('inventory already has PFN attachment')
    seen=set();available=0;matched=0
    guests=value.get('guests',{});bindings=value.get('bindings',{})
    for obj in value['objects']:
        if 'file_page_pfns' in obj:raise ValueError('object already has PFN attachment')
        key=obj['object_key'];scope=(obj['kind'],tuple(key) if key is not None else None)
        if scope in seen:raise ValueError('duplicate object within one layer')
        seen.add(scope)
        refs=obj.get('references',[])
        if (len({r['role'] for r in refs})!=len(refs) or
                any(guests.get(r['role'])!=r['sandbox_id'] for r in refs)):
            raise ValueError('object sandbox reference conflict')
        row=dict(status='UNAVAILABLE',reasons=[],chunks=[],complete_object_physical_bytes=None,
            exclusive_physical_bytes=None,page_charge_owner='NOT_MEASURED')
        obj['file_page_pfns']=row
        if obj['kind']!='regular':row['reasons']=['UNSUPPORTED_OBJECT_KIND'];continue
        if not query or key!=['regular',*(query.get('key') or [])]:
            row['reasons']=['NO_MATCHING_SAVED_QUERY'];continue
        matched+=1;row.update(query_source=query['source'],query_context_source=query['context_source'])
        reasons=list(query['errors']);context=obj.get('pfn_context',{})
        if inventory.get('simulated',False)!=query['simulated']:reasons.append('SIMULATION_SCOPE_MISMATCH')
        if inventory.get('status')=='FAIL':reasons.append('INVENTORY_FAILED')
        identity=context.get('identity',{})
        if any(type(identity.get(k)) is not int for k in FIELDS):reasons.append('OBJECT_IDENTITY_NOT_RECORDED')
        elif identity!=query['identity']:reasons.append('OBJECT_VERSION_MISMATCH')
        if not context.get('host_boot_id'):reasons.append('OBJECT_HOST_BOOT_NOT_RECORDED')
        elif context['host_boot_id']!=query['host_boot_id']:reasons.append('HOST_BOOT_MISMATCH')
        if any(vm.get('boot_id')!=context.get('host_boot_id') for vm in bindings.values()):
            reasons.append('INVENTORY_BINDING_HOST_BOOT_CONFLICT')
        start=context.get('start_monotonic_ns');end=context.get('end_monotonic_ns')
        if any(type(n) is not int for n in (start,end)) or not 0<=start<end:
            reasons.append('OBJECT_WINDOW_NOT_RECORDED')
        elif any(not start<=c['start_monotonic_ns']<=c['end_monotonic_ns']<=end for c in query['chunks']):
            reasons.append('QUERY_OUTSIDE_OBJECT_WINDOW')
        if reasons:row['reasons']=reasons;continue
        available+=1;row['chunks']=copy.deepcopy(query['chunks'])
        row['status']='PARTIAL' if any(c['unknown_pages'] or c.get('charge',{}).get('status')=='PARTIAL' for c in query['chunks']) else 'OBSERVED_LISTED_CHUNKS'
        if query['chunks'] and all('charge' in c for c in query['chunks']):row['page_charge_owner']='OBSERVED_PER_CHUNK'
        row['scope']='separate per-chunk file-page PFNs; non-atomic, no cross-chunk or cross-object total'
    value['pfn_attachment']=dict(version=VERSION,inventory_source=inventory_source,
        matched_objects=matched,available_objects=available,unavailable_objects=len(value['objects'])-available,
        query_source=query['source'] if query else None,unmatched_query=bool(query and not matched),
        status='UNAVAILABLE' if not available else 'PARTIAL' if available<len(value['objects']) or
            any(o['file_page_pfns']['status']=='PARTIAL' for o in value['objects']) else 'OBSERVED_LISTED_CHUNKS',
        cross_object_pfn_union_bytes=None,cross_chunk_pfn_union_bytes=None,
        dedup_with_guest_ram='NOT_MEASURED',page_charge_owner='NOT_MEASURED',
        simulated=inventory.get('simulated',False),
        raw_pfn_policy='LOCAL_ONLY; do not publish',validation='OFFLINE_SAVED_DATA_ONLY')
    if any(o['file_page_pfns'].get('page_charge_owner')=='OBSERVED_PER_CHUNK' for o in value['objects']):
        value['pfn_attachment']['page_charge_owner']='OBSERVED_PER_CHUNK'
    return value


def main():
    p=argparse.ArgumentParser(description=__doc__)
    source=p.add_mutually_exclusive_group(required=True)
    source.add_argument('--inventory',type=Path);source.add_argument('--calibration-result',type=Path)
    p.add_argument('--saved-query',type=Path);p.add_argument('--query-context',type=Path)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inventory=read_json(a.inventory) if a.inventory else calibration_inventory(a.calibration_result)
    query=load_query(a.saved_query,a.query_context or a.calibration_result) if a.saved_query else None
    value=attach(inventory,query,inventory_source=str(a.inventory or a.calibration_result))
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print('status='+value['pfn_attachment']['status']+' result='+str(a.output))


if __name__=='__main__':main()
