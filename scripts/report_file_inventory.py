"""Offline audit of visible-file proofs and paused exact-kernel cache exports.

Only aggregates and hashes are emitted. Raw PFNs and kernel addresses stay local.
No live service, VM or kernel access is performed by this report.
"""
import argparse
import csv
import json
from pathlib import Path
import stat
import struct

from archive_trenvx_run_data import sha256
from cached_file_inventory import recount
from exp1_physical_workload import verify_replay
from kernel_file_cache import KERNEL_SHA
from measure_file_physical import PAGE, PFN_MASK
from trenvx_whole_cache import records, same_physical_mapping


def load(path):
    return json.loads(path.read_text())


def frozen_selection(raw, ranges, vmemmap, lru_bit=4):
    """Select from all exported structs, including folio flags on tail pages."""
    assert len(raw) == sum(end-start for start,end in ranges)*64
    chunks=[];offset=0
    for start,end in ranges:
        assert start < end and (not chunks or start >= chunks[-1][1])
        chunks.append((start,end,offset));offset += (end-start)*64
    def fields(pfn):
        for start,end,offset in chunks:
            if start <= pfn < end:
                return struct.unpack_from('<8Q',raw,offset+(pfn-start)*64)
        raise AssertionError('compound head outside exported System RAM')
    selected=[];tails=0
    for start,end,offset in chunks:
        for i,(flags,head,*_) in enumerate(struct.iter_unpack('<8Q',raw[offset:offset+(end-start)*64])):
            if head&1:
                address=head-1
                assert address >= vmemmap and (address-vmemmap)%64==0
                flags=fields((address-vmemmap)//64)[0];tails+=1
            if flags&(1<<lru_bit) and not flags&(1<<19):selected.append(start+i)
    return selected,tails


def physical_types(rows, host, snapshot_rows=(), nonresident_pfns=(), allocation_inodes=()):
    assert len({r['guest_pfn'] for r in rows})==len(rows)
    assert len({p for p,_,_ in host})==len(host)
    assert {r['guest_pfn'] for r in rows}=={p for p,_,_ in host}
    by_pfn={r['guest_pfn']:r for r in rows};counts={};unions={};unknown_host=0
    fallback={r['guest_pfn']:r for r in snapshot_rows};cold=set(nonresident_pfns)
    assert len(fallback)==len(snapshot_rows) and len(cold)==len(nonresident_pfns)
    assert not set(fallback)&cold and set(fallback)|cold <= set(by_pfn)
    expected={stat.S_IFREG:'regular_file',stat.S_IFBLK:'block_device',stat.S_IFDIR:'directory',stat.S_IFLNK:'symlink'}
    for pfn,first,last in host:
        row=by_pfn[pfn]
        name='unknown_mapping'
        if row['status']=='PASS':
            name=expected.get(stat.S_IFMT(row['mode']),'other_inode')
            if row['inode_type']=='filesystem_allocation_metadata':
                assert row['mode']==0 and row['inode']==1 and row['inode_pointer'] in allocation_inodes,'unverified allocator object'
                name='filesystem_allocation_metadata'
            assert name==row['inode_type']
        else:assert row['status']=='UNKNOWN'
        counts[name]=counts.get(name,0)+1
        if pfn in fallback or pfn in cold:
            assert not first&((1<<63)|(1<<62)) and same_physical_mapping(first,last)
            if pfn in cold:continue
            item=fallback[pfn];first=item['entry'];last=item['recheck_entry']
            assert first&(1<<61) and same_physical_mapping(first,item['last'])
        if first&(1<<63):assert first&PFN_MASK,'hidden resident host PFN'
        if not first&(1<<63) or first&(1<<62) or not same_physical_mapping(first,last):
            unknown_host+=1;continue
        unions.setdefault(name,set()).add(first&PFN_MASK)
    total=set().union(*unions.values()) if unions else set()
    return dict(inode_type_pages=counts,inode_type_unique_physical_bytes={k:len(v)*PAGE for k,v in unions.items()},
        frozen_guest_file_cache_physical_bytes=len(total)*PAGE,host_unknown_pages=unknown_host,
        cross_type_duplicate_host_pages=sum(len(v) for v in unions.values())-len(total),
        snapshot_fallback_pages=len(fallback),proven_nonresident_pages=len(cold),
        classification_status='PARTIAL' if counts.get('other_inode',0) or counts.get('unknown_mapping',0) else 'PASS',
        frozen_cache_physical_status='PARTIAL' if unknown_host else 'PASS')


def audit_frozen_fixture(directory,*,require_complete_cache=True):
    saved=load(directory/'frozen-fixture-result.json');before=load(directory/'before.json');after=load(directory/'after.json')
    actual=recount(directory);kernel=load(directory/'kernel-file-types.json')
    assert saved['status']=='PASS' and saved['calibration_scope']=='paused exact-kernel point'
    assert saved['interval_fixture_status']==actual['fixture_checks']
    assert kernel['checks']['known_file_contents_match_ram']==kernel['checks']['known_file_type_device_inode_index']=='PASS'
    assert kernel['frozen_cache_physical_status']=='PASS' or not require_complete_cache and kernel['frozen_cache_physical_status']=='PARTIAL'
    assert before['mode']=='fixture' and before['catalog']['regular_paths']==6 and len(before['files'])==5
    final={r['path']:r for r in after['files']};expected=[];nonresident=0
    for file in before['files']:
        other=final[file['path']];assert file['status']==other['status']=='PASS' and file['identity']==other['identity']
        if not file['pages']:assert file['resident_indices']==other['resident_indices']==[]
        for page in file['pages']:
            assert page['status']=='PASS'
            expected.append(dict(path=file['path'],index=page['index'],valid_bytes=page['valid_bytes'],sha256=page['sha256'],identity=file['identity']))
        nonresident+=sum(r['status']=='NONRESIDENT' for r in other['pages'])
    content=saved['contents_after_measurement']
    assert content['status']=='PASS' and content['checked_pages']==len(expected)==3
    assert content['pages']==expected
    return dict(status='PASS',scope='known paused-point identity/content plus post-measurement file content; no interval stability claim',
                known_pages=3,interval_fixture_status=actual['fixture_checks'],after_export_nonresident_pages=nonresident,
                post_measurement_content='PASS',content_read_cost_in_analysis=True)


def allocation_audit(directory,owners,exported,ranges,vmemmap,expected,delta):
    """Decode archived metadata bytes independently of the live field reader."""
    receipt=load(directory/'allocation-owner-proof.json');raw=(directory/'allocation-owner-pages.bin').read_bytes()
    assert receipt['status']=='PASS' and len(raw)==4096*len(receipt['pages'])
    pages={}
    import hashlib,re
    for i,page in enumerate(receipt['pages']):
        base=page['physical_address'];data=raw[i*4096:(i+1)*4096]
        assert base%4096==0 and base not in pages and any(start*4096<=base<end*4096 for start,end in ranges)
        assert page['entry']&(1<<63) and page['entry']&PFN_MASK and same_physical_mapping(page['entry'],page['last'])
        assert hashlib.sha256(data).hexdigest()==page['sha256'];pages[base]=data
    compiled={}
    for line in (directory/'kernel-symbols.txt').read_text().splitlines():
        items=line.split()
        if len(items)==3 and items[2]=='ext4_sops':compiled[items[2]]=int(items[0],16)
    buddy=736 if expected==KERNEL_SHA else 704
    asm=(directory/'ext4_mb_init_backend.asm').read_bytes()
    assert re.search(rb'mov\s+0x380\(%rdi\),%rax',asm)
    assert re.search(rb'mov\s+%rax,0x'+format(buddy,'x').encode()+rb'\(%r15\)',asm)
    assert re.search(rb'movq\s+\$0x1,0x40\(%rax\)',asm)
    assert re.search(rb'movq\s+\$0x'+format(compiled['ext4_sops'],'x').encode()+rb',0x30\(%rbx\)',(directory/'__ext4_fill_super.asm').read_bytes())
    verified=set()
    for obj in receipt['objects']:
        assert obj['status']=='PASS' and obj['buddy_offset']==buddy and obj['ext4_sops']==compiled['ext4_sops']+delta
        fields=obj['fields'];decoded={}
        for name,field in fields.items():
            chunks=[];count=0
            for piece in field['pieces']:
                gpa=piece['physical_address'];size=piece['size'];offset=gpa%4096
                assert 0<size<=4096-offset
                assert size==min(len(bytes.fromhex(field['hex']))-count,4096-((field['address']+count)%4096))
                chunks.append(pages[gpa&~4095][offset:offset+size]);count+=size
            data=b''.join(chunks);assert data.hex()==field['hex']
            decoded[name]=int.from_bytes(data,'little')
        inode=obj['inode_pointer'];sb=obj['superblock_pointer']
        expected_fields={'inode_mode':(inode,2,0),'inode_number':(inode+64,8,1),
            'inode_superblock':(inode+40,8,sb),'superblock_operations':(sb+48,8,obj['ext4_sops']),
            'superblock_private':(sb+896,8,decoded['superblock_private']),
            'buddy_inode':(decoded['superblock_private']+buddy,8,inode)}
        assert set(fields)==set(expected_fields)|{'mapping_host'}
        assert decoded['mapping_host']==inode and len(bytes.fromhex(fields['mapping_host']['hex']))==8
        for name,(address,size,value) in expected_fields.items():
            assert fields[name]['address']==address and len(bytes.fromhex(fields[name]['hex']))==size and decoded[name]==value
        matching=[r for r in owners if r.get('inode_pointer')==inode and r.get('inode_type')=='filesystem_allocation_metadata']
        assert matching
        for row in matching:
            assert row['mode']==0 and row['inode']==1 and row['superblock_pointer']==sb
            assert fields['mapping_host']['address']==row['mapping_pointer']
            # Rebind each row to its separately archived actual struct page.
            offset=0
            for start,end in ranges:
                if start<=row['guest_pfn']<end:
                    actual=struct.unpack_from('<8Q',exported,offset+(row['guest_pfn']-start)*64)
                    assert actual[3]==row['mapping_pointer'] and actual[4]==row['index'];break
                offset+=(end-start)*64
            else:raise AssertionError('allocator page outside System RAM')
        assert inode not in verified;verified.add(inode)
    assert {r['inode_pointer'] for r in owners if r.get('inode_type')=='filesystem_allocation_metadata'}==verified
    return verified


def kernel_audit(directory, proof, backend='agentenv'):
    saved=load(directory/'kernel-file-types.json');meta=load(directory/'frozen-page-structs.json')
    owners=load(directory/'kernel-page-owners.json')
    expected=KERNEL_SHA
    if backend=='trenvx':
        from trenvx_kernel_cache import KERNEL_SHA as expected
    assert saved['status']=='PASS' and saved['kernel_sha256']==expected
    assert all(v=='PASS' for v in saved['checks'].values())
    assert meta['record_size']==64 and meta['lru_bit'] in (4,5) and meta['swapbacked_bit']==19
    from guest_whole_cache_probe import ram_ranges
    assert meta['ram_ranges_pfns']==[list(pair) for pair in ram_ranges(proof['iomem'])]
    bases={r['page_pointer']-r['guest_pfn']*64 for r in owners if r['status']=='PASS'}
    assert len(bases)==1
    base=next(iter(bases));assert meta.get('vmemmap_base',base)==base
    exported=(directory/'frozen-page-structs.bin').read_bytes()
    selected,tails=frozen_selection(exported,meta['ram_ranges_pfns'],base,meta['lru_bit'])
    assert set(selected)=={r['guest_pfn'] for r in owners} and len(selected)==len(owners)
    fallback=load(directory/'snapshot-page-fallback.json') if backend=='trenvx' else []
    cold=load(directory/'nonresident-snapshot-pfns.json') if backend=='trenvx' else []
    # No independently archived cold-page mincore receipts in this first bridge.
    # Refuse an offline PASS if the live run encountered such pages.
    assert not cold,'cold snapshot-page evidence requires a stronger offline receipt'
    allocation_inodes=set()
    if (directory/'allocation-owner-proof.json').exists():
        compiled_text=next(int(line.split()[0],16) for line in (directory/'kernel-symbols.txt').read_text().splitlines() if line.split()[-1]=='_text')
        allocation_inodes=allocation_audit(directory,owners,exported,meta['ram_ranges_pfns'],base,expected,
            load(directory/'before.json')['kernel_symbols']['_text']-compiled_text)
    actual=physical_types(owners,records(directory/'frozen-typed-ram.bin'),fallback,cold,allocation_inodes)
    assert sum(actual['inode_type_pages'].values())==saved['selected_guest_pages']==len(selected)
    assert {k:v for k,v in actual['inode_type_pages'].items() if k!='unknown_mapping'}==saved['inode_type_pages']
    assert actual['inode_type_pages'].get('unknown_mapping',0)==saved['unknown_guest_pages']
    # Earlier calibration did not mark mode=0 as untyped. Keep its raw receipt
    # unchanged, and explicitly publish the stricter offline assessment.
    if 'frozen_guest_file_cache_physical_bytes' in saved:
        assert actual['frozen_guest_file_cache_physical_bytes']==saved['frozen_guest_file_cache_physical_bytes']
        assert actual['host_unknown_pages']==saved['frozen_guest_file_cache_host_unknown_pages']
        assert actual['classification_status']==saved['classification_status']
    for function,digest in saved['compiled_layout_evidence_sha256'].items():assert sha256(directory/(function+'.asm'))==digest
    if meta['lru_bit']==4:
        import re
        assert re.search(rb'lock btrq\s+\$0x4,\(%rdi\)',(directory/'folio_isolate_lru.asm').read_bytes())
    symbols={}
    for line in (directory/'kernel-symbols.txt').read_text().splitlines():
        parts=line.split()
        if len(parts)==3 and parts[2] in ('_text','folio_mapping','pfn_to_online_page','generic_fillattr'):symbols[parts[2]]=int(parts[0],16)
    guest=load(directory/'before.json')['kernel_symbols']
    assert len({guest[name]-symbols[name] for name in symbols})==1 and len(symbols)==4
    for file in load(directory/'before.json')['files']:
        for page in file['pages']:
            if page['status']!='PASS':continue
            row=next(r for r in owners if r['guest_pfn']==page['pfn'])
            assert row['inode_type']=='regular_file' and row['device']==file['identity']['st_dev'] and row['inode']==file['identity']['st_ino'] and row['index']==page['index']
    config=load(directory/'vmm-config.json')
    boot=config['boot-source']['boot_args'] if backend=='agentenv' else config['config']['payload']['cmdline']
    if backend=='trenvx':
        layout=load(directory/'layout-proof.json');assert layout['status']=='PASS' and layout['kernel_sha256']==expected
        assert layout['lru_bit']==4 and layout['swapbacked_bit']==19 and layout['page_struct_size']==64
        for name,digest in layout['layout_sha256'].items():assert sha256(directory/(name+'-layout.txt'))==digest
        assert sha256(directory/'internal-pageflags.txt')==layout['flags_sha256']
    return dict(actual,allocation_objects_verified=len(allocation_inodes),offline_recount='PASS',selected_guest_pages=len(selected),compound_tail_records_in_all_ram=tails,
        cache_scope_status='PASS' if meta['lru_bit']==4 else 'FAIL',
        internal_flag_bit_used=meta['lru_bit'],
        scope_error=None if meta['lru_bit']==4 else 'Used internal PG_active bit 5 instead of PG_lru bit 4; not complete file-LRU cache.',
        correct_file_lru_guest_pages=len(frozen_selection(exported,meta['ram_ranges_pfns'],base,4)[0]),
        saved_classification_status=saved['classification_status'],kernel_sha256=saved['kernel_sha256'],
        decoder_elapsed_ns=saved['controller_elapsed_ns'],metadata_read_bytes=saved['metadata_read_bytes'],
        damon_reclaim_boot_options=[word for word in boot.split() if word.startswith('damon_reclaim.')],
        unique_regular_file_inodes=len({(r['device'],r['inode']) for r in owners if r.get('inode_type')=='regular_file'}))


def trenvx_report(raw):
    from run_whole_analysis_controls import inspect
    from report_whole_analysis_controls import memory
    measured,_=inspect(raw,'trenvx','on');cfg=load(raw/'config.json');result=load(raw/'result.json')
    assert cfg['kernel_file_types'] and cfg['paused_cache_boundary']
    for group in ('kernel_cache_sources','whole_cache_sources','guest_file_physical_sources'):
        for name,digest in cfg[group].items():assert sha256(raw/name)==digest
    assert result['checks']['kernel_cache_fixture_before_replay']=='PASS'
    points=[]
    for name in ('kernel-cache-calibration','whole-cache'):
        directory=raw/(name+'-files');fixture=load(directory/'fixture-result.json')
        assert fixture['status']=='PASS' and fixture['fixture_hot_pages']==3 and fixture['cold_and_sparse_stayed_nonresident']
        kernel=kernel_audit(directory,load(raw/name/'ram-attestation.json'),backend='trenvx')
        pause=load(raw/name/'paused-boundary.json')
        assert pause['status']==pause['original_vmm_identity']==pause['heartbeat_continuity']=='PASS'
        a=pause['before_running_counter'];b=pause['paused_counter'];c=pause['resumed_counter']
        assert a[0]<a[1]<=b[0]==b[1]<=c[0]<c[1]
        points.append(dict(name=name,kernel_cache=kernel,fixture=fixture,pause={k:pause[k] for k in ('paused_interval_ns','measurement_status','frozen_guest_selected_unknown_pages')}))
    phases=[json.loads(line) for line in (raw/'diagnostic-events.jsonl').read_text().splitlines()]
    assert all(r['status']=='PASS' for r in phases)
    return dict(backend='trenvx',run_id=raw.name,status='PASS',trajectory_and_post_scan_task='PASS',cleanup=measured['cleanup'],
                timing_ns={k:measured[k] for k in ('guest_tools_ns','runner_calls_ns','task_and_final_checks_ns','full_run_ns')},
                guest_samples=measured['guest_samples'],host_samples=measured['host_samples'],
                phase_ns={r['phase']:r['elapsed_ns'] for r in phases},memory=memory(raw,'trenvx',True),points=points,
                source_sha256=cfg['kernel_cache_sources'],cache_policy='preserved host/guest caches; known upper-file fixture before replay; no global drop',
                caveat='Guest file-LRU excludes DAX filesystem content. Host image/snapshot cache remains separately reported; no direct memory-saving comparison with AgentENV.')


def report(raws,trenvx=None):
    runs=[];hashes={}
    for raw in raws:
        result=load(raw/'result.json');cfg=load(raw/'config.json')
        for name,digest in cfg['source_sha256'].items():assert sha256(raw/'code'/name)==digest
        row=dict(run_id=raw.name,status=result['status'],exp1=cfg['exp1'],full_run_ns=result['controller_elapsed_ns'],
            cleanup=result['checks']['cleanup'],source_sha256=cfg['source_sha256'],
            resources={key:cfg[key] for key in ('cpu','memory_mib','host_kernel','image','cache_policy')})
        assert row['cleanup']=='PASS'
        if row['status']=='FAIL':
            row['failure']=result['error']
            directory=raw/'file-inventory'
            if (directory/'kernel-file-types.json').exists():
                row['kernel_cache']=kernel_audit(directory,load(raw/'whole-cache/ram-attestation.json'))
                row['kernel_cache_caveat']='Frozen point audited independently; overall run failed. Post-analysis task checks were not completed.'
        else:
            assert all(v=='PASS' for v in result['checks'].values())
            from agentenv_whole_cache import recount as whole_recount
            interval=whole_recount(raw/'whole-cache');saved_interval=load(raw/'whole-cache/result.json')
            assert all(saved_interval[key]==value for key,value in interval.items())
            row['earlier_interval_measurement_status']=saved_interval['measurement_status']
            row['analysis_cost']={key:saved_interval[key] for key in ('controller_elapsed_ns','collector_cpu_user_seconds','collector_cpu_system_seconds','collector_process_maxrss_kib')}
            directory=raw/'file-inventory';saved=load(directory/'result.json');actual=recount(directory)
            assert all(actual[key]==value for key,value in saved.items())
            row['file_inventory']=actual
            if cfg.get('calibration_scope')=='frozen':row['frozen_fixture']=audit_frozen_fixture(directory)
            before=load(directory/'before.json');after=load(directory/'after.json')
            row['inventory_cost']={k:after[k] for k in ('guest_elapsed_ns','cpu_user_seconds','cpu_system_seconds','process_maxrss_kib') if k in after}
            row['inventory_cost']['before_guest_elapsed_ns']=before['guest_elapsed_ns']
            receipt=load(raw/'whole-cache/paused-boundary.json')
            assert receipt['status']==receipt['heartbeat_continuity']==receipt['original_vmm_identity']=='PASS'
            a=receipt['before_running_counter'];b=receipt['paused_counter'];c=receipt['resumed_counter']
            assert a[0]<a[1]<=b[0]==b[1]<=c[0]<c[1]
            row['pause']={key:receipt[key] for key in ('paused_interval_ns','measurement_status','frozen_guest_selected_unknown_pages','frozen_guest_selected_physical_bytes')}
            row['pause']['scope']='request through verified resumed heartbeat; includes API and verification'
            if cfg['exp1']:
                verify_replay(raw/'replay')
                for name in ('official-before.json','official-after.json'):
                    official=load(raw/name)
                    assert official['status']=='PASS' and official['exit_code']==0
                    assert official['parser_results']['num_tests_passed']==4 and official['parser_results']['num_tests_failed']==0
                assert (raw/'source-before.stdout').read_bytes()==(raw/'source-after.stdout').read_bytes()
                events=[json.loads(line) for line in (raw/'events.jsonl').read_text().splitlines()]
                assert all(event['exit_code']==0 for event in events)
                row['phase_ns']={e['phase']:e['end_monotonic_ns']-e['start_monotonic_ns'] for e in events}
                steps=list(csv.DictReader((raw/'replay/steps.tsv').open(),delimiter='\t'))
                row['guest_tools_ns']=sum(int(step['duration_ns']) for step in steps)
                from report_whole_analysis_controls import memory
                row['memory']=memory(raw,'agentenv',True)
                row['trajectory_and_post_scan_task']='PASS'
            if (directory/'kernel-file-types.json').exists():
                row['kernel_cache']=kernel_audit(directory,load(raw/'whole-cache/ram-attestation.json'))
        for name in ('config.json','result.json','file-inventory/result.json','file-inventory/before.json','file-inventory/after.json',
                     'file-inventory/frozen-page-structs.bin','file-inventory/frozen-typed-ram.bin','file-inventory/kernel-page-owners.json',
                     'file-inventory/kernel-file-types.json','file-inventory/allocation-owner-proof.json','file-inventory/allocation-owner-pages.bin','replay/actions.tsv','replay/patch.diff'):
            if (raw/name).exists():hashes[raw.name+'/'+name]=sha256(raw/name)
        runs.append(row)
    other=trenvx_report(trenvx) if trenvx is not None else None
    if other:
        for name in ('config.json','result.json','replay/actions.tsv','replay/patch.diff','whole-cache-files/frozen-page-structs.bin',
                     'whole-cache-files/frozen-typed-ram.bin','whole-cache-files/snapshot-page-fallback.json','whole-cache-files/kernel-file-types.json','whole-cache-files/allocation-owner-proof.json','whole-cache-files/allocation-owner-pages.bin'):
            hashes[trenvx.name+'/'+name]=sha256(trenvx/name)
    return dict(status='PASS',offline_recount='PASS',runs=runs,trenvx=other,evidence_sha256=hashes,
        units=dict(memory='bytes; MiB=2^20',pages='4096 bytes',time='nanoseconds; host and guest clocks separate'),
        acceptance_3='PARTIAL',limitations=[
            'Only internal PG_lru bit 4 defines the new file-LRU scope. Historical bit-5 points fail this scope check; raw receipts remain unchanged.',
            'Mode-zero inode-one is allocator metadata only with archived ext4 pointer proof. Regular-file pages are not all byte-verified or attributed to task paths.',
            'Host provider/metadata/shared-layer ownership and non-LRU file coverage are incomplete. Do not treat guest file-LRU as DAX content.',
            'New inspection has explicit pause/read costs; no matched performance pair for this variant.',
            'DAMON reclaim is enabled in actual boot config; its causal effect was not isolated.'])


def plot(value, output, backend='agentenv'):
    if output.exists():raise FileExistsError(output)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    row=next(r for r in reversed(value['runs']) if r['status']=='PASS' and r.get('kernel_cache',{}).get('cache_scope_status')=='PASS' and r['exp1']) if backend=='agentenv' else value['trenvx']
    fig,axes=plt.subplots(3,1,figsize=(10,10),constrained_layout=True)
    guest=row['memory']['periodic_guest']['samples']
    times=[(r['timestamp_ns']-guest[0]['timestamp_ns'])/1e9 for r in guest]
    for key in guest[0]['components_bytes']:
        axes[0].plot(times,[r['components_bytes'][key]/2**20 for r in guest],label=key)
    axes[0].plot(times,[r['nonfree_bytes']/2**20 for r in guest],color='black',label='nonfree')
    axes[0].set(title=backend+' Exp1: guest runner samples only',xlabel='Seconds since first guest sample',ylabel='Guest logical memory (MiB)')
    axes[0].legend(ncol=3,fontsize=7)
    host=row['memory']['host']['samples']
    keys=('pss_bytes','rss_bytes') if backend=='agentenv' else ('cgroup_current_bytes','cgroup_file_bytes','cgroup_anon_bytes','cgroup_kernel_bytes')
    for key in keys:
        axes[1].plot([r['elapsed_seconds'] for r in host],[r[key]/2**20 for r in host],label=key)
    axes[1].set(title='Owned VMM; shared service cgroup excluded' if backend=='agentenv' else 'Owned sandbox cgroup; orchestrator/scanner excluded',xlabel='Seconds since first host sample',ylabel='Host memory (MiB)')
    axes[1].legend(fontsize=8)
    physical=(row['kernel_cache'] if backend=='agentenv' else row['points'][-1]['kernel_cache'])['inode_type_unique_physical_bytes']
    axes[2].bar(list(physical),[v/2**20 for v in physical.values()])
    for i,(name,amount) in enumerate(physical.items()):axes[2].text(i,amount/2**20,f'{amount/2**20:.3f}',ha='center',va='bottom')
    axes[2].set(title='One paused file-LRU physical point, not a time series',ylabel='Unique host physical memory (MiB)')
    for axis in axes:axis.grid(axis='y',alpha=.2)
    fig.savefig(output);plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path);parser.add_argument('raw',type=Path,nargs='+')
    parser.add_argument('--plot',type=Path)
    parser.add_argument('--plot-trenvx',type=Path)
    parser.add_argument('--trenvx',type=Path)
    parser.add_argument('--trenvx-failure',type=Path)
    parser.add_argument('--closeout',type=Path)
    args=parser.parse_args();value=report(args.raw,args.trenvx)
    if args.trenvx_failure:
        failed=args.trenvx_failure;r=load(failed/'result.json');receipt=load(failed/'archive-manifest.json')
        assert r['status']=='FAIL' and not r['full_trajectory_verified']
        assert r['checks']['sdk_cleanup']==r['checks']['service_shutdown']=='PASS'
        assert 'unexpected actual TrEnv-X kernel asset' in r['error']
        value['trenvx_early_failure']=dict(run_id=failed.name,status='FAIL',stage='kernel configuration path before frozen replay',full_trajectory_verified=False,cleanup='PASS',
            reason='Actual kernel is a run-owned template alias, not the fixed global filename; hash verification was not reached.',
            archive_bytes=receipt['archive_bytes'],archive_sha256=receipt['archive_sha256'],gzip_test=receipt['gzip_test'],tar_compare=receipt['tar_compare'],archive_retained=(failed/'data.tar.gz').is_file())
    if args.closeout:
        receipt=load(args.closeout);assert receipt['status']=='PASS'
        data=receipt['data']
        minima=[v for k,v in data.items() if k.endswith('_minimum_observed_free_bytes')]
        value['closeout']=dict(status='PASS',raw_retained_bytes=data['raw_retained_bytes'],final_free_bytes=data['final_free_bytes'],minimum_observed_trenvx_free_bytes=min(minima),
            original_firecracker_count=len(data['firecracker_pids']),owned_resources_removed=True,reference_checkpoint_hash_valid=True,failed_image_retained=True,staged=False)
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    if args.plot:plot(value,args.plot)
    if args.plot_trenvx:plot(value,args.plot_trenvx,backend='trenvx')
    print(f'status=PASS runs={len(value["runs"])} result_file={args.output}')


if __name__=='__main__':main()
