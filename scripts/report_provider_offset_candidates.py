"""Reverse saved buffer sources into guest cache membership; metadata only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct

from kernel_cache_metadata import decode
from measure_file_physical import validate_slots
from provider_slot_cache import PAGE
from report_provider_segment_unknown import classify

MISSING = 'offset_not_in_initial_candidates'
CLASSES = ('all_sources_absent_both', 'mixed_absent_and_stable_cache',
    'mixed_absent_and_new_cache', 'other_with_absent_source', 'unresolved_no_absent_proof')


def reverse_page(offset, regions, slots, identity):
    """Use explicit file VMAs only; never infer offsets through anonymous holes."""
    found = set()
    for region in regions:
        if not region.get('path', '').startswith('/dev/ublkb'):
            continue
        if (region['device'], region['inode']) != (identity['device'], identity['inode']):
            continue
        if region['permissions'] != 'rw-p':
            continue
        start = offset * PAGE
        if start < region['offset'] or start + PAGE > region['offset'] + region['end'] - region['start']:
            continue
        address = region['start'] + start - region['offset']
        for slot in slots:
            if slot['host'] <= address and address + PAGE <= slot['host'] + slot['size']:
                guest = slot['guest'] + address - slot['host']
                assert guest % PAGE == 0
                found.add(guest // PAGE)
    return found


def membership(offset, maps, slots, identity, left, right):
    first = reverse_page(offset, maps['before'], slots, identity)
    last = reverse_page(offset, maps['after'], slots, identity)
    if len(first) != 1 or first != last:
        return 'mapping_unresolved', None
    guest = next(iter(first))
    before, after = left.get(guest), right.get(guest)
    if before == 0 or after == 0:
        return 'membership_unknown', guest
    if before is None and after is None:
        return 'absent_both', guest
    if before is None:
        return 'entered_cache', guest
    if after is None:
        return 'left_cache', guest
    return ('stable_cache' if before == after else 'cache_identity_changed'), guest


def classify_sources(details, maps, slots, identity, left, right):
    validate_slots(slots)
    target = {p for p, kinds in details['reasons'].items() if kinds == {MISSING}}
    all_offsets = {o for p in target for o in details['source_pages'][p]}
    labels = {o: membership(o, maps, slots, identity, left, right) for o in all_offsets}
    missing = all_offsets - details['candidate_offsets']
    counts = Counter({k: 0 for k in CLASSES})
    pages = []
    for pfn in sorted(target):
        offsets = details['source_pages'][pfn]
        kinds = {labels[o][0] for o in offsets}
        if kinds == {'absent_both'}:
            kind = 'all_sources_absent_both'
        elif kinds == {'absent_both', 'stable_cache'}:
            kind = 'mixed_absent_and_stable_cache'
        elif 'absent_both' in kinds and 'entered_cache' in kinds and kinds <= {'absent_both', 'entered_cache', 'stable_cache'}:
            kind = 'mixed_absent_and_new_cache'
        else:
            kind = 'other_with_absent_source' if 'absent_both' in kinds else 'unresolved_no_absent_proof'
        counts[kind] += 1
        pages.append(dict(pfn=pfn, classification=kind, source_pages=sorted(offsets)))
    entered = {labels[o][1] for o in missing if labels[o][0] == 'entered_cache'}
    summary = dict(missing_candidate_physical_pages=len(target), by_source_membership_pages=dict(counts),
        missing_source_offset_pages=len(missing),
        missing_offset_membership=dict(sorted(Counter(labels[o][0] for o in missing).items())),
        newly_entered_guest_pages=len(entered),
        pages_with_absent_source=sum(counts.values()) - counts['unresolved_no_absent_proof'],
        unresolved_without_absent_source_pages=counts['unresolved_no_absent_proof'])
    assert sum(counts.values()) == len(target)
    return summary, dict(pages=pages, offsets=[dict(source_page=o, membership=labels[o][0],
        guest_pfn=labels[o][1], missing_initial_candidate=o in missing) for o in sorted(all_offsets)])


def report(source, prior_unknown, initial, output):
    source, output = source.resolve(), output.resolve()
    assert not output.exists() and not output.is_relative_to(source) and not source.is_relative_to(output)
    slots = json.loads((source / 'ram-attestation.json').read_text())['slots']
    identity = json.loads((source / 'snapshot-backend.json').read_text())['identity']
    prior = {p['point']: p for p in json.loads(prior_unknown.read_text())['points']}
    initialization = {p['point']: p for p in json.loads(initial.read_text())['points']}
    points, private = [], []
    rows = struct.Struct('<4Q')
    for saved in map(json.loads, (source / 'samples.jsonl').read_text().splitlines()):
        point = saved['point']
        prefix = source / f'pair-{point:04}'
        read = lambda suffix: Path(str(prefix) + suffix).read_bytes()
        before, after = read('-before.bin'), read('-after.bin')
        raw = json.loads(read('-provider.json'))
        details = {}
        original = classify(before, after, *(list(rows.iter_unpack(read(s))) for s in
            ('-host.bin', '-backend.bin', '-lower.bin')), raw['first'], raw['last'], details=details)
        assert original == {k: v for k, v in prior[point].items() if k != 'point'}
        result, trace = classify_sources(details, json.loads(read('-maps.json')), slots, identity,
            decode(before)[1], decode(after)[1])
        assert result['missing_candidate_physical_pages'] == original['unknown_by_reason_pages'][MISSING]
        assert original['unattributed_pages'] * PAGE == saved['provider_segment_unattributed_resident_bytes']
        explained = result['pages_with_absent_source']
        remaining = original['unattributed_pages'] - initialization[point]['whole_initial_pages'] - explained
        points.append(dict(point=point, **result, remaining_unexplained_pages=remaining))
        private.append(dict(point=point, **trace))
    summary = dict(status='PASS', page_size=PAGE, point_count=len(points), points=points,
        physical_union='UNCHANGED', old_metrics='UNCHANGED',
        scope='Stable missing-candidate whole buffer PFNs; membership at two sampled endpoints, not a continuous history.',
        online_collection='UNCHANGED', new_cache_admission='NONE; diagnostics only')
    output.mkdir(parents=True)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (output / 'private-details.json').write_text(json.dumps(private, indent=2) + '\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--prior-unknown', type=Path, required=True)
    parser.add_argument('--initial', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.source, args.prior_unknown, args.initial, args.output)
    print(f"status=PASS points={result['point_count']} result_dir={args.output}")
