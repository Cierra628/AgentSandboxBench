"""Read saved GRPO boundary/periodic memory records; do not run guests or change sampling."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path

from sandbox_runtime_monitor import boundary_runner

ROOT=Path(__file__).resolve().parents[1]
FIELDS='MemTotal MemFree Buffers Cached Shmem AnonPages Active_file Inactive_file Dirty'.split()
PHASE_SETS=dict(without_baseline={'before','after','complete'},without_before={'baseline','after','complete'},
    without_after={'baseline','before','complete'},without_complete={'baseline','before','after'},
    without_segment_edges={'before','after'})


def read(path):return json.loads(path.read_text())
def rows(path):
    with path.open() as stream:return list(csv.DictReader(stream,delimiter='\t'))
def nonfree(row):return (int(row['MemTotal_kib'])-int(row['MemFree_kib']))*1024


def phase_check(memory,steps,role,segment):
    expected=[('baseline','000')]+[(phase,step['step']) for step in steps for phase in ('before','after')]+[('complete','999')]
    assert [(r['phase'],r['step']) for r in memory]==expected
    assert all(r['sandbox']==r['branch']==role and r['round']==segment for r in memory)
    assert all(all(int(r[k+'_kib'])>=0 for k in FIELDS) for r in memory)
    assert all(int(b['timestamp_ns'])>=int(a['timestamp_ns']) for a,b in zip(memory,memory[1:]))


def role_summary(boundary,periodic):
    assert all(int(b['timestamp_ns'])>=int(a['timestamp_ns']) for a,b in zip(boundary,boundary[1:]))
    peak=max(map(nonfree,boundary))
    ablation={name:peak-max(nonfree(r) for r in boundary if r['phase'] in phases) for name,phases in PHASE_SETS.items()}
    lo=min(int(r['timestamp_ns']) for r in boundary);hi=max(int(r['timestamp_ns']) for r in boundary)
    assert all(int(b['timestamp_ns'])>=int(a['timestamp_ns']) for a,b in zip(periodic,periodic[1:]))
    selected=[r for r in periodic if lo<=int(r['timestamp_ns'])<=hi];assert selected
    return dict(boundary_rows=len(boundary),boundary_nonfree_peak_bytes=peak,
        boundary_cached_peak_bytes=max(int(r['Cached_kib'])*1024 for r in boundary),
        peak_phases=sorted({r['phase'] for r in boundary if nonfree(r)==peak}),
        ablation_peak_loss_bytes=ablation,periodic_rows_in_boundary_span=len(selected),
        periodic_nonfree_peak_in_boundary_span_bytes=max(map(nonfree,selected)),
        periodic_cached_peak_in_boundary_span_bytes=max(int(r['Cached_kib'])*1024 for r in selected),
        boundary_minus_periodic_nonfree_peak_bytes=peak-max(map(nonfree,selected)))


def assess(source):
    assert source['status']=='PASS' and source['stage']=='complete' and source['comparison']=='boundary-vmstat'
    assert [r['boundary_vmstat_mode'] for r in source['runs']]==['keep','skip','skip','keep']
    runs=[]
    for saved in source['runs']:
        raw=ROOT/saved['raw'];task=read(raw/'result.json');cfg=read(raw/'effective-config.json');manifest=read(raw/'runtime-monitor.json')
        assert task['status']==manifest['status']=='PASS' and task['grpo_setup_schedule']=='per-guest-setup'
        assert manifest['profile']=='light' and manifest['interval_seconds']==.1 and len(manifest['sessions'])==7
        original=(raw/'frozen-input/guest-runner.original.sh').read_text()
        actual=(raw/'frozen-input/guest-runner.sh').read_text()
        skip=saved['boundary_vmstat_mode']=='skip'
        assert actual==boundary_runner(original,guest_phases=True,skip_vmstat=skip)
        # The current runner has exactly these calls, in this order around the original action.
        points=['  sample_memory before "$step"','  start_ns="$(python3 -c',
            '  (cd /testbed && timeout','  end_ns="$(python3 -c','  sample_memory after "$step"']
        assert all(actual.count(p)==1 for p in points)
        assert [actual.index(p) for p in points]==sorted(actual.index(p) for p in points)
        assert 'import time; print(time.monotonic_ns())' in actual
        memory_function=actual.split('sample_memory() {\n',1)[1].split('\n}\n',1)[0]
        assert memory_function.count('date +%s%N')==1 and memory_function.count('  awk ')==1
        assert memory_function.count('/proc/meminfo')==1
        phase_counts=Counter();role_data=[];actions=0;segments=0
        for role,path in cfg['executed_paths'].items():
            boundary=[]
            for segment in path:
                directory=raw/'rollouts'/role/segment;steps=rows(directory/'steps.tsv');memory=rows(directory/'guest-memory.tsv')
                phase_check(memory,steps,role,segment)
                boundary.extend(memory);phase_counts.update(r['phase'] for r in memory);actions+=len(steps);segments+=1
            sessions=[s for s in manifest['sessions'] if s['role']==role];assert len(sessions)==1
            session=sessions[0];periodic=rows(raw/session['raw_directory']/'guest-memory.tsv')
            assert all(r['role']==role and r['sandbox_id']==session['sandbox_id'] for r in periodic)
            assert len(periodic)==session['receipt']['samples']
            info=role_summary(boundary,periodic)
            assert info['boundary_nonfree_peak_bytes']==task['paths'][role]['guest_observed_peak_bytes']
            role_data.append(dict(role=role,**info))
        assert actions==189 and segments==35 and len(role_data)==7
        assert dict(phase_counts)==dict(baseline=35,before=189,after=189,complete=35)
        assert sum(phase_counts.values())==2*actions+2*segments==448
        runs.append(dict(index=saved['index'],boundary_vmstat_mode=saved['boundary_vmstat_mode'],
            raw=str(raw.relative_to(ROOT)),phase_counts=dict(phase_counts),actions=actions,segments=segments,
            boundary_meminfo_reads=448,boundary_date_calls=448,boundary_awk_calls=448,
            existing_action_timer_python_calls=2*actions,roles=role_data))
    all_roles=[r for run in runs for r in run['roles']]
    return dict(status='PASS_OFFLINE_ASSESSMENT',runs=runs,
        ablation_affected_roles={name:sum(r['ablation_peak_loss_bytes'][name]>0 for r in all_roles) for name in PHASE_SETS},
        ablation_maximum_peak_loss_bytes={name:max(r['ablation_peak_loss_bytes'][name] for r in all_roles) for name in PHASE_SETS},
        boundary_exceeds_periodic_roles=sum(r['boundary_minus_periodic_nonfree_peak_bytes']>0 for r in all_roles),
        maximum_boundary_minus_periodic_nonfree_peak_bytes=max(r['boundary_minus_periodic_nonfree_peak_bytes'] for r in all_roles),
        logical_curve='Periodic runtime sessions; MemTotal-MemAvailable and Cached; boundary rows not merged',
        boundary_peak='MemTotal-MemFree from runner boundaries; retained in task result and checked by verify_path/audit',
        comparison_scope='Common MemTotal-MemFree counter within each guest boundary wall-time span; no interpolation or cross-guest alignment',
        candidate=dict(status='DESIGN_ONLY_NOT_IMPLEMENTED',scope='Explicit skip-boundary-vmstat candidate only; defaults unchanged',
            reads_retained_per_run=448,before_after_reads_reusing_existing_timers=378,segment_edge_reads_unchanged=70,
            potential_external_date_calls_removed=378,potential_external_awk_calls_removed=378,
            potential_external_calls_removed_total=756,new_python_processes=0,performance='NOT_MEASURED'),
        new_live_guests=0,new_live_actions=0,runtime_settings_changed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=assess(read(args.source));args.output.mkdir(parents=True,exist_ok=False)
    value['source']=str(args.source)
    (args.output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
    data=[dict(index=run['index'],boundary_vmstat_mode=run['boundary_vmstat_mode'],
        **{k:v for k,v in role.items() if k not in ('peak_phases','ablation_peak_loss_bytes')},
        peak_phases=','.join(role['peak_phases']),**{name+'_loss_bytes':n for name,n in role['ablation_peak_loss_bytes'].items()})
        for run in value['runs'] for role in run['roles']]
    with (args.output/'roles.tsv').open('x') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(data[0]),delimiter='\t');writer.writeheader();writer.writerows(data)
    print('status='+value['status']+' result_dir='+str(args.output))


if __name__=='__main__':main()
