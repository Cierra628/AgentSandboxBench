"""Explain changed saved segments using source offsets and request counters only."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import struct

from provider_slot_cache import PAGE, UNIT, UNKNOWN, MASK, PRESENT
from report_provider_segment_unknown import classify
from report_provider_tail import compare

CLASSES = ('source_change_observed', 'initial_fill_observed', 'same_source_one_read_proven',
    'same_source_history_unknown', 'incomplete_observation', 'incomplete_source_history',
    'ambiguous_alias_evidence')


def page_evidence(first, last, start):
    """Prove request/source history, not byte equality or cache admission.

    Requires the fixed single-writer GET_DATA protocol and no counter wrap.
    Each within-observation entry pair must agree before its source is used.
    """
    transition = compare(first, last)
    tables = [r['segments'][k][start:start + PAGE // UNIT]
        for r in (first, last) for k in ('before', 'after')]
    assert all(len(t) == PAGE // UNIT for t in tables)
    result = dict(counter_evidence=transition)
    if any(a != b or c != d or a[0] % 2 or c[0] % 2
            for a, b, c, d in zip(*tables, strict=True)):
        return dict(result, classification='incomplete_observation')
    before, after = tables[0], tables[2]
    changed = sum(a[1] != UNKNOWN and b[1] != UNKNOWN and a[1] != b[1]
        for a, b in zip(before, after, strict=True))
    initial = sum(tuple(a) == (0, UNKNOWN) and b[1] != UNKNOWN
        for a, b in zip(before, after, strict=True))
    result.update(source_changed_segments=changed, initially_unknown_filled_segments=initial)
    if changed:
        return dict(result, classification='source_change_observed')
    if initial:
        return dict(result, classification='initial_fill_observed')
    if any(a[1] == UNKNOWN or b[1] == UNKNOWN for a, b in zip(before, after, strict=True)):
        return dict(result, classification='incomplete_source_history')
    # Equal final sources can hide A -> B -> A. Only exactly one completed
    # read with matching prefix generations and offsets identifies a refill.
    if transition['kind'] == 'one_read':
        header = last['before']
        refilled = False
        for index, (a, b) in enumerate(zip(before, after, strict=True), start):
            covered = index * UNIT < header[5]
            refilled |= covered
            if b[0] != a[0] + (4 if covered else 0):
                break
            if covered and b[1] != header[4] + index * UNIT:
                break
        else:
            if refilled:
                return dict(result, classification='same_source_one_read_proven')
    return dict(result, classification='same_source_history_unknown')


def analyze_point(args):
    details = {}
    original = classify(*args, details=details)
    target = {p for p, reasons in details['reasons'].items() if reasons == {'segment_record_changed'}}
    evidence = defaultdict(list)
    for a, b in zip(args[-2]['rows'], args[-1]['rows'], strict=True):
        address, capacity = a['before'][2:4]
        headers = [r[k] for r in (a, b) for k in ('before', 'after')]
        if any(h[6] == 5 or h[2:4] != [address, capacity] for h in headers):
            continue
        for index, (entry, final) in enumerate(zip(a['pagemap'], b['pagemap'], strict=True)):
            pfn = entry & MASK
            virtual = a['first_address'] + index * PAGE
            if entry != final or not entry & PRESENT or pfn not in target or virtual < address or virtual + PAGE > address + capacity:
                continue
            start = (virtual - address) // UNIT
            result = page_evidence(a, b, start)
            evidence[pfn].append(dict(slot=a['name'], buffer_range=[start * UNIT, start * UNIT + PAGE], **result))
    counts = Counter({k: 0 for k in CLASSES})
    counters = Counter()
    private = []
    for pfn in sorted(target):
        aliases = evidence[pfn]
        assert aliases
        kinds = {r['classification'] for r in aliases}
        kind = next(iter(kinds)) if len(kinds) == 1 else 'ambiguous_alias_evidence'
        counts[kind] += 1
        history = {r['counter_evidence']['kind'] for r in aliases}
        counters[next(iter(history)) if len(history) == 1 else 'mixed_alias_counters'] += 1
        private.append(dict(pfn=pfn, classification=kind, observations=aliases))
    assert sum(counts.values()) == len(target) == original['unknown_by_reason_pages']['segment_record_changed']
    return dict(target_pages=len(target), classification_pages=dict(counts),
        counter_evidence_pages=dict(sorted(counters.items())),
        original_unattributed_pages=original['unattributed_pages']), private


def report(source, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    identity = json.loads((source / 'provider/identity.json').read_text())
    assert identity['slots'] and all(s['negotiated_flags'] & 4 and s['content_validity'] == 'before_overwrite'
        and s['segments']['schema'] == 1 for s in identity['slots'])
    points, traces = [], []
    row = struct.Struct('<4Q')
    for saved in map(json.loads, (source / 'samples.jsonl').read_text().splitlines()):
        prefix = source / f"pair-{saved['point']:04}"
        read = lambda suffix: Path(str(prefix) + suffix).read_bytes()
        raw = json.loads(read('-provider.json'))
        args = (read('-before.bin'), read('-after.bin'), *(list(row.iter_unpack(read(s))) for s in
            ('-host.bin', '-backend.bin', '-lower.bin')), raw['first'], raw['last'])
        result, trace = analyze_point(args)
        assert result['original_unattributed_pages'] * PAGE == saved['provider_segment_unattributed_resident_bytes']
        points.append(dict(point=saved['point'], **result))
        traces.append(dict(point=saved['point'], pages=trace))
    summary = dict(status='PASS', page_size=PAGE, point_count=len(points), points=points,
        scope='Changed whole buffer PFNs still unattributed by existing admission; per-point source history only.',
        interpretation='Different source offsets do not prove different byte values; equal endpoints do not prove an unchanged intermediate history.',
        current_physical_union='UNCHANGED', admission_rules='UNCHANGED', live_actions=False)
    output.mkdir(parents=True)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'private-details.json').write_text(json.dumps(traces, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.source, args.output)
    print(f"status=PASS points={result['point_count']} result_dir={args.output}")
