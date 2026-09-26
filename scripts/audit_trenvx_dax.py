"""Offline known-file PFN audit for the network-free DAX fixture."""
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    out=Path(sys.argv[1]).resolve()
    result=json.loads((out/'result.json').read_text())
    assert result['status']=='PASS' and all(x=='PASS' for x in result['checks'].values())
    config=json.loads((out/'config.json').read_text())
    expected=json.loads((out/'expected.json').read_text())
    for name,digest in config['sources'].items():
        assert sha(out/name)==digest
    for name,digest in expected['lower_image_sha256'].items():
        assert sha(out/name)==digest
        assert (out/'A'/name).stat().st_ino==(out/'B'/name).stat().st_ino==(out/name).stat().st_ino
    for branch in ('A','B'):
        serial=(out/f'{branch}.serial.log').read_text()
        assert f'ASB_READY branch={branch} hash={expected["original_sha256"]} kernel=6.1.134' in serial
        changed=expected['altered_sha256'] if branch=='A' else expected['original_sha256']
        assert f'ASB_AFTER branch={branch} hash={changed} lower={expected["original_sha256"]}' in serial
        for device,mount in [('pmem0','base'),('pmem1','checkpoint')]:
            assert any(line.startswith(f'/dev/{device} /{mount} ext4 ro,') and 'dax=always' in line for line in serial.splitlines())
    summaries={}
    for phase in ('before','after'):
        with (out/f'{phase}-pages.tsv').open() as f:
            rows=list(csv.DictReader(f,delimiter='\t'))
        assert len(rows)==2*len(expected['blocks'])
        by_branch={}
        pss={}
        for branch in ('A','B'):
            selected=[row for row in rows if row['branch']==branch]
            assert [int(row['file_block']) for row in selected]==expected['blocks']
            regions=[]
            for line in (out/f'{phase}-{branch}.maps').read_text().splitlines():
                fields=line.split(maxsplit=5)
                if len(fields)==6 and fields[5]==str(out/branch/'checkpoint-0001.ext4'):
                    assert int(fields[4])==expected['layer_inode']
                    start,end=(int(x,16) for x in fields[0].split('-'))
                    regions.append((start,end,int(fields[2],16)))
            pfns=[]
            for row in selected:
                offset=int(row['file_block'])*4096
                addresses=[start+offset-file_offset for start,end,file_offset in regions if file_offset<=offset<file_offset+end-start]
                assert addresses==[int(row['host_address'],16)]
                entry=int(row['pagemap_entry'],16)
                pfn=entry&((1<<55)-1)
                assert entry&(1<<63) and pfn
                pfns.append(pfn)
            assert len(set(pfns))==len(pfns)
            by_branch[branch]=pfns
            candidate=False
            total=0
            for line in (out/f'{phase}-{branch}.smaps').read_text().splitlines():
                if re.match(r'^[0-9a-f]+-[0-9a-f]+\s',line):
                    fields=line.split(maxsplit=5)
                    name=Path(fields[5]).name if len(fields)==6 else ''
                    candidate=name=='rootfs.ext4' or name.startswith('checkpoint-') and name.endswith('.ext4')
                elif candidate and line.startswith('Pss:'):
                    total+=int(line.split()[1])
            pss[branch]=total
        assert by_branch['A']==by_branch['B'], 'different host PFNs for corresponding known layer pages'
        summary=json.loads((out/f'{phase}.json').read_text())
        assert summary['shared_known_pages']==summary['unique_known_pages']==len(expected['blocks'])
        summaries[phase]=dict(shared_known_pages=len(expected['blocks']),
                             raw_dax_mapping_pss_kib=pss,
                             known_physical_mib=len(expected['blocks'])/256)
    upper={}
    for branch in ('A','B'):
        image=out/branch/'writable-rootfs.ext4'
        p=subprocess.run(['debugfs','-R','stat /root/known.bin',str(image)],capture_output=True,timeout=15)
        (out/f'{branch}-upper-stat.stdout').write_bytes(p.stdout)
        (out/f'{branch}-upper-stat.stderr').write_bytes(p.stderr)
        p.check_returncode()
        if branch=='A':
            size=int(re.search(rb'\bSize: (\d+)',p.stdout)[1])
            blocks=int(re.search(rb'\bBlockcount: (\d+)',p.stdout)[1])
            assert size==config['mib']*1024**2 and blocks*512>=size
            target=out/'A-upper-known.bin'
            if not target.exists():
                dump=subprocess.run(['debugfs','-R',f'dump /root/known.bin {target}',str(image)],capture_output=True,timeout=15)
                (out/'A-upper-dump.stdout').write_bytes(dump.stdout)
                (out/'A-upper-dump.stderr').write_bytes(dump.stderr)
                dump.check_returncode()
            assert target.stat().st_size==size and sha(target)==expected['altered_sha256']
            upper[branch]=dict(size_bytes=size,allocated_bytes=blocks*512,sha256=sha(target))
        else:
            assert b'File not found' in p.stderr and not p.stdout.strip()
            upper[branch]=dict(known_file_absent=True)
    audit=dict(status='PASS',phases=summaries,
               upper_copy=upper,auditor_sha256=sha(Path(__file__)),
               limitation='known immutable layer pages only; raw PSS includes metadata and is not equivalent to payload size')
    (out/'offline-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    print(f'status=PASS result_dir={out}')


if __name__=='__main__':
    main()
