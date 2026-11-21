"""Opt-in guest counter reads, embedded in the already scheduled content command."""
import struct
import time
from pathlib import Path

from kernel_cache_metadata import FIELDS


def guest_point():
    start = time.monotonic_ns()
    raw = Path('/proc/asb_pagecache').read_bytes()
    end = time.monotonic_ns()
    magic, *values = struct.unpack_from('<8s12Q', raw)
    assert magic == b'ASBCACH1' and values[:3] == [1, 4096, 1 << 21]
    assert len(raw) == 104 + 16 * values[3] and not any(values[6:9])
    print('ASB_POINT', start, end, *values)


def measured_command(command, *, before):
    import inspect
    prefix = 'import struct,time\nfrom pathlib import Path\n' + inspect.getsource(guest_point)
    return prefix + ('guest_point()\n' if before else '') + command + '\nguest_point()'


def points(stdout, count):
    lines = [line.split()[1:] for line in stdout.splitlines() if line.startswith('ASB_POINT ')]
    assert len(lines) == count and all(len(v) == len(FIELDS)+2 for v in lines), stdout[-800:]
    return [dict(zip(('guest_start_ns', 'guest_end_ns', *FIELDS), map(int, v))) for v in lines]
