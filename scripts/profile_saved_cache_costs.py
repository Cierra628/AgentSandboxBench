"""Profile saved restored-cache metadata only; no live services or payload reads."""
import argparse
import importlib.util
import inspect
import json
from pathlib import Path
import statistics
import time

import numpy as np

from fast_cache_metadata import decode_array, join_arrays
from provider_slot_cache import account_observed_provider, recount_point
from snapshot_cache_residency import account
from snapshot_layer_metadata import RawMemoryLayer, account_layers

ROOT = Path(__file__).resolve().parents[1]
UNKNOWN = (1 << 64) - 1
SOURCE_FILES = ('fast_cache_metadata.py', 'kernel_cache_metadata.py',
                'snapshot_cache_residency.py', 'snapshot_layer_metadata.py', 'provider_slot_cache.py')
COUNTED_STAGES = ('guest_decode', 'backend_offsets_before', 'lower_offsets',
                  'backend_offsets_after', 'row_assembly', 'vmm_account',
                  'backend_account', 'lower_account', 'provider_account')


def timed(costs, name, function):
    wall = time.monotonic_ns()
    cpu = time.thread_time_ns()
    value = function()
    costs[name] = dict(cpu_ns=time.thread_time_ns()-cpu, elapsed_ns=time.monotonic_ns()-wall)
    return value


def replay(sample, raw, mapper, layer):
    """Time one offline pass of the same pure operations and validate outputs."""
    costs = {}
    before, after = raw['-before.bin'], raw['-after.bin']
    decoded_before, decoded_after = timed(costs, 'guest_decode',
        lambda: (decode_array(before), decode_array(after)))
    provider, maps = timed(costs, 'offline_json_decode', lambda:
        (json.loads(raw['-provider.json']), json.loads(raw['-maps.json'])))
    host, direct, lower = [np.frombuffer(raw[suffix], dtype='<u8').reshape(-1, 4)
        for suffix in ('-host.bin', '-backend.bin', '-lower.bin')]
    pages = decoded_before[1]
    pfns = pages[pages[:, 1] != 0, 0]
    mapper.regions = maps['before']
    offsets = timed(costs, 'backend_offsets_before', lambda: mapper.offsets(pfns))
    lower_offsets = timed(costs, 'lower_offsets', lambda: layer.offsets(offsets))
    mapper.regions = maps['after']
    last_offsets = timed(costs, 'backend_offsets_after', lambda: mapper.offsets(pfns))
    assert np.array_equal(offsets, direct[:, 1]), 'saved direct offsets differ'
    assert np.array_equal(lower_offsets, lower[:, 1]), 'saved lower offsets differ'
    # The saved last queries already have the changed-offset rows invalidated.
    assert np.all(direct[last_offsets != offsets, 3] == 0)
    assert np.all(lower[last_offsets != offsets, 3] == 0)

    def assemble():
        rows = np.column_stack((pfns, host[:, 1], host[:, 2], host[:, 3])).astype('<u8', copy=False)
        backing = np.column_stack((pfns, offsets, direct[:, 2], direct[:, 3])).astype('<u8', copy=False)
        lower_rows = np.column_stack((pfns, lower_offsets, lower[:, 2], lower[:, 3])).astype('<u8', copy=False)
        backing[last_offsets != offsets, 3] = 0
        lower_rows[last_offsets != offsets, 3] = 0
        return rows, backing, lower_rows
    host, direct, lower = timed(costs, 'row_assembly', assemble)
    point = timed(costs, 'vmm_account', lambda: join_arrays(decoded_before, decoded_after, host))
    point.update(timed(costs, 'backend_account', lambda: account(decoded_before, decoded_after, host, direct)))
    point.update(timed(costs, 'lower_account', lambda: account_layers(decoded_before, decoded_after, host, direct, lower)))
    measured, physical = timed(costs, 'provider_account', lambda: account_observed_provider(
        before, after, host, direct, lower, provider['first'], provider['last'],
        decoded=(decoded_before, decoded_after)))
    point.update(measured)
    assert all(sample[k] == value for k, value in point.items()), 'saved measurement differs'

    def serialize():
        return dict(host=host.tobytes(), backend=direct.tobytes(), lower=lower.tobytes(),
            provider=json.dumps(provider).encode()+b'\n', maps=json.dumps(maps)+'\n',
            sample=json.dumps(dict(sample, **point))+'\n')
    encoded = timed(costs, 'serialization_after_point_timer', serialize)
    return dict(point=sample['point'], costs=costs,
        replay_inside_point_cpu_ns=sum(costs[name]['cpu_ns'] for name in COUNTED_STAGES),
        online_point_cpu_ns=sample['host_thread_cpu_ns'],
        online_provider_query_cpu_ns=sample['provider_query_cpu_ns'],
        cache_pages=len(pfns), maps_before=len(maps['before']), maps_after=len(maps['after']),
        provider_serialized_bytes=len(encoded['provider']), physical=physical)


def query_shape(offsets):
    ordered = np.unique(offsets[offsets != np.uint64(UNKNOWN)])
    return dict(requested_rows=len(offsets), file_rows=int(np.count_nonzero(offsets != np.uint64(UNKNOWN))),
        unique_file_offsets=len(ordered), contiguous_runs=0 if not len(ordered) else
        int(np.count_nonzero(np.diff(ordered) != 1))+1)


def captured_snapshot_class(source):
    """Keep historical profiling on its captured offsets after runtime changes."""
    path = source/'code/snapshot_cache_residency.py'
    spec = importlib.util.spec_from_file_location('captured_snapshot_cache', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert inspect.getsource(account) == inspect.getsource(module.account), 'backend accounting differs from capture'
    return module.SnapshotCache


def profile(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    # Keep this replay tied to the effective captured implementation, not a later rewrite.
    for name in SOURCE_FILES:
        if name == 'snapshot_cache_residency.py':
            continue
        assert (source/'code'/name).read_bytes() == (ROOT/'scripts'/name).read_bytes(), name+' differs from capture'
    snapshot_class = captured_snapshot_class(source)
    dependency = 'measure_file_physical.py'
    assert (source.parents[2]/'code'/dependency).read_bytes() == (ROOT/'scripts'/dependency).read_bytes(), dependency+' differs from capture'
    samples = [json.loads(line) for line in (source/'samples.jsonl').read_text().splitlines()]
    state = json.loads((source/'result.json').read_text())
    assert state['status'] == 'PASS' and state['samples'] == len(samples) == 10
    inputs = []
    inventory = {}
    for sample in samples:
        prefix = source/f"pair-{sample['point']:04}"
        raw = {}
        for suffix in ('-before.bin', '-after.bin', '-host.bin', '-backend.bin', '-lower.bin', '-provider.json', '-maps.json'):
            path = Path(str(prefix)+suffix)
            raw[suffix] = path.read_bytes()
            st = path.stat(); inventory[path.name] = [st.st_ino, st.st_size, st.st_mtime_ns]
        inputs.append(raw)
    mapper = snapshot_class.__new__(snapshot_class)
    mapper.slots = json.loads((source/'ram-attestation.json').read_text())['slots']
    identity = json.loads((source/'snapshot-backend.json').read_text())['identity']
    mapper.identity = tuple(identity[k] for k in ('device', 'inode', 'rdev'))
    metadata = json.loads((source/'lower-metadata/metadata.json').read_text())
    layer = RawMemoryLayer.__new__(RawMemoryLayer)
    # Use the already preserved index directly; do not reopen or read the layer payload.
    layer.segments = RawMemoryLayer.decode_index((source/'lower-metadata/index.bin').read_bytes(), metadata['index_offset'])
    layer.starts = [s[0] for s in layer.segments]
    points = []
    for sample, raw in zip(samples, inputs, strict=True):
        result = replay(sample, raw, mapper, layer)
        _, physical = recount_point(source, sample)
        assert result.pop('physical') == physical, 'fast/scalar physical PFN sets differ'
        result['query_shapes'] = {name: query_shape(np.frombuffer(raw[suffix], dtype='<u8').reshape(-1, 4)[:, 1])
            for name, suffix in (('backend', '-backend.bin'), ('lower', '-lower.bin'))}
        points.append(result)
    for name, expected in inventory.items():
        st = (source/name).stat()
        assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected
    stages = {name: {key: statistics.mean(p['costs'][name][key] for p in points)
        for key in ('cpu_ns', 'elapsed_ns')} for name in points[0]['costs']}
    online = statistics.mean(p['online_point_cpu_ns'] for p in points)
    replayed = statistics.mean(p['replay_inside_point_cpu_ns'] for p in points)
    query = statistics.mean(p['online_provider_query_cpu_ns'] for p in points)
    outside = state['host_thread_total_cpu_ns']-sum(p['online_point_cpu_ns'] for p in points)
    assert outside >= 0
    summary = dict(status='PASS', source=str(source.relative_to(ROOT)), points=points,
        pass_count=1, stage_means=stages, replay_inside_point_stages=list(COUNTED_STAGES),
        online_point_cpu_mean_ns=online, replay_inside_point_cpu_mean_ns=replayed,
        online_provider_query_cpu_mean_ns=query,
        online_minus_replay_and_provider_mean_ns=online-replayed-query,
        online_thread_cpu_outside_point_total_ns=outside,
        online_thread_cpu_outside_point_mean_ns=outside/len(points),
        exact_metrics_and_pfn_sets_equal_points=len(points),
        captured_source_matches=[name for name in SOURCE_FILES if name != 'snapshot_cache_residency.py'],
        snapshot_offsets_implementation='captured source', backend_account_matches_capture=True,
        input_files_unchanged=len(inventory), captured_dependency_matches=[dependency],
        timing_scope=dict(online_point='From before guest publication request through accounting; before serialization and writes.',
            replay_inside_point='Saved-data pure operations only; file loading, offline JSON decoding, scalar validation and serialization excluded.',
            difference='Cross-run timing difference, not an exact attribution of the original online CPU.',
            serialization='In-memory encoding only, outside the original point timer; no output I/O timed.',
            unreplayed=['guest publication request/handshake and export memory reads', 'three VMM pagemap bulk reads and entry selection',
                'two live maps reads/parses', 'two direct backend and two lower mincore/BPF queries',
                'provider metadata queries except their recorded aggregate cost', 'loop/import/check overhead']),
        raw_inventory=inventory)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile(args.source, args.output)
