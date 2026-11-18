"""Decode non-faulting, named-file cache queries; keep v1 whole-guest data intact."""
import struct

from kernel_cache_metadata import MASK, PRESENT, SWAPPED, PHYSICAL_MASK

PAGE = 4096
HEADER = struct.Struct('<8s18Q')
RECORD = struct.Struct('<3Q')
FIELDS = ('version', 'page_size', 'records', 'start_ns', 'end_ns',
          'file_dev', 'file_ino', 'file_generation', 'size_before', 'size_after',
          'mapping_dev', 'mapping_ino', 'mapping_generation',
          'generation_before', 'generation_after', 'unknown_records',
          'mapping_pages_before', 'mapping_pages_after')
IDENTITY = ('file_dev', 'file_ino', 'file_generation', 'mapping_dev',
            'mapping_ino', 'mapping_generation')
MAX_PAGES = 8192  # One declared file, at most 32 MiB. Not whole-guest coverage.


def decode(raw):
    if len(raw) < HEADER.size:
        raise ValueError('short file-cache header')
    magic, *values = HEADER.unpack_from(raw)
    header = dict(zip(FIELDS, values))
    if magic != b'ASBFILE1' or header['version'] != 1 or header['page_size'] != PAGE:
        raise ValueError('unsupported file-cache format')
    if (header['records'] > MAX_PAGES or
            len(raw) != HEADER.size + header['records'] * RECORD.size or
            header['end_ns'] < header['start_ns'] or
            header['generation_after'] < header['generation_before'] or
            max(header['size_before'], header['size_after']) > MAX_PAGES * PAGE):
        raise ValueError('invalid file-cache header or length')
    rows = {}
    pfns = set()
    for index, pfn, tag in RECORD.iter_unpack(raw[HEADER.size:]):
        if index in rows or pfn in pfns or pfn >= 1 << 21 or tag >> 1 > header['generation_after']:
            raise ValueError('duplicate or invalid file-cache page')
        if index >= MAX_PAGES:
            raise ValueError('file page index exceeds query scope')
        rows[index] = (pfn, tag)
        pfns.add(pfn)
    if sum(tag == 0 for pfn, tag in rows.values()) != header['unknown_records']:
        raise ValueError('file-cache unknown count differs')
    return header, rows


def account(before, after, guest_before, guest_after, host_rows):
    """Stable named-file lifetimes joined to existing three-read host observations.

    Inputs are decoded file queries, decoded whole-guest metadata and host dicts.
    The caller must validate sandbox/boot/VMM identity and acquisition ordering.
    No original PFN list is used: a newly loaded PFN can qualify on the next pair.
    """
    left, first = before
    right, last = after
    if any(left[k] != right[k] for k in IDENTITY):
        raise ValueError('file or mapping identity changed')
    if len({left['size_before'], left['size_after'], right['size_before'], right['size_after']}) != 1:
        raise ValueError('file size changed during query pair')
    if left['end_ns'] > right['start_ns']:
        raise ValueError('file queries overlap or are reversed')
    host = {r['guest_pfn']: r for r in host_rows}
    if len(host) != len(host_rows):
        raise ValueError('duplicate host guest PFN')
    known = set()
    stable = nonresident = swapped = unknown = 0
    for index, (pfn, tag) in last.items():
        if (not tag or first.get(index) != (pfn, tag) or
                guest_before[1].get(pfn) != tag or guest_after[1].get(pfn) != tag or pfn not in host):
            unknown += 1
            continue
        row = host[pfn]
        a, b, c = (int(row[k]) for k in ('first', 'middle', 'last'))
        if ((a ^ b) | (b ^ c)) & PHYSICAL_MASK:
            unknown += 1
        elif a & PRESENT:
            if not a & MASK:
                raise ValueError('host PFN hidden')
            known.add(a & MASK)
            stable += 1
        elif a & SWAPPED:
            swapped += 1
        else:
            nonresident += 1
    complete_before = not left['unknown_records'] and left['records'] == left['mapping_pages_before'] == left['mapping_pages_after']
    complete_after = not right['unknown_records'] and right['records'] == right['mapping_pages_before'] == right['mapping_pages_after']
    return dict(status='PARTIAL' if unknown or not complete_before or not complete_after else 'OBSERVED',
        enumeration_consistent_before=complete_before, enumeration_consistent_after=complete_after,
        current_file_cache_before_bytes=len(first) * PAGE if complete_before else None,
        current_file_cache_after_bytes=len(last) * PAGE if complete_after else None,
        observed_candidate_before_bytes=len(first) * PAGE,
        observed_candidate_after_bytes=len(last) * PAGE,
        confirmed_host_union_bytes=len(known) * PAGE,
        stable_resident_guest_pages=stable, host_nonresident_pages=nonresident,
        host_swapped_pages=swapped, unconfirmed_after_pages=unknown,
        retired_or_changed_offsets=sum(last.get(i) != v for i, v in first.items()),
        added_or_changed_offsets=sum(first.get(i) != v for i, v in last.items()),
        scope='one declared file mapping; paired non-atomic observations, not all guest cache',
        snapshot_atomic=False,
        # Raw PFNs are local evidence for later cross-guest set union, not a sum.
        confirmed_host_pfns=sorted(known))
