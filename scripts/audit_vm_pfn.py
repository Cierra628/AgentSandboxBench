"""Recompute one VM PFN smoke from archived inputs; never access live processes."""
import array
import csv
import hashlib
import json
from pathlib import Path
import sys


def main():
    out = Path(sys.argv[1]).resolve()
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'PASS'
    assert all(value == 'PASS' for value in result['checks'].values())
    cfg = json.loads((out / 'config.json').read_text())
    for name, digest in cfg['source_sha256'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    sid = (out / 'sandbox-id.txt').read_text().strip()
    measured = json.loads((out / 'host/physical-memory.json').read_text())
    pid = measured['sandbox_to_vmm_pid'][sid]
    slots = {}
    for line in (out / 'host/ioctl.tsv').read_text().splitlines():
        if '\t' not in line:
            continue
        fields = line.split('\t')
        assert len(fields) == 6 and int(fields[5]) == 0
        if int(fields[0]) == pid:
            slots[int(fields[1])] = tuple(int(value, 16) for value in fields[2:5])
    assert sorted((g, size) for g, size, _ in slots.values()) == [(0, 0xc0000000), (0x100000000, 0x40000000)]
    probe = out / 'host/probes' / f'mixfs-exp5-{sid}'
    marker = json.loads((probe / 'marker.json').read_text())
    assert marker['sandbox_id'] == sid
    assert (out / 'host/marker-read.bin').read_bytes() == marker['marker_ascii'].encode()
    pfns = array.array('I')
    pfns.frombytes((probe / 'file-pfns.bin').read_bytes())
    with (out / 'host/page-map.tsv').open() as f:
        rows = list(csv.DictReader(f, delimiter='\t'))
    assert [int(r['guest_pfn']) for r in rows] == list(pfns)
    assert len(rows) == marker['file_pfn_count'] == measured['guest_file_cache_logical_pages']
    unique = set()
    resident = 0
    for row in rows:
        guest_addr = int(row['guest_pfn']) * 4096
        matches = [host + guest_addr - guest for guest, size, host in slots.values()
                   if guest <= guest_addr < guest + size]
        assert matches == [int(row['host_virtual_address'], 16)]
        entry = int(row['pagemap_entry'], 16)
        if entry & (1 << 63):
            host_pfn = entry & ((1 << 55) - 1)
            assert host_pfn
            unique.add(host_pfn)
            resident += 1
    assert resident == measured['guest_file_cache_resident_mappings']
    assert len(unique) / 256 == measured['guest_file_cache_host_physical_mib']
    fixture_cases = []
    if cfg.get('fixture_mib'):
        for case in ('shared', 'cow', 'private'):
            parent = out / 'fixture' / case
            fixture = json.loads((parent / case / 'fixture.json').read_text())
            enumerated = array.array('I')
            enumerated.frombytes((parent / case / 'file-pfns.bin').read_bytes())
            file_set = set(enumerated)
            n = cfg['fixture_mib'] * 256
            assert fixture['pages_per_mapping'] == n
            with (parent / 'known-page-map.tsv').open() as f:
                known_rows = list(csv.DictReader(f, delimiter='\t'))
            assert len(known_rows) == 2*n
            guest_sets, host_sets = [set(), set()], [set(), set()]
            for number, row in enumerate(known_rows):
                index, page_index = divmod(number, n)
                assert int(row['mapping']) == index and int(row['index']) == page_index
                page = fixture['mappings'][index]['pages'][page_index]
                pfn, flags = int(row['guest_pfn']), int(row['guest_flags'], 16)
                assert pfn == page['pfn'] and flags == page['flags']
                is_cow = case == 'cow' and index == 1 and page_index % 2 == 0
                if is_cow:
                    assert flags & (1 << 12) and pfn not in file_set
                else:
                    assert not flags & ((1 << 12) | (1 << 14)) and pfn in file_set
                addresses = [h + pfn * 4096 - g for g, size, h in slots.values() if g <= pfn*4096 < g+size]
                assert addresses == [int(row['host_address'], 16)]
                entry = int(row['host_pagemap_entry'], 16)
                host_pfn = entry & ((1 << 55)-1)
                assert entry & (1 << 63) and host_pfn
                guest_sets[index].add(pfn)
                host_sets[index].add(host_pfn)
            expected = {'shared': (n, n), 'cow': (n*3//2, n//2), 'private': (n*2, 0)}[case]
            for pair in (guest_sets, host_sets):
                assert (len(pair[0] | pair[1]), len(pair[0] & pair[1])) == expected
            for index, mapping in enumerate(fixture['mappings']):
                assert mapping['sha256'] == fixture['cow_sha256' if case == 'cow' and index == 1 else 'original_sha256']
            fixture_cases.append(case)
    audit = dict(status='PASS', logical_pages=len(rows), resident_mappings=resident,
                 unique_host_pages=len(unique), fixture_cases=fixture_cases,
                 note='archived live reads; not a consistency proof for intervening page migration')
    (out / 'offline-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(f'status=PASS result_dir={out}')


if __name__ == '__main__':
    main()
