"""Offline intersection of guest type checks and archived host mappings."""
import array
import csv
import json
from pathlib import Path
import struct
from guest_file_type_check import is_file_lru


def report(out):
    sid=(out/'sandbox-id.txt').read_text().strip()
    probe=out/'host/probes'/f'mixfs-exp5-{sid}'
    pfns=array.array('I');pfns.frombytes((probe/'file-pfns.bin').read_bytes())
    flags={}
    for label in ('before','after'):
        raw=(probe/f'flags-{label}.bin').read_bytes();assert len(raw)==8*len(pfns)
        flags[label]=[entry[0] for entry in struct.iter_unpack('<Q',raw)]
        receipt=json.loads((probe/f'flags-{label}.json').read_text())
        assert receipt['status']=='PASS' and receipt['sandbox_id']==sid and receipt['selected_pages']==len(pfns)
        assert receipt['still_file_lru_pages']==sum(map(is_file_lru,flags[label]))
    with (out/'host/page-map.tsv').open() as stream:
        rows=list(csv.DictReader(stream,delimiter='\t'))
    assert [int(row['guest_pfn']) for row in rows]==list(pfns)
    good=set();unknown=set();nonresident=0
    for index,row in enumerate(rows):
        entry=int(row['pagemap_entry'],16)
        if not entry&(1<<63):nonresident+=1;continue
        host_pfn=entry&((1<<55)-1);assert host_pfn
        target=good if all(is_file_lru(flags[label][index]) for label in flags) else unknown
        target.add(host_pfn)
    return dict(status='PARTIAL',selected_guest_pages=len(pfns),
        still_file_lru_before=sum(map(is_file_lru,flags['before'])),
        still_file_lru_after=sum(map(is_file_lru,flags['after'])),
        changed_flag_values=sum(a!=b for a,b in zip(flags['before'],flags['after'])),
        selected_nonresident_mappings=nonresident,
        type_bracketed_file_lru_host_bytes=len(good-unknown)*4096,
        invalid_or_unknown_host_bytes=len(unknown)*4096,
        host_pfn_classification_overlap_pages=len(unknown&good),
        current_file_cache_physical_bytes=None,
        scope='selected earlier file-LRU PFNs with qualifying guest type at both checks; host PFNs deduplicated',
        limitations=['Checks are not atomic; PFN reuse between matching checks is not excluded',
                     'File-LRU includes metadata; no task file inode attribution or unselected new file pages',
                     'Single VM only; not all host cache or DAX; type-check process can affect memory'])
