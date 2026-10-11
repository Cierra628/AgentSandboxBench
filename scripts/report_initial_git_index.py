"""Audit saved index intervention results without starting sandboxes."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from diagnose_initial_git_index import index_debug,MODES,ORDER
from guest_damon_control import parse as parse_damon
from guest_diskstats import parse,delta
from git_step_diagnostic import add_performance_records,add_trace2_records

ROOT=Path(__file__).resolve().parents[1]


def stage_entries(data):
    result={}
    assert data.endswith(b'\0')
    for record in data.split(b'\0')[:-1]:
        meta,name=record.split(b'\t',1);name=os.fsdecode(name);mode,oid,number=meta.split()
        assert number==b'0' and len(oid)==40 and name not in result
        result[name]=dict(mode=mode.decode(),oid=oid.decode())
    return result


def audit(directory):
    source=json.loads((directory/'result.json').read_text());assert source['status']=='PASS'
    protocol=json.loads((directory/'protocol.json').read_text())
    for name,digest in json.loads((directory/'inputs.json').read_text()).items():
        assert hashlib.sha256((directory/'tools'/name).read_bytes()).hexdigest()==digest
        if name.endswith('.py') and name!='diagnose_initial_git_index.py':assert hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()==digest
    spec=importlib.util.spec_from_file_location('saved_index_collector',directory/'tools/diagnose_initial_git_index.py')
    collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
    index_debug=collector.index_debug
    stat_only=protocol.get('stat_only',False);full_stat=protocol.get('full_stat_rows',False)
    order=collector.STAT_ORDER if stat_only else collector.ORDER
    assert protocol['modes']==list(MODES) and protocol['index_order']==['first',*order]
    assert protocol['maximum_guests']==2 and protocol['periodic_monitoring'] is False
    assert [row['mode'] for row in source['guests']]==list(MODES)
    assert len({row['sandbox_id'] for row in source['guests']})==2
    result=dict(status='PASS',source_result_sha256=hashlib.sha256((directory/'result.json').read_bytes()).hexdigest(),
                result_dir=str(directory.relative_to(ROOT)),image=source['image'],guests=[],checks=dict(
                    index_objects='PASS',timing_and_io_recompute='PASS',tree='PASS',damon_state='PASS',cpu_restore='PASS',cleanup='PASS',
                    full_trajectory='NOT_RUN',monitoring_overhead='NOT_ACCEPTED',full_per_file_stat_recompute='PASS' if full_stat else 'NOT_AVAILABLE'))
    result.update(stat_only=stat_only,full_stat_rows=full_stat)
    trees=set()
    for row in source['guests']:
        mode=row['mode'];raw=directory/mode
        measured=json.loads((raw/'result.json').read_text());assert measured==row['measurement']
        assert row['status']==row['cleanup']=='PASS' and measured['status']=='PASS'
        freq=json.loads((directory/f'{mode}-frequency/result.json').read_text());assert freq==row['frequency']
        assert freq['status']==freq['restoration']['status']=='PASS'
        assert freq['restoration']['after']=={str(c):dict(scaling_min_freq='800000',scaling_max_freq='2801000',scaling_governor='schedutil') for c in (2,4,66,68)}
        assert freq['restoration']==json.loads((directory/f'{mode}-frequency/frequency-policy/restored.json').read_text())
        assert [r['cpu'] for r in freq['binding']]==[2,4]
        assert not any(r['sandboxID']==row['sandbox_id'] for r in json.loads((directory/f'{mode}-list-after.stdout').read_text()))
        for name,phase in (('prepare','prepare'),('before','before_task'),('after','after_task')):
            receipt=json.loads((directory/f'{mode}-{name}.json').read_text())
            assert receipt['status']=='PASS' and receipt['sandbox_id']==row['sandbox_id'] and receipt['exit_code']==0
            assert receipt['observations']==parse_damon(receipt['stdout'],mode,phase)
        old=stage_entries((raw/'original-stage.bin').read_bytes());new=stage_entries((raw/'updated-stage.bin').read_bytes())
        common=set(old)&set(new)
        assert measured['object_changes']==dict(added=len(set(new)-set(old)),removed=len(set(old)-set(new)),
            changed=sum(old[k]!=new[k] for k in common),unchanged=sum(old[k]==new[k] for k in common))
        for name,key in (('original','initial'),('updated','updated')):
            entries=index_debug((raw/f'{name}-debug.bin').read_bytes())
            assert set(entries)==set(old if name=='original' else new)
            stats=measured[key];assert stats['entries']==len(entries)
            assert stats['nonzero_flags']==sum(v['flags']!=0 for v in entries.values())
            if full_stat:
                actuals=json.loads((raw/f'{name}-actual-stat.json').read_text())
                assert collector.summarize_stat(entries,actuals)==stats
            for example in stats['examples']:
                assert example['index']==entries[example['path']]
                assert example['differences']==[k for k in example['actual'] if example['actual'][k]!=example['index'][k]]
        output=dict(mode=mode,git_version=measured['git_version'],config=measured['config'],original_head=measured['original_head'],
                    initial_stat=measured['initial'],updated_stat=measured['updated'],object_changes=measured['object_changes'],runs=[])
        if stat_only:
            assert measured['stat_only'] is True
            refreshed=stage_entries((raw/'refreshed-stage.bin').read_bytes());assert refreshed==old
            entries=index_debug((raw/'refreshed-debug.bin').read_bytes());assert set(entries)==set(old)
            actuals=json.loads((raw/'refreshed-actual-stat.json').read_text())
            assert collector.summarize_stat(entries,actuals)==measured['refresh']['stat']
            assert measured['refresh']['staged_objects_equal_original'] is True
            assert measured['refresh']['exit_code']==int((raw/'refresh-exit-code.txt').read_text())
            assert measured['refresh']['exit_code'] in (0,1) and not (raw/'refresh.stderr').read_bytes()
            clock=json.loads((raw/'refresh-clock.json').read_text());assert clock==measured['refresh']['clock']
            assert clock['exit_code']==measured['refresh']['exit_code'] and abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<2_000_000
            output['refresh']=measured['refresh']
        assert [r['index_source'] for r in measured['runs']]==['first',*order]
        for number,row_run in enumerate(measured['runs']):
            assert row_run['position']==number
            trial=raw/f"{number}-{row_run['index_source']}"
            assert not (trial/'stdout').read_bytes() and not (trial/'stderr').read_bytes()
            clock=json.loads((trial/'clock.json').read_text());assert clock==row_run['clock'] and clock['exit_code']==0
            assert abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<2_000_000
            argv=['git','-C','/testbed','add','-A']
            performance=add_performance_records((trial/'performance.txt').read_text(),argv)
            trace=add_trace2_records((trial/'events.jsonl').read_text(),performance,argv)
            disks=delta(*(parse((trial/f'{phase}.diskstats').read_text()) for phase in ('before','after')))
            assert performance==row_run['performance'] and trace==row_run['trace2'] and disks==row_run['diskstats']
            assert trace['command_seconds']*1e9<=clock['monotonic_raw_ns']+2_000_000
            trees.add(row_run['tree']);output['runs'].append(row_run)
        result['guests'].append(output)
    assert trees=={'94ab79f8caf3c568fead53aade5a784f41455d50'}
    result['tree']=trees.pop()
    result['limitations']=['Two intervention guests and five add calls each; not a full trajectory or performance estimate',
        'Initial inspection warms metadata; first add warms contents; default DAMON may evict during trials; no guaranteed warm cache',
        'Entire old/updated index is switched: stat fields, one changed object and one added path differ; not a single-field causal intervention',
        'Per-file lstat was aggregated with four examples; complete lstat rows were not retained, so full stat-count recomputation is unavailable',
        'Guest device-wide block counters are not process-owned bytes or host physical I/O; native timers may nest and omit work',
        'Probe overhead was not calibrated for natural replay; CPU policy requests do not guarantee exclusive or strictly fixed-frequency CPUs']
    if stat_only:
        result['checks']['refresh_staged_objects_equal_original']='PASS'
        result['limitations'][2]='Git --refresh preserves paths/modes/content objects/stages; index metadata and optional extensions can change; not a single-stat-field intervention'
    if full_stat:result['limitations'][3]='Complete per-file lstat rows retained; full stat aggregates recomputed; this does not identify individual fields causing Git work or where image metadata changed'
    check=directory/'final-resource-check.json'
    if check.exists():
        result['final_resource_check']=json.loads(check.read_text())
        assert result['final_resource_check']['status']=='PASS'
        assert result['final_resource_check']['project_sandboxes']==[] and result['final_resource_check']['owned_probe_processes']==[]
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    value=audit(args.directory.resolve());args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(f'status=PASS guests=2 add_calls=10 output={args.output}')
