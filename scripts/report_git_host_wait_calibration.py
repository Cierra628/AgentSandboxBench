"""Recompute the eight-phase Git probe/timing calibration without running a VM."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def report(directory):
    r=json.loads((directory/'result.json').read_text())
    assert r['status']=='PASS' and r['cleanup']=='PASS'
    for name,digest in json.loads((directory/'inputs.json').read_text()).items():
        for parent in ('tools','guest'):
            assert hashlib.sha256((directory/parent/name).read_bytes()).hexdigest()==digest
    for name,digest in json.loads((directory/'host-inputs.json').read_text()).items():
        assert hashlib.sha256((directory/'host-sources'/name).read_bytes()).hexdigest()==digest
    rows=r['runs'];assert len(rows)==8
    protocol=json.loads((directory/'protocol.json').read_text())
    clock_calibration=protocol.get('clock_calibration',False)
    kvm=protocol.get('host_kvm_faults',False)
    assert not kvm or not clock_calibration
    if kvm:
        from owned_thread_wait import parse_kvm_faults,combined_wait_script
        functional=json.loads((directory/'functional.json').read_text())
        assert functional==r['functional'] and functional['status']=='PASS'
        counts=parse_kvm_faults((directory/'functional.stdout').read_text(),[x['tid'] for x in r['identity']['binding']])
        assert counts==functional['kvm_faults']
        assert r['functional_validation']==dict(try_event='PASS' if sum(x['try_async_get_page'] for x in counts['threads'])>0 else 'NOT_VALIDATED',async_setup='PASS' if sum(x['successful_async_setup'] for x in counts['threads'])>0 else 'NOT_VALIDATED')
    bash_times=protocol.get('bash_command_times',False)
    add_performance=protocol.get('add_performance',False)
    add_trace2=protocol.get('add_trace2',False)
    open_close=protocol.get('open_close',False)
    open_only=protocol.get('open_only',False)
    diskstats=protocol.get('guest_diskstats',False)
    cache_state=protocol.get('guest_cache_state',False)
    damon_state=protocol.get('guest_damon_state',False)
    assert not damon_state or cache_state
    damon_format=protocol.get('guest_damon_format','separate')
    assert not damon_state or damon_format in ('vmstat','separate')
    assert not cache_state or (add_trace2 and not diskstats and not kvm)
    assert not open_only or open_close
    assert not open_close or add_trace2
    assert not add_performance or (clock_calibration and bash_times)
    assert not add_trace2 or add_performance
    assert [(x['comparison'],x['label'],x['enabled']) for x in rows]==[tuple(x) for x in protocol['order']]
    reference=None
    for row in rows:
        raw=directory/'guest'/row['phase'];m=json.loads((raw/'result.json').read_text())
        assert m==row['measurement'] and m['status']=='PASS' and len(m['iterations'])==64
        assert m['phase_ns']>=m['command_total_ns']>0
        stdout=hashlib.sha256((raw/'stdout').read_bytes()).hexdigest()
        if reference is None:reference=stdout
        assert stdout==reference
        for i,item in enumerate(m['iterations']):
            assert item['iteration']==i and item['exit_code']==0 and item['stderr_bytes']==0
            assert item['stdout_sha256']==stdout
            assert json.loads((raw/f'clock-{i:03d}.json').read_text())==item['clock']
            assert item['clock']['exit_code']==0 and item['clock']['monotonic_raw_ns']>0
            if clock_calibration:
                iteration=raw/f'iteration-{i:03d}'
                assert hashlib.sha256((iteration/'stdout/025.log').read_bytes()).hexdigest()==stdout
                assert (iteration/'stderr/025.log').read_bytes()==b''
                names={p.name for p in iteration.glob('git-025-*') if p.name!='git-025-clocks.json'}
                suffix='time.tsv' if bash_times else 'clocks.json'
                expected={f'git-025-add-{suffix}',f'git-025-diff-{suffix}'} if row['enabled'] or add_performance else set()
                if add_performance and (row['enabled'] or add_trace2):expected.add('git-025-add-performance.txt')
                if add_trace2 and (row['enabled'] or open_close or diskstats or cache_state):expected.add('git-025-add-events.jsonl')
                if diskstats and row['enabled']:expected.update({'git-025-before.diskstats','git-025-after.diskstats'})
                if cache_state and (row['enabled'] or damon_state):expected.update({f'git-025-{phase}.{kind}' for phase in ('before','after') for kind in ('vmstat','meminfo')})
                if cache_state and (row['enabled'] or damon_state):
                    from guest_cache_state import collect
                    collect(iteration,include_damon=damon_state and row['enabled'])
                if damon_state and row['enabled'] and damon_format=='separate':expected.update({f'git-025-{phase}.damon' for phase in ('before','after')})
                assert names==expected
                if add_performance and (row['enabled'] or add_trace2):
                    from git_step_diagnostic import add_performance_records
                    records=add_performance_records((iteration/'git-025-add-performance.txt').read_text())
                    assert records==item['add_performance']
                    assert records['command_seconds']<=item['segments']['add'][0]+.003
                else:assert 'add_performance' not in item
                if add_trace2 and (row['enabled'] or open_close or diskstats or cache_state):
                    from git_step_diagnostic import add_trace2_records
                    trace=add_trace2_records((iteration/'git-025-add-events.jsonl').read_text(),item['add_performance'])
                    assert trace==item['add_trace2'] and trace['command_seconds']<=item['segments']['add'][0]+.003
                else:assert 'add_trace2' not in item
                if diskstats and row['enabled']:
                    from guest_diskstats import parse,delta
                    observed=delta(parse((iteration/'git-025-before.diskstats').read_text()),parse((iteration/'git-025-after.diskstats').read_text()))
                    if protocol.get('diskstats_validation')=='offline':assert 'guest_diskstats' not in item
                    else:assert observed==item['guest_diskstats']
                else:assert 'guest_diskstats' not in item
                if open_close and row['enabled']:
                    from git_step_diagnostic import open_close_records
                    assert open_close_records(iteration,open_only=open_only)==item['open_close']
                else:
                    assert not list(iteration.glob('directory-*.json')) and 'open_close' not in item
                if row['enabled'] or add_performance:
                    assert set(item['segments'])=={'add','diff'}
                    if bash_times:
                        for name,values in item['segments'].items():
                            assert len(values)==3 and all(math.isfinite(v) and v>=0 for v in values)
                            assert [float(v) for v in (iteration/f'git-025-{name}-time.tsv').read_text().split()]==values
                        assert sum(v[0] for v in item['segments'].values())<=item['clock']['monotonic_raw_ns']/1e9+.002
                    else:
                        for name,clock in item['segments'].items():
                            assert clock['exit_code']==0 and clock['monotonic_raw_ns']>0
                            assert json.loads((iteration/f'git-025-{name}-clocks.json').read_text())==clock
                        assert sum(c['monotonic_raw_ns'] for c in item['segments'].values())<=item['clock']['monotonic_raw_ns']
        assert m['command_total_ns']==sum(x['clock']['monotonic_raw_ns'] for x in m['iterations'])
        if clock_calibration:
            from git_step_diagnostic import instrument_clock_only
            original='''#!/bin/bash
set -eu
testbed="$1"; action="$2"; out="$3"; timeout_seconds=10; step=025
mkdir -p "$out/stdout" "$out/stderr"
set +e
  (cd /testbed && timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" bash "$action") \\
    > "$out/stdout/$step.log" 2> "$out/stderr/$step.log"
  status=$?
exit "$status"
'''
            assert (raw/'runner.sh').read_text()==instrument_clock_only(original,'/workspace/git-wait/command-clock',split_commands='bash' if bash_times and (row['enabled'] or add_performance) else row['enabled'],add_performance=add_performance and (row['enabled'] or add_trace2),add_trace2=add_trace2 and (row['enabled'] or open_close or diskstats or cache_state),guest_diskstats=diskstats and row['enabled'],guest_cache_state=cache_state and (row['enabled'] or damon_state),guest_damon_state=damon_state and row['enabled'],guest_damon_format=damon_format,open_close_library='/workspace/git-wait/directory-profile.so' if open_close and row['enabled'] else None).replace('cd /testbed','cd "$testbed"')
            assert not (raw/'events.jsonl').exists() and row['host_wait'] is None
        else:
            events=[json.loads(x) for x in (raw/'events.jsonl').read_text().splitlines()]
            starts=[e for e in events if e['event']=='start'];exits=[e for e in events if e['event']=='exit']
            assert [e['argv'] for e in starts]==[['git','add','-A'],['git','diff','--cached']]*64
            assert len(exits)==128 and all(e['code']==0 for e in exits)
            assert {e['sid'] for e in starts}=={e['sid'] for e in exits}
        if not clock_calibration and (row['enabled'] or kvm):
            wait=json.loads((directory/row['phase']).with_suffix('.json').read_text())
            assert wait==row['host_wait'] and wait['status']=='PASS'
            assert {x['tid'] for x in wait['threads']}=={x['tid'] for x in r['identity']['binding']}
            assert ('kvm_faults' in wait)==(kvm and row['enabled'])
            if kvm and row['enabled']:
                parsed=parse_kvm_faults((directory/row['phase']).with_suffix('.stdout').read_text(),[x['tid'] for x in r['identity']['binding']])
                assert parsed==wait['kvm_faults']
                assert (directory/row['phase']).with_suffix('.bt').read_text()==combined_wait_script((directory/'host-sources/trace_owned_vcpu_wait.bt').read_text(),(directory/'host-sources/trace_owned_vcpu_faults.bt').read_text())
        else:assert row['host_wait'] is None
    frequency=json.loads((directory/'fixed-frequency/result.json').read_text())
    assert frequency==r['frequency'] and frequency['status']=='PASS' and frequency['restoration']['status']=='PASS'
    differences={}
    for metric in ('command_total_ns','phase_ns'):
        values={}
        for comparison in ('aa','ab'):
            selected=[x for x in rows if x['comparison']==comparison]
            a=sum(x['measurement'][metric] for x in selected if x['label']=='A')/2
            b=sum(x['measurement'][metric] for x in selected if x['label']=='B')/2
            values[comparison]=100*(b/a-1)
        differences[metric]=values
    assert differences==r['differences_percent']
    gate='PASS' if all(abs(x['aa'])<=5 and x['ab']<=5 for x in differences.values()) else 'NOT_ACCEPTED'
    assert gate==r['diagnostic_gate']
    result=dict(status='PASS',diagnostic_gate=gate,checked_phases=8,checked_actions=512,
                differences_percent=differences,stdout_sha256=reference,
                source_sha256=hashlib.sha256((directory/'result.json').read_bytes()).hexdigest(),
                limits=['one mirrored group, no confidence interval',
                        'warm synthetic Git workload; native Git trace cost not measured',
                        'only incremental host probe overhead, not platform monitoring acceptance'])
    if clock_calibration:
        result['limits']=['one mirrored group, no confidence interval','warm synthetic Git workload; cannot guarantee cost on Exp1','only incremental command-clock wrappers, not platform monitoring acceptance']
        if bash_times:result['limits'][-1]='only incremental Bash command timing, not platform monitoring acceptance'
        if add_performance:result['limits'][-1]='only incremental add-native performance logging; Bash command timing in both conditions; not platform monitoring acceptance'
        if add_trace2:result['limits'][-1]='only incremental add Trace2 EVENT; native performance and Bash time in both conditions; not platform monitoring acceptance'
    if open_close:result['limits'][-1]='incremental add opendir/closedir libc timing only; directory reads and stat calls NOT_MEASURED; not platform monitoring acceptance'
    if open_only:result['limits'][-1]=result['limits'][-1].replace('opendir/closedir','opendir').replace('directory reads and stat','closedir, directory reads and stat')
    if diskstats:result['limits'][-1]='Incremental guest diskstats endpoints; guest-wide and includes logging; counters not pure waiting or host I/O; not platform monitoring acceptance'
    if cache_state:result['limits'][-1]='incremental vmstat/meminfo endpoints only; non-atomic guest-wide state and delayed accounting; not monitoring acceptance'
    if damon_state:result['limits'][-1]='incremental DAMON region-operation statistics; no precise physical reclamation attribution; not monitoring acceptance'
    if kvm:
        result['functional_validation']=r['functional_validation']
        result['limits'][-1]='Incremental KVM counters; queue probe in both conditions; no synchronous fault or worker latency measurement; not monitoring acceptance'
    (directory/'offline-report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'status=PASS gate={gate} checked_actions=512 result_dir={directory}')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);report(p.parse_args().directory)
