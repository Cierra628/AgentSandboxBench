"""Join named guest file mappings to attested KVM RAM; raw PFNs remain local."""
import hashlib
import json
import os
from pathlib import Path
import time

from guest_file_type_check import is_file_lru
from measure_file_physical import captured_slots,identity,parse_maps,ram_address,read_entry


def validate_guest(snapshot,sid):
    assert snapshot['status']=='PASS' and snapshot['sandbox_id']==sid and snapshot['page_size']==4096
    files=snapshot['files'];assert files and len({row['path'] for row in files})==len(files)
    for file in files:
        metadata=file['identity'];size=metadata['st_size'];assert size>0
        assert file['total_pages']==(size+4095)//4096
        region=parse_maps(file['mapping'])[0]
        assert region['start']==file['address'] and region['offset']==0
        mapped_metadata=file.get('mapping_identity',metadata)
        assert all(mapped_metadata[key]==metadata[key] for key in ('st_size','st_mtime_ns','st_ctime_ns'))
        assert region['inode']==mapped_metadata['st_ino'] and region['device']==mapped_metadata['st_dev']
        assert region['permissions']=='rw-p' and region['end']-region['start']>=size
        indices=file['selected_indices'];assert sorted(set(indices))==indices
        assert [row['index'] for row in file['pages']]==indices
        for page in file['pages']:
            assert 0<=page['index']<file['total_pages']
            assert page['offset']==page['index']*4096 and page['valid_bytes']==min(4096,size-page['offset'])
            assert page['pfn']==page['entry']&((1<<55)-1)
            if page['entry']&(1<<63):assert page['pfn']>0
    return files


def recount(before,after,rows,sid):
    first=validate_guest(before,sid);last=validate_guest(after,sid)
    assert before['pid']==after['pid'] and before['boot_id']==after['boot_id']
    assert [f['path'] for f in first]==[f['path'] for f in last]
    qualified=[];unknown=set();file_rows=[];expected_keys=[]
    for initial,left,right in zip(before['initial_residency'],first,last):
        assert initial['path']==left['path']
        assert len(initial['resident_before'])==left['total_pages']
        assert [i for i,hot in enumerate(initial['resident_before']) if hot]==left['selected_indices']
        assert left['identity']==right['identity'] and left['selected_indices']==right['selected_indices']
        assert left.get('mapping_identity')==right.get('mapping_identity')
        assert left.get('mapping_source')==right.get('mapping_source')
        assert left['address']==right['address'] and left['mapping']==right['mapping']
        stable=0
        for a,b in zip(left['pages'],right['pages']):
            key=(left['path'],a['index']);expected_keys.append(key)
            matches=[row for row in rows if (row['owner'],row['index'])==key];assert len(matches)==1
            row=matches[0];assert row['guest_pfn']==a['pfn']
            assert row['offset']==a['offset'] and row['valid_bytes']==a['valid_bytes']
            valid=(a['entry']&(1<<61) and b['entry']&(1<<63) and b['entry']&(1<<61)
                and a['pfn']==b['pfn'] and a['sha256']==b['sha256']
                and is_file_lru(a['flags']) and is_file_lru(b['flags']))
            if row['entry']&(1<<63):
                assert row['entry']&((1<<55)-1), 'resident host PFN hidden or missing'
                assert row['content_sha256']==a['sha256'],'host/guest content mismatch'
            if valid:
                qualified.append(dict(row,kind='file_content'));stable+=1
            elif row['entry']&(1<<63):unknown.add(row['entry']&((1<<55)-1))
        file_rows.append(dict(path=left['path'],identity=left['identity'],total_pages=left['total_pages'],
            initially_resident_pages=len(left['selected_indices']),type_and_pfn_verified_pages=stable,
            initially_nonresident_pages=left['total_pages']-len(left['selected_indices'])))
    assert [(row['owner'],row['index']) for row in rows]==expected_keys
    known={row['entry']&((1<<55)-1) for row in qualified if row['entry']&(1<<63)}
    conflict=known&unknown;known-=unknown
    valid_count=sum(f['type_and_pfn_verified_pages'] for f in file_rows)
    return dict(status='PASS' if valid_count==len(rows) and not unknown else 'PARTIAL',page_size=4096,
        files=file_rows,selected_mapping_pages=len(rows),verified_mapping_pages=valid_count,
        verified_unique_host_pages=len(known),verified_file_cache_physical_bytes=len(known)*4096,
        unknown_resident_unique_pages=len(unknown),classification_conflict_pages=len(conflict),
        nonresident_host_mappings=sum(not row['entry']&(1<<63) for row in rows),
        allocated_tail_page_rule='count full 4096-byte page, validate only logical content bytes',
        scope='declared files and pages resident before this probe; single VM; not all task or host cache',
        limitations=['Non-atomic rechecks cannot rule out intervening PFN reuse',
            'Residency checked after prior file-LRU analysis; mincore and mapping reads can change cache',
            'File pages absent before the probe are not read; full file hashes taken after both host checks',
            'No upper/backing-layer classification, complete host cache or multi-VM sharing conclusion'])


def run_named_files(aenv,sid,out,pid,slots,manifest_path):
    started=time.monotonic_ns();directory=out/'named-files';directory.mkdir()
    tools=directory/'tools';tools.mkdir()
    source=Path(__file__).with_name('guest_named_file_probe.py')
    (tools/source.name).write_bytes(source.read_bytes())
    cfg=json.loads(manifest_path.read_text());cfg['sandbox_id']=sid
    (tools/'manifest.json').write_text(json.dumps(cfg,indent=2)+'\n')
    guest='/workspace/asb-named-files';guest_tools='/workspace/asb-named-tools'
    aenv('named-upload','upload',sid,str(tools),guest_tools)
    aenv('named-start','exec',sid,'bash','-lc',
        f'nohup timeout 160 python3 {guest_tools}/{source.name} {guest_tools}/manifest.json {guest} >/tmp/asb-named-probe.log 2>&1 </dev/null &')
    def wait(label,marker):
        aenv(label,'exec',sid,'bash','-lc',f'for i in $(seq 1 100); do test -f {guest}/{marker} && exit 0; sleep 0.1; done; cat /tmp/asb-named-probe.log; exit 1')
    wait('named-ready','ready')
    aenv('named-before-download','download',sid,guest+'/before.json',str(directory/'before.json'))
    before=json.loads((directory/'before.json').read_text());files=validate_guest(before,sid)
    old=json.loads((out/'host/vmm-identity.json').read_text())
    vm=dict(id=sid,pid=pid,start_time_ticks=old['start_time_ticks'],executable=old['executable'],
        cgroup=old['cgroup'].strip().split('::')[1])
    proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    actual=captured_slots(out/'host/ioctl.tsv',pid,list(slots))
    assert actual==[dict(guest=g,size=size,host=host) for g,size,host in slots.values()]
    rows=[];host_started=time.monotonic_ns();rechecks=[]
    with (proc/'pagemap').open('rb',buffering=0) as pm,(proc/'mem').open('rb',buffering=0) as memory:
        for file in files:
            for page in file['pages']:
                address=ram_address(page['pfn'],actual);entry=read_entry(pm.fileno(),address)
                digest=None
                if entry&(1<<63):
                    content=os.pread(memory.fileno(),page['valid_bytes'],address)
                    assert len(content)==page['valid_bytes']
                    digest=hashlib.sha256(content).hexdigest()
                    assert digest==page['sha256'],'guest page and host RAM content differ'
                rows.append(dict(vm=sid,owner=file['path'],index=page['index'],offset=page['offset'],
                    valid_bytes=page['valid_bytes'],guest_pfn=page['pfn'],address=address,entry=entry,content_sha256=digest))
        for row in rows:
            entry=read_entry(pm.fileno(),row['address']);assert entry==row['entry'],'host mapping changed'
            rechecks.append(dict(owner=row['owner'],index=row['index'],address=row['address'],entry=entry))
    (directory/'pages.json').write_text(json.dumps(rows,indent=2)+'\n')
    host_elapsed=time.monotonic_ns()-host_started
    aenv('named-after-request','exec',sid,'touch',guest+'/capture-after');wait('named-after-ready','after-ready')
    aenv('named-after-download','download',sid,guest+'/after.json',str(directory/'after.json'))
    after=json.loads((directory/'after.json').read_text())
    final_entries=[]
    with (proc/'pagemap').open('rb',buffering=0) as pm:
        for row in rows:
            entry=read_entry(pm.fileno(),row['address']);assert entry==row['entry'],'host mapping changed across guest recheck'
            final_entries.append(dict(owner=row['owner'],index=row['index'],address=row['address'],entry=entry))
    (directory/'host-rechecks.json').write_text(json.dumps(dict(first=rechecks,after_guest=final_entries),indent=2)+'\n')
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    result=recount(before,after,rows,sid)
    aenv('named-release','exec',sid,'touch',guest+'/release');wait('named-done','done')
    aenv('named-result-download','download',sid,guest+'/result.json',str(directory/'guest-result.json'))
    receipt=json.loads((directory/'guest-result.json').read_text());assert receipt['status']=='PASS'
    assert [f['path'] for f in receipt['files']]==cfg['files']
    assert all(a['identity']==b['identity'] for a,b in zip(receipt['files'],files))
    result.update(guest_receipt=receipt,controller_elapsed_ns=time.monotonic_ns()-started,
                  host_read_elapsed_ns=host_elapsed,guest_before_elapsed_ns=before['elapsed_ns'],
                  guest_after_elapsed_ns=after['elapsed_ns'])
    (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
