"""Review saved observation intervals, without changing collection or querying."""
import argparse
import json
from pathlib import Path
import statistics

import numpy as np

from kernel_cache_metadata import PHYSICAL_MASK

ROOT=Path(__file__).resolve().parents[1]
ORDER=('guest_before','decode_before','vmm_first','maps_before','backend_offsets_before',
       'backend_query_first','lower_offsets','lower_query_first','provider_first','vmm_middle',
       'provider_last','guest_after','decode_after','backend_query_last','lower_query_last','vmm_last')
PREP=('maps_before','backend_offsets_before','lower_offsets')


def mapping_intervals(ptes):
    ptes=np.asarray(ptes,dtype='<u8').reshape(-1,3)
    first,middle,last=ptes.T
    early=((first^middle)&PHYSICAL_MASK)!=0
    late=((middle^last)&PHYSICAL_MASK)!=0
    same_ends=((first^last)&PHYSICAL_MASK)==0
    return dict(first_middle_only=int(np.count_nonzero(early&~late)),
                middle_last_only=int(np.count_nonzero(~early&late)),
                both_intervals=int(np.count_nonzero(early&late)),
                returned_to_first=int(np.count_nonzero(early&late&same_ends)),
                stable=int(np.count_nonzero(~early&~late)))


def point_windows(point):
    stages=point['host_stage_costs']
    assert tuple(stages)[:len(ORDER)]==ORDER
    cursor=point['host_sample_start_ns']
    for stage in stages.values():
        assert cursor<=stage['host_start_ns']<=stage['host_end_ns']
        assert stage['elapsed_ns']==stage['host_end_ns']-stage['host_start_ns']
        cursor=stage['host_end_ns']
    assert cursor<=point['host_sample_start_ns']+point['host_elapsed_ns']
    def span(first,last):
        return stages[last]['host_end_ns']-stages[first]['host_start_ns']
    before=point['guest_publication_before']
    assert before['guest_start_ns']<=before['guest_end_ns']<=point['guest_start_ns']<=point['guest_end_ns']
    return dict(host_guest_read_span_ns=span('guest_before','guest_after'),
                guest_publication_span_ns=point['guest_end_ns']-before['guest_start_ns'],
                guest_publication_gap_ns=point['guest_start_ns']-before['guest_end_ns'],
                host_vmm_first_middle_span_ns=span('vmm_first','vmm_middle'),
                host_vmm_middle_last_span_ns=span('vmm_middle','vmm_last'),
                host_vmm_first_last_span_ns=span('vmm_first','vmm_last'),
                preparation_elapsed_ns=sum(stages[k]['elapsed_ns'] for k in PREP),
                first_queries_elapsed_ns=sum(stages[k]['elapsed_ns'] for k in ('backend_query_first','lower_query_first')))


def review(source,partition,output):
    source,partition,output=source.resolve(),partition.resolve(),output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory={}
    def read(path):
        st=path.stat();inventory[str(path.relative_to(ROOT))]=[st.st_ino,st.st_size,st.st_mtime_ns]
        return path.read_bytes()
    previous=json.loads(read(partition/'summary.json'))
    local=json.loads(read(partition/'guest-identities.local.json'))
    assert previous['status']=='PASS' and previous['source']==str(source.relative_to(ROOT))
    samples=[json.loads(line) for line in read(source/'samples.jsonl').splitlines()]
    assert json.loads(read(source/'result.json'))['status']=='PASS'
    assert len(samples)==len(previous['points'])==len(local)==10
    # These sources determine ordering, publication clocks and mapping predicates.
    for name in ('live_task_cache.py','guest_cache_exporter.py','snapshot_cache_residency.py',
                 'provider_slot_cache.py','fast_cache_metadata.py'):
        captured=read(source/'code'/name)
        if name!='snapshot_cache_residency.py':
            assert captured==(ROOT/'scripts'/name).read_bytes(),name
    helpers=[json.loads(read(source/relative/'snapshot-query-result.json')) for relative in (Path('.'),Path('lower-query'))]
    points=[];details=[]
    for sample,prior,ids in zip(samples,previous['points'],local,strict=True):
        point=sample['point'];assert point==prior['point']==ids['point']
        rows=np.frombuffer(read(source/f'pair-{point:04}-host.bin'),dtype='<u8').reshape(-1,4)
        selected=np.array([str(int(row[0])) in ids['unknown'] and
                           ids['unknown'][str(int(row[0]))]=='vmm_pte_changed' for row in rows],dtype=bool)
        counts=mapping_intervals(rows[selected,1:])
        assert counts['stable']==0
        assert counts['first_middle_only']+counts['middle_last_only']+counts['both_intervals']==prior['counts']['vmm_pte_changed']
        first_scan=0
        for helper in helpers:
            q=helper['query_costs'][2*point]
            assert q['query']==2*point+1
            first_scan+=q['stages']['mincore_scan']['elapsed_ns']
        points.append(dict(point=point,unconfirmed_guest_capacity_bytes=prior['unconfirmed_guest_capacity_bytes'],
                           absent_before_pages=prior['added_or_reused_subtypes']['absent_before'],
                           changed_identity_pages=prior['added_or_reused_subtypes']['tag_changed'],
                           mapping_changes=counts,**point_windows(sample),
                           first_query_mincore_elapsed_ns=first_scan))
        for row in rows[selected]:
            a,b,c=map(int,row[1:])
            details.append(dict(point=point,guest_pfn=int(row[0]),
                                first_middle=bool((a^b)&PHYSICAL_MASK),
                                middle_last=bool((b^c)&PHYSICAL_MASK)))
    for path,expected in inventory.items():
        st=(ROOT/path).stat();assert [st.st_ino,st.st_size,st.st_mtime_ns]==expected
    duration_keys=[k for k in points[0] if k.endswith('_ns')]
    means={k:statistics.mean(p[k] for p in points) for k in duration_keys}
    totals={k:sum(p['mapping_changes'][k] for p in points) for k in points[0]['mapping_changes']}
    value=dict(status='PASS',source=str(source.relative_to(ROOT)),points=points,
               mapping_row_totals=totals,duration_means_ns=means,
               interpretation='Differences between saved PTE reads, not exact change times or event counts. Guest and host clock intervals computed separately.',
               candidate=dict(scope='Existing private segmented-provider batch path only; other paths unchanged',
                              preparation_stages=list(PREP),
                              sequence=['guest_before','decode_before','maps_before','backend_offsets_before','lower_offsets',
                                        'vmm_first','backend_query_first','lower_query_first','provider_first','vmm_middle',
                                        'provider_last','guest_after','decode_after','backend_query_last','lower_query_last','vmm_last'],
                              movable_recorded_preparation_mean_ns=means['preparation_elapsed_ns'],
                              host_guest_read_span_change_in_fixed_duration_model_ns=0,
                              scope_of_estimate='Recorded preparation work removable from VMM observation interval under unchanged step durations; not measured new latency or fewer unknown pages'),
               input_files_unchanged=len(inventory),input_inventory=inventory,
               runtime_changed=False,live_operations=False,payload_reads=False,new_performance_measurements=0)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    (output/'mapping-intervals.local.json').write_text(json.dumps(details,indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--partition',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();review(args.source,args.partition,args.output)
