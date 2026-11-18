"""Split saved special mapping statistics; no /proc queries or RAM binary reads."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np

from kernel_cache_metadata import MASK, PHYSICAL_MASK, PRESENT
from vmm_page_evidence import metadata, REGION_KEYS

PAGE = 4096
MIB = 1048576
KINDS = ('io_uring', 'vvar', 'vvar_vclock', 'vsyscall', 'kvm_vcpu')


def kind(path):
    if path == 'anon_inode:[io_uring]':return 'io_uring'
    if path.startswith('anon_inode:kvm-vcpu:'):return 'kvm_vcpu'
    return {'[vvar]':'vvar', '[vvar_vclock]':'vvar_vclock', '[vsyscall]':'vsyscall'}.get(path, 'other')


def split(body, rows):
    """Keep smaps sums distinct from PFN unions, including same-inode aliases."""
    before = metadata(body['maps_before'], body['smaps_before'])
    after = {tuple(r[k] for k in REGION_KEYS):r for r in metadata(body['maps_after'], body['smaps_after'])}
    segments = body['summary']['nonram_segments']
    count = max((s['row_offset']+s['pages'] for s in segments if s['row_offset'] is not None), default=0)
    if rows.shape != (count, 3):raise ValueError('non-RAM binary length differs from saved segments')
    details=[];physical={k:set() for k in (*KINDS, 'other')};unknown={k:0 for k in (*KINDS, 'other')}
    for s in segments:
        r=before[s['region_index']];category=kind(r['mapping_path'])
        tail=after.get(tuple(r[k] for k in REGION_KEYS))
        pfns=None
        if s['status'] in ('OBSERVED_NONATOMIC', 'PARTIAL'):
            a=s['row_offset'];x=rows[a:a+s['pages']]
            stable=(((x[:,0]^x[:,1])|(x[:,1]^x[:,2]))&PHYSICAL_MASK)==0
            good=stable&((x[:,0]&PRESENT)!=0)&((x[:,0]&MASK)!=0)
            pfns=set(map(int,x[good,0]&MASK))
            if len(pfns)*PAGE!=s['physical_union_bytes']:raise ValueError('segment union differs from original analysis')
            physical[category].update(pfns)
        unknown[category]+=s['unknown_virtual_pages']
        if category=='other':continue
        row=dict(category=category, region_index=s['region_index'], path=r['mapping_path'],
            device=r['device'], inode=r['inode'], offset=r['offset'], flags=' '.join(r.get('vm_flags',[])),
            virtual_bytes=s['virtual_bytes'], saved_status=s['status'], unknown_virtual_pages=s['unknown_virtual_pages'],
            pfn_union_observed_bytes=None if pfns is None else len(pfns)*PAGE)
        # Never prorate whole-VMA RSS/PSS to a partial range or fill missing fields with zero.
        for side,m in (('before',r),('after',tail)):
            valid=(m is not None and m.get('smaps_status')=='OBSERVED' and
                   (m['start'],m['end'])==(s['start'],s['end']))
            for key in ('Rss','Pss'):
                row[side+'_'+key.lower()+'_bytes']=m['smaps_bytes'].get(key) if valid else None
        details.append(row)
    union=set().union(*physical.values())
    if len(union)*PAGE!=body['summary']['ordinary_nonram_stable_host_union_bytes']:
        raise ValueError('non-RAM union differs from original analysis')
    groups=[]
    for category in KINDS:
        selected=[r for r in details if r['category']==category]
        if not selected:continue
        g=dict(category=category, segments=len(selected), virtual_bytes=sum(r['virtual_bytes'] for r in selected),
            unknown_virtual_pages=unknown[category],
            pfn_union_observed_bytes=(len(physical[category])*PAGE if all(r['pfn_union_observed_bytes'] is not None for r in selected) else None),
            pfn_coverage='PARTIAL' if unknown[category] else 'OBSERVED_NONATOMIC')
        for field in ('before_rss_bytes','after_rss_bytes','before_pss_bytes','after_pss_bytes'):
            values=[r[field] for r in selected]
            g[field]=sum(values) if all(v is not None for v in values) else None
        groups.append(g)
    return dict(groups=groups, mappings=details, ordinary_nonram_union_bytes=len(union)*PAGE)


def analyze(raw):
    read=lambda p:json.loads(p.read_text())
    results=[];mappings=[];sources=[]
    for role in ('private-a','private-b'):
        live=raw/'live-cache'/role
        collector=read(live/'result.json');binding=read(live/'vmm-child-binding.json')
        analysis=read(live/'vmm-page-analysis.json')
        if collector['status']!='PASS' or binding['status']!='PASS':raise ValueError('collector/binding not PASS')
        for sample in map(json.loads,(live/'samples.jsonl').read_text().splitlines()):
            n=sample['point'];path=live/f'pair-{n:04}-vmm.json';body=read(path)
            if (body['ram_manifest']['host_identity']!=collector['host_identity'] or
                body['ram_manifest']['sandbox_id']!=collector['sandbox_id'] or
                body['ram_manifest']['guest_boot_id']!=collector['guest_boot_id'] or
                body['host_boot_id']!=binding['vmm_binding']['boot_id']):raise ValueError('saved identity conflict')
            if dict(point=n,**body['summary'])!=analysis['points'][n]:raise ValueError('saved summary conflict')
            data=np.fromfile(live/body['nonram_path'],dtype='<u8')
            if data.size%3:raise ValueError('truncated non-RAM triple')
            value=split(body,data.reshape(-1,3))
            for g in value['groups']:results.append(dict(role=role,point=n,source=str(path),**g))
            for m in value['mappings']:mappings.append(dict(role=role,point=n,source=str(path),**m))
            sources.append(str(path))
    return dict(status='PASS_OFFLINE_SPLIT_PARTIAL_COVERAGE',points=len(sources),groups=results,mappings=mappings,
        missing_special_pfn='io_uring/vvar/vvar_vclock/vsyscall were not queried; never reconstruct from RSS/PSS',
        ram_binary_reads=0,live_queries=0)


def write_csv(path,rows):
    with path.open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=analyze(a.raw);a.output.mkdir(parents=True,exist_ok=False)
    with (a.output/'summary.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    write_csv(a.output/'groups.csv',result['groups']);write_csv(a.output/'mappings.csv',result['mappings'])
    print('status='+result['status']+' result_dir='+str(a.output))


if __name__=='__main__':main()
