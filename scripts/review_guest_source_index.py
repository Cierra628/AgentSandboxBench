"""Explain saved raw-layer offsets for stable unknown guest pages, offline only."""
import argparse
from bisect import bisect_right
from collections import Counter
import json
from pathlib import Path
import struct

import numpy as np

from snapshot_layer_metadata import RawMemoryLayer, HEADER, MAGIC, MAGIC1, UNKNOWN

ROOT = Path(__file__).resolve().parents[1]
PAGE = 4096
SECTOR = 512
ROWS = struct.Struct('<4Q')
DISK_ROWS = struct.Struct('<2Q')


def explain_page(segments, page):
    """Check complete logical/file continuity; do not change the live mapper."""
    pieces = []
    def result(reason, candidate=UNKNOWN):
        return dict(reason=reason, candidate_file_page=candidate, pieces=pieces)
    if page == UNKNOWN:
        return result('unknown_input')
    begin = int(page)*8; end = begin+8; cursor = begin
    starts = [s[0] for s in segments]
    physical_begin = None
    while cursor < end:
        i = bisect_right(starts, cursor)-1
        if i < 0 or cursor >= segments[i][1]:
            return result('index_gap')
        start, stop, physical, zeroed = segments[i]
        length = min(end, stop)-cursor
        pieces.append(dict(segment_index=i, page_sector_offset=cursor-begin,
                           sectors=length, segment_sectors=stop-start, zeroed=zeroed))
        if zeroed:
            return result('zero_segment')
        position = physical+cursor-start
        if physical_begin is None:
            physical_begin = position
        if position != physical_begin+cursor-begin:
            return result('file_discontinuity')
        cursor += length
    if physical_begin % 8:
        return result('unaligned_file_page')
    return result('cross_segment_contiguous' if len(pieces)>1 else 'single_segment', physical_begin//8)


def review(source, partition, output):
    source, partition, output = source.resolve(), partition.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    inventory = {}
    def read(path):
        st = path.stat(); inventory[str(path.relative_to(ROOT))] = [st.st_ino, st.st_size, st.st_mtime_ns]
        return path.read_bytes()
    prior = json.loads(read(partition.parent/'summary.json'))
    assert prior['status'] == 'PASS' and prior['source'] == str(source.relative_to(ROOT))
    local = json.loads(read(partition))
    directory = source/'lower-metadata'
    meta = json.loads(read(directory/'metadata.json'))
    head, tail = [HEADER.unpack_from(read(directory/name)) for name in ('header.bin', 'trailer.bin')]
    assert head[:2] == tail[:2] == (MAGIC, MAGIC1)
    assert head[3]&1 and not tail[3]&1 and tail[3]&6 == 6
    assert tail[4:] == (meta['index_offset'], meta['index_count'], meta['virtual_size'])
    index = read(directory/'index.bin')
    assert len(index) == meta['index_count']*DISK_ROWS.size
    segments = RawMemoryLayer.decode_index(index, meta['index_offset'])
    wire = [(low, high) for low, high in DISK_ROWS.iter_unpack(index) if low>>50]
    assert len(wire) == len(segments)
    assert all(a[1] <= b[0] for a,b in zip(segments, segments[1:]))
    layer = RawMemoryLayer.__new__(RawMemoryLayer)
    layer.segments, layer.starts = segments, [s[0] for s in segments]
    points, details, identities, candidates = [], [], set(), set()
    for p in local:
        guests = sorted(int(g) for g, reason in p['unknown'].items() if reason == 'backend_without_pfn')
        assert len(guests) == prior['points'][p['point']]['counts']['backend_without_pfn']
        if not guests:
            continue
        prefix = source/f"pair-{p['point']:04}"
        direct, lower = [{r[0]:r[1:] for r in ROWS.iter_unpack(read(Path(str(prefix)+suffix)))}
                         for suffix in ('-backend.bin', '-lower.bin')]
        requested = {r[0] for r in lower.values() if r[0] != UNKNOWN}
        observations = []
        for ordinal, guest in enumerate(guests):
            page = direct[guest][0]; identities.add(guest)
            assert page != UNKNOWN and page*PAGE < meta['virtual_size']
            explanation = explain_page(segments, page)
            original = int(layer.offsets(np.array([page], dtype='<u8'))[0])
            assert original == lower[guest][0] == UNKNOWN
            candidate = explanation['candidate_file_page']
            if candidate != UNKNOWN:
                candidates.add(candidate)
            pieces = explanation['pieces']
            observations.append(dict(page_ordinal=ordinal, reason=explanation['reason'],
                original_offset_unknown=True, candidate_offset_known=candidate != UNKNOWN,
                candidate_in_original_requests=candidate in requested,
                piece_sectors=[q['sectors'] for q in pieces],
                index_segment_sectors=[q['segment_sectors'] for q in pieces],
                zero_flags=[q['zeroed'] for q in pieces],
                layer_tags=[wire[q['segment_index']][1]>>56 for q in pieces]))
            details.append(dict(point=p['point'], guest_pfn=guest, device_page=page,
                old_file_page=original, **explanation,
                wire_rows=[dict(segment_index=q['segment_index'], low=wire[q['segment_index']][0],
                                high=wire[q['segment_index']][1]) for q in pieces]))
        points.append(dict(point=p['point'], observations=observations))
    for relative, expected in inventory.items():
        st = (ROOT/relative).stat(); assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected
    reasons = Counter(q['reason'] for p in points for q in p['observations'])
    value = dict(status='PASS', source=str(source.relative_to(ROOT)), points=points,
        distinct_guest_pages=len(identities), distinct_candidate_file_pages=len(candidates),
        observations=sum(len(p['observations']) for p in points), reason_counts=dict(reasons),
        index_rows=meta['index_count'], nonempty_segments=len(segments), empty_rows=meta['index_count']-len(segments),
        page_bytes=PAGE, sector_bytes=SECTOR, format_max_segment_sectors=(1<<14)-1,
        input_files_unchanged=len(inventory), input_inventory=inventory,
        interpretation='Candidate raw-file offsets derived from saved index fields, not resident PFNs or a new physical measurement.',
        runtime_changed=False, live_operations=False, payload_reads=False, performance_measurements=0)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    (output/'index-rows.local.json').write_text(json.dumps(details, indent=2)+'\n')
    print('status=PASS result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--partition', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); review(args.source, args.partition, args.output)
