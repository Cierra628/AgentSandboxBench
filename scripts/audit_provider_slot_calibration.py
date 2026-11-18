"""Audit the observed no-stable-window boundary without rerunning any guest."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import statistics
import struct

from audit_lower_cache import check_point
from audit_snapshot_cache import audit
from report_live_task_cache import recount


def verify(storage):
    receipt=json.loads((storage/'result.json').read_text())
    assert receipt['status']=='PASS'
    source=storage/'guest-cache';directory=source/'live-cache/restored'
    fixture=audit(source)
    points=recount(directory)
    identity=json.loads((directory/'provider/identity.json').read_text())
    assert identity['binary_sha256']==receipt['provider_binary']['sha256']
    slots=identity['slots'];assert len(slots)==16
    assert all(r['nr_queues']==1 and r['queue_depth']==16 and r['max_io_buf_bytes']==256*1024 for r in slots)
    assert len({r['image_config'] for r in slots})==len({r['dev_id'] for r in slots})==1
    reads=[];states=set();cost=[]
    for point in points:
        prefix=directory/f"pair-{point['point']:04}"
        raw=json.loads(Path(str(prefix)+'-provider.json').read_text())
        stable=valid=0
        for first,last in zip(raw['first']['rows'],raw['last']['rows'],strict=True):
            rows=[first['before'],first['after'],last['before'],last['after']]
            stable_row=rows.count(rows[0])==4 and rows[0][1]%2==0
            stable+=stable_row
            valid+=stable_row and rows[0][6]==3 and 0<rows[0][5]<=rows[0][3]
            states.update(row[6] for row in rows)
        assert stable==point['provider_stable_slots'] and valid==point['provider_valid_slots']
        # This audit intentionally checks the observed boundary. A future positive
        # attribution run needs an independent byte-range audit, not this assertion.
        assert valid==0, 'positive provider coverage is outside this no-window audit'
        assert point['host_provider_current_bytes']==point['host_provider_old_snapshot_bytes']==0
        three,pfns=check_point(directory,point)
        assert point['host_four_current_union_bytes']==len(pfns)*4096==three['host_three_current_union_bytes']
        assert point['host_four_unconfirmed_guest_capacity_bytes']==three['host_three_unconfirmed_guest_capacity_bytes']
        assert point['provider_resolved_guest_pages']==0
        assert point['provider_unattributed_resident_bytes']==point['provider_whole_allocation_resident_bytes']
        # Independent allocation-residency count, deliberately excludes edge pages.
        resident=set();edge=set()
        for first,last in zip(raw['first']['rows'],raw['last']['rows'],strict=True):
            address,capacity=first['before'][2:4]
            for i,(x,y) in enumerate(zip(first['pagemap'],last['pagemap'],strict=True)):
                if x!=y or not x>>63 or not x&((1<<55)-1):continue
                va=first['first_address']+i*4096
                target=resident if address<=va and va+4096<=address+capacity else edge
                target.add(x&((1<<55)-1))
        assert len(resident)*4096==point['provider_whole_allocation_resident_bytes']
        assert len(edge)==point['provider_mixed_or_edge_resident_pages']
        reads.append(sum(row['after'][7] for row in raw['last']['rows']))
        cost.append(dict(cpu_ns=raw['first']['cpu_ns']+raw['last']['cpu_ns'],
            elapsed_ns=raw['first']['elapsed_ns']+raw['last']['elapsed_ns']))
    released=[]
    for path in sorted((storage/'service-provider-slots').glob('*.bin')):
        row=struct.unpack('<8Q',path.read_bytes()[:64]);released.append(row)
    assert len(released)==16 and all(row[6]==5 and row[5]==0 and row[1]%2==0 for row in released)
    final_reads=sum(row[7] for row in released)
    assert final_reads>0
    lines=next(p for p in (storage/'service-logs/serial').glob('*/firecracker.log') if 'Paused' in p.read_text()).read_text().splitlines()
    def ns(line):
        sec,frac=line.split()[0].split('.')
        return int(datetime.fromisoformat(sec).replace(tzinfo=timezone.utc).timestamp())*10**9+int(frac.ljust(9,'0'))
    pause_request=next(ns(x) for x in lines if 'received a Patch request' in x and 'Paused' in x)
    pause_done=next(ns(x) for x in lines if "'pause vm' VMM action" in x)
    resume_request=next(ns(x) for x in lines if 'received a Patch request' in x and 'Resumed' in x and ns(x)>pause_request)
    resume_done=next(ns(x) for x in lines if "'resume vm' VMM action" in x and ns(x)>resume_request)
    events=json.loads((source/'events.json').read_text())
    samples={name:[json.loads(line) for line in (source/'live-cache'/name/'samples.jsonl').read_text().splitlines()]
             for name in ('parent','restored')}
    session_states={name:json.loads((source/'live-cache'/name/'result.json').read_text()) for name in samples}
    keys=['guest_cached_bytes','host_vmm_confirmed_bytes','host_current_union_bytes','host_lower_current_bytes',
          'host_three_current_union_bytes','host_four_current_union_bytes','host_four_unconfirmed_guest_capacity_bytes',
          'host_provider_current_bytes','provider_unattributed_resident_bytes','provider_mixed_or_edge_resident_pages',
          'host_provider_old_snapshot_bytes','provider_valid_slots','provider_stable_slots']
    return dict(status='PASS',coverage='NO_STABLE_VALID_WINDOW',storage_run=storage.name,
        fixture_checks=fixture['calibration_phases'],binary_sha256=identity['binary_sha256'],
        sample_counts={name:len(rows) for name,rows in samples.items()},slot_count=16,
        allocation_capacity_bytes=sum(r['max_io_buf_bytes'] for r in slots),
        metadata_table_file_bytes=16*4096,complete_reads_after_release=final_reads,
        sampled_complete_read_counts=reads,sampled_content_states=sorted(states),
        initial={key:points[0][key] for key in keys},
        restored_ranges={key:[min(p[key] for p in points),max(p[key] for p in points)] for key in keys},
        native_checkpoint_pause_bounds_ns=[resume_request-pause_done,resume_done-pause_request],
        checkpoint_interface_ns=next(e['duration_ns'] for e in events if e['stage']=='checkpoint'),
        observation_gap_ns=samples['restored'][0]['timestamp_ns']-samples['parent'][-1]['timestamp_ns'],
        costs=dict(provider_query_cpu_mean_ns=statistics.mean(c['cpu_ns'] for c in cost),
            provider_query_elapsed_mean_ns=statistics.mean(c['elapsed_ns'] for c in cost),
            restored_pair_cpu_mean_ns=statistics.mean(p['host_thread_cpu_ns'] for p in points),
            restored_pair_elapsed_mean_ns=statistics.mean(p['host_elapsed_ns'] for p in points),
            session_thread_cpu_ns={name:state['host_thread_total_cpu_ns'] for name,state in session_states.items()},
            monitoring_impact='NOT_CONTROLLED; dev daemon; no slowdown percentage'),
        metadata_counters={key:max(p[side][key] for group in samples.values() for p in group
            for side in ('metadata_before','metadata_after')) for key in ('duplicate_adds','missing_removes','out_of_range','moved_pages','stale_records')},
        checks=dict(content_and_process_restore='PASS',three_level_independent_recount='PASS',
            no_valid_window_independent_check='PASS',allocation_residency_independent_recount='PASS',
            all_slots_released_invalid='PASS',positive_provider_attribution_live='NOT_OBSERVED'),
        storage_budget=receipt['budget'],host_available_min_bytes=receipt['observed_host_available_min_bytes'],
        cleanup=receipt['cleanup'],raw_sha256=fixture['raw_sha256'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('storage',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=verify(args.storage.resolve())
    with args.output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print('status=PASS coverage='+result['coverage']+' result_dir='+str(args.output.parent))
