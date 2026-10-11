"""Offline recheck of the two short paused boundaries; no live API operations."""
import argparse
import json
from pathlib import Path

from archive_trenvx_run_data import sha256
from guest_whole_cache_probe import selected_flags
from measure_file_physical import PAGE, PFN_MASK, ram_address
from report_whole_analysis_controls import memory
from run_whole_analysis_controls import inspect
from trenvx_whole_cache import records, same_physical_mapping


def load(path):
    return json.loads(path.read_text())


def frozen(directory, backend):
    receipt=load(directory/'paused-boundary.json')
    assert receipt['status']=='PASS'
    assert receipt['original_vmm_identity']==receipt['heartbeat_continuity']=='PASS'
    before=receipt['before_running_counter'];paused=receipt['paused_counter'];resumed=receipt['resumed_counter']
    assert before[0]<before[1]<=paused[0]==paused[1]<=resumed[0]<resumed[1]
    assert 0<receipt['pause_interface_and_verification_ns']<receipt['paused_interval_ns']
    assert receipt['resume_interface_and_heartbeat_ns']>0
    selected=set(selected_flags((directory/'before-flags.bin').read_bytes(),load(directory/'before.json')['ram_ranges_pfns']))
    source=records(directory/('before-ram.bin' if backend=='agentenv' else 'ram-pages.bin'))
    final=records(directory/'frozen-ram-recheck.bin')
    assert {p for p,_,_ in source}=={p for p,_,_ in final}==selected
    assert len(source)==len(final)==len(selected)
    source={p:(a,b) for p,a,b in source}
    stable_files={};regions=[]
    if backend=='trenvx':
        for item in load(directory/'host-scan.json')['backing_files']:
            if item.get('role')!='vm-memory-snapshot':continue
            index=item['index'];a=(directory/f'backing-{index}-before.bin').read_bytes();b=(directory/f'backing-{index}-after.bin').read_bytes()
            assert len(a)==len(b)
            table={}
            for offset,first,last in records(directory/f'backing-{index}-pages.bin'):
                assert a[offset] and first&(1<<63) and first&(1<<61) and first&PFN_MASK
                if b[offset] and same_physical_mapping(first,last):table[offset]=first&PFN_MASK
            stable_files[index]=(table,{i for i,(first,last) in enumerate(zip(a,b)) if not first and not last})
            regions.extend((index,region) for region in item['regions'])
    proof=load(directory/'ram-attestation.json');pfns=set();changed=unknown=fallback=cold=0
    for pfn,first,last in final:
        initial,second=source[pfn];assert first==initial
        stable=same_physical_mapping(initial,second) and same_physical_mapping(initial,last)
        if not stable:changed+=1
        if not stable or first&(1<<62):unknown+=1;continue
        if first&(1<<63):
            assert first&PFN_MASK;pfns.add(first&PFN_MASK);continue
        if backend=='agentenv':unknown+=1;continue
        address=ram_address(pfn,proof['slots'])
        candidates=[(index,(address-r['start']+r['offset'])//PAGE) for index,r in regions
                    if r['start']<=address<r['end'] and r['permissions']=='rw-p']
        assert len(candidates)==1
        index,offset=candidates[0];table,nonresident=stable_files[index]
        if offset in table:pfns.add(table[offset]);fallback+=1
        elif offset in nonresident:cold+=1
        else:unknown+=1
    actual=dict(frozen_host_mapping_changed_pages=changed,frozen_guest_selected_unknown_pages=unknown,
                frozen_guest_selected_physical_bytes=len(pfns)*PAGE)
    assert all(receipt[key]==value for key,value in actual.items())
    if 'frozen_guest_snapshot_fallback_pages' in receipt:assert receipt['frozen_guest_snapshot_fallback_pages']==fallback
    assert receipt['measurement_status']==('PASS' if changed==unknown==0 else 'PARTIAL')
    return dict(receipt,**actual,selected_guest_pages=len(selected),snapshot_fallback_pages=fallback,
                proven_nonresident_pages=cold,offline_recount='PASS')


def report(raws):
    runs=[];hashes={}
    for backend,raw in zip(('agentenv','trenvx'),raws):
        row,identity=inspect(raw,backend,'on')
        cfg=load(raw/'config.json')
        assert cfg['paused_boundary' if backend=='agentenv' else 'paused_cache_boundary']
        source_hashes=cfg['source_sha256' if backend=='agentenv' else 'whole_cache_sources']
        for name,digest in source_hashes.items():
            source=raw/'code'/name if backend=='agentenv' else raw/name
            assert sha256(source)==digest
        pause=frozen(raw/'whole-cache',backend)
        pause['paused_interval_scope']='controller pause request start to resumed heartbeat verified; includes API and verification; exact frozen duration not measured'
        measured=memory(raw,backend,True)
        analysis=load(raw/'whole-cache/result.json')
        analysis_cost={key:analysis[key] for key in ('controller_elapsed_ns','collector_cpu_user_seconds','collector_cpu_system_seconds',
                      'collector_process_maxrss_kib','collector_scope') if key in analysis}
        phases={}
        if backend=='agentenv':
            for line in (raw/'events.jsonl').read_text().splitlines():
                event=json.loads(line);phases[event['phase']]=event['end_monotonic_ns']-event['start_monotonic_ns']
        else:
            for line in (raw/'diagnostic-events.jsonl').read_text().splitlines():
                event=json.loads(line);phases[event['phase']]=event['elapsed_ns']
        runs.append(dict(backend=backend,run_id=raw.name,status='PASS',resource_configuration={k:cfg[k] for k in ('cpu','memory_mib','host_kernel')},
            pause=pause,physical=row['physical'],timing_ns={key:row[key] for key in ('guest_tools_ns','runner_calls_ns','task_and_final_checks_ns','full_run_ns')},
            recorded_phase_ns=phases,analysis_cost=analysis_cost,memory=measured,cleanup=row['cleanup'],source_sha256=source_hashes,
            guest_samples=row['guest_samples'],host_samples=row['host_samples']))
        names=['config.json','result.json','replay/actions.tsv','replay/steps.tsv','replay/patch.diff','whole-cache/paused-boundary.json',
               'whole-cache/frozen-ram-recheck.bin','whole-cache/before-flags.bin','whole-cache/after-flags.bin','whole-cache/result.json']
        for name in names:hashes[f'{backend}/{raw.name}/{name}']=sha256(raw/name)
    return dict(status='PASS',protocol='one owned VM per backend; complete frozen Exp1 then paused host scan and resumed task checks',
        task='Prettier #14400, 26 frozen actions',rollout='single path, no checkpoint',runs=runs,evidence_sha256=hashes,
        units=dict(memory='bytes; MiB=2^20 bytes',timing='host monotonic nanoseconds; guest tool durations use guest clock',pages='4096 bytes'),
        cache_policy='existing host cache retained, no host or guest global drop; heartbeat variant separate from older controls',
        conclusion='Both selected pre-pause RAM sets remained stable; original VM heartbeat and post-scan functionality passed. Entire interval observations remain PARTIAL.',
        limitations=['One short run per backend, no matched on/off performance controls for this new paused variant.',
                     'Guest flags were exported before pause; no simultaneous guest flags and host PFNs; post-resume classifications may differ.',
                     'Shared image cache includes other users and metadata. Whole task inode-attributed contents remain null.',
                     'Pause freezes guest execution only; host cache and shared provider activity can still change.',
                     'Pause is an explicit analysis interruption; do not call it passive monitoring or zero overhead.',
                     'Guest logical, host cgroup/PSS and physical unions have different scopes and must not be added.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('agentenv',type=Path);parser.add_argument('trenvx',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();value=report((args.agentenv,args.trenvx))
    with args.output.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
    print(f'status=PASS runs=2 result_file={args.output}')


if __name__=='__main__':main()
