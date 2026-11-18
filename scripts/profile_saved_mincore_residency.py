"""Compare residency dictionary construction offline; no mincore, BPF or VM.

Offsets come from saved queries. Every residency byte is synthetic, including
unrequested gap bytes; original live vectors were not saved.
"""
import argparse
import ast
import copy
import ctypes
import json
from pathlib import Path
import statistics
import time

import numpy as np

UNKNOWN = (1 << 64)-1
ROOT = Path(__file__).resolve().parents[1]
PATTERN = np.array([0, 1, 2, 3, 128, 129, 254, 255], dtype='u1')
ORIGINAL = 'residency.update((int(offset), vector[int(offset)-start]) for offset in run)'
CANDIDATE = 'residency.update((offset, vector[offset-start]) for offset in map(int, run))'


def projection_functions(source):
    """Extract the actual batch update, then compare one conversion per offset."""
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SnapshotCache')
    query = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'query')
    loop = next(n for n in query.body if isinstance(n, ast.For))
    branch = next(n for n in loop.body if isinstance(n, ast.If) and
                  ast.unparse(n.test) == 'self.batch_entries is not None')
    original = branch.body[-1]
    if ast.dump(original) != ast.dump(ast.parse(ORIGINAL).body[0]):
        raise ValueError('captured batch residency update differs from the reviewed expression')
    functions, texts = {}, {}
    for name, update in [('current', original), ('candidate', ast.parse(CANDIDATE).body[0])]:
        function = ast.parse('def project(chunks):\n residency = {}\n for run, start, vector in chunks:\n  pass\n return residency').body[0]
        function.body[1].body = [copy.deepcopy(update)]
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        namespace = {}
        exec(compile(module, '<offline-residency-'+name+'>', 'exec'), namespace)
        functions[name] = namespace['project']
        texts[name] = ast.unparse(module)
    return functions, texts


def synthetic_chunks(offsets, phase):
    ordered = np.unique(offsets[offsets != np.uint64(UNKNOWN)])
    splits = np.flatnonzero(np.diff(ordered) > 2)+1
    chunks = []
    for run in np.split(ordered, splits):
        if not len(run):
            continue
        start = int(run[0]); length = int(run[-1])-start+1
        vector = (ctypes.c_ubyte*length)()
        np.ctypeslib.as_array(vector)[:] = PATTERN[(np.arange(length)+(start+phase) % len(PATTERN)) % len(PATTERN)]
        chunks.append((run, start, vector))
    return ordered, chunks


def check_projection(result, ordered, phase):
    expected = {int(offset): int(PATTERN[(int(offset)+phase) % len(PATTERN)]) for offset in ordered}
    assert result == expected, 'residency bytes differ from the synthetic oracle'
    assert list(result) == list(expected), 'requested order differs'
    assert all(type(k) is int and type(v) is int for k, v in result.items()), 'Python int types changed'
    return sum(v & 1 for v in result.values())


def timed(function, chunks):
    wall, cpu = time.monotonic_ns(), time.thread_time_ns()
    result = function(chunks)
    return result, dict(cpu_ns=time.thread_time_ns()-cpu, elapsed_ns=time.monotonic_ns()-wall)


def profile(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory = {}

    def read(relative):
        path = source/relative
        st = path.stat(); inventory[str(relative)] = [st.st_ino, st.st_size, st.st_mtime_ns]
        return path.read_bytes()

    state = json.loads(read('result.json'))
    samples = [json.loads(line) for line in read('samples.jsonl').splitlines()]
    assert state['status'] == 'PASS' and state['samples'] == len(samples) == 10
    assert [p['point'] for p in samples] == list(range(10))
    captured = read('code/snapshot_cache_residency.py')
    functions, texts = projection_functions(captured.decode())
    logs = {}
    for name, relative in [('backend', Path('.')), ('lower', Path('lower-query'))]:
        metadata = json.loads(read(relative/'snapshot-backend.json'))
        assert metadata['batch_lookup']['mincore_max_gap_pages'] == 1
        log = json.loads(read(relative/'snapshot-query-result.json'))
        assert log['returncode'] == 0 and len(log['queries']) == 20
        assert [q['query'] for q in log['queries']] == list(range(1, 21))
        logs[name] = log['queries']
    inputs = []
    # All file reads and synthetic vector allocation/fill finish before timing.
    for sample in samples:
        point = sample['point']
        arrays = {name: np.frombuffer(read(f'pair-{point:04}-{name}.bin'), dtype='<u8').reshape(-1, 4)
                  for name in ('backend', 'lower')}
        for side in range(2):
            for layer_index, name in enumerate(('backend', 'lower')):
                offsets = arrays[name][:, 1]; query = logs[name][2*point+side]
                phase = query['query']+layer_index
                ordered, chunks = synthetic_chunks(offsets, phase)
                assert len(ordered) == query['requested_pages']
                assert int(np.count_nonzero(offsets == np.uint64(UNKNOWN))) == query['no_file_mapping_pages']
                inputs.append(dict(point=point, layer=name, side='first' if side == 0 else 'last',
                    query=query['query'], ordered=ordered, chunks=chunks, phase=phase,
                    candidate_first=bool((point+side+layer_index) % 2)))
    output.mkdir(parents=True)
    for name, text in texts.items():
        (output/(name+'.py')).write_text(text+'\n')
    records = []
    for item in inputs:
        order = ['candidate', 'current'] if item['candidate_first'] else ['current', 'candidate']
        costs, resident = {}, {}
        for name in order:
            result, cost = timed(functions[name], item['chunks'])
            costs[name] = cost
            resident[name] = check_projection(result, item['ordered'], item['phase'])
            del result  # Release outside timing, before the next implementation.
        assert resident['current'] == resident['candidate']
        requested = len(item['ordered'])
        queried = sum(len(vector) for _, _, vector in item['chunks'])
        records.append(dict(point=item['point'], layer=item['layer'], side=item['side'], query=item['query'],
            order=order, costs=costs, requested_pages=requested, merged_runs=len(item['chunks']),
            queried_pages=queried, extra_gap_pages=queried-requested,
            synthetic_phase=item['phase'], synthetic_resident_requested_pages=resident['current'],
            exact_bytes_equal=True, python_int_types_equal=True, requested_order_equal=True))
    for relative, expected in inventory.items():
        st = (source/relative).stat(); assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected
    points = [dict(point=point, costs={name: {key: sum(r['costs'][name][key] for r in records if r['point'] == point)
        for key in ('cpu_ns', 'elapsed_ns')} for name in ('current', 'candidate')}) for point in range(10)]
    means = {name: {key: statistics.mean(p['costs'][name][key] for p in points)
        for key in ('cpu_ns', 'elapsed_ns')} for name in ('current', 'candidate')}
    value = dict(status='PASS', source=str(source.relative_to(ROOT)), queries=records, points=points,
        four_query_means=means, timing_passes_per_implementation_per_query=1,
        current_first_queries=sum(r['order'][0] == 'current' for r in records),
        candidate_first_queries=sum(r['order'][0] == 'candidate' for r in records),
        synthetic_pattern=PATTERN.tolist(), original_expression=ORIGINAL, candidate_expression=CANDIDATE,
        input_files_unchanged=len(inventory), input_inventory=inventory, runtime_changed=False,
        scope='Dictionary creation and requested-offset projection only. Precomputed runs/starts and synthetic ctypes byte vectors. No vector allocation/fill, mincore, BPF, filtering, result restoration or full collector timing.',
        data_meaning='Real saved offsets; all residency bytes synthetic. Full byte and Python int types retained; gap keys absent. Original live vectors unavailable.',
        live_operations=False, payload_reads=False)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); profile(args.source, args.output)
