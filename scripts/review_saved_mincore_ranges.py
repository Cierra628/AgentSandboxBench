"""Review saved mincore request ranges only; no live queries or payload reads."""
import argparse
import json
from pathlib import Path

import numpy as np

PAGE = 4096
UNKNOWN = (1 << 64)-1
GAPS = (0, 1, 2, 4, 8, 16)


def query_ranges(offsets, gap):
    """Half-open file-page ranges; gap is the maximum skipped pages per join."""
    ordered = np.unique(offsets[offsets != np.uint64(UNKNOWN)])
    if not len(ordered):
        return []
    splits = np.flatnonzero(np.diff(ordered) > gap+1)+1
    return [(int(run[0]), int(run[-1])+1) for run in np.split(ordered, splits)]


def union_ranges(ranges):
    """Union overlapping/adjacent page intervals, including both file namespaces."""
    result = []
    for start, end in sorted(ranges):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def range_pages(ranges):
    return sum(end-start for start, end in ranges)


def summarize(queries, gap):
    seen = []
    points = []
    for query in queries:
        spans = query_ranges(query['offsets'], gap)
        assert all(0 <= a < b and b*PAGE <= query['mapping_length'] for a, b in spans)
        seen = union_ranges(seen+spans)
        requested = len(np.unique(query['offsets'][query['offsets'] != np.uint64(UNKNOWN)]))
        assert requested == query['requested_pages']
        pages = range_pages(spans)
        points.append(dict(layer=query['layer'], point=query['point'], side=query['side'],
            query=query['query'], calls=len(spans), requested_unique_pages=requested,
            queried_pages=pages, extra_queried_pages=pages-requested,
            max_span_pages=max((b-a for a,b in spans), default=0),
            vector_chunks=sum((b-a+PAGE-1)//PAGE for a,b in spans),
            possible_keys_per_map=range_pages(seen),
            observed_returned_entries=query['returned_entries']))
    return dict(gap_pages=gap, calls=sum(p['calls'] for p in points),
        requested_unique_pages=sum(p['requested_unique_pages'] for p in points),
        queried_pages=sum(p['queried_pages'] for p in points),
        extra_queried_pages=sum(p['extra_queried_pages'] for p in points),
        max_span_pages=max((p['max_span_pages'] for p in points), default=0),
        vector_chunks=sum(p['vector_chunks'] for p in points),
        possible_keys_per_map=range_pages(seen),
        possible_entries_copied=sum(p['possible_keys_per_map'] for p in points),
        points=points)


def review(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory = {}
    def read(relative):
        path = source/relative
        st = path.stat(); inventory[str(relative)] = [st.st_ino, st.st_size, st.st_mtime_ns]
        return path.read_bytes()
    assert json.loads(read('result.json'))['status'] == 'PASS'
    samples = [json.loads(line) for line in read('samples.jsonl').splitlines()]
    assert len(samples) == 10 and [p['point'] for p in samples] == list(range(10))
    captured = read('code/snapshot_cache_residency.py').decode()
    assert 'pid == TARGET' in captured and '@entries[@idx[tid]]=(@token[0],$pfn)' in captured
    live = read('code/live_task_cache.py').decode()
    assert live.index('self._start_snapshot_queries(proc)') < live.index('self.thread.start()')
    logs, metadata = {}, {}
    for name, relative in [('backend',Path('.')), ('lower',Path('lower-query'))]:
        log = json.loads(read(relative/'snapshot-query-result.json'))
        assert log['returncode'] == 0 and len(log['queries']) == len(log['query_costs']) == 20
        logs[name] = log
        metadata[name] = json.loads(read(relative/'snapshot-backend.json'))
        info = metadata[name]['batch_lookup']['map_info']
        assert (info['type'],info['bytes_key'],info['bytes_value'],info['flags'],info['max_entries']) == ('hash',8,16,0,131072)
    queries = []
    for sample in samples:
        point = sample['point']
        group = []
        for name in ('backend','lower'):
            rows = np.frombuffer(read(f'pair-{point:04}-{name}.bin'), dtype='<u8').reshape(-1,4)
            offsets = rows[:,1]
            for side, label in enumerate(('first','last')):
                i = 2*point+side
                q, cost = logs[name]['queries'][i], logs[name]['query_costs'][i]
                outer = sample['host_stage_costs'][name+'_query_'+label]
                assert q['query'] == cost['query'] == i+1
                assert outer['host_start_ns'] <= cost['host_start_ns'] <= cost['host_end_ns'] <= outer['host_end_ns']
                assert q['no_file_mapping_pages'] == int(np.count_nonzero(offsets == np.uint64(UNKNOWN)))
                assert q['missing_kernel_results'] == 0
                assert q['bpf_lookup']['returncode'] == -2
                group.append(dict(layer=name,point=point,side=label,query=q['query'],offsets=offsets,
                    requested_pages=q['requested_pages'],mapping_length=metadata[name]['mapping_length'],
                    returned_entries=q['bpf_lookup']['returned_entries'],start=cost['host_start_ns'],end=cost['host_end_ns']))
        group.sort(key=lambda q:q['start'])
        assert [(q['layer'],q['side']) for q in group] == [('backend','first'),('lower','first'),('backend','last'),('lower','last')]
        queries.extend(group)
    assert all(a['end'] <= b['start'] for a,b in zip(queries,queries[1:]))
    policies = [summarize(queries, gap) for gap in GAPS]
    baseline = policies[0]
    assert all(p['possible_keys_per_map'] == p['observed_returned_entries'] for p in baseline['points']), 'shared-tracer key model differs from observed tables'
    for policy in policies:
        policy.update(calls_saved=baseline['calls']-policy['calls'],
            calls_reduced_percent=100*(baseline['calls']-policy['calls'])/baseline['calls'],
            extra_query_percent=100*policy['extra_queried_pages']/baseline['queried_pages'],
            map_capacity=131072,map_headroom=131072-policy['possible_keys_per_map'])
    for relative, expected in inventory.items():
        st = (source/relative).stat(); assert [st.st_ino,st.st_size,st.st_mtime_ns] == expected
    value = dict(status='PASS',queries_checked=len(queries),input_files_unchanged=len(inventory),
        input_inventory=inventory,baseline_prefix_counts_match_queries=40,
        policies=policies,recommended_gap_pages=1,
        key_scope='Each tracer sees both layers in this process. Prefix unions include all 40 calls, not just the owning layer.',
        candidate_scope='Projected calls/page lookups/key upper bounds from saved offsets. No candidate residency, PFN, elapsed or CPU measurement.',
        live_operations=False,payload_reads=False,performance_experiments=0)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS result_dir='+str(output.parent))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args();review(args.source,args.output)
