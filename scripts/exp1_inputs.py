"""Validate frozen Exp1 inputs and compare the checkpoint's restored progress."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

MANIFEST_SHA256 = '6ea843b464f5f7ec3c081c539386593027ede2335c5309b945a9462feddceaa7'
PATCH_SHA256 = 'f1cc23ac4f5ac1cff87eb7f8c54f8176c1a47cec37fa4cb6907b9dd8ad199af2'
EXPECTED_FAILURES = {'008': 127, '016': 127}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkpoint_step(value):
    if value is None:
        return None
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 25:
        raise ValueError('checkpoint_after_step must be 001..025, or null for full replay')
    return f'{int(value):03d}'


def validate_trajectory(directory):
    if sha(directory/'actions.tsv') != MANIFEST_SHA256:
        raise ValueError('unsupported trajectory: this adapter requires frozen Prettier #14400')
    rows = [line.split('\t', 2) for line in (directory/'actions.tsv').read_text().splitlines()]
    expected = [f'{i:03d}' for i in range(1, 27)]
    if [row[0] for row in rows] != expected:
        raise ValueError('invalid action order')
    if sorted(p.name for p in (directory/'actions').glob('*.sh')) != [s+'.sh' for s in expected]:
        raise ValueError('missing or extra shell actions')
    hashes = {step: sha(directory/'actions'/f'{step}.sh') for step, _, _ in rows}
    if any(hashes[step] != digest for step, digest, _ in rows):
        raise ValueError('action content differs from frozen manifest')
    return dict(task_id='prettier__prettier-14400', action_count=26,
                manifest_sha256=MANIFEST_SHA256, action_sha256=hashes,
                cwd='/testbed', session_semantics='one bash per frozen action',
                expected_failures=EXPECTED_FAILURES, expected_patch_sha256=PATCH_SHA256)


def boundary_state(replay=Path('/workspace/artifacts/replay'), testbed=Path('/testbed')):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(testbed), *args], timeout=20)
    with (replay/'steps.tsv').open() as stream:
        rows = list(csv.DictReader(stream, delimiter='\t'))
    files = [replay/'steps.tsv', replay/'baseline-tree.txt', replay/'original-head.txt']
    files += [replay/kind/(row['step']+'.log') for row in rows for kind in ('stdout', 'stderr')]
    # Include both edited files and untracked task files; do not index or reset the task.
    names = set(git('diff', '--name-only', '-z', 'HEAD').decode().split('\0'))
    names.update(git('ls-files', '--others', '--exclude-standard', '-z').decode().split('\0'))
    names.update(('src/language-html/embed.js', 'src/language-html/utils/index.js'))
    task_files = {}
    for name in sorted(names - {''}):
        path = testbed/name
        if path.is_symlink():
            task_files[name] = {'symlink': str(path.readlink())}
        else:
            task_files[name] = sha(path) if path.is_file() else None
    return dict(steps=[r['step'] for r in rows],
                failures={r['step']: int(r['exit_code']) for r in rows if int(r['exit_code'])},
                head=git('rev-parse', 'HEAD').decode().strip(),
                diff_sha256=hashlib.sha256(git('diff', '--binary', 'HEAD')).hexdigest(),
                status_sha256=hashlib.sha256(git('status', '--porcelain=v1', '-z')).hexdigest(),
                replay_sha256={str(p.relative_to(replay)): sha(p) for p in files},
                task_files=task_files)


def verify_boundary(before, after, step):
    step = checkpoint_step(step)
    if step is None:
        raise ValueError('restore boundary requires a checkpoint step')
    if before['steps'] != [f'{i:03d}' for i in range(1, int(step)+1)]:
        raise ValueError('checkpoint progress does not match requested step')
    if before['failures'] != {k: v for k, v in EXPECTED_FAILURES.items() if k <= step}:
        raise ValueError('unexpected prefix failures')
    if not before['task_files'] or not before['replay_sha256'] or before != after:
        raise ValueError('restored files, outputs or progress differ from checkpoint')
    return dict(status='PASS', checkpoint_after_step=step,
                continuation_start_step=f'{int(step)+1:03d}', before=before, after=after,
                scope='completed outputs, replay progress, Git state and changed task files; process checked separately')


if __name__ == '__main__':
    if sys.argv[1:] != ['boundary']:
        raise SystemExit('usage: exp1_inputs.py boundary')
    print(json.dumps(boundary_state()))
