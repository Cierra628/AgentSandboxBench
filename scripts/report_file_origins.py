"""Offline declared-file source evidence and legacy named-page recheck."""
import argparse
import json
from pathlib import Path

from archive_trenvx_run_data import sha256
from named_guest_file_physical import recount
from run_trenvx_file_origins import ROOT, audit


def legacy_recheck():
    results=[]
    for run_id in ('20261001T144843Z-3014019','20261001T145206Z-3015223'):
        directory=ROOT/'.artifacts/vm-pfn-smoke'/run_id/'named-files'
        before=json.loads((directory/'before.json').read_text())
        after=json.loads((directory/'after.json').read_text())
        rows=json.loads((directory/'pages.json').read_text())
        checked=recount(before,after,rows,before['sandbox_id'])
        saved=json.loads((directory/'result.json').read_text())
        assert all(saved[key]==value for key,value in checked.items())
        present=[row for row in rows if row['entry']&(1<<63)]
        assert all(row['entry']&((1<<55)-1) for row in present)
        results.append(dict(run_id=run_id,status=checked['status'],selected_pages=len(rows),
            present_host_mappings=len(present),hidden_host_pfns=0,
            verified_file_cache_physical_bytes=checked['verified_file_cache_physical_bytes'],
            raw_sha256={name:sha256(directory/name) for name in ('before.json','after.json','pages.json','result.json')}))
    return results


def report(batch, fixture, failed_fixture):
    checked=audit(batch);saved=json.loads((batch/'result.json').read_text());raw=Path(saved['run']['raw_dir'])
    host=json.loads((fixture/'result.json').read_text());assert host['status']=='PASS'
    cfg=json.loads((fixture/'config.json').read_text())
    assert all(sha256(fixture/name)==digest for name,digest in cfg['sources'].items())
    content=json.loads((fixture/'content-result.json').read_text());assert content['status']=='PASS'
    cleanup=json.loads((fixture/'cleanup.json').read_text());assert not cleanup['errors'] and not cleanup['remaining_mounts']
    failed=json.loads((failed_fixture/'result.json').read_text());assert failed['status']=='FAIL'
    failed_rows=json.loads((failed_fixture/'metadata-result.json').read_text())['files']
    assert all(row['status']=='UNKNOWN' and 'trusted.overlay.uuid' in row['error'] for row in failed_rows)
    fields=('path','status','origin','content_verified','sha256','physical_memory')
    return dict(status='PASS',third_acceptance='PARTIAL',units=dict(time='ns',memory='bytes'),
        scope='file source checks plus defensive offline fix; no new physical-cache measurement',
        host_fixture=dict(status='PASS',kernel=cfg['host_kernel'],vm_count=0,
            scope=host['scope'],checks=host['checks'],source_sha256=cfg['sources'],
            metadata_elapsed_ns=json.loads((fixture/'metadata-result.json').read_text())['elapsed_ns'],
            content_elapsed_ns=content['elapsed_ns'],
            files=[{key:row[key] for key in fields if key in row} for row in content['files']]),
        first_host_failure=dict(status='FAIL',reason='root UUID identity xattr was conservatively rejected',
            correction='accept only a 16-byte UUID on a layer root directory; regression added',
            cleanup=failed['checks']['cleanup'],superseded_by_separate_run=True),
        trenvx=dict(status='PASS',origins=checked['source_proof'],
            action_manifest_sha256=saved['run']['action_manifest_sha256'],patch_sha256=saved['run']['patch_sha256'],
            source_sha256=json.loads((batch/'code/sha256.json').read_text()),
            image_sha256=saved['identity']['seed_sha256'],cpu=saved['identity']['cpu'],
            memory_mib=saved['identity']['memory_mib'],
            task_window_ns=saved['run']['task_window_ns'],guest_action_sum_ns=saved['run']['guest_action_sum_ns'],
            controller_end_to_end_ns=saved['run']['controller_end_to_end_ns'],
            checks=json.loads((raw/'result.json').read_text())['checks'],
            official_task_result=json.loads((raw/'official-task-result.json').read_text())['parser_results'],
            protocol=saved['protocol']),
        hidden_pfn_fix=dict(regression='present host mapping with PFN zero must be rejected, not counted as 4096 bytes',
            collector_already_rejects_hidden_pfns=True,
            patched_source_sha256=sha256(ROOT/'scripts/named_guest_file_physical.py'),
            legacy_raw_rechecks=legacy_recheck()),
        limitations=['Only nine host fixture paths and three real task files; no complete task inventory',
            'Host uses directory layers on kernel 6.19.9+; VM has one upper over base; no new multi-layer guest evidence',
            'No new upper-file physical pages, unmapped backing cache, multi-VM deduplication or checkpoint/restore impact',
            'Redirect/metacopy/symlink/data-only layers remain unknown and are not zero',
            'Metadata and content scans are non-atomic; content reads may warm caches',
            'One full functional replay with sampling disabled; timings are not monitoring-overhead contrasts',
            'Successful private images discarded with hash inventory; cannot reanalyse their memory or restore them'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path);parser.add_argument('--host-fixture',type=Path,required=True)
    parser.add_argument('--failed-fixture',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=report(args.batch,args.host_fixture,args.failed_fixture)
    with args.output.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')
    print(f'status=PASS third_acceptance=PARTIAL output={args.output}')
