"""Array accounting for live sampling; scalar kernel_cache_metadata is the oracle."""
import numpy as np

from kernel_cache_metadata import HEADER, decode_header, PHYSICAL_MASK, PRESENT, SWAPPED, MASK


def decode_array(raw):
    info = decode_header(raw)
    rows = np.frombuffer(raw, dtype='<u8', offset=HEADER.size).reshape(-1, 2)
    if len(rows) > 1 and np.any(rows[1:, 0] <= rows[:-1, 0]):
        rows = rows[np.argsort(rows[:, 0])]
        if np.any(rows[1:, 0] == rows[:-1, 0]):
            raise ValueError('duplicate guest PFN')
    if np.any(rows[:, 0] >= info['max_pfn']) or np.any((rows[:, 1] >> 1) > info['generation']):
        raise ValueError('out-of-range PFN or future lifetime')
    if int(np.count_nonzero(rows[:, 1] == 0)) != info['stale_records']:
        raise ValueError('stale record count differs')
    return info, rows


def matching_tags(source, pfns):
    """Return source tags at PFNs, with zero for missing entries."""
    values = np.zeros(len(pfns), dtype='<u8')
    if len(source):
        indices = np.searchsorted(source[:, 0], pfns)
        valid = indices < len(source)
        positions = np.flatnonzero(valid)
        positions = positions[source[indices[positions], 0] == pfns[positions]]
        values[positions] = source[indices[positions], 1]
    return values


def join_arrays(before, after, host):
    left, pages = before
    right, last = after
    wanted = pages[pages[:, 1] != 0]
    if host.shape != (len(wanted), 4):
        raise ValueError('host rows do not match initial metadata PFNs')
    if not np.array_equal(host[:, 0], wanted[:, 0]):
        host = host[np.argsort(host[:, 0])]
        if not np.array_equal(host[:, 0], wanted[:, 0]):
            raise ValueError('host rows do not match initial metadata PFNs')
    same_lifetime = matching_tags(last, wanted[:, 0]) == wanted[:, 1]
    first, middle, end = host[:, 1], host[:, 2], host[:, 3]
    same_mapping = (((first ^ middle) | (middle ^ end)) & PHYSICAL_MASK) == 0
    accepted = same_lifetime & same_mapping
    resident = accepted & ((first & PRESENT) != 0)
    physical = first[resident] & MASK
    if np.any(physical == 0):
        raise ValueError('host PFN hidden')
    swapped = accepted & ((first & PRESENT) == 0) & ((first & SWAPPED) != 0)
    nonresident = accepted & ((first & (PRESENT | SWAPPED)) == 0)
    stable = int(np.count_nonzero(resident))
    absent = int(np.count_nonzero(nonresident))
    swapped_count = int(np.count_nonzero(swapped))
    added = int(np.count_nonzero((last[:, 1] == 0) | (matching_tags(pages, last[:, 0]) != last[:, 1])))
    return dict(metadata_before=left, metadata_after=right,
        guest_cache_before_bytes=left['records']*4096, guest_cache_after_bytes=right['records']*4096,
        host_confirmed_cache_bytes=int(np.unique(physical).size)*4096,
        stable_resident_guest_pages=stable,
        host_mapping_changed_pages=int(np.count_nonzero(same_lifetime & ~same_mapping)),
        retired_or_reused_pages=int(np.count_nonzero(~same_lifetime)),
        new_or_unknown_after_pages=added, host_nonresident_pages=absent,
        host_swapped_pages=swapped_count,
        unconfirmed_after_pages=right['records']-stable-absent-swapped_count,
        interpretation='confirmed host-resident PFN union for unchanged guest cache lifetimes across this sampling interval')
