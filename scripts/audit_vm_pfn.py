"""Recompute one VM PFN smoke from archived inputs; never access live processes."""
import array
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--output',type=Path,help='new audit receipt; default directory/offline-audit.json')
    args=parser.parse_args();out=args.directory.resolve()
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
    named_summary=None
    if cfg.get('exp1'):
        identity=json.loads((out/'host/vmm-identity.json').read_text())
        assert identity['pid']==pid and identity['sandbox_id']==sid and identity['marker_verified']
        assert identity['start_time_ticks']>0
        expected='0::/'+str(Path(cfg['cgroup']).relative_to('/sys/fs/cgroup'))+'\n'
        assert identity['cgroup']==expected
        assert measured['dax_file_host_physical_pss_mib']==0
        from exp1_physical_workload import verify_replay
        verified=verify_replay(out/'replay')
        correctness=json.loads((out/'exp1-correctness.json').read_text())
        assert all(correctness[key]==value for key,value in verified.items())
        assert correctness['independent_svg_properties']=='PASS'
        before=json.loads((out/'exp1-independent-before.stdout').read_text())
        after=json.loads((out/'exp1-independent-after.stdout').read_text())
        assert not before['svg']['javascript_formatted']
        assert all(row['javascript_formatted'] and row['idempotent'] for row in after.values())
    if cfg.get('named_files_manifest'):
        from named_guest_file_physical import recount
        directory=out/'named-files'
        before=json.loads((directory/'before.json').read_text());after=json.loads((directory/'after.json').read_text())
        named_rows=json.loads((directory/'pages.json').read_text())
        computed=recount(before,after,named_rows,sid)
        named_saved=json.loads((directory/'result.json').read_text())
        assert all(named_saved[key]==value for key,value in computed.items())
        manifest=json.loads((directory/'tools/manifest.json').read_text())
        assert manifest['sandbox_id']==sid and manifest['files']==[f['path'] for f in before['files']]
        for row in named_rows:
            guest_addr=row['guest_pfn']*4096
            addresses=[h+guest_addr-g for g,size,h in slots.values() if g<=guest_addr<g+size]
            assert addresses==[row['address']]
        if (directory/'host-rechecks.json').exists():
            rechecks=json.loads((directory/'host-rechecks.json').read_text())
            expected=[{key:row[key] for key in ('owner','index','address','entry')} for row in named_rows]
            assert rechecks['first']==rechecks['after_guest']==expected
        receipt=json.loads((directory/'guest-result.json').read_text())
        assert receipt==named_saved['guest_receipt'] and receipt['status']=='PASS'
        assert [f['path'] for f in receipt['files']]==manifest['files']
        assert all(a['identity']==b['identity'] for a,b in zip(receipt['files'],before['files']))
        named_summary=computed
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
    if named_summary is not None:audit['named_files']=named_summary
    target=args.output or out/'offline-audit.json'
    with target.open('x') as stream:stream.write(json.dumps(audit,indent=2)+'\n')
    print(f'status=PASS result_dir={out}')


if __name__ == '__main__':
    main()
