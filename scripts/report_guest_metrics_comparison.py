"""Recheck matched guest/host collector runs; no invented acceptance threshold."""
import argparse
import hashlib
import json
from pathlib import Path

from report_memory_metrics import report
from run_exp1_monitor_balanced import estimate, schedule
from run_guest_metrics_comparison import comparisons

IDENTITY = ('workload_image', 'sandbox_cpu', 'sandbox_memory_mib',
            'action_manifest_sha256', 'aenv_version', 'host_kernel',
            'agentenv_container_image', 'agentenv_container_id',
            'overlaybd_io_engine', 'overlaybd_direct_io')


def audit(directories):
    directories=[path.resolve() for path in directories]
    assert directories and len(set(directories))==len(directories)
    groups=[];identity=None;hashes=None
    for directory in directories:
        source=json.loads((directory/'result.json').read_text())
        protocol=source['protocol']
        assert source['status']=='PASS' and protocol['stage']=='balanced'
        assert protocol['order']==schedule(protocol['seed'],1)[0]
        assert protocol['guest_sample_interval_seconds']==protocol['host_sample_interval_seconds']==.1
        assert protocol['threshold'] is None and protocol['physical_pages'].startswith('NOT_MEASURED')
        recorded=json.loads((directory/'source-hashes.json').read_text())
        if hashes is None:hashes=recorded
        assert recorded==hashes, 'collector or controller code changed across groups'
        snapshots=sorted((directory/'code').iterdir())
        assert len(snapshots)==len(recorded)
        assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in snapshots]==list(recorded.values())
        rows=source['runs'];assert len(rows)==8 and [r['label'] for r in rows]==protocol['order']
        memory=[]
        for row in rows:
            fixed={key:row['metadata'][key] for key in IDENTITY}
            if identity is None:identity=fixed
            assert fixed==identity
            raw=Path(row['raw_dir']); audit_dir=Path(row['audit_dir'])
            cfg=json.loads((audit_dir/'effective-config.json').read_text())
            assert cfg['fixed_frequency_window'] and cfg['damon_control_mode']=='damon_disabled'
            assert cfg['drop_guest_caches']==0 and cfg['sample_interval_seconds']==.1
            assert cfg['guest_sampling']==cfg['host_sampling']==(row['label']=='light')
            observed=report(dict(backend='agentenv',mode='full',raw=str(raw),audit=str(audit_dir)))
            assert observed['guest_memory']['sample_count']==row['guest_sample_count']
            assert observed['host_memory']['sample_count']==row['sample_count']
            assert observed['phase_breakdown']['guest_action_sum']['duration_ns']==row['guest_action_sum_ns']
            assert observed['phase_breakdown']['controller_runner_calls']['duration_ns']==row['controller_exec_ns']
            assert int((raw/'task-window-duration-ns.txt').read_text())==row['task_window_ns']
            frequency=json.loads((raw/'fixed-frequency/result.json').read_text())
            assert frequency['status']=='PASS'
            restored=json.loads((raw/'fixed-frequency/frequency-policy/restored.json').read_text())
            assert restored['status']=='PASS' and not restored['errors']
            for label in ('prepare','before_task','after_task'):
                damon=json.loads((raw/f'damon-{label}.json').read_text())
                assert damon['status']=='PASS'
            if row['label']=='light':
                assert observed['guest_composition']['status']=='PASS'
                memory.append(dict(raw=str(raw),samples=row['guest_sample_count'],
                    peak=observed['guest_composition']['composition_at_observed_peak'],
                    host=observed['host_memory']))
            else:assert observed['guest_composition']['status']=='NOT_MEASURED'
        groups.append(dict(result_dir=str(directory),seed=protocol['seed'],
            result_sha256=hashlib.sha256((directory/'result.json').read_bytes()).hexdigest(),
            comparison=comparisons(rows),memory=memory))
    analysis={}
    for metric in groups[0]['comparison']:
        analysis[metric]={
            'monitor':estimate([group['comparison'][metric]['light_vs_off_percent'] for group in groups]),
            'control':estimate([group['comparison'][metric]['control_B_vs_A_percent'] for group in groups])}
    return dict(status='PASS',scope='offline correctness, memory, timing and cleanup audit; no performance threshold',
        group_count=len(groups),full_replays=8*len(groups),identity=identity,groups=groups,analysis=analysis,
        limitations=['Joint original guest collector and host cgroup sampling at target interval 100 ms; impacts not separated',
            'Guest samples begin after initial Git preparation; per-step boundaries add samples to the periodic loop',
            'Five groups give approximate t intervals on group log ratios; shared-host load and dependence remain',
            'Task window includes collector startup and stop; full lifetime also includes fixed settling and transfers',
            'No physical scans, checkpoint or restore in these comparisons; full task physical pages remain unmeasured',
            'Independent SVG checks passed; official task tests not run in these AgentENV groups'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=audit(args.directories)
    with args.output.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')
    print(f"status=PASS full_replays={value['full_replays']} output={args.output}")
