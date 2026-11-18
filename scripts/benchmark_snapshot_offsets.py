"""One paired offline pass over the saved 10 points; no live/device operations."""
import argparse
import ast
import inspect
import json
from pathlib import Path
import statistics
import textwrap

import numpy as np

from profile_saved_cache_costs import (ROOT, SOURCE_FILES, captured_snapshot_class, replay)
from snapshot_cache_residency import SnapshotCache
from snapshot_layer_metadata import RawMemoryLayer

SUFFIXES = ('-before.bin', '-after.bin', '-host.bin', '-backend.bin', '-lower.bin',
            '-provider.json', '-maps.json')


def outside_offsets(text):
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SnapshotCache')
    cls.body = [n for n in cls.body if not isinstance(n, ast.FunctionDef) or n.name != 'offsets']
    return ast.dump(tree, include_attributes=False)


def recorded_mapper(cls, slots, identity):
    mapper = cls.__new__(cls)
    mapper.slots, mapper.identity = slots, identity
    calls = []
    method = mapper.offsets
    def offsets(pfns):
        result = method(pfns)
        calls.append(result)
        return result
    mapper.offsets = offsets
    return mapper, calls


def offset_candidate(baseline, method):
    """Replace only offsets, keeping the capture's other methods and globals."""
    namespace = dict(baseline.offsets.__globals__)
    exec(compile(textwrap.dedent(inspect.getsource(method)), '<offset-candidate>', 'exec'), namespace)
    return type('OffsetCandidate', (baseline,), dict(offsets=namespace['offsets']))


def benchmark(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    for name in SOURCE_FILES:
        previous, current = (source/'code'/name).read_bytes(), (ROOT/'scripts'/name).read_bytes()
        if name != 'snapshot_cache_residency.py':
            assert previous == current, name+' differs from capture'
    assert (source.parents[2]/'code/measure_file_physical.py').read_bytes() == (ROOT/'scripts/measure_file_physical.py').read_bytes()
    baseline = captured_snapshot_class(source)
    candidate = offset_candidate(baseline, SnapshotCache.offsets)
    samples = [json.loads(line) for line in (source/'samples.jsonl').read_text().splitlines()]
    assert json.loads((source/'result.json').read_text())['status'] == 'PASS'
    assert [p['point'] for p in samples] == list(range(10))
    inputs, inventory = [], {}
    for p in samples:
        prefix = source/f"pair-{p['point']:04}"
        raw = {}
        for suffix in SUFFIXES:
            path = Path(str(prefix)+suffix)
            raw[suffix] = path.read_bytes()
            st = path.stat(); inventory[path.name] = [st.st_ino, st.st_size, st.st_mtime_ns]
        inputs.append(raw)
    slots = json.loads((source/'ram-attestation.json').read_text())['slots']
    identity = json.loads((source/'snapshot-backend.json').read_text())['identity']
    identity = tuple(identity[k] for k in ('device', 'inode', 'rdev'))
    metadata = json.loads((source/'lower-metadata/metadata.json').read_text())
    layer = RawMemoryLayer.__new__(RawMemoryLayer)
    layer.segments = RawMemoryLayer.decode_index((source/'lower-metadata/index.bin').read_bytes(), metadata['index_offset'])
    layer.starts = [s[0] for s in layer.segments]
    points = []
    for sample, raw in zip(samples, inputs, strict=True):
        order = ['before', 'after'] if sample['point'] % 2 == 0 else ['after', 'before']
        results, offsets, physical = {}, {}, {}
        for name in order:
            mapper, calls = recorded_mapper(baseline if name == 'before' else candidate, slots, identity)
            result = replay(sample, raw, mapper, layer)
            physical[name] = result.pop('physical')
            assert len(calls) == 2
            offsets[name] = calls
            result['offset_pair_cpu_ns'] = sum(result['costs'][k]['cpu_ns'] for k in
                ('backend_offsets_before', 'backend_offsets_after'))
            result['offset_pair_elapsed_ns'] = sum(result['costs'][k]['elapsed_ns'] for k in
                ('backend_offsets_before', 'backend_offsets_after'))
            results[name] = result
        assert all(np.array_equal(a, b) for a, b in zip(offsets['before'], offsets['after'], strict=True)), 'offsets differ'
        assert physical['before'] == physical['after'], 'physical PFN sets differ'
        points.append(dict(point=sample['point'], order=order, results=results,
            offsets_equal=True, physical_sets_equal=True, physical_pages=len(physical['before'])))
    for name, expected in inventory.items():
        st = (source/name).stat()
        assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected
    means = {name: {key: statistics.mean(p['results'][name][key] for p in points)
        for key in ('offset_pair_cpu_ns', 'offset_pair_elapsed_ns', 'replay_inside_point_cpu_ns')}
        for name in ('before', 'after')}
    value = dict(status='PASS', source=str(source.relative_to(ROOT)), points=points, means=means,
        timing_passes_per_implementation=1, points_per_implementation=10,
        endpoint_offset_comparisons=20, metrics_and_physical_sets_equal_points=10,
        cpu_reduction_fraction=1-means['after']['offset_pair_cpu_ns']/means['before']['offset_pair_cpu_ns'],
        baseline='actual successful capture source', candidate='current SnapshotCache.offsets only',
        method='One pass each; alternate which implementation runs first by point. Inputs preloaded; checks outside timed stages.',
        scope='Offline offset conversion and saved-data accounting; no live I/O or online total-cost claim.',
        input_files_unchanged=len(inventory), raw_inventory=inventory)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    benchmark(args.source, args.output)
