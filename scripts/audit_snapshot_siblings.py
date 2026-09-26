"""Recompute archived known-page and process checks after both VMs are deleted."""
import array
import csv
import hashlib
import json
from pathlib import Path
import sys


def main():
    out = Path(sys.argv[1]).resolve()
    result = json.loads((out / 'result.json').read_text())
    assert result['status'] == 'PASS' and all(value == 'PASS' for value in result['checks'].values())
    cfg = json.loads((out / 'config.json').read_text())
    for name, digest in cfg['sources'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    parent = json.loads((out / 'parent-state.json').read_text())
    slots = {}
    for line in (out / 'host/ioctl.tsv').read_text().splitlines():
        if '\t' not in line:
            continue
        fields = line.split('\t')
        assert len(fields) == 6 and int(fields[5]) == 0
        slots.setdefault(int(fields[0]), {})[int(fields[1])] = tuple(int(x,16) for x in fields[2:5])
    n = cfg['mib'] * 256
    summaries = {}
    host_by_phase = {}
    for phase in ('before-write','after-write'):
        directory = out / phase
        measured = json.loads((directory / 'physical-memory.json').read_text())
        summary = json.loads((directory / 'summary.json').read_text())
        assert summary['collector'] == measured
        with (directory / 'known-pages.tsv').open() as f:
            rows = list(csv.DictReader(f, delimiter='\t'))
        assert len(rows) == 2*n
        host_by_phase[phase] = {}
        for branch in ('A','B'):
            state = json.loads((directory / f'{branch}-state.json').read_text())
            assert state['page_count'] == n and len(state['pages']) == n
            for key in ('pid','start_ticks','boot_id'):
                assert state[key] == parent[key]
            assert state['mapped_sha256'] == parent['altered_sha256' if phase=='after-write' and branch=='A' else 'backing_sha256']
            assert state['backing_sha256'] == parent['backing_sha256']
            guest_rows = [r for r in rows if r['branch']==branch]
            assert len(guest_rows) == n
            sid_to_pid = measured['sandbox_to_vmm_pid']
            pid = int(guest_rows[0]['host_pid'])
            sid = next(sid for sid,p in sid_to_pid.items() if p==pid)
            probe = directory / 'probes' / f'mixfs-exp5-{sid}'
            marker = json.loads((probe / 'marker.json').read_text())
            assert marker['sandbox_id'] == sid
            enumerated = array.array('I')
            enumerated.frombytes((probe / 'file-pfns.bin').read_bytes())
            enumerated = set(enumerated)
            assert sorted((g,size) for g,size,_ in slots[pid].values()) == [(0,0xc0000000),(0x100000000,0x40000000)]
            host_pfns = []
            file_count = 0
            for index,row in enumerate(guest_rows):
                page = state['pages'][index]
                guest_pfn, flags = int(row['guest_pfn']),int(row['guest_flags'],16)
                assert int(row['page_index'])==index and guest_pfn==page['pfn'] and flags==page['flags']
                is_anon = phase=='after-write' and branch=='A' and index%2==0
                if is_anon:
                    assert flags & (1<<12) and guest_pfn not in enumerated
                else:
                    assert not flags & ((1<<12)|(1<<14)) and guest_pfn in enumerated
                    file_count+=1
                matches=[h+guest_pfn*4096-g for g,size,h in slots[pid].values() if g<=guest_pfn*4096<g+size]
                assert matches == [int(row['host_address'],16)]
                entry=int(row['host_pagemap_entry'],16)
                host_pfn=entry&((1<<55)-1)
                assert entry&(1<<63) and host_pfn
                host_pfns.append(host_pfn)
            assert len(set(host_pfns))==n
            assert summary['file_pages'][branch]==file_count
            assert summary['anonymous_pages'][branch]==n-file_count
            host_by_phase[phase][branch]=host_pfns
        a,b=host_by_phase[phase]['A'],host_by_phase[phase]['B']
        computed=dict(same_index_host_pfns=sum(x==y for x,y in zip(a,b)),
                      intersecting_host_pfns=len(set(a)&set(b)),
                      total_unique_host_pfns=len(set(a)|set(b)))
        assert all(summary[k]==value for k,value in computed.items())
        summaries[phase]=computed
    changes={branch:sum(x!=y for x,y in zip(host_by_phase['before-write'][branch],host_by_phase['after-write'][branch]))
             for branch in ('A','B')}
    audit=dict(status='PASS',mib=cfg['mib'],before=summaries['before-write'],after=summaries['after-write'],
               changed_host_pfns_by_branch=changes,
               note='non-atomic live pagemap samples; changed PFNs alone do not prove a cause')
    (out/'offline-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(f'status=PASS result_dir={out}')


if __name__=='__main__':
    main()
