"""Read-only replay, sampler and timing audit of the persistent collector study."""
import argparse
import hashlib
import json
from pathlib import Path
from report_memory_metrics import report
from run_persistent_metrics_comparison import summarize


def audit(directory):
    source=json.loads((directory/'result.json').read_text())
    assert source['status']=='PASS'
    protocol=source['protocol'];assert protocol['interval_seconds']==.1 and protocol['threshold'] is None
    hashes=json.loads((directory/'source-hashes.json').read_text())
    snapshots=sorted((directory/'code').iterdir())
    assert len(snapshots)==len(hashes)
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in snapshots]==list(hashes.values())
    result=dict(status='PASS',protocol=protocol,groups=[],memory=[],
                scope='replay correctness, recorded sampler operation and matched timing; not all acceptance criteria')
    identity=None
    for group,rows in enumerate(source['groups'],1):
        order=protocol['orders'][group-1]
        assert [row['label'] for row in rows]==order
        assert len(rows)==(4 if protocol['stage']=='smoke' else 12)
        if protocol['stage']=='balanced':assert order==order[::-1] and len(set(order))==6
        for row in rows:
            raw=Path(row['raw_dir']);audit_dir=Path(row['audit_dir']);case=row['label']
            cfg=json.loads((audit_dir/'effective-config.json').read_text())
            assert cfg['sample_interval_seconds']==.1 and cfg['drop_guest_caches']==0
            assert cfg['sandbox_cpu']==2 and cfg['sandbox_memory_mib']==4096
            assert cfg['fixed_frequency_window'] and cfg['damon_control_mode']=='damon_disabled'
            assert cfg['guest_sampling']==(case in ('guest','both'))
            assert cfg['host_sampling']==cfg['host_monitor_light']==(case in ('host','both'))
            observed=report(dict(backend='agentenv',mode='full',raw=str(raw),audit=str(audit_dir)))
            assert observed['guest_memory']['sample_count']==row['guest_sample_count']
            assert observed['host_memory']['sample_count']==row['sample_count']
            for key,phase in [('guest_action_sum_ns','guest_action_sum'),('controller_exec_ns','controller_runner_calls'),
                              ('controller_end_to_end_ns','full_run_including_cleanup')]:
                assert row[key]==observed['phase_breakdown'][phase]['duration_ns']
            assert int((raw/'task-window-duration-ns.txt').read_text())==row['task_window_ns']
            frequency=json.loads((raw/'fixed-frequency/result.json').read_text())
            restored=json.loads((raw/'fixed-frequency/frequency-policy/restored.json').read_text())
            assert frequency['status']==restored['status']=='PASS' and not restored['errors']
            for phase in ('prepare','before_task','after_task'):
                receipt=json.loads((raw/f'damon-{phase}.json').read_text())
                assert receipt['status']=='PASS' and receipt['mode']=='damon_disabled'
            metadata=dict(line.split('=',1) for line in (raw/'metadata.env').read_text().splitlines())
            fixed={k:metadata[k] for k in source['identity']}
            if identity is None:identity=fixed
            assert fixed==identity==source['identity']
            if case in ('guest','both'):
                receipt=json.loads((raw/'replay/collector-status.json').read_text())
                assert receipt==row['guest_collector'] and receipt['samples']==row['guest_sample_count']
                assert observed['guest_composition']['status']=='PASS'
                result['memory'].append(dict(group=group,case=case,raw_dir=str(raw),
                    guest_samples=row['guest_sample_count'],collector=receipt,
                    observed_peak=observed['guest_composition']['composition_at_observed_peak'],host=observed['host_memory']))
        result['groups'].append(dict(order=order,contrasts=summarize([rows])))
    result['analysis']=summarize(source['groups']);assert result['analysis']==source['analysis']
    result.update(replays=sum(map(len,source['groups'])),identity=identity,
        limitations=['One task, fixed frequency and DAMON disabled; shared CPUs and host cache remain',
            'Factorial differences are descriptive; host/guest interactions need not be additive',
            'Five-group log-ratio t intervals assume approximate independence; no mandatory performance threshold',
            'New runner variant, different from original sampler; historical groups are not pooled',
            'Collector RSS includes shared mappings; short actions may have no sample; observed peak only',
            'No physical scan, checkpoint, restore, complete host attribution or official task test in this comparison'])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();value=audit(args.directory.resolve())
    with args.output.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')
    print(f"status=PASS replays={value['replays']} output={args.output}")
