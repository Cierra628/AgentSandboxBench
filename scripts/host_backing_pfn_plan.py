"""Saved-data-only planning and offset coverage for referenced ordinary files.

No target stat/open, /proc, mincore, page-table, BPF or service calls. A plan
requires a fresh live inventory and identity checks before future execution.
"""
import argparse
import copy
import json
from pathlib import Path

from host_backing_evidence import summarize
from host_backing_pfns import attach,load_query
from host_file_pfn_query import PAGE,FIELDS,MAX_CHUNK_PAGES,RAW_BUDGET

VERSION='host-backing-pfn-plan-v1'
ROW_BYTES=32
METADATA_ALLOWANCE_PER_CHUNK=64*1024
ENVELOPE_ALLOWANCE_BYTES=4*1024*1024


def make_plan(raw,*,source=None,record_budget=RAW_BUDGET,chunk_pages=MAX_CHUNK_PAGES,row_bytes=ROW_BYTES):
    """Select every bound ordinary object, not just resident pages or hot files."""
    if row_bytes not in (32,80):raise ValueError('unsupported query row width')
    if type(record_budget) is not int or not 0<record_budget<=RAW_BUDGET:
        raise ValueError('record budget must be positive and <= existing 1 GiB limit')
    if type(chunk_pages) is not int or not 0<chunk_pages<=MAX_CHUNK_PAGES:
        raise ValueError('chunk pages must be within the existing query limit')
    if raw.get('status')!='COLLECTED':raise ValueError('successful saved backing inventory required')
    inventory=summarize(raw)
    rows={(r['device'],r['inode']):r for r in raw['expanded_files']['files']}
    targets=[];excluded=[]
    for obj in sorted(inventory['objects'],key=lambda o:(o['kind'],str(o['object_key']))):
        if obj['kind']!='regular' or not obj['references']:
            excluded.append(dict(object_key=obj['object_key'],kind=obj['kind'],references=obj['references'],
                reason='SEPARATE_OBJECT_KIND' if obj['kind']!='regular' else 'NO_BOUND_SANDBOX_REFERENCE'))
            continue
        row=rows[tuple(obj['object_key'][1:])];size=row.get('size');reasons=[]
        alias=row.get('queried_alias')
        path=alias.get('path') if isinstance(alias,dict) else alias
        if alias is None:path=row.get('residency',{}).get('path')
        # Never follow these saved paths during planning; old PIDs may be gone/reused.
        if not isinstance(path,str) or not path:reasons.append('NO_SAVED_QUERY_PATH')
        if type(size) is not int or size<0:reasons.append('NO_VALID_SAVED_SIZE');size=None
        if obj['source_status']!='OBSERVED' or obj['query']['status']!='OBSERVED':reasons.append('NO_STABLE_SAVED_QUERY')
        identity={k:row[k] for k in FIELDS if k in row}
        if any(type(identity.get(k)) is not int for k in ('device','inode','size','mtime_ns')):
            reasons.append('SAVED_FINGERPRINT_INCOMPLETE')
        if 'rdev' not in identity and 'rdev' in row.get('residency',{}):identity['rdev']=row['residency']['rdev']
        pages=None if size is None else (size+PAGE-1)//PAGE
        count=None if pages is None else (pages+chunk_pages-1)//chunk_pages
        binary=None if pages is None else pages*row_bytes
        if binary is not None and binary>=RAW_BUDGET:reasons.append('OBJECT_EXCEEDS_EXISTING_QUERY_BUDGET')
        # Block the whole plan on budget failure; never silently choose a prefix/subset.
        ranges=[]
        if pages and not reasons and binary+count*METADATA_ALLOWANCE_PER_CHUNK+ENVELOPE_ALLOWANCE_BYTES<record_budget:
            for start in range(0,pages,chunk_pages):
                n=min(chunk_pages,pages-start)
                ranges.append(dict(first_page=start,pages=n,mapping_length=min(n*PAGE,size-start*PAGE)))
        elif pages and not reasons:reasons.append('OBJECT_EXCEEDS_RECORD_BUDGET')
        targets.append(dict(object_key=obj['object_key'],references=copy.deepcopy(obj['references']),
            categories=obj['categories'],candidate_path=path,saved_identity=identity,
            missing_identity_fields=[k for k in FIELDS if k not in identity],
            live_identity_check='REQUIRED: full pinned identity, host boot, VMM and reference checks before/after; no alias fallback',
            size_bytes=size,page_offsets=pages,chunk_count=count,binary_row_bytes=binary,ranges=ranges,
            status='BLOCKED' if reasons else 'EMPTY_AT_SAVED_INVENTORY' if pages==0 else 'PLANNED_NOT_OBSERVED',
            reasons=reasons,complete_object_physical_bytes=None))
    known=all(t['binary_row_bytes'] is not None for t in targets)
    binary=sum(t['binary_row_bytes'] for t in targets) if known else None
    count=sum(t['chunk_count'] for t in targets) if known else None
    allowance=None if count is None else count*METADATA_ALLOWANCE_PER_CHUNK+ENVELOPE_ALLOWANCE_BYTES
    fits=binary is not None and binary+allowance<record_budget
    reasons=[]
    if not targets:reasons.append('NO_BOUND_ORDINARY_TARGETS')
    if any(t['reasons'] for t in targets):reasons.append('TARGETS_BLOCKED')
    if not fits:reasons.append('TOTAL_RECORD_BUDGET_NOT_SATISFIED')
    return dict(version=VERSION if row_bytes==32 else 'host-backing-charge-plan-v1',status='BLOCKED' if reasons else 'PLANNED_NOT_EXECUTED',source=source,
        guests=inventory['guests'],bindings=inventory['bindings'],targets=targets,excluded=excluded,reasons=reasons,
        inventory_limits=dict(unresolved_sources=inventory['coverage']['unresolved_sources'],
            process_errors=inventory['coverage']['process_errors'],source_statuses=inventory['source_statuses']),
        budget=dict(limit_bytes=record_budget,binary_row_bytes=binary,metadata_allowance_bytes=allowance,
            planned_record_bytes=None if binary is None else binary+allowance,strictly_within_limit=fits,
            metadata_allowance_per_chunk=METADATA_ALLOWANCE_PER_CHUNK,envelope_allowance_bytes=ENVELOPE_ALLOWANCE_BYTES,
            chunk_pages=chunk_pages,max_chunk_pages=MAX_CHUNK_PAGES,
            actual_record_bytes=None,actual_wall_seconds=None,actual_cpu_seconds=None,
            timing_estimate='NOT_EXTRAPOLATED: one 1 MiB query does not establish full-list cost',
            runtime_requirement='future runner must meter all actual records and stop on cap; no truncation or automatic retry'),
        ordering='kind then serialized device/inode key; fixed before queries, independent of mincore bits',
        execution='OFFLINE_ONLY; rebuild plan from fresh live inventory, then pin and revalidate before any live query',
        complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None,
        cross_object_pfn_union_bytes=None,cross_chunk_pfn_union_bytes=None)


def offset_coverage(plan,evidence=None):
    """Count queried offsets, not physical bytes. Preserve each chunk separately."""
    if evidence is not None and (evidence['guests']!=plan['guests'] or evidence['bindings']!=plan['bindings']):
        raise ValueError('saved PFN evidence belongs to different sandbox/VMM bindings')
    objects={}
    for obj in (evidence or {}).get('objects',[]):
        key=(obj['kind'],tuple(obj['object_key']) if obj['object_key'] is not None else None)
        if key in objects:raise ValueError('duplicate saved PFN object')
        objects[key]=obj
    results=[]
    for target in plan['targets']:
        pages=target['page_offsets'];obj=objects.get(('regular',tuple(target['object_key'])))
        record=dict(object_key=target['object_key'],references=target['references'],planned_page_offsets=pages,
            observed_page_offsets=0,known_page_offsets=0,unknown_observed_page_offsets=0,
            unqueried_page_offsets=pages,chunks=[],complete_object_physical_bytes=None)
        if obj is not None:
            if obj['references']!=target['references']:raise ValueError('saved PFN sandbox references differ')
            if 'page_charge_attribution' in obj:record['page_charge_attribution']=copy.deepcopy(obj['page_charge_attribution'])
            context=obj.get('pfn_context',{});identity=context.get('identity',{})
            pfns=obj.get('file_page_pfns',{})
            if pfns.get('status') in ('OBSERVED_LISTED_CHUNKS','PARTIAL'):
                if any(identity.get(k)!=v for k,v in target['saved_identity'].items()):
                    raise ValueError('saved PFN file version differs from plan')
                ranges=[]
                for chunk in sorted(pfns['chunks'],key=lambda c:c['first_page']):
                    start=chunk['first_page'];n=chunk['pages'];unknown=chunk['unknown_pages']
                    if (pages is None or any(type(v) is not int for v in (start,n,unknown)) or
                            start<0 or n<=0 or start+n>pages or not 0<=unknown<=n or
                            ranges and start<ranges[-1][1]):raise ValueError('invalid/overlapping saved PFN ranges')
                    ranges.append((start,start+n));record['observed_page_offsets']+=n
                    record['known_page_offsets']+=n-unknown;record['unknown_observed_page_offsets']+=unknown
                    # Do not expose/recombine raw PFNs here, or sum per-chunk physical quantities.
                    record['chunks'].append({k:copy.deepcopy(v) for k,v in chunk.items() if k!='physical_pfns'})
                record['unqueried_page_offsets']=pages-record['observed_page_offsets']
        empty_validated=pages==0 and obj is not None and obj.get('file_page_pfns',{}).get('status')=='EMPTY_REVALIDATED' and obj.get('pfn_context',{}).get('identity',{}).get('size')==0
        record['status']=('EMPTY_REVALIDATED' if empty_validated else 'UNKNOWN_SIZE' if pages is None else 'EMPTY_NOT_REVALIDATED' if pages==0 else
            'ALL_OFFSETS_OBSERVED_NONATOMIC' if record['known_page_offsets']==pages else
            'PARTIAL' if record['observed_page_offsets'] else 'NOT_OBSERVED')
        results.append(record)
    known_sizes=all(r['planned_page_offsets'] is not None for r in results)
    total=sum(r['planned_page_offsets'] for r in results) if known_sizes else None
    known=sum(r['known_page_offsets'] for r in results)
    return dict(version=VERSION,status='OFFSET_COVERAGE_ONLY',objects=results,
        total_planned_page_offsets=total,observed_page_offsets=sum(r['observed_page_offsets'] for r in results),
        known_page_offsets=known,unknown_observed_page_offsets=sum(r['unknown_observed_page_offsets'] for r in results),
        unqueried_page_offsets=sum(r['unqueried_page_offsets'] for r in results) if known_sizes else None,
        known_offset_fraction=known/total if total else None,
        known_offset_fraction_scope='unique file offsets in listed bound ordinary objects, not physical memory coverage',
        empty_objects_revalidated=sum(r['status']=='EMPTY_REVALIDATED' for r in results),
        empty_objects_not_revalidated=sum(r['status']=='EMPTY_NOT_REVALIDATED' for r in results),
        cross_chunk_pfn_union_bytes=None,cross_object_pfn_union_bytes=None,
        complete_sandbox_host_memory_bytes=None,complete_sandbox_host_cache_bytes=None)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--saved-raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--saved-query',type=Path);p.add_argument('--query-context',type=Path)
    p.add_argument('--record-budget-bytes',type=int,default=RAW_BUDGET)
    a=p.parse_args()
    if a.query_context and not a.saved_query:p.error('--query-context requires --saved-query')
    raw=json.loads(a.saved_raw.read_text());plan=make_plan(raw,source=str(a.saved_raw.resolve()),record_budget=a.record_budget_bytes)
    query=load_query(a.saved_query,a.query_context) if a.saved_query else None
    evidence=attach(summarize(raw),query) if query else None
    value=dict(plan=plan,coverage=offset_coverage(plan,evidence),validation='OFFLINE_SAVED_DATA_ONLY')
    with a.output.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
    print('status='+plan['status']+' targets='+str(len(plan['targets']))+' result='+str(a.output))
    return 1 if plan['status']=='BLOCKED' else 0


if __name__=='__main__':raise SystemExit(main())
