"""Classify saved untouched buffer pages; reads metadata only, never payloads."""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from provider_slot_cache import MAGIC, MASK, PAGE, PRESENT, UNIT, UNKNOWN


def initial_pages(first, last):
    """Return whole resident PFN classes, subject to the fixed source ordering.

    Zero generation means no request has covered that sector, not zero bytes.
    Any conflicting, boundary, or unstable observed alias blocks admission.
    """
    assert [r['name'] for r in first['rows']] == [r['name'] for r in last['rows']]
    kinds = defaultdict(set)
    resident, boundary, blocked = set(), set(), set()
    for a, b in zip(first['rows'], last['rows'], strict=True):
        headers = [r[k] for r in (a, b) for k in ('before', 'after')]
        address, capacity = headers[0][2:4]
        assert all(h[0] == MAGIC for h in headers)
        assert address % UNIT == capacity % UNIT == 0
        assert a['first_address'] == b['first_address'] == address // PAGE * PAGE
        copies = [r['segments'][k] for r in (a, b) for k in ('before', 'after')]
        assert all(r['segments']['schema'] == 1 and r['segments']['unit_bytes'] == UNIT for r in (a, b))
        assert all(len(c) == capacity // UNIT for c in copies)
        allocation = all(h[2:4] == headers[0][2:4] and h[6] != 5 for h in headers)
        for index, (x, y) in enumerate(zip(a['pagemap'], b['pagemap'], strict=True)):
            aliases = {v & MASK for v in (x, y) if v & PRESENT and v & MASK}
            if x != y or not aliases or any(h[6] == 5 for h in headers):
                blocked.update(aliases)
                continue
            pfn = x & MASK
            virtual = a['first_address'] + index * PAGE
            if virtual < address or virtual + PAGE > address + capacity:
                boundary.add(pfn)
                blocked.add(pfn)
                continue
            resident.add(pfn)
            begin = (virtual - address) // UNIT
            records = list(zip(*(c[begin:begin + PAGE // UNIT] for c in copies), strict=True))
            stable = allocation and len(records) == PAGE // UNIT and all(
                v[0][0] % 2 == 0 and all(tuple(r) == tuple(v[0]) for r in v) for v in records)
            initial = sum(tuple(v[0]) == (0, UNKNOWN) for v in records)
            kind = ('whole_initial' if initial == PAGE // UNIT else 'partial_initial' if initial else 'other') if stable else 'other'
            if kind == 'whole_initial':
                # A valid legacy read covering this page would contradict the
                # generation-zero invariant; fail rather than reclassify it.
                legacy = all(h == headers[0] for h in headers) and headers[0][1] % 2 == 0
                assert not (legacy and headers[0][6] == 3 and virtual + PAGE <= address + headers[0][5])
            kinds[pfn].add(kind)
    whole = {p for p, k in kinds.items() if k == {'whole_initial'} and p not in blocked}
    partial = {p for p, k in kinds.items() if k == {'partial_initial'} and p not in blocked}
    return dict(resident=resident, boundary=boundary, whole_initial=whole, partial_initial=partial,
        other=resident - whole - partial)


def report(source, prior_unknown, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    prior = {p['point']: p for p in json.loads(prior_unknown.read_text())['points']}
    points = []
    for saved in map(json.loads, (source / 'samples.jsonl').read_text().splitlines()):
        point = saved['point']
        raw = json.loads((source / f'pair-{point:04}-provider.json').read_text())
        sets = initial_pages(raw['first'], raw['last'])
        counts = {k + '_pages': len(v) for k, v in sets.items()}
        assert counts['resident_pages'] * PAGE == saved['provider_whole_allocation_resident_bytes']
        assert counts['boundary_pages'] == prior[point]['boundary_pages']
        whole, partial = counts['whole_initial_pages'], counts['partial_initial_pages']
        assert whole + partial == prior[point]['unknown_by_reason_pages']['segment_never_filled']
        unknown = prior[point]['unattributed_pages']
        assert unknown * PAGE == saved['provider_segment_unattributed_resident_bytes']
        points.append(dict(point=point, **counts, original_unknown_pages=unknown,
            remaining_unknown_after_allocation_class_pages=unknown - whole))
    summary = dict(status='PASS', page_size=PAGE, point_count=len(points), points=points,
        meaning='Whole untouched pages: allocation/registration residency, not proven zero initialization.',
        physical_union='UNCHANGED', original_residency='UNCHANGED',
        scope='Fixed non-pooled GET_DATA source; four matching segment copies; all observed aliases checked.')
    output.mkdir(parents=True)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--prior-unknown', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.source, args.prior_unknown, args.output)
    print(f"status=PASS points={result['point_count']} result_dir={args.output}")
