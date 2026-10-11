"""Small owned guest fixture: validate runner probes without a performance gate."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from guest_runner_phases import collect, instrument

ORDER = (False, False, False, False, False, True, True, False)
ITERATIONS = 4


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(base):
    saved = json.loads((base/'result.json').read_text())
    assert saved['status'] == 'PASS' and saved['order'] == list(ORDER)
    original = (base/'original.sh').read_text()
    assert digest(base/'original.sh') == saved['original_sha256']
    assert digest(base/'actions/025.sh') == saved['action_sha256']
    output_hashes = set(); differences = []; maxima = []
    for index, enabled in enumerate(ORDER):
        directory = base/f'phase-{index}'
        # The original fixture path is recorded because offline extraction moves it.
        expected = instrument(original, baseline_commands=True) if enabled else original
        assert (directory/'runner.sh').read_text() == expected.replace('/testbed', saved['fixture'])
        rows = saved['phases'][index]['iterations']
        assert len(rows) == ITERATIONS
        for number, row in enumerate(rows):
            out = directory/f'iteration-{number}'
            clock = json.loads((directory/f'clock-{number}.json').read_text())
            assert not (directory/f'stdout-{number}.log').read_bytes()
            assert not (directory/f'stderr-{number}.log').read_bytes()
            assert clock == row['clock'] and clock['exit_code'] == 0 and clock['monotonic_raw_ns'] > 0
            delta = abs(clock['realtime_ns'] - clock['monotonic_raw_ns'])
            assert delta <= 2_000_000  # clock consistency tolerance, not an overhead target
            maxima.append(delta)
            steps = list(csv.DictReader((out/'steps.tsv').read_text().splitlines(), delimiter='\t'))
            assert len(steps) == 1 and steps[0]['step'] == '025' and steps[0]['exit_code'] == '0'
            assert not (out/'stderr/025.log').read_bytes() and not (out/'patch.diff').read_bytes()
            output_hashes.add(digest(out/'stdout/025.log'))
            if enabled:
                assert collect(out, int(steps[0]['duration_ns']), baseline_commands=True) == row['phases']
            else:
                assert not (out/'runner-phases.tsv').exists() and not (out/'runner-envelope.json').exists()
    assert len(output_hashes) == 1
    assert len(saved['phases']) == len(ORDER)
    for start, name in ((0, 'identical_off'), (4, 'off_on_on_off')):
        totals = [sum(r['clock']['monotonic_raw_ns'] for r in saved['phases'][i]['iterations'])
                  for i in range(start, start+4)]
        differences.append(dict(comparison=name, difference_percent=100*((totals[1]+totals[2])/(totals[0]+totals[3])-1)))
    return dict(status='PASS', iterations=ITERATIONS*len(ORDER), max_clock_difference_ns=max(maxima),
                differences=differences, performance_threshold=None,
                scope='small warm fixture; function and clock calibration only; no formal monitoring acceptance',
                result_sha256=digest(base/'result.json'))


def guest(inputs):
    base = inputs/'calibration'; base.mkdir()
    shutil.copy2(inputs/'guest-runner-original.sh', base/'original.sh')
    fixture = base/'fixture'; fixture.mkdir()
    for i in range(16):
        directory = fixture/f'd{i:02}'; directory.mkdir()
        for j in range(16):
            (directory/f'f{j:02}').write_text('original\n')
    for argv in (['init','-q'], ['config','user.name','fixture'], ['config','user.email','fixture@localhost'],
                 ['add','-A'], ['commit','-qm','fixture']):
        subprocess.run(['git','-C',str(fixture)]+argv, check=True, capture_output=True, timeout=15)
    (fixture/'d00/f00').write_text('updated\n')
    actions = base/'actions'; actions.mkdir()
    shutil.copy2(inputs/'actions/025.sh', actions/'025.sh')
    shutil.copy2(inputs/'actions.tsv', base/'actions.tsv')
    original = (base/'original.sh').read_text(); phases = []
    env = dict(os.environ, ACTIONS_DIR=str(actions), GUEST_MEMORY_SAMPLING='0', DROP_GUEST_CACHES='0',
               START_STEP='001', END_STEP='026')
    started = time.monotonic_ns()
    for index, enabled in enumerate(ORDER):
        directory = base/f'phase-{index}'; directory.mkdir()
        source = instrument(original, baseline_commands=True) if enabled else original
        runner = directory/'runner.sh'; runner.write_text(source.replace('/testbed', str(fixture)))
        rows = []
        for number in range(ITERATIONS):
            out = directory/f'iteration-{number}'; clock = directory/f'clock-{number}.json'
            command = ['bash',str(runner)]
            if enabled:
                command = [str(inputs/'command-clock'),str(out/'runner-envelope.json')]+command
            p = subprocess.run([str(inputs/'command-clock'),str(clock)]+command,
                               env=dict(env, OUTPUT_DIR=str(out)), capture_output=True, timeout=15)
            (directory/f'stdout-{number}.log').write_bytes(p.stdout)
            (directory/f'stderr-{number}.log').write_bytes(p.stderr)
            assert p.returncode == 0 and not p.stdout and not p.stderr
            row = dict(clock=json.loads(clock.read_text()))
            if enabled:
                steps = list(csv.DictReader((out/'steps.tsv').read_text().splitlines(), delimiter='\t'))
                row['phases'] = collect(out, int(steps[0]['duration_ns']), baseline_commands=True)
            rows.append(row)
        phases.append(dict(enabled=enabled, iterations=rows))
    result = dict(status='PASS', order=list(ORDER), fixture=str(fixture), phases=phases,
                  total_ns=time.monotonic_ns()-started, original_sha256=digest(base/'original.sh'),
                  action_sha256=digest(actions/'025.sh'), fixture_files=256,
                  cache_policy='no cache drop; host and guest defaults preserved')
    (base/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    checked = audit(base)
    (base/'audit.json').write_text(json.dumps(checked, indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--guest', type=Path, required=True)
    guest(parser.parse_args().guest)
