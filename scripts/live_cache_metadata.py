"""One-page guest cache metadata and offline host-PFN accounting for a declared file."""
import struct

PAGE = 4096
MAGIC = b'ASBLIVE1'
HEADER = struct.Struct('<8sQQQIIQQQ')
SIZE = 1024 * 1024
PHASES = ('empty', 'half', 'full', 'reread', 'released', 'complete')
PFN_MASK = (1 << 55) - 1
PRESENT = 1 << 63
SWAPPED = 1 << 62
PHYSICAL_MASK = PFN_MASK | PRESENT | SWAPPED | (1 << 61)


def decode(raw):
    if len(raw) != PAGE:
        raise ValueError('metadata must be one base page')
    magic, seq, tick, elapsed, phase, count, cached, nonfree, guest_time = HEADER.unpack_from(raw)
    if magic != MAGIC or seq & 1:
        raise ValueError('metadata is not a completed publication')
    if phase >= len(PHASES) or count != SIZE // PAGE:
        raise ValueError('unsupported metadata layout')
    pfns = list(struct.unpack_from('<' + 'I' * count, raw, HEADER.size))
    return dict(sequence=seq, heartbeat=tick, guest_elapsed_ns=elapsed, phase=PHASES[phase],
                guest_cached_bytes=cached * 1024, guest_nonfree_bytes=nonfree * 1024,
                guest_timestamp_ns=guest_time, file_pfns=pfns)


def summarize(snapshot, rows):
    selected = {pfn for pfn in snapshot['file_pfns'] if pfn}
    if len(rows) != len(selected) or {row['guest_pfn'] for row in rows} != selected:
        raise ValueError('host rows do not cover declared guest PFNs exactly once')
    known, unknown = set(), 0
    nonresident = swapped = 0
    for row in rows:
        first, last = row['first'], row['last']
        if (first ^ last) & PHYSICAL_MASK:
            unknown += 1
        elif first & PRESENT:
            if not first & PFN_MASK:
                raise ValueError('resident host PFN is hidden')
            known.add(first & PFN_MASK)
        elif first & SWAPPED:
            swapped += 1
        else:
            nonresident += 1
    return dict(declared_file_guest_cache_bytes=sum(bool(pfn) for pfn in snapshot['file_pfns']) * PAGE,
                guest_unique_file_pages=len(selected), host_confirmed_cache_bytes=len(known) * PAGE,
                host_unique_resident_pages=len(known), host_mapping_changed_pages=unknown,
                host_nonresident_pages=nonresident, host_swapped_pages=swapped)
