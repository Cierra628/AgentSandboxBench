"""Offline audit of the command-scoped identity probe, with public DNS evidence."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import statistics

from guest_git_wait import audit_identity
from report_trenvx_git_wait import summarize as git_wait_summary


def socket_counts(path):
    samples=[json.loads(line) for line in path.read_text().splitlines()]
    assert samples[-1]['status']=='STOPPED'
    counts=collections.Counter();errors=0;polls=0
    for sample in samples[:-1]:
        for process in sample['observations']:
            if process['comm']!='git':continue
            for thread in process['threads']:
                descriptor=thread.get('poll_descriptor')
                if descriptor:
                    polls+=1
                    errors+=int('read_error' in descriptor)
                    if descriptor.get('protocol')=='udp' and descriptor.get('remote_port')==53:
                        counts['udp_dns_53']+=1
    return dict(total_samples=len(samples)-1,poll_samples=polls,udp_dns_53_samples=counts['udp_dns_53'],
                descriptor_read_errors=errors,probe_scan_total_ns=sum(s['scan_ns'] for s in samples[:-1]))


def summarize(raw):
    replay=git_wait_summary(raw)
    audited=audit_identity(raw/'identity-probe')
    assert audited==json.loads((raw/'identity-audit.json').read_text())
    downloaded=json.loads((raw/'identity-download.json').read_text())
    assert downloaded['status']=='PASS' and downloaded['probe_error'] is None
    assert hashlib.sha256((raw/'identity-probe.tar.gz').read_bytes()).hexdigest()==downloaded['archive_sha256']
    rows=[]
    for record in audited['records']:
        observed=socket_counts(raw/'identity-probe'/(record['prefix']+'.process.jsonl'))
        default=record['label'] in ('default','identity-default')
        assert observed['udp_dns_53_samples']>0 if default else observed['udp_dns_53_samples']==0
        rows.append(dict(label=record['label'],exit_code=record['exit_code'],command_result=record['command_result'],
            seconds=record['clock']['monotonic_raw_ns']/1e9,
            cpu_seconds=(record['clock']['user_ns']+record['clock']['system_ns'])/1e9,
            realtime_minus_monotonic_ns=record['clock']['realtime_ns']-record['clock']['monotonic_raw_ns'],
            sockets=observed))
    cfg=json.loads((raw/'config.json').read_text())
    assert cfg['git_identity_diagnostic'] and cfg['git_wait_diagnostic'] and not cfg['checkpoint_after']
    assert not cfg['guest_memory_sampling'] and not cfg['host_sample_interval_seconds']
    return dict(status='PASS',third_acceptance='PARTIAL',run_id=raw.name,
        scope='one owned VM; ABBA reset and identity-generation probes followed by original frozen replay',
        identity_probe=rows,reset_means_seconds={label:statistics.mean(r['seconds'] for r in rows[:4] if r['label']==label)
                                               for label in ('default','explicit')},
        state_checks='HEAD, index tree and working diff identical after each probe; reflog identities may differ',
        original_replay=dict(official_assertions=4,timing_ns=replay['timing_ns'],commands=replay['commands']),
        units='seconds, except explicit _ns fields; sample counts are observations, not syscall invocation counts',
        conclusion='Observed default Git identity generation waits on UDP DNS port 53; command-scoped identity removes the observed reset wait.',
        limits=['one VM diagnostic, not independent repeated performance trials',
                'original frozen runner still uses original identity environment',
                'no full-run speedup or new monitoring overhead estimate measured',
                'no memory sampling, physical page scans or checkpoint/restore impact measured',
                'legacy failed probe remains FAIL and its missing guest data cannot be reconstructed'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=summarize(args.raw.resolve(strict=True))
    with args.output.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(f'status=PASS third_acceptance=PARTIAL output={args.output}')
