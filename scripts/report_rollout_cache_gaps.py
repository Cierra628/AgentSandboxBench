"""Offline partition of saved restored-cache gaps and cross-session windows."""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from kernel_cache_metadata import decode, PRESENT, SWAPPED, PHYSICAL_MASK, MASK
from report_live_task_cache import recount
from snapshot_cache_residency import FILE, UNKNOWN


def analyze(raw, out):
    directory=raw/'live-cache/restored-g1'
    samples=recount(directory)
    source=recount(raw/'live-cache/backbone-g2')
    queries=json.loads((directory/'snapshot-query-result.json').read_text())['queries']
    assert len(queries)==len(samples)*2
    rows=[];details=[];windows=[]
    categories=('new_or_reused_after','host_mapping_changed','file_pfn_mismatch','swapped',
                'no_backend_offset','backend_zero_both','backend_changed','other')
    for p in samples:
        prefix=directory/f"pair-{p['point']:04}"
        _,before=decode(Path(str(prefix)+'-before.bin').read_bytes())
        _,after=decode(Path(str(prefix)+'-after.bin').read_bytes())
        h={int(r[0]):tuple(map(int,r[1:])) for r in np.fromfile(str(prefix)+'-host.bin',dtype='<u8').reshape(-1,4)}
        b={int(r[0]):tuple(map(int,r[1:])) for r in np.fromfile(str(prefix)+'-backend.bin',dtype='<u8').reshape(-1,4)}
        counts=dict.fromkeys(categories,0);confirmed=0
        for g,tag in after.items():
            reason=None
            if not tag or before.get(g)!=tag:reason='new_or_reused_after'
            else:
                a,m,z=h[g];offset,first,last=b[g]
                stable=not (((a^m)|(m^z))&PHYSICAL_MASK)
                backed=first==last and bool(first&PRESENT)
                present=bool(a&PRESENT);swap=bool(a&SWAPPED)
                mismatch=present and bool(a&FILE) and backed and (a&MASK)!=(first&MASK)
                if not stable:reason='host_mapping_changed'
                elif mismatch:reason='file_pfn_mismatch'
                elif present or (backed and not swap):confirmed+=1
                elif swap:reason='swapped'
                elif offset==UNKNOWN:reason='no_backend_offset'
                elif first==last==0:reason='backend_zero_both'
                elif first!=last:reason='backend_changed'
                else:reason='other'
            if reason:
                counts[reason]+=1
                details.append(dict(point=p['point'],guest_pfn=g,lifetime=tag,reason=reason,
                    host_entries=list(h[g]) if g in h else None,backend_entries=list(b[g]) if g in b else None))
        assert sum(counts.values())*4096==p['host_current_unconfirmed_guest_capacity_bytes']
        assert confirmed+sum(counts.values())==len(after)
        q=queries[2*p['point']:2*p['point']+2]
        rows.append(dict(point=p['point'],after_pages=len(after),confirmed_guest_pages=confirmed,
            unknown_pages=sum(counts.values()),unknown_mib=sum(counts.values())/256,
            **counts,direct_nonresident_capacity_mib=counts['backend_zero_both']/256,
            lower_provider_coverage='NOT_COLLECTED',
            lifecycle_unknown_capacity_mib=counts['new_or_reused_after']/256,
            backend_nonresident_first=q[0]['requested_pages']-q[0]['mincore_resident_pages'],
            backend_nonresident_last=q[1]['requested_pages']-q[1]['mincore_resident_pages'],
            missing_kernel_results=sum(v['missing_kernel_results'] for v in q),
            old_snapshot_mib=p['snapshot_old_version_bytes']/2**20,
            current_union_mib=p['host_current_union_bytes']/2**20))
        left=p['host_sample_start_ns'];right=left+p['host_elapsed_ns']
        def distance(s):
            a=s['host_sample_start_ns'];z=a+s['host_elapsed_ns']
            return max(left-z,a-right,0)
        nearest=min(source,key=distance)
        a=nearest['host_sample_start_ns'];z=a+nearest['host_elapsed_ns']
        overlaps=[s['point'] for s in source if max(left,s['host_sample_start_ns'])<=min(right,s['host_sample_start_ns']+s['host_elapsed_ns'])]
        windows.append(dict(restored_point=p['point'],restored_start_ns=left,restored_end_ns=right,
            nearest_source_point=nearest['point'],source_start_ns=a,source_end_ns=z,
            interval_gap_ms=distance(nearest)/1e6,overlap_source_points=overlaps))
    out.mkdir(parents=True,exist_ok=False)
    for name,data in [('gap-counts.csv',rows),('query-windows.csv',windows)]:
        with (out/name).open('x') as f:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
    (out/'unknown-pages.json').write_text(json.dumps(details,indent=2)+'\n')
    summary=dict(status='PASS',raw=str(raw),restored_points=len(samples),source_points=len(source),
        gap_partition=rows,window_pairs=windows,business_runs=0,historical_data_modified=False,
        note='Counts partition guest page capacity, not physical bytes. No simultaneous cross-session union.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--raw',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(analyze(a.raw,a.output),indent=2))
