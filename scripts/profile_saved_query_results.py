"""Offline query preparation and recoverable result replay; no live BPF or VM.

Only fully confirmed queries recover the post-filter PFN dictionary. Full BPF
map payloads, syscall timing and unconfirmed PFNs are not reconstructed.
"""
import argparse
import ast
import copy
import ctypes
import json
from pathlib import Path
import statistics
import time
from types import SimpleNamespace

import numpy as np

from profile_saved_cache_costs import captured_snapshot_class
from snapshot_cache_residency import MASK, PRESENT

UNKNOWN = (1 << 64)-1
ROOT = Path(__file__).resolve().parents[1]


def original_result_function(source):
    """Keep the captured query tail verbatim; supply its already-filtered batch."""
    cls = captured_snapshot_class(source)
    tree = ast.parse((source/'code/snapshot_cache_residency.py').read_text())
    query = next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='query')
    start = next(i for i,n in enumerate(query.body) if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='result' for t in n.targets))
    if not any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='batch' for t in n.targets)
               for n in query.body[start:]):
        raise ValueError('capture must contain the pre-array inline batch result loop')
    body = [copy.deepcopy(n) for n in query.body[start:] if not (isinstance(n,ast.Assign)
            and any(isinstance(t,ast.Name) and t.id=='batch' for t in n.targets))]
    function = ast.parse('def replay_result(self, offsets, ordered, residency, batch): pass').body[0]
    function.body = body
    module = ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    namespace = dict(cls.query.__globals__)
    exec(compile(module,'<captured-query-tail>','exec'),namespace)
    return namespace['replay_result'],ast.unparse(module)


def array_result(self, offsets, ordered, residency, batch):
    """Offline candidate only: vectorize row expansion, retaining missing rows."""
    values = [batch.get(int(offset)) for offset in ordered]
    found = np.fromiter((value is not None for value in values),dtype=bool,count=len(values))
    pfns = np.fromiter((0 if value is None else value for value in values),dtype='<u8',count=len(values))
    resident = np.fromiter((residency[int(offset)]&1 for offset in ordered),dtype=bool,count=len(ordered))
    entries = np.where(found & (pfns != 0) & resident, pfns | np.uint64(PRESENT), np.uint64(0))
    file_mask = offsets != np.uint64(UNKNOWN)
    indices = np.searchsorted(ordered,offsets[file_mask])
    result = np.zeros(len(offsets),dtype='<u8')
    result[file_mask] = entries[indices]
    missing = int(np.count_nonzero(~found[indices]))
    self.query_statistics.append(dict(query=self.token,requested_pages=len(ordered),
        missing_kernel_results=missing,mincore_resident_pages=sum(v&1 for v in residency.values()),
        confirmed_pfn_pages=int(np.count_nonzero(result & np.uint64(PRESENT))),
        no_file_mapping_pages=int(np.count_nonzero(offsets == np.uint64(UNKNOWN)))))
    if self.batch_entries is not None:
        self.query_statistics[-1]['bpf_lookup'] = dict(self.batch_entries.last_statistics)
    return result


def recover_batch(offsets, result, statistics):
    """Return exact logical mappings only if every requested row is confirmed."""
    file_mask = offsets != np.uint64(UNKNOWN)
    ordered = np.unique(offsets[file_mask])
    assert statistics['requested_pages']==len(ordered), 'request count mismatch'
    assert statistics['no_file_mapping_pages']==len(offsets)-int(np.count_nonzero(file_mask)), 'unknown count mismatch'
    confirmed = (result & np.uint64(PRESENT)) != 0
    assert statistics['confirmed_pfn_pages']==int(np.count_nonzero(confirmed)), 'saved output was invalidated or count differs'
    assert np.all(result[~file_mask]==0), 'unknown offset has a confirmed result'
    assert np.all((result==0) | (confirmed & ((result & np.uint64(MASK))!=0))), 'unexpected query encoding'
    if (statistics['missing_kernel_results'] != 0 or
            statistics['mincore_resident_pages'] != len(ordered) or
            not np.all(confirmed[file_mask])):
        return None
    batch = {}
    for offset,value in zip(offsets[file_mask],result[file_mask],strict=True):
        key,pfn = int(offset),int(value)&MASK
        if key in batch: assert batch[key]==pfn, 'conflicting duplicate offset'
        batch[key] = pfn
    assert len(batch)==len(ordered)
    # Canonical insertion order; original HASH traversal order was not saved.
    batch = {int(key):batch[int(key)] for key in ordered}
    residency = {int(key):1 for key in ordered}
    return batch,residency


def prepare_unique(offsets):
    return np.unique(offsets[offsets != np.uint64(UNKNOWN)])


def prepare_runs(ordered):
    splits = np.flatnonzero(np.diff(ordered) != 1)+1
    return np.split(ordered,splits)


def allocate_vectors(runs):
    vector = None
    for run in runs:
        if not len(run): continue
        vector = (ctypes.c_ubyte*len(run))()
    return vector


def timed(function, *args):
    wall,cpu = time.monotonic_ns(),time.thread_time_ns()
    value = function(*args)
    return value,dict(cpu_ns=time.thread_time_ns()-cpu,elapsed_ns=time.monotonic_ns()-wall)


def context(query):
    return SimpleNamespace(token=query['query'],query_statistics=[],
        batch_entries=SimpleNamespace(last_statistics=query['bpf_lookup']))


def profile(source, output):
    source,output = source.resolve(),output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory = {}
    def read(relative):
        path = source/relative
        st = path.stat();inventory[str(relative)] = [st.st_ino,st.st_size,st.st_mtime_ns]
        return path.read_bytes()
    state = json.loads(read('result.json'))
    samples = [json.loads(line) for line in read('samples.jsonl').splitlines()]
    assert state['status']=='PASS' and state['samples']==len(samples)==10
    assert [p['point'] for p in samples]==list(range(10))
    for name in ('snapshot_cache_residency.py','live_task_cache.py'):
        captured = read('code/'+name)
        if name != 'snapshot_cache_residency.py':
            assert captured==(ROOT/'scripts'/name).read_bytes(), 'runtime differs from capture: '+name
    reference,reference_text = original_result_function(source)
    cls = captured_snapshot_class(source);mapper = cls.__new__(cls)
    mapper.slots = json.loads(read('ram-attestation.json'))['slots']
    identity = json.loads(read('snapshot-backend.json'))['identity']
    mapper.identity = tuple(identity[k] for k in ('device','inode','rdev'))
    logs = {}
    for name,relative in [('backend',Path('.')),('lower',Path('lower-query'))]:
        data = json.loads(read(relative/'snapshot-query-result.json'))
        assert data['returncode']==0 and len(data['queries'])==20
        logs[name]=data['queries']
        assert [q['query'] for q in logs[name]]==list(range(1,21))
    # Preload/validate all metadata and recovered logical inputs before timing.
    inputs = []
    for sample in samples:
        point = sample['point']
        arrays = {name:np.frombuffer(read(f'pair-{point:04}-{name}.bin'),dtype='<u8').reshape(-1,4)
                  for name in ('backend','lower')}
        assert np.array_equal(arrays['backend'][:,0],arrays['lower'][:,0])
        maps = json.loads(read(f'pair-{point:04}-maps.json'))
        mapper.regions=maps['before'];first=mapper.offsets(arrays['backend'][:,0])
        mapper.regions=maps['after'];last=mapper.offsets(arrays['backend'][:,0])
        assert np.array_equal(first,arrays['backend'][:,1])
        # Only the last outputs could be cleared after the query; do not silently
        # use a modified vector as the query's original result.
        assert np.array_equal(first,last), 'mapping changed: original last-query output not established'
        for side in (0,1):
            for name in ('backend','lower'):
                rows=arrays[name];query=logs[name][2*point+side]
                assert query['bpf_lookup']['mode']=='batch' and query['bpf_lookup']['returncode']==-2
                recovered=recover_batch(rows[:,1],rows[:,2+side],query)
                inputs.append(dict(point=point,layer=name,side=side,offsets=rows[:,1],expected=rows[:,2+side],
                    query=query,recovered=recovered))
    output.mkdir(parents=True)
    (output/'captured_result_tail.py').write_text(reference_text+'\n')
    results=[]
    for item in inputs:
        ordered,unique_cost=timed(prepare_unique,item['offsets'])
        runs,split_cost=timed(prepare_runs,ordered)
        vector,alloc_cost=timed(allocate_vectors,runs)
        assert len(ordered)==item['query']['requested_pages']
        record=dict(point=item['point'],layer=item['layer'],side=item['side'],
            requested_rows=len(item['offsets']),requested_pages=len(ordered),
            contiguous_runs=sum(bool(len(run)) for run in runs),
            preparation=dict(offset_unique=unique_cost,run_split=split_cost,vector_allocation=alloc_cost),
            result_replay='UNAVAILABLE' if item['recovered'] is None else 'FULLY_CONFIRMED_INPUT',
            saved_missing_kernel_results=item['query']['missing_kernel_results'])
        del vector,runs
        if item['recovered'] is not None:
            batch,residency=item['recovered']
            order=['scalar','array'] if item['point']%2==0 else ['array','scalar']
            costs={}
            for name in order:
                ctx=context(item['query'])
                result,cost=timed(reference if name=='scalar' else array_result,
                    ctx,item['offsets'],ordered,residency,batch)
                assert np.array_equal(result,item['expected']), 'replayed output differs'
                assert result.dtype==np.dtype('<u8')
                assert ctx.query_statistics==[item['query']], 'replayed statistics differ'
                costs[name]=cost
            record.update(result_order=order,result_costs=costs,output_equal=True,all_statistics_equal=True)
        results.append(record)
        with (output/'queries.jsonl').open('a') as log:log.write(json.dumps(record)+'\n')
    for relative,expected in inventory.items():
        st=(source/relative).stat();assert [st.st_ino,st.st_size,st.st_mtime_ns]==expected
    points=[]
    for sample in samples:
        point=sample['point'];selected=[r for r in results if r['point']==point]
        available=[r for r in selected if 'result_costs' in r]
        assert len(selected)==4
        points.append(dict(point=point,preparation={name:{key:sum(r['preparation'][name][key] for r in selected)
            for key in ('cpu_ns','elapsed_ns')} for name in selected[0]['preparation']},
            available_result_queries=len(available),result_costs={name:{key:sum(r['result_costs'][name][key] for r in available)
            for key in ('cpu_ns','elapsed_ns')} for name in ('scalar','array')}))
    value=dict(status='PASS',source=str(source.relative_to(ROOT)),timing_passes=1,points=points,queries=results,
        preparation_query_count=len(results),result_query_count=sum('result_costs' in r for r in results),
        means=dict(preparation={name:{key:statistics.mean(p['preparation'][name][key] for p in points)
            for key in ('cpu_ns','elapsed_ns')} for name in points[0]['preparation']},
            available_result_costs={name:{key:statistics.mean(p['result_costs'][name][key] for p in points)
            for key in ('cpu_ns','elapsed_ns')} for name in ('scalar','array')}),
        scope='40 query preparations; post-filter result loop/statistics/output only where full logical inputs recover. No mincore, BPF calls or full-map filtering timed.',
        unavailable=['per-page direct-backend mincore bytes and unconfirmed PFNs','original full HASH key/token/PFN payload and traversal order','kernel syscall subtimings'],
        reconstructed='Fully confirmed requests give exact requested PFNs and residency=1; dictionary insertion canonicalized, not original HASH order.',
        missing_statistics='Recomputed only for recoverable result queries; unavailable queries retain saved counts, not a reconstructed result.',
        input_files_unchanged=len(inventory),input_inventory=inventory,runtime_changed=False)
    (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();profile(args.source,args.output)
