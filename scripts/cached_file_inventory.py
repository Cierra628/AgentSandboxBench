"""Join streamed visible-file page proofs to one attested, paused AgentENV VM."""
import hashlib
import json
import os
from pathlib import Path
import time

from guest_file_type_check import is_file_lru
from guest_whole_cache_probe import selected_flags
from measure_file_physical import identity, ram_address, read_entry, PAGE, PFN_MASK
from trenvx_whole_cache import RECORD,dump,records,same_physical_mapping


def load(directory,name):return json.loads((directory/name).read_text())


def start(aenv,sid,out,mode,kernel_file_types=False):
    directory=out/'file-inventory';directory.mkdir();guest='/dev/shm/asb-file-inventory-'+sid
    cfg=dict(sandbox_id=sid,mode=mode,root='/workspace/asb-inventory-fixture' if mode=='fixture' else '/')
    if kernel_file_types:cfg['kernel_file_types']=True
    dump(directory/'config.json',cfg)
    aenv('inventory-config','upload',sid,str(directory/'config.json'),'/workspace/asb-cache-tools/inventory-config.json')
    aenv('inventory-start','exec',sid,'bash','-lc',f'nohup timeout 600 env ASB_CACHE_WAIT_SECONDS=300 PYTHONDONTWRITEBYTECODE=1 python3 /workspace/asb-cache-tools/guest_cached_file_inventory.py /workspace/asb-cache-tools/inventory-config.json {guest} >/dev/shm/asb-file-inventory.log 2>&1 </dev/null &')
    wait(aenv,sid,guest,'ready')
    for name in ('before.json','inventory-before.json','inventory-before-flags.bin','iomem.txt'):download(aenv,sid,guest,directory,name)
    return directory,guest


def wait(aenv,sid,guest,name):
    aenv('inventory-wait-'+name,'exec',sid,'bash','-lc',f'for i in $(seq 1 850); do test -f {guest}/{name} && exit 0; test -f {guest}/failure.txt && break; sleep 0.1; done; cat {guest}/failure.txt /dev/shm/asb-file-inventory.log; exit 1',timeout=90)


def download(aenv,sid,guest,directory,name):aenv('inventory-download-'+name,'download',sid,guest+'/'+name,str(directory/name))


def host_capture(directory,vm,proof):
    started=time.monotonic_ns();before=load(directory,'before.json');assert before['sandbox_id']==vm['sandbox_id']
    assert (directory/'iomem.txt').read_text()==proof['iomem']
    proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'));rows=[]
    boundary=load(directory,'inventory-before.json')
    selected=selected_flags((directory/'inventory-before-flags.bin').read_bytes(),boundary['ram_ranges_pfns'])
    with (proc/'pagemap').open('rb',buffering=0) as pm,(proc/'mem').open('rb',buffering=0) as mem:
        for file in before['files']:
            for page in file['pages']:
                if page['status']!='PASS':continue
                address=ram_address(page['pfn'],proof['slots']);entry=read_entry(pm.fileno(),address)
                digest=hashlib.sha256(os.pread(mem.fileno(),page['valid_bytes'],address)).hexdigest() if entry&(1<<63) else None
                last=read_entry(pm.fileno(),address)
                rows.append(dict(path=file['path'],index=page['index'],guest_pfn=page['pfn'],entry=entry,last=last,sha256=digest))
        ram=[(pfn,read_entry(pm.fileno(),ram_address(pfn,proof['slots'])),0) for pfn in selected]
        ram=[(pfn,first,read_entry(pm.fileno(),ram_address(pfn,proof['slots']))) for pfn,first,_ in ram]
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    (directory/'selected-ram.bin').write_bytes(b''.join(RECORD.pack(*r) for r in ram))
    dump(directory/'host-pages.json',rows);dump(directory/'host-receipt.json',dict(status='PASS',elapsed_ns=time.monotonic_ns()-started))


def finish(aenv,sid,directory,guest,calibration_scope='interval'):
    aenv('inventory-after-request','exec',sid,'touch',guest+'/capture-after');wait(aenv,sid,guest,'after-ready')
    for name in ('after.json','inventory-after.json','inventory-after-flags.bin'):download(aenv,sid,guest,directory,name)
    aenv('inventory-release','exec',sid,'touch',guest+'/release');wait(aenv,sid,guest,'done')
    result=recount(directory);dump(directory/'result.json',result)
    if result['mode']=='fixture':
        if calibration_scope=='frozen':
            kernel=load(directory,'kernel-file-types.json')
            assert kernel['status']==kernel['frozen_cache_physical_status']=='PASS'
            assert kernel['checks']['known_file_contents_match_ram']==kernel['checks']['known_file_type_device_inode_index']=='PASS'
            before=load(directory,'before.json');after=load(directory,'after.json');final={r['path']:r for r in after['files']}
            for file in before['files']:
                other=final[file['path']]
                assert file['status']==other['status']=='PASS' and file['identity']==other['identity']
                if not file['pages']:assert file['resident_indices']==other['resident_indices']==[]
            contents=json.loads(aenv('fixture-content-after-measurement','exec',sid,'python3',
                '/workspace/asb-cache-tools/guest_cached_file_inventory.py','--verify-fixture-content',guest))
            assert contents['status']=='PASS' and contents['checked_pages']==3
            dump(directory/'frozen-fixture-result.json',dict(status='PASS',calibration_scope='paused exact-kernel point',
                interval_fixture_status=result['fixture_checks'],contents_after_measurement=contents,
                caveat='Post-resume cache changes stay recorded; content reads follow the frozen point and inventory-after export. The later legacy cache export can include these reads.'))
        else:
            assert result['fixture_checks']=='PASS' and result['file_contents_verified_physical_bytes']==3*PAGE,result
    return result


def recount(directory):
    before=load(directory,'before.json');after=load(directory,'after.json');cfg=load(directory,'config.json')
    assert before['sandbox_id']==after['sandbox_id']==cfg['sandbox_id']
    assert before['page_size']==PAGE
    hosts={(r['path'],r['index']):r for r in load(directory,'host-pages.json')}
    assert len(hosts)==len(load(directory,'host-pages.json'))
    final={r['path']:r for r in after['files']};assert set(final)=={r['path'] for r in before['files']}
    meta=load(directory,'inventory-before.json');lastmeta=load(directory,'inventory-after.json')
    selected=set(selected_flags((directory/'inventory-before-flags.bin').read_bytes(),meta['ram_ranges_pfns']))
    final_selected=set(selected_flags((directory/'inventory-after-flags.bin').read_bytes(),lastmeta['ram_ranges_pfns']))
    ram=records(directory/'selected-ram.bin');assert len(ram)==len(selected) and {p for p,_,_ in ram}==selected
    all_cache={p:a&PFN_MASK for p,a,b in ram if a&(1<<63) and same_physical_mapping(a,b) and p in final_selected}
    assert all(all_cache.values()),'hidden resident host PFN'
    verified=set();guest_known=set();unknown=0;qualifying=0;skipped=0;fixture_ok=True
    for file in before['files']:
        other=final[file['path']]
        if file['status']!='PASS' or other['status']!='PASS':skipped+=1;unknown+=len(file['pages']);continue
        assert file['identity']==other['identity']
        right={p['index']:p for p in other['pages']};assert len(right)==len(other['pages'])
        if cfg['mode']=='fixture':
            expected={'hot-a':[0,1],'hot-tail':[0]}.get(Path(file['path']).name,[])
            if len(file['aliases'])==2:expected=[0,1]
            fixture_ok &= file['selected_indices']==expected if file['identity']['st_size'] else not file['pages']
            fixture_ok &= file['resident_indices']==other['resident_indices']
        for page in file['pages']:
            key=(file['path'],page['index']);row=hosts.get(key);end=right.get(page['index'])
            valid=(page['status']=='PASS' and end is not None and end['status']=='PASS' and row is not None
                and page['pfn']==end['pfn']==row['guest_pfn'] and page['sha256']==end['sha256']==row['sha256']
                and is_file_lru(page['flags']) and is_file_lru(end['flags'])
                and row['entry']&(1<<63) and same_physical_mapping(row['entry'],row['last'])
                and page['pfn'] in all_cache and (row['entry']&PFN_MASK)==all_cache[page['pfn']])
            if valid:verified.add(row['entry']&PFN_MASK);guest_known.add(page['pfn']);qualifying+=1
            else:unknown+=1
    cache=set(all_cache.values());assert verified<=cache
    return dict(status='PASS',measurement_status='PARTIAL',mode=cfg['mode'],root=cfg['root'],
        file_attribution_status='PASS' if not unknown and not skipped and not before['catalog']['errors'] else 'PARTIAL',
        hardlink_alias_names_merged=before['catalog']['regular_paths']-len(before['files']),
        file_contents_verified_physical_bytes=len(verified)*PAGE,verified_file_page_references=qualifying,
        verified_unique_host_pages=len(verified),verified_guest_pfns=len(guest_known),unknown_file_page_references=unknown,
        inventory_regular_paths=before['catalog']['regular_paths'],inventory_unique_files=len(before['files']),
        skipped_files=skipped,inventory_errors=before['catalog']['errors'],excluded_entry_counts=before['catalog']['excluded_entry_counts'],
        all_guest_cache_stable_physical_bytes=len(cache)*PAGE,guest_cache_unattributed_physical_bytes=len(cache-verified)*PAGE,
        guest_boundary_unknown_or_changed_pages=len(selected)-len(all_cache),
        guest_classification_added_pages=len(final_selected-selected),guest_classification_removed_pages=len(selected-final_selected),
        duplicate_file_page_references_removed=qualifying-len(verified),fixture_checks=('PASS' if fixture_ok and not unknown and not skipped and not before['catalog']['errors'] else 'FAIL') if cfg['mode']=='fixture' else None,
        whole_task_file_content_physical_bytes=None,
        scope='visible ext4 regular-file initially resident content; host PFN dedup; same VM paused host scan; non-atomic guest exports',
        limitations=['All-root traversal cannot name deleted/unreachable files or directory/block metadata.',
            'Other filesystem types and symlinks are excluded explicitly; no cold pages are intentionally read.',
            'Identity/content/PFN rechecks cannot exclude all intervening page reuse.',
            'This is guest file-content attribution; host image/metadata/provider memory not attributed.'])
