"""Guest-only non-faulting query interface. No file payload reads or policy writes."""
import os
from pathlib import Path
import stat
import time

from file_cache_metadata import decode, MAX_PAGES, PAGE

# Same seven state/counter names as sandbox_runtime_monitor.DAMON_FIELDS.
DAMON_FIELDS = ('enabled', 'kdamond_pid', 'nr_reclaim_tried_regions',
                'bytes_reclaim_tried_regions', 'nr_reclaimed_regions',
                'bytes_reclaimed_regions', 'nr_quota_exceeds')
DAMON_SETTINGS = ('min_age', 'sample_interval', 'aggr_interval', 'quota_ms',
                  'quota_sz', 'quota_reset_interval_ms', 'wmarks_high',
                  'wmarks_mid', 'wmarks_low', 'wmarks_interval', 'skip_anon',
                  'monitor_region_start', 'monitor_region_end')


def damon_snapshot(parameters=Path('/sys/module/damon_reclaim/parameters')):
    started = time.monotonic_ns()
    values, unavailable = {}, {}
    for name in DAMON_FIELDS + DAMON_SETTINGS:
        try:
            value = (parameters/name).read_text().strip()
            if name in DAMON_FIELDS:
                if name == 'enabled':
                    if value not in ('Y', 'N'):
                        raise ValueError('invalid enabled value')
                else:
                    value = int(value)
                    if value < (-1 if name == 'kdamond_pid' else 0):
                        raise ValueError('invalid counter')
            values[name] = value
        except (OSError, ValueError) as error:
            values[name] = None
            unavailable[name] = str(error)
    return dict(status='UNAVAILABLE' if unavailable else 'OBSERVED',
        start_ns=started, end_ns=time.monotonic_ns(), values=values,
        unavailable=unavailable, writes=False,
        interpretation='guest-wide DAMON region-operation counters; not per-file cause or physical bytes freed')


def file_query(path, endpoint=Path('/proc/asb_filecache')):
    """The proc write supplies an FD only; the kernel scans its existing mapping.

    Missing files (before the fixture write) are explicitly unavailable. A missing
    kernel interface is an error, never a fallback to a touching mmap/mincore probe.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as error:
        return None, dict(status='UNAVAILABLE', reason=str(error), path=str(path))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= MAX_PAGES * PAGE:
            raise ValueError('query requires a regular file <=32 MiB')
        with endpoint.open('r+b', buffering=0) as query:
            request = (str(fd)+'\n').encode()
            if query.write(request) != len(request):
                raise OSError('short file-cache request write')
            query.seek(0)
            raw = query.read()
        header, _ = decode(raw)
        after = os.fstat(fd)
        identity = lambda st: {k: getattr(st, k) for k in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        if (identity(before) != identity(after) or header['file_dev'] != before.st_dev or
                header['file_ino'] != before.st_ino or
                header['size_before'] != before.st_size or header['size_after'] != after.st_size):
            raise ValueError('file identity or content metadata changed during query')
        return raw, dict(status='OBSERVED', path=str(path), identity=identity(after),
                         header=header, payload_read_bytes=0, locked_file_bytes=0)
    finally:
        os.close(fd)


def capture(directory, publication, path, sandbox_id, boot_id):
    """Sidecar files preserve the original ASBCACH1/control protocol unchanged."""
    import json
    started = time.monotonic_ns()
    raw, receipt = file_query(path)
    if raw is not None:
        with (directory/f'file-{publication:04}.bin').open('xb') as output:
            output.write(raw)
    result = dict(version=1, publication=publication, sandbox_id=sandbox_id, boot_id=boot_id, file_cache=receipt,
                  damon=damon_snapshot(), start_ns=started, end_ns=time.monotonic_ns())
    with (directory/f'file-{publication:04}.json').open('x') as output:
        json.dump(result, output)
        output.write('\n')
    return result
