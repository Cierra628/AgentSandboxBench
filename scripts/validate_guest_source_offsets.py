"""Compare new raw-layer offsets with a saved capture, without live queries."""
import argparse
import json
from pathlib import Path

import numpy as np

from review_guest_source_index import explain_page
from audit_lower_cache import captured_layer_offsets
from snapshot_layer_metadata import RawMemoryLayer, SEGMENT, UNKNOWN

ROOT = Path(__file__).resolve().parents[1]


def validate(source, candidates, output):
    source, candidates, output = source.resolve(), candidates.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory = {}
    def read(path):
        st = path.stat()
        inventory[str(path.relative_to(ROOT))] = [st.st_ino, st.st_size, st.st_mtime_ns]
        return path.read_bytes()
    previous = json.loads(read(candidates))
    expected = {(q['point'], q['guest_pfn']): q for q in previous}
    assert len(expected) == 12
    meta = json.loads(read(source/'lower-metadata/metadata.json'))
    index = read(source/'lower-metadata/index.bin')
    assert len(index) == meta['index_count']*SEGMENT.size
    layer = RawMemoryLayer.__new__(RawMemoryLayer)
    layer.segments = RawMemoryLayer.decode_index(index, meta['index_offset'])
    layer.starts = [s[0] for s in layer.segments]
    layer.tags = [high>>56 for low, high in SEGMENT.iter_unpack(index) if low>>50]
    assert len(layer.tags) == len(layer.segments)
    assert all(a[1] <= b[0] for a, b in zip(layer.segments, layer.segments[1:]))
    legacy = RawMemoryLayer.__new__(RawMemoryLayer)
    legacy.segments, legacy.starts = layer.segments, layer.starts
    baseline = captured_layer_offsets(read(source/'code/snapshot_layer_metadata.py'))
    points, changed_local, seen = [], [], set()
    for point in range(10):
        prefix = source/f'pair-{point:04}'
        direct, lower = [np.frombuffer(read(Path(str(prefix)+suffix)), dtype='<u8').reshape(-1, 4)
                         for suffix in ('-backend.bin', '-lower.bin')]
        assert np.array_equal(direct[:, 0], lower[:, 0])
        old = baseline(layer, direct[:, 1])
        assert np.array_equal(old, lower[:, 1]), 'captured mapper differs from saved offsets'
        new = layer.offsets(direct[:, 1])
        assert new.dtype == old.dtype == np.dtype('<u8') and new.shape == old.shape
        assert np.array_equal(new[old != UNKNOWN], old[old != UNKNOWN])
        assert np.array_equal(legacy.offsets(direct[:, 1]), old), 'legacy no-tag contract changed'
        changes = np.flatnonzero(new != old)
        assert np.all(old[changes] == UNKNOWN) and np.all(new[changes] != UNKNOWN)
        original_requests = set(map(int, old[old != UNKNOWN]))
        target_count = 0
        for row in changes:
            guest, device, candidate = int(direct[row, 0]), int(direct[row, 1]), int(new[row])
            explanation = explain_page(layer.segments, device)
            assert explanation['reason'] == 'cross_segment_contiguous'
            assert explanation['candidate_file_page'] == candidate
            assert all(layer.tags[p['segment_index']] == 0 for p in explanation['pieces'])
            key = (point, guest)
            if key in expected:
                assert device == expected[key]['device_page']
                assert candidate == expected[key]['candidate_file_page']
                seen.add(key); target_count += 1
            changed_local.append(dict(point=point, guest_pfn=guest, device_page=device,
                                      candidate_file_page=candidate,
                                      in_original_requests=candidate in original_requests))
        points.append(dict(point=point, rows=len(old), old_known=int(np.count_nonzero(old != UNKNOWN)),
                           old_unknown=int(np.count_nonzero(old == UNKNOWN)),
                           new_known=int(np.count_nonzero(new != UNKNOWN)),
                           new_unknown=int(np.count_nonzero(new == UNKNOWN)),
                           newly_mapped=len(changes), target_candidates_matched=target_count,
                           old_valid_unchanged=True, legacy_no_tags_unchanged=True))
    assert seen == set(expected)
    for path, expected_stat in inventory.items():
        st = (ROOT/path).stat()
        assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected_stat
    value = dict(status='PASS', source=str(source.relative_to(ROOT)), points=points,
                 page_bytes=4096, points_compared=10,
                 total_rows=sum(p['rows'] for p in points),
                 old_valid_rows_preserved=sum(p['old_known'] for p in points),
                 newly_mapped_rows=len(changed_local), target_candidates_matched=len(seen),
                 distinct_new_file_pages=len({q['candidate_file_page'] for q in changed_local}),
                 newly_mapped_rows_not_in_original_requests=sum(not q['in_original_requests'] for q in changed_local),
                 input_files_unchanged=len(inventory), input_inventory=inventory,
                 offset_meaning='raw file page offsets, not resident physical page numbers',
                 physical_metrics_changed=False, live_operations=False, payload_reads=False,
                 performance_measurements=0)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    (output/'new-offsets.local.json').write_text(json.dumps(changed_local, indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--candidates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); validate(args.source, args.candidates, args.output)
