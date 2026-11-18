"""Compare runtime batch result reconstruction with captured code; no timing/VM."""
import argparse
import json
from pathlib import Path

import numpy as np

from profile_saved_query_results import original_result_function, recover_batch, prepare_unique, context
from snapshot_cache_residency import SnapshotCache


def validate(source, output):
    source,output=source.resolve(),output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory={}
    def read(relative):
        path=source/relative;st=path.stat()
        inventory[str(relative)]=[st.st_ino,st.st_size,st.st_mtime_ns]
        return path.read_bytes()
    assert json.loads(read('result.json'))['status']=='PASS'
    read('code/snapshot_cache_residency.py')
    reference,_=original_result_function(source)
    log=json.loads(read('lower-query/snapshot-query-result.json'))
    assert log['returncode']==0 and len(log['queries'])==20
    assert [q['query'] for q in log['queries']]==list(range(1,21))
    compared=[]
    for point in range(10):
        rows=np.frombuffer(read(f'pair-{point:04}-lower.bin'),dtype='<u8').reshape(-1,4)
        offsets=rows[:,1];ordered=prepare_unique(offsets)
        for side in (0,1):
            query=log['queries'][2*point+side]
            assert query['bpf_lookup']['mode']=='batch' and query['bpf_lookup']['returncode']==-2
            expected=rows[:,2+side]
            recovered=recover_batch(offsets,expected,query)
            assert recovered is not None, 'query lacks complete post-filter inputs'
            batch,residency=recovered
            old,new=context(query),context(query)
            before=reference(old,offsets,ordered,residency,batch)
            after=SnapshotCache._batch_result(new,offsets,ordered,residency,batch)
            assert np.array_equal(before,expected) and np.array_equal(after,expected), 'query output differs'
            assert before.dtype==after.dtype==np.dtype('<u8')
            assert old.query_statistics==new.query_statistics==[query], 'query statistics differ'
            compared.append(dict(point=point,side=side,requested_rows=len(offsets),
                output_equal=True,all_statistics_equal=True,missing_kernel_results=query['missing_kernel_results']))
    for relative,expected in inventory.items():
        st=(source/relative).stat();assert [st.st_ino,st.st_size,st.st_mtime_ns]==expected
    value=dict(status='PASS',source=str(source),queries=compared,compared_queries=len(compared),
        missing_kernel_results=sum(p['missing_kernel_results'] for p in compared),
        input_files_unchanged=len(inventory),input_inventory=inventory,timing_run=False,live_operations=False,
        scope='20 fully recoverable lower-layer queries; captured result tail versus actual runtime array helper')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print('status=PASS result_dir='+str(output.parent))
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();validate(args.source,args.output)
