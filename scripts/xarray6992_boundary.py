"""Capture the frozen Xarray step-22 boundary without executing task actions."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

PREFIX = ('read_repo', 'trace_index_state', 'core_patch')
NEXT = 'core_verify'
PIPE_STDOUT_SHA = '66a4fc50cddc48f5feac753a6dde7cc8f246d6ad6f9bcf74a60370ad04496f6d'


def verify(before, after, cfg):
    assert before == after, 'restored file state or execution progress changed'
    assert after['base_commit'] == cfg['base_commit']
    assert after['next_segment'] == NEXT and after['next_segment_absent'] is True
    expected = [dict(segment=s, step=f'{i:03}', exit_code=code)
                for s in PREFIX for i, code in enumerate(cfg['expected_exit_codes'][s], 1)]
    assert len(expected) == 22 and len(after['completed_steps']) == 22
    for observed, wanted in zip(after['completed_steps'], expected):
        if observed == wanted:
            continue
        assert wanted == dict(segment='trace_index_state', step='002', exit_code=0)
        assert observed == dict(wanted, exit_code=141)
        path = '/workspace/artifacts/exp5/backbone/trace_index_state/stdout/002.log'
        assert after['files_sha256'][path] == PIPE_STDOUT_SHA
    for filename in cfg['base_file_sha256']:
        assert '/testbed/' + filename in after['files_sha256']
    for segment in PREFIX:
        path = '/workspace/artifacts/exp5/backbone/' + segment + '/actions.tsv'
        assert after['files_sha256'][path] == cfg['input_sha256']['segments/' + segment + '/actions.tsv']
    return dict(status='PASS', checkpoint_after_global_step=22, continuation_global_step=23,
                next_segment=NEXT, files_compared=len(after['files_sha256']),
                scope='source, frozen inputs, completed prefix outputs and exact step-22 progress; next segment absent')


def capture(cfg, prefix=PREFIX, next_segment=NEXT, verify_state=verify, extra_files=()):
    progress = []
    roots = [Path('/workspace/mixfs-workload')]
    for segment in prefix:
        directory = Path('/workspace/artifacts/exp5/backbone') / segment
        with (directory / 'steps.tsv').open() as f:
            steps = list(csv.DictReader(f, delimiter='\t'))
        assert [r['step'] for r in steps] == [f'{i:03}' for i in range(1, len(steps) + 1)]
        progress.extend(dict(segment=segment, step=r['step'], exit_code=int(r['exit_code'])) for r in steps)
        roots.append(directory)
    files = [Path('/testbed') / name for name in (*cfg['base_file_sha256'],*extra_files)]
    for root in roots:
        assert root.is_dir()
        files.extend(p for p in root.rglob('*') if p.is_file())
    hashes = {}
    for p in sorted(set(files)):
        assert not p.is_symlink()
        hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    state = dict(base_commit=subprocess.check_output(['git', '-C', '/testbed', 'rev-parse', 'HEAD'], text=True).strip(),
                 git_status=subprocess.check_output(['git', '-C', '/testbed', 'status', '--porcelain'], text=True),
                 completed_steps=progress, next_segment=next_segment,
                 next_segment_absent=not (Path('/workspace/artifacts/exp5/backbone') / next_segment).exists(),
                 files_sha256=hashes)
    verify_state(state, state, cfg)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture(json.loads(args.config.read_text()))))
