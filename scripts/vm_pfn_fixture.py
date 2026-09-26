"""Host checks for known mappings, called within the existing VM smoke lifecycle."""
import array
import json
import os
from pathlib import Path
import struct


def run_fixture(run, aenv, sid, out, pid, slots, mib, senior):
    source = Path(__file__).with_name('guest_pfn_fixture.py')
    tools_dir = out / 'fixture-tools'
    tools_dir.mkdir()
    (tools_dir / source.name).write_bytes(source.read_bytes())
    (tools_dir / 'physical-cache-probe.py').write_bytes((senior / 'workloads/prettier-6604-rl-fork/physical-cache-probe.py').read_bytes())
    aenv('fixture-upload', 'upload', sid, str(tools_dir), '/workspace/asb-tools')
    # Stop only the initial smoke's guest probe, by its recorded PID.
    aenv('initial-probe-stop', 'exec', sid, 'bash', '-lc',
         'kill "$(cat /tmp/asb-probe.pid)"; for i in $(seq 1 40); do '
         'kill -0 "$(cat /tmp/asb-probe.pid)" 2>/dev/null || break; sleep 0.05; done; '
         f'rm -f /dev/shm/mixfs-exp5-{sid}/marker.json')
    aenv('fixture-start', 'exec', sid, 'bash', '-lc',
         f'nohup timeout 160 python3 /workspace/asb-tools/guest_pfn_fixture.py {sid} {mib} >/tmp/asb-fixture.log 2>&1 </dev/null &')
    summary = []
    for case in ('shared', 'cow', 'private'):
        guest_dir = f'/workspace/asb-pfn-fixture/{case}'
        aenv(f'{case}-ready', 'exec', sid, 'bash', '-lc',
             f'for i in $(seq 1 100); do test -f {guest_dir}/ready && exit 0; sleep 0.1; done; cat /tmp/asb-fixture.log; exit 1')
        parent = out / 'fixture' / case
        parent.mkdir(parents=True)
        aenv(f'{case}-download', 'download', sid, guest_dir, str(parent))
        probe = parent / case
        fixture = json.loads((probe / 'fixture.json').read_text())
        run(f'{case}-join', ['python3', str(senior / 'physical_cache.py'), str(out), '1', str(parent), str(parent / 'physical.json')])
        enumerated = array.array('I')
        enumerated.frombytes((probe / 'file-pfns.bin').read_bytes())
        enumerated = set(enumerated)
        sets = []
        host_sets = []
        anon = set()
        file_pages = set()
        fd = os.open(f'/proc/{pid}/pagemap', os.O_RDONLY)
        try:
            with (parent / 'known-page-map.tsv').open('w') as table:
                table.write('mapping\tindex\tguest_pfn\tguest_flags\thost_address\thost_pagemap_entry\n')
                for index, mapping in enumerate(fixture['mappings']):
                    guest_set, host_set = set(), set()
                    for page_index, page in enumerate(mapping['pages']):
                        pfn, flags = page['pfn'], page['flags']
                        is_cow = case == 'cow' and index == 1 and page_index % 2 == 0
                        if is_cow:
                            assert flags & (1 << 12) and pfn not in enumerated
                            anon.add(pfn)
                        else:
                            assert not flags & ((1 << 12) | (1 << 14)) and pfn in enumerated
                            file_pages.add(pfn)
                        addresses = [h + pfn * 4096 - g for g, size, h in slots.values() if g <= pfn * 4096 < g + size]
                        assert len(addresses) == 1
                        entry = struct.unpack('Q', os.pread(fd, 8, addresses[0] // 4096 * 8))[0]
                        host_pfn = entry & ((1 << 55) - 1)
                        assert entry & (1 << 63) and host_pfn
                        table.write(f'{index}\t{page_index}\t{pfn}\t{flags:x}\t{addresses[0]:x}\t{entry:x}\n')
                        guest_set.add(pfn)
                        host_set.add(host_pfn)
                    sets.append(guest_set)
                    host_sets.append(host_set)
        finally:
            os.close(fd)
        n = fixture['pages_per_mapping']
        expected_union = {'shared': n, 'cow': n * 3 // 2, 'private': n * 2}[case]
        expected_intersection = {'shared': n, 'cow': n // 2, 'private': 0}[case]
        for pair in (sets, host_sets):
            assert len(pair[0] | pair[1]) == expected_union
            assert len(pair[0] & pair[1]) == expected_intersection
        assert len(file_pages) == (2*n if case == 'private' else n)
        assert len(anon) == (n//2 if case == 'cow' else 0)
        summary.append(dict(case=case, status='PASS', known_file_pages=len(file_pages), known_anon_pages=len(anon),
                            guest_unique_pages=expected_union, host_unique_pages=expected_union, intersection_pages=expected_intersection))
        (out / 'fixture-results.json').write_text(json.dumps(summary, indent=2))
        aenv(f'{case}-continue', 'exec', sid, 'touch', f'{guest_dir}/continue')
    aenv('fixture-done', 'exec', sid, 'bash', '-lc',
         'for i in $(seq 1 50); do test -f /workspace/asb-pfn-fixture/done && exit 0; sleep 0.1; done; cat /tmp/asb-fixture.log; exit 1')
