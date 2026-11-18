"""Compare gap-one/two request counts offline, without queries or payload reads."""
import argparse
import json
from pathlib import Path

import numpy as np

from review_saved_mincore_ranges import UNKNOWN, query_ranges, summarize

ROOT=Path(__file__).resolve().parents[1]


def check_requested_filter(offsets):
    """Select original offsets from each span; synthetic full bytes only."""
    ordered=np.unique(offsets[offsets!=np.uint64(UNKNOWN)])
    expected={int(off):int(off)%256 for off in ordered}
    for gap in (1,2):
        projected={}
        for start,end in query_ranges(offsets,gap):
            vector=np.arange(end-start,dtype='<u8')
            vector=((vector+np.uint64(start%256))%256).astype('u1')
            first,last=np.searchsorted(ordered,[start,end])
            projected.update((int(off),int(vector[int(off)-start])) for off in ordered[first:last])
        assert projected==expected and list(projected)==list(expected)
    return len(ordered)


def compare_queries(queries,capacity):
    policies=[summarize(queries,gap) for gap in (1,2)]
    current,candidate=policies
    assert all(p['possible_keys_per_map']==p['observed_returned_entries'] for p in current['points']), \
        'gap-one prefix keys differ from saved returned entries'
    assert current['possible_keys_per_map']<=capacity
    for query in queries:assert check_requested_filter(query['offsets'])==query['requested_pages']
    for policy in policies:
        policy.update(map_capacity=capacity,map_headroom=capacity-policy['possible_keys_per_map'])
    delta=dict(calls_saved=current['calls']-candidate['calls'],
        calls_reduced_percent=100*(current['calls']-candidate['calls'])/current['calls'] if current['calls'] else 0,
        additional_queried_pages=candidate['queried_pages']-current['queried_pages'],
        additional_query_percent=100*(candidate['queried_pages']-current['queried_pages'])/current['queried_pages'] if current['queried_pages'] else 0,
        additional_keys_per_map=candidate['possible_keys_per_map']-current['possible_keys_per_map'],
        additional_entries_copied=candidate['possible_entries_copied']-current['possible_entries_copied'])
    return dict(policies=policies,delta_from_gap_one=delta,
                candidate_within_saved_capacity=candidate['possible_keys_per_map']<=capacity)


def review(source,output):
    source,output=source.resolve(),output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory={}
    def read(relative):
        path=source/relative;st=path.stat()
        inventory[str(relative)]=[st.st_ino,st.st_size,st.st_mtime_ns]
        return path.read_bytes()
    assert json.loads(read('result.json'))['status']=='PASS'
    samples=[json.loads(line) for line in read('samples.jsonl').splitlines()]
    assert len(samples)==10 and [p['point'] for p in samples]==list(range(10))
    captured=read('code/snapshot_cache_residency.py').decode()
    live=read('code/live_task_cache.py').decode()
    # Historical counts are governed by captured gap-one code and metadata.
    assert live==(ROOT/'scripts/live_task_cache.py').read_text()
    assert 'pid == TARGET' in captured and '@entries[@idx[tid]]=(@token[0],$pfn)' in captured
    assert 'splits = np.flatnonzero(np.diff(ordered) > 2)+1' in captured
    assert 'splits = np.flatnonzero(np.diff(ordered) != 1)+1' in captured
    assert 'residency.update((offset,vector[offset-start]) for offset in map(int,run))' in captured
    assert 'batch = self.batch_entries.read(ordered, self.token)' in captured
    assert live.index('self._start_snapshot_queries(proc)')<live.index('self.thread.start()')
    logs,metadata={},{}
    for name,relative in [('backend',Path('.')),('lower',Path('lower-query'))]:
        log=json.loads(read(relative/'snapshot-query-result.json'))
        assert log['returncode']==0 and len(log['queries'])==len(log['query_costs'])==20
        logs[name]=log
        metadata[name]=json.loads(read(relative/'snapshot-backend.json'))
        batch=metadata[name]['batch_lookup'];info=batch['map_info']
        assert batch['mincore_max_gap_pages']==1
        assert (info['type'],info['bytes_key'],info['bytes_value'],info['flags'],info['max_entries'])==('hash',8,16,0,131072)
    queries=[]
    for sample in samples:
        point=sample['point'];group=[]
        for name in ('backend','lower'):
            rows=np.frombuffer(read(f'pair-{point:04}-{name}.bin'),dtype='<u8').reshape(-1,4)
            offsets=rows[:,1]
            for side,label in enumerate(('first','last')):
                i=2*point+side
                q,cost=logs[name]['queries'][i],logs[name]['query_costs'][i]
                outer=sample['host_stage_costs'][name+'_query_'+label]
                assert q['query']==cost['query']==i+1
                assert outer['host_start_ns']<=cost['host_start_ns']<=cost['host_end_ns']<=outer['host_end_ns']
                assert q['no_file_mapping_pages']==int(np.count_nonzero(offsets==np.uint64(UNKNOWN)))
                assert q['missing_kernel_results']==0 and q['bpf_lookup']['returncode']==-2
                group.append(dict(layer=name,point=point,side=label,query=q['query'],offsets=offsets,
                    requested_pages=q['requested_pages'],mapping_length=metadata[name]['mapping_length'],
                    returned_entries=q['bpf_lookup']['returned_entries'],start=cost['host_start_ns'],end=cost['host_end_ns']))
        group.sort(key=lambda q:q['start'])
        assert [(q['layer'],q['side']) for q in group]==[('backend','first'),('lower','first'),('backend','last'),('lower','last')]
        queries.extend(group)
    assert all(a['end']<=b['start'] for a,b in zip(queries,queries[1:]))
    result=compare_queries(queries,131072)
    for relative,expected in inventory.items():
        st=(source/relative).stat();assert [st.st_ino,st.st_size,st.st_mtime_ns]==expected
    value=dict(status='PASS',source=str(source.relative_to(ROOT)),queries_checked=len(queries),
        baseline_gap_pages=1,candidate_gap_pages=2,baseline_prefix_counts_match_queries=len(queries),
        requested_filter_queries_checked=len(queries),input_files_unchanged=len(inventory),input_inventory=inventory,
        **result,key_scope='Each map sees both layers in serial query order; count their file-offset prefix union, not a sum of separate per-file counts.',
        candidate_scope='Projected calls, page-query counts, key and copy upper bounds only. Synthetic bytes check requested-only filtering. No candidate PFNs or CPU/elapsed/residency measurements.',
        runtime_changed=False,live_operations=False,payload_reads=False,performance_experiments=0)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS result_dir='+str(output.parent))
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();review(args.source,args.output)
