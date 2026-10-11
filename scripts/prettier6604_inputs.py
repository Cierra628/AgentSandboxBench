"""Validate the pinned Exp3 Prettier backbone without contacting a model."""
import base64
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT/'configs/prettier6604-backbone.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay_command(command):
    if '\n' not in command and '\t' not in command:
        return command
    encoded = base64.b64encode(command.encode()).decode()
    return f"printf '%s' '{encoded}' | base64 -d | bash -o pipefail"


def validate(config=CONFIG):
    cfg = json.loads(config.read_text())
    assert cfg['task_id'] == 'prettier__prettier-6604'
    assert cfg['base_commit'] == 'affa24ce764bd14ac087abeef0603012f5c635d6'
    assert cfg['path'] == ['read_repo','read_typescript','core_patch','core_verify','backbone_finish']
    assert cfg['guest_cpu'] == 2 and cfg['guest_memory_mib'] == 4096
    assert cfg['sample_interval_seconds'] == .1 and cfg['action_timeout_seconds'] == 600
    workload = ROOT/cfg['workload']
    assert workload.resolve() == ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/prettier-6604-rl-fork'
    task = json.loads((workload/'task.json').read_text())
    assert task['source_image'] == cfg['source_image'] and task['base_commit'] == cfg['base_commit']
    assert cfg['source_image'].endswith('@sha256:8159d38fcbd6d5f09402d93d3e15cf2a891058ddd835a224f1d6b3357a87cc94')
    for filename, key in [('agent-sampling.json','sampling_sha256'),('rollouts.json','rollouts_sha256'),('guest-runner.sh','runner_sha256')]:
        assert sha(workload/filename) == cfg[key], filename
    sample = json.loads((workload/'agent-sampling.json').read_text())
    selected = {s['segment']:s for s in sample['segments'] if s['role']=='backbone'}
    count = 0
    for segment in cfg['path']:
        manifest = workload/'segments'/segment/'actions.tsv'
        assert sha(manifest) == cfg['manifest_sha256'][segment]
        with manifest.open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
        actions = selected[segment]['actions']
        assert len(rows) == len(actions)
        assert [a['exit_code'] for a in actions] == cfg['expected_exit_codes'][segment]
        for index,(row,action) in enumerate(zip(rows,actions),1):
            assert row['step'] == f'{index:03}'
            assert row['command'] == replay_command(action['command'])
            assert hashlib.sha256(row['command'].encode()).hexdigest() == row['sha256']
        count += len(rows)
    assert count == 29
    output = selected['backbone_finish']['actions'][0]['output']
    reference = output[output.index('diff --git a/src/'):output.index('=== new test files ===')]
    assert hashlib.sha256(reference.encode()).hexdigest() == cfg['reference_patch_sha256']
    return cfg,workload,reference
