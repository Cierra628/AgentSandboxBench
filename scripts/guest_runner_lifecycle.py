"""Diagnostic runner/collector boundaries and dual clock; frozen actions unchanged."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def replace_once(source,anchor,replacement):
    assert source.count(anchor)==1, 'unexpected lifecycle anchor'
    return source.replace(anchor,replacement)


def mark(tag):
    return f'printf \'{tag}\\t%s\\n\' "$EPOCHREALTIME" >> "$out/runner-lifecycle.tsv"\n'


def instrument(source,collector=False):
    source=replace_once(source,'set -euo pipefail\n','set -euo pipefail\nlifecycle_entry=$EPOCHREALTIME\n')
    anchor='mkdir -p "$out/stdout" "$out/stderr"\n'
    source=replace_once(source,anchor,anchor+'''printf 'entry\\t%s\\n' "$lifecycle_entry" > "$out/runner-lifecycle.tsv"
unset lifecycle_entry
''')
    if collector:
        anchor='  rm -f "$out/.memory-ready"\n'
        source=replace_once(source,anchor,mark('collector_start')+anchor)
        anchor='  test -s "$out/.memory-ready" || exit 1\n'
        source=replace_once(source,anchor,anchor+mark('collector_ready'))
        anchor='  trap - EXIT\n'
        source=replace_once(source,anchor,anchor+mark('collector_stop_start'))
        anchor='  exit "$original_rc"\n'
        source=replace_once(source,anchor,mark('collector_stop_end')+anchor)
    anchor='git -c user.name=asb-fixture -c user.email=asb-fixture@localhost -C /testbed add -A\n'
    assert source.count(anchor)==2
    source=source.replace(anchor,mark('baseline')+anchor,1)
    anchor='for action in "$actions"/*.sh; do'
    source=replace_once(source,anchor,mark('actions')+anchor)
    anchor='if [[ $end_step == 026 ]]; then'
    source=replace_once(source,anchor,mark('finalize')+anchor)
    return source+'\n'+mark('body_exit')


def collect(directory,collector,action_sum_ns,controller_ns=None):
    rows=[r.split('\t') for r in (directory/'runner-lifecycle.tsv').read_text().splitlines()]
    tags=['entry','baseline','actions','finalize','body_exit']
    if collector:tags=['entry','collector_start','collector_ready',*tags[1:],'collector_stop_start','collector_stop_end']
    assert [r[0] for r in rows]==tags and all(len(r)==2 for r in rows)
    times={tag:int(Decimal(value)*10**9) for tag,value in rows}
    assert all(a<=b for a,b in zip(times.values(),list(times.values())[1:])), 'nonmonotonic boundary clock'
    clock=json.loads((directory/'lifecycle-envelope.json').read_text())
    assert clock['exit_code']==0 and clock['monotonic_raw_ns']>0
    assert abs(clock['realtime_ns']-clock['monotonic_raw_ns'])<=2_000_000
    span=times[tags[-1]]-times['entry']
    assert span<=clock['realtime_ns']+2_000
    durations=dict(entry_to_baseline_ns=times['baseline']-times['entry'],
        baseline_ns=times['actions']-times['baseline'],
        action_loop_ns=times['finalize']-times['actions'],
        finalize_ns=times['body_exit']-times['finalize'],
        body_ns=times['body_exit']-times['entry'],
        collector_startup_ns=times['collector_ready']-times['collector_start'] if collector else 0,
        collector_stop_ns=times['collector_stop_end']-times['collector_stop_start'] if collector else 0)
    assert durations['action_loop_ns']>=action_sum_ns-2_000_000
    durations['loop_outside_action_timers_ns']=durations['action_loop_ns']-action_sum_ns
    result=dict(status='PASS',clock=clock,boundaries_ns=times,durations_ns=durations,
        envelope_minus_marked_ns=clock['realtime_ns']-span,collector_measured=collector,
        scope='diagnostic guest runner; shell builtin wall boundaries, outer monotonic raw/realtime duration; no checkpoint')
    if controller_ns is not None:
        assert controller_ns>=clock['monotonic_raw_ns']-2_000_000
        result['controller_minus_guest_envelope_ns']=controller_ns-clock['monotonic_raw_ns']
    return result


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_calibration(base):
    saved=json.loads((base/'result.json').read_text());assert saved['status']=='PASS' and saved['iterations']==32
    original=(base/'original.sh').read_text();assert digest(base/'original.sh')==saved['original_sha256']
    hashes=set();deltas=[]
    for index,row in enumerate(saved['runs']):
        assert row['enabled']==((index//4)%4 in (1,2))
        out=base/row['directory'];clock=json.loads((out/'outer-clock.json').read_text())
        expected=instrument(original) if row['enabled'] else original
        assert (out/'runner.sh').read_text()==expected.replace('/testbed',saved['fixture'])
        assert clock==row['clock'] and clock['exit_code']==0
        assert not (out/'stdout.log').read_bytes() and not (out/'stderr.log').read_bytes()
        steps=list(csv.DictReader((out/'replay/steps.tsv').read_text().splitlines(),delimiter='\t'))
        assert len(steps)==1 and steps[0]['step']=='025' and steps[0]['exit_code']=='0'
        hashes.add(digest(out/'replay/stdout/025.log'))
        delta=abs(clock['realtime_ns']-clock['monotonic_raw_ns']);assert delta<=2_000_000;deltas.append(delta)
        if row['enabled']:
            checked=collect(out/'replay',False,int(steps[0]['duration_ns']))
            assert checked==row['lifecycle']
        else:assert not (out/'replay/runner-lifecycle.tsv').exists()
    assert len(saved['runs'])==32 and len(hashes)==1
    return dict(status='PASS',iterations=32,max_clock_difference_ns=max(deltas),
        scope='small warm fixture validates boundary and outer clocks; no task monitoring threshold',performance_threshold=None)


def calibrate(inputs):
    base=inputs/'lifecycle-calibration';base.mkdir()
    original=(inputs/'lifecycle-original.sh').read_text()
    (base/'original.sh').write_text(original)
    fixture=base/'fixture';fixture.mkdir();(fixture/'sample').write_text('original\n')
    for argv in (['init','-q'],['config','user.name','fixture'],['config','user.email','fixture@localhost'],['add','-A'],['commit','-qm','fixture']):
        subprocess.run(['git','-C',str(fixture),*argv],check=True,capture_output=True,timeout=15)
    (fixture/'sample').write_text('updated\n')
    actions=base/'actions';actions.mkdir();shutil.copy2(inputs/'actions/025.sh',actions/'025.sh')
    shutil.copy2(inputs/'actions.tsv',base/'actions.tsv');rows=[]
    env=dict(os.environ,ACTIONS_DIR=str(actions),GUEST_MEMORY_SAMPLING='0',DROP_GUEST_CACHES='0',START_STEP='001',END_STEP='026')
    for index in range(32):
        # Four off/on/on/off blocks, four iterations at each position.
        enabled=(index//4)%4 in (1,2)
        directory=base/f'run-{index:02d}';directory.mkdir()
        source=instrument(original) if enabled else original
        runner=directory/'runner.sh';runner.write_text(source.replace('/testbed',str(fixture)))
        replay=directory/'replay';replay.mkdir();command=['bash',str(runner)]
        if enabled:command=[str(inputs/'command-clock'),str(replay/'lifecycle-envelope.json'),*command]
        run=subprocess.run([str(inputs/'command-clock'),str(directory/'outer-clock.json'),*command],
            env=dict(env,OUTPUT_DIR=str(replay)),capture_output=True,timeout=15)
        (directory/'stdout.log').write_bytes(run.stdout);(directory/'stderr.log').write_bytes(run.stderr)
        assert run.returncode==0
        row=dict(directory=directory.name,enabled=enabled,clock=json.loads((directory/'outer-clock.json').read_text()))
        if enabled:
            steps=list(csv.DictReader((replay/'steps.tsv').read_text().splitlines(),delimiter='\t'))
            row['lifecycle']=collect(replay,False,int(steps[0]['duration_ns']))
        rows.append(row)
    (base/'result.json').write_text(json.dumps(dict(status='PASS',iterations=32,runs=rows,
        original_sha256=digest(base/'original.sh'),fixture=str(fixture)),indent=2)+'\n')
    (base/'audit.json').write_text(json.dumps(audit_calibration(base),indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--calibrate',type=Path,required=True)
    calibrate(parser.parse_args().calibrate)
