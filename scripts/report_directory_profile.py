"""Recheck raw fixture output and recompute the diagnostic overhead gate offline."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def report(directory):
    original = json.loads((directory / 'result.json').read_text())
    assert original['status'] == 'PASS' and original['cleanup'] == 'PASS'
    measured = json.loads((directory / 'guest/result.json').read_text())
    assert measured == original['calibration']
    inputs = json.loads((directory / 'inputs.json').read_text())
    for name, digest in inputs.items():
        for parent in ('tools', 'guest'):
            assert hashlib.sha256((directory / parent / name).read_bytes()).hexdigest() == digest
    protocol = json.loads((directory / 'protocol.json').read_text())
    assert protocol.get('coarse', False) == measured.get('coarse', False)
    if protocol.get('fixed_frequency', False):
        frequency = json.loads((directory / 'fixed-frequency/result.json').read_text())
        assert frequency == original['frequency'] and frequency['restoration']['status'] == 'PASS'
        assert frequency['status'] == 'PASS'
        for name, digest in json.loads((directory / 'host-inputs.json').read_text()).items():
            assert hashlib.sha256((directory / 'host-sources' / name).read_bytes()).hexdigest() == digest
    rows = measured['runs']
    expected = [f'warmup-{i}' for i in range(4)] + ['nonzero-exit']
    for group in range(5):
        for comparison in (('aa', 'ab') if group % 2 == 0 else ('ab', 'aa')):
            order = ('A', 'B', 'B', 'A') if group % 2 == 0 else ('B', 'A', 'A', 'B')
            expected.extend(f'{comparison}-{group}-{i}-{label}' for i, label in enumerate(order))
    assert [r['label'] for r in rows] == expected
    assert json.loads((directory / 'guest/progress.json').read_text()) == rows
    reference = None
    for row in rows:
        label = row['label']
        raw = directory / 'guest' / label
        output = (raw / 'stdout').read_bytes()
        assert hashlib.sha256(output).hexdigest() == row['stdout_sha256']
        assert (raw / 'stderr').read_bytes() == b''
        assert row['elapsed_ns'] > 0
        assert row['exit_code'] == (19 if label == 'nonzero-exit' else 0)
        enabled = label in ('warmup-1', 'warmup-2', 'nonzero-exit') or (label.startswith('ab-') and label.endswith('-B'))
        assert row['enabled'] == enabled
        value = json.loads(output)
        assert value['entries'] == 262144 and value['errno_checks'] == 'PASS'
        if reference is None:
            reference = value
        assert value == reference
        records = list(raw.glob('directory-*.json'))
        assert len(records) == int(enabled)
        if enabled:
            calls = json.loads(records[0].read_text())['calls']
            if measured.get('coarse', False):
                assert {k: v['count'] for k, v in calls.items()} == dict(opendir=8193, closedir=8192)
                assert calls['opendir']['null_with_errno'] == 1
                assert calls['closedir']['null_with_errno'] == 0
            else:
                assert {k: v['count'] for k, v in calls.items()} == dict(opendir=8193, closedir=8192, readdir=151552, readdir64=143360)
                assert calls['opendir']['null_with_errno'] == 1
                assert calls['readdir']['null_with_errno'] == 8192
                assert calls['readdir64']['null_with_errno'] == 0
                assert calls['readdir']['null_without_errno'] == calls['readdir64']['null_without_errno'] == 4096
            for v in calls.values():
                assert v['total_ns'] >= v['max_ns'] > 0
    differences = {}
    for comparison in ('aa', 'ab'):
        values = []
        for group in range(5):
            selected = [r for r in rows if r['label'].startswith(f'{comparison}-{group}-')]
            a = statistics.mean(r['elapsed_ns'] for r in selected if r['label'].endswith('-A'))
            b = statistics.mean(r['elapsed_ns'] for r in selected if r['label'].endswith('-B'))
            values.append(100 * (b / a - 1))
        assert values == measured[comparison + '_percent']
        differences[comparison] = values
    gate = 'PASS' if max(map(abs, differences['aa'])) <= 5 and max(differences['ab']) <= 5 else 'NOT_ACCEPTED'
    assert gate == measured['diagnostic_gate']
    result = dict(status='PASS', correctness='PASS', cleanup='PASS',
                  diagnostic_gate=gate, coarse=measured.get('coarse', False), checked_children=len(rows), differences_percent=differences,
                  input_hashes=inputs, source_result_sha256=hashlib.sha256((directory / 'result.json').read_bytes()).hexdigest(),
                  limits=['Synthetic warm directories, not Git or Exp1.',
                          ('Temporary CPU policy requested, not exclusive CPUs or proof of constant actual frequency.' if protocol.get('fixed_frequency', False) else 'No CPU frequency control; noisy AA comparisons retained.'),
                          'Normal process exit only; abnormal termination not covered.',
                          ('Only opendir/closedir libc duration; directory reads and stat calls NOT_MEASURED.' if measured.get('coarse', False) else 'Libc duration includes cached directory entries, not individual syscalls.'),
                          'null_with_errno is the observed errno, not necessarily a new error.'])
    (directory / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = report(args.directory)
    print(f"status={result['status']} gate={result['diagnostic_gate']} checked_children={result['checked_children']} result_dir={args.directory}")
