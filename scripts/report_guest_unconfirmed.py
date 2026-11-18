"""Partition saved after-sample guest identities, including provider coverage.

Reads saved metadata only. Does not invoke old report CLI hashing, physical
re-audits, payload reads, live queries, services or performance measurements.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import struct

import numpy as np

from kernel_cache_metadata import decode
from provider_slot_cache import account_provider, full_coverage, provider_context
from report_cache_unknown import CATEGORIES, classify
from snapshot_cache_residency import SnapshotCache

ROOT = Path(__file__).resolve().parents[1]
PAGE = 4096
ROW = struct.Struct('<4Q')
FIELDS = ('confirmed', 'provider_confirmed', *CATEGORIES)
GUARDED = ('metadata_stale', 'metadata_before_stale', 'added_or_reused',
           'vmm_pte_changed', 'file_pfn_conflict', 'swapped')


def partition_reasons(reasons, provider_pages):
    """Provider proof may complete stable source uncertainty, not bypass guards."""
    assert set(reasons.values()) <= set(('confirmed', *CATEGORIES))
    provider_pages = set(provider_pages)
    assert provider_pages <= reasons.keys()
    before = {p for p, reason in reasons.items() if reason == 'confirmed'}
    rescued = provider_pages-before
    assert all(reasons[p] not in GUARDED for p in rescued)
    result = dict(reasons)
    for p in rescued:
        result[p] = 'provider_confirmed'
    totals = Counter(result.values())
    counts = {key: totals[key] for key in FIELDS}
    assert sum(counts.values()) == len(reasons)
    return counts, result, rescued


def classify_point(before, after, host, direct, lower, offset_after, first, last):
    _, left = decode(before); header, right = decode(after)
    wanted = {p for p, tag in left.items() if tag}
    for rows in (host, direct, lower):
        assert len(rows) == len(wanted) and {r[0] for r in rows} == wanted
    reasons = {}
    baseline = classify(left, right, host, direct, offset_after, lower, details=reasons)
    context = provider_context(before, after, host, direct, lower)
    assert context[2] == {p for p, reason in reasons.items() if reason == 'confirmed'}
    details = []
    for segmented in (False, True):
        detail = {}
        account_provider(before, after, host, direct, lower, first, last,
                         segmented=segmented, details=detail, _context=context)
        details.append(detail)
    coverage = {}
    for detail in details:
        for p, spans in detail['coverage'].items():
            coverage.setdefault(p, []).extend(spans)
    provider_pages = full_coverage(coverage)
    counts, final, rescued = partition_reasons(reasons, provider_pages)
    assert counts['confirmed'] == baseline['confirmed']
    unknown = {p for p, reason in final.items() if reason in CATEGORIES}
    assert len(unknown) == sum(counts[k] for k in CATEGORIES)
    assert len(unknown)+counts['confirmed']+len(rescued) == header['records']
    subtypes = Counter()
    for p, reason in final.items():
        if reason == 'added_or_reused':
            subtypes['absent_before' if p not in left else 'tag_changed'] += 1
    return dict(guest_pages=header['records'], counts=counts,
        unconfirmed_guest_capacity_bytes=len(unknown)*PAGE,
        three_layer_unconfirmed_guest_capacity_bytes=(header['records']-baseline['confirmed'])*PAGE,
        added_or_reused_subtypes={k: subtypes[k] for k in ('absent_before', 'tag_changed')},
        provider_rescued_from=dict(Counter(reasons[p] for p in rescued))), final, rescued


def report(source, output):
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
    mapper = SnapshotCache.__new__(SnapshotCache)
    mapper.slots = json.loads(read('ram-attestation.json'))['slots']
    identity = json.loads(read('snapshot-backend.json'))['identity']
    mapper.identity = tuple(identity[k] for k in ('device', 'inode', 'rdev'))
    points, local = [], []
    for saved in samples:
        prefix = f"pair-{saved['point']:04}"
        before, after = read(prefix+'-before.bin'), read(prefix+'-after.bin')
        host, direct, lower = [list(ROW.iter_unpack(read(prefix+suffix)))
                              for suffix in ('-host.bin', '-backend.bin', '-lower.bin')]
        mapper.regions = json.loads(read(prefix+'-maps.json'))['after']
        pfns = np.array([r[0] for r in direct], dtype='<u8')
        offset_after = dict(zip(map(int, pfns), map(int, mapper.offsets(pfns))))
        provider = json.loads(read(prefix+'-provider.json'))
        point, reasons, rescued = classify_point(before, after, host, direct, lower, offset_after,
                                                provider['first'], provider['last'])
        assert point['unconfirmed_guest_capacity_bytes'] == saved['host_four_segment_unconfirmed_guest_capacity_bytes']
        assert point['three_layer_unconfirmed_guest_capacity_bytes'] == saved['host_three_unconfirmed_guest_capacity_bytes']
        assert point['counts']['provider_confirmed'] == saved['provider_segment_resolved_guest_pages']
        assert sum(point['added_or_reused_subtypes'].values()) == point['counts']['added_or_reused']
        points.append(dict(point=saved['point'], **point))
        # Guest page identities stay in the local artifact, not public evidence.
        local.append(dict(point=saved['point'], unknown={p: r for p, r in reasons.items() if r in CATEGORIES},
                          provider_confirmed=sorted(rescued)))
    for relative, expected in inventory.items():
        st = (source/relative).stat(); assert [st.st_ino, st.st_size, st.st_mtime_ns] == expected
    value = dict(status='PASS', source=str(source.relative_to(ROOT)), page_size=PAGE,
        category_order=list(FIELDS), points=points, input_files_unchanged=len(inventory), input_inventory=inventory,
        interpretation='Disjoint after-sample guest identities in existing classifier priority order; counts times 4096 are guest capacity, not unknown host physical bytes.',
        provider_scope='Union of complete current-content byte coverage from legacy slots and segments; no double counting. Existing physical union evidence reused, not recomputed as a new measurement.',
        unavailable='Saved zero source results do not distinguish nonresident content from unavailable/unconfirmed PFNs.',
        runtime_changed=False, live_operations=False, payload_reads=False, performance_measurements=0)
    output.mkdir(parents=True)
    (output/'summary.json').write_text(json.dumps(value, indent=2)+'\n')
    (output/'guest-identities.local.json').write_text(json.dumps(local, indent=2)+'\n')
    with (output/'points.csv').open('w') as stream:
        writer = csv.DictWriter(stream, fieldnames=['point', 'guest_pages', *FIELDS, 'unconfirmed_guest_capacity_bytes'])
        writer.writeheader()
        writer.writerows(dict(point=p['point'], guest_pages=p['guest_pages'], **p['counts'],
                              unconfirmed_guest_capacity_bytes=p['unconfirmed_guest_capacity_bytes']) for p in points)
    print('status=PASS points='+str(len(points))+' result_dir='+str(output))
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report(args.source, args.output)
