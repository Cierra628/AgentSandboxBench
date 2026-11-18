"""Offline vector allocation comparison; saved offsets, synthetic bytes/PFNs only."""
import argparse
import ast
import ctypes
import errno
import inspect
import json
from pathlib import Path
import statistics
import textwrap
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import snapshot_cache_residency as runtime

ROOT=Path(__file__).resolve().parents[1]
UNKNOWN=(1<<64)-1
PATTERN=np.array([0,1,2,3,128,129,254,255],dtype='u1')
ALLOCATION='                vector = (ctypes.c_ubyte*length)()'
GROWTH='''                if length > capacity:
                    vector = (ctypes.c_ubyte*length)()
                    capacity = length'''
START='            scan_started = _query_clock()'


def query_functions(source):
    """Only alter batch allocation; reset capacity inside each query's scan timer."""
    assert 'splits = np.flatnonzero(np.diff(ordered) > 2)+1' in source
    cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='SnapshotCache')
    helper=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_batch_result')
    current=ast.parse(textwrap.dedent(inspect.getsource(runtime.SnapshotCache._batch_result))).body[0]
    assert ast.dump(helper,include_attributes=False)==ast.dump(current,include_attributes=False), 'batch result helper differs from capture'
    assert source.count(ALLOCATION+'\n')==source.count(START+'\n')==1
    candidate=source.replace(START+'\n',START+'\n            vector = None\n            capacity = 0\n')
    candidate=candidate.replace(ALLOCATION+'\n',GROWTH+'\n')
    functions={}
    for name,text in [('current',source),('candidate',candidate)]:
        cls=next(n for n in ast.parse(text).body if isinstance(n,ast.ClassDef) and n.name=='SnapshotCache')
        query=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='query')
        module=ast.fix_missing_locations(ast.Module(body=[query],type_ignores=[]))
        namespace=dict(vars(runtime))
        exec(compile(module,'<offline-vector-'+name+'>','exec'),namespace)
        functions[name]=namespace['query']
    return functions,candidate


def allocation_functions():
    """Time only the reviewed allocation statements and their loop/setup."""
    functions={}
    for name,body in [('current',ALLOCATION.strip()),('candidate',textwrap.dedent(GROWTH))]:
        module=ast.parse('def allocate(lengths):\n vector = None\n'+
                         (' capacity = 0\n' if name=='candidate' else '')+
                         ' for length in lengths:\n  pass\n return vector')
        loop=next(n for n in module.body[0].body if isinstance(n,ast.For))
        loop.body=ast.parse(body).body
        module=ast.fix_missing_locations(module)
        namespace={'ctypes':ctypes}
        exec(compile(module,'<offline-vector-allocation-'+name+'>','exec'),namespace)
        functions[name]=namespace['allocate']
    return functions


def query_runs(offsets):
    ordered=np.unique(offsets[offsets!=np.uint64(UNKNOWN)])
    runs=[run for run in np.split(ordered,np.flatnonzero(np.diff(ordered)>2)+1) if len(run)]
    return ordered,[(int(run[0]),int(run[-1])-int(run[0])+1) for run in runs]


def allocation_counts(lengths):
    capacity=0;growth=[]
    for length in lengths:
        if length>capacity: growth.append(length);capacity=length
    return dict(current=dict(allocations=len(lengths),allocated_vector_bytes=sum(lengths)),
                candidate=dict(allocations=len(growth),allocated_vector_bytes=sum(growth)),
                maximum_vector_bytes=capacity)


def synthetic_query(function,offsets,phase=0,batch=True,fail_at=None,batch_error=False):
    """Run the complete real query body with Python substitutes for every interface.

    mincore writes only its requested prefix. Old tail bytes deliberately remain.
    No library loading, mapping, BPF API, file payload or device access occurs.
    """
    offsets=np.asarray(offsets,dtype='<u8')
    s=runtime.SnapshotCache.__new__(runtime.SnapshotCache)
    s.fd,s.identity,s.address=78,(1,2,0),100*runtime.PAGE
    s.tracer=SimpleNamespace(poll=lambda:None)
    s.token,s.mapfds=4,dict(token=79,entries=78)
    s.query_statistics,s.query_costs=[],[]
    events=[];vectors=[];projections=[]
    def pfn(offset):return 0 if offset%11==0 else offset%100000+100
    def update(fd,key,token,flags):events.append(('token',));return 0
    def scalar(fd,key,value):
        offset=int(ctypes.cast(key,ctypes.POINTER(ctypes.c_uint64))[0])
        events.append(('scalar',offset))
        if offset%13==0:return -errno.ENOENT
        value[0],value[1]=s.token,pfn(offset);return 0
    s.lib=SimpleNamespace(bpf_map_update_elem=update,bpf_map_lookup_elem=scalar)
    def mincore(address,length,vector):
        first=(address-s.address)//runtime.PAGE;count=length//runtime.PAGE
        assert address==s.address+first*runtime.PAGE and length==count*runtime.PAGE
        assert count>0 and len(vector)>=count
        events.append(('mincore',first,count));vectors.append(vector)
        if len(vectors)==fail_at:return -1
        np.ctypeslib.as_array(vector)[:count]=PATTERN[(np.arange(count)+(first+phase)%len(PATTERN))%len(PATTERN)]
        return 0
    s.libc=SimpleNamespace(mincore=mincore)
    def read(ordered,token):
        events.append(('batch',tuple(map(int,ordered)),token))
        if batch_error:raise RuntimeError('synthetic batch failure')
        result={int(off):pfn(int(off)) for off in ordered if int(off)%13!=0}
        s.batch_entries.last_statistics=dict(mode='batch',returncode=-errno.ENOENT,returned_entries=len(result))
        return result
    s.batch_entries=SimpleNamespace(read=read,last_costs={
        'batch_lookup':dict(cpu_ns=0,elapsed_ns=0),'batch_filter':dict(cpu_ns=0,elapsed_ns=0)}) if batch else None
    def restore(offsets,ordered,residency,values):
        projections.append(dict(residency))
        return runtime.SnapshotCache._batch_result(s,offsets,ordered,residency,values)
    s._batch_result=restore
    identity=SimpleNamespace(st_dev=1,st_ino=2,st_rdev=0)
    with patch.object(runtime.os,'fstat',return_value=identity):
        try:result=function(s,offsets)
        except (AssertionError,RuntimeError,MemoryError) as error:
            return dict(error=type(error).__name__,state=s,events=events,vectors=vectors,projections=projections)
    return dict(result=result,state=s,events=events,vectors=vectors,projections=projections)


def compare(functions,offsets,phase):
    results={name:synthetic_query(fn,offsets,phase) for name,fn in functions.items()}
    a,b=results['current'],results['candidate']
    assert 'error' not in a and 'error' not in b
    assert np.array_equal(a['result'],b['result']) and a['result'].dtype==b['result'].dtype==np.dtype('<u8')
    assert a['state'].query_statistics==b['state'].query_statistics and a['events']==b['events']
    ordered,runs=query_runs(offsets)
    expected={int(off):int(PATTERN[(int(off)+phase)%len(PATTERN)]) for off in ordered}
    for result in results.values():
        assert result['projections']==[expected]
        assert all(type(k) is int and type(v) is int for k,v in result['projections'][0].items())
        assert [event[1:] for event in result['events'] if event[0]=='mincore']==runs
        assert len(result['state'].query_costs)==1
        assert set(result['state'].query_costs[0]['stages'])=={
            'prepare','mincore_scan','batch_lookup','batch_filter','result_restore'}
    return dict(full_bytes_equal=True,requested_results_equal=True,all_statistics_equal=True,
                interface_order_and_spans_equal=True,synthetic_statistics=a['state'].query_statistics[0])


def timed(function,lengths):
    wall,cpu=time.monotonic_ns(),time.thread_time_ns()
    vector=function(lengths)
    cost=dict(cpu_ns=time.thread_time_ns()-cpu,elapsed_ns=time.monotonic_ns()-wall)
    del vector  # Last buffer release is outside timing in both versions.
    return cost


def profile(source,output):
    source,output=source.resolve(),output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory={}
    def read(relative):
        path=source/relative;st=path.stat()
        inventory[str(relative)]=[st.st_ino,st.st_size,st.st_mtime_ns]
        return path.read_bytes()
    state=json.loads(read('result.json'))
    samples=[json.loads(line) for line in read('samples.jsonl').splitlines()]
    assert state['status']=='PASS' and state['samples']==len(samples)==10
    assert [p['point'] for p in samples]==list(range(10))
    captured=read('code/snapshot_cache_residency.py')
    # Compare the captured gap-one implementation, not the current query policy.
    functions,candidate=query_functions(captured.decode())
    logs={}
    for name,relative in [('backend',Path('.')),('lower',Path('lower-query'))]:
        metadata=json.loads(read(relative/'snapshot-backend.json'))
        assert metadata['batch_lookup']['mincore_max_gap_pages']==1
        log=json.loads(read(relative/'snapshot-query-result.json'))
        assert log['returncode']==0 and len(log['queries'])==20
        assert [q['query'] for q in log['queries']]==list(range(1,21))
        logs[name]=log['queries']
    items=[]
    for point in range(10):
        for layer_index,name in enumerate(('backend','lower')):
            rows=np.frombuffer(read(f'pair-{point:04}-{name}.bin'),dtype='<u8').reshape(-1,4)
            offsets=rows[:,1];ordered,runs=query_runs(offsets)
            query=logs[name][2*point]
            assert query['requested_pages']==len(ordered)
            assert query['no_file_mapping_pages']==int(np.count_nonzero(offsets==np.uint64(UNKNOWN)))
            items.append(dict(point=point,layer=name,query=query['query'],offsets=offsets,runs=runs,
                              lengths=[length for _,length in runs],phase=2*point+1+layer_index))
    output.mkdir(parents=True)
    (output/'candidate-runtime.py').write_text(candidate)
    (output/'current-runtime.py').write_bytes(captured)
    # Correctness and all input preparation complete before allocation timing.
    checks=[compare(functions,item['offsets'],item['phase']) for item in items]
    allocations=allocation_functions();records=[]
    for index,(item,check) in enumerate(zip(items,checks,strict=True)):
        order=['current','candidate'] if index%2==0 else ['candidate','current']
        costs={name:timed(allocations[name],item['lengths']) for name in order}
        records.append(dict(point=item['point'],layer=item['layer'],side='first',query=item['query'],
                            order=order,costs=costs,merged_runs=len(item['runs']),
                            requested_pages=int(np.count_nonzero(np.unique(item['offsets'])!=np.uint64(UNKNOWN))),
                            queried_pages=sum(item['lengths']),counts=allocation_counts(item['lengths']),
                            synthetic_phase=item['phase'],**check))
    for relative,expected in inventory.items():
        st=(source/relative).stat();assert [st.st_ino,st.st_size,st.st_mtime_ns]==expected
    points=[dict(point=point,costs={name:{key:sum(r['costs'][name][key] for r in records if r['point']==point)
        for key in ('cpu_ns','elapsed_ns')} for name in allocations}) for point in range(10)]
    means={name:{key:statistics.mean(p['costs'][name][key] for p in points)
                 for key in ('cpu_ns','elapsed_ns')} for name in allocations}
    counts={name:{key:sum(r['counts'][name][key] for r in records)
                  for key in ('allocations','allocated_vector_bytes')} for name in allocations}
    value=dict(status='PASS',source=str(source.relative_to(ROOT)),queries=records,points=points,
        first_two_query_allocation_means_ns=means,allocation_totals=counts,
        maximum_vector_bytes=max(r['counts']['maximum_vector_bytes'] for r in records),
        timing_passes_per_implementation_per_query=1,current_first_queries=10,candidate_first_queries=10,
        input_files_unchanged=len(inventory),input_inventory=inventory,
        synthetic_pattern=PATTERN.tolist(),runtime_changed=False,live_operations=False,payload_reads=False,
        data_meaning='Saved first-query offsets only; every mincore byte and PFN and BPF response is synthetic. No live vector replay.',
        timing_scope='Allocation statements, growth branch, per-query setup and Python loop over precomputed lengths only. Intermediate vector release included; last release excluded equally. No input read, run splitting, fill, mincore, BPF, dictionary projection, result restore or complete collector timing.',
        memory_meaning='Counts of allocated ctypes vector bytes and maximum vector capacity; not resident physical memory or collector RSS.')
    (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();profile(args.source,args.output)
