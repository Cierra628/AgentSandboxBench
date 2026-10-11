"""Audit seven frozen paths and export role-specific manifests without resampling."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from prettier6604_inputs import replay_command, sha, validate


def sampled_segment(samples, role, segment):
    exact = [s for s in samples if s['role'] == role and s['segment'] == segment]
    candidates = exact or [s for s in samples if s['role'] == 'backbone' and s['segment'] == segment]
    if len(candidates) != 1:
        raise ValueError(f'ambiguous or missing sampled segment: {role}/{segment}')
    return candidates[0]


def rows_for(sample):
    rows = []
    for i, action in enumerate(sample['actions'], 1):
        command = replay_command(action['command'])
        rows.append(dict(step=f'{i:03}', sha256=hashlib.sha256(command.encode()).hexdigest(), command=command))
    return rows


def audit(workload, samples, rollouts, original_names=None):
    mismatches = []
    paths = {}
    for role, path in rollouts.items():
        count = 0
        exits = []
        for segment in path:
            original = (original_names or {}).get(segment, segment)
            sample = sampled_segment(samples, role, original)
            with (workload/'segments'/segment/'actions.tsv').open() as stream:
                reader = csv.DictReader(stream, delimiter='\t')
                if reader.fieldnames != ['step', 'sha256', 'command']:
                    raise ValueError('invalid manifest header: '+segment)
                actual = list(reader)
            for i, row in enumerate(actual, 1):
                if row['step'] != f'{i:03}' or row['sha256'] != hashlib.sha256(row['command'].encode()).hexdigest():
                    raise ValueError('invalid manifest action: '+segment+'/'+str(i))
            expected = rows_for(sample)
            count += len(actual)
            exits.extend(action['exit_code'] for action in sample['actions'])
            if actual != expected:
                mismatches.append(dict(role=role, segment=segment, sampled_role=sample['role'],
                                       actual_count=len(actual), expected_count=len(expected),
                                       differing_steps=[f'{i+1:03}' for i in range(max(len(actual), len(expected)))
                                                        if i >= len(actual) or i >= len(expected) or actual[i] != expected[i]]))
        paths[role] = dict(segment_count=len(path), action_count=count, sampled_expected_exit_codes=exits)
    return dict(status='FAIL' if mismatches else 'PASS',
                scope='offline action equality; no live replay or task correctness claim',
                paths=paths, mismatches=mismatches,
                logical_segment_count=sum(len(p) for p in rollouts.values()),
                logical_action_count=sum(p['action_count'] for p in paths.values()))


def prepare(output):
    cfg, workload, _ = validate()
    output = output.resolve()
    if output == workload or workload in output.parents:
        raise ValueError('export must remain outside the original workload')
    # Validate all original inputs before creating any new directory.
    sampling = json.loads((workload/'agent-sampling.json').read_text())
    samples = sampling['segments']
    rollouts = json.loads((workload/'rollouts.json').read_text())
    inputs = [workload/'agent-sampling.json', workload/'rollouts.json', workload/'guest-runner.sh', workload/'task.json',
              *sorted((workload/'segments').glob('*/actions.tsv'))]
    before = {str(p.relative_to(workload)): sha(p) for p in inputs}
    original = audit(workload, samples, rollouts)
    output.mkdir(parents=True, exist_ok=False)
    normalized = output/'workload'
    normalized.mkdir()
    names = {}
    corrected = {}
    for role, path in rollouts.items():
        corrected[role] = []
        for segment in path:
            sample = sampled_segment(samples, role, segment)
            # Inspect actions were sampled separately and cannot be shared by name.
            name = role+'_inspect' if segment in ('early_inspect', 'late_inspect') else segment
            corrected[role].append(name)
            names[name] = segment
            target = normalized/'segments'/name/'actions.tsv'
            content = 'step\tsha256\tcommand\n'+''.join(
                r['step']+'\t'+r['sha256']+'\t'+r['command']+'\n' for r in rows_for(sample))
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_text() != content:
                    raise ValueError('attempted reuse of different sampled actions: '+name)
            else:
                target.write_text(content)
    for name in ('guest-runner.sh', 'task.json', 'agent-sampling.json'):
        shutil.copy2(workload/name, normalized/name)
    (normalized/'rollouts.json').write_text(json.dumps(corrected, indent=2)+'\n')
    exported = audit(normalized, samples, corrected, names)
    after = {str(p.relative_to(workload)): sha(p) for p in inputs}
    if before != after or exported['status'] != 'PASS':
        raise ValueError('original inputs changed or exported actions differ from sampling')
    provenance = dict(task_id=cfg['task_id'], sampling_sha256=cfg['sampling_sha256'],
                      original_input_sha256=before, original_inputs_unchanged=True,
                      exported_sha256={str(p.relative_to(normalized)): sha(p) for p in sorted(normalized.rglob('*')) if p.is_file()},
                      original_segment_names=names,
                      variant='role-specific inspect manifests from original sampling; original checkout untouched',
                      resampled=False, sampled_commands_changed=False, original_manifests_changed=False,
                      caveat='GRPO and BPO retain 35 and 17 segment executions; corrected prefix sharing gives TVCache 17 rather than historical 13. No schedule has been run.')
    for name, data in [('original-audit.json', original), ('exported-audit.json', exported), ('provenance.json', provenance)]:
        (output/name).write_text(json.dumps(data, indent=2)+'\n')
    print(f"original_status={original['status']} exported_status={exported['status']} result_dir={output}")
    return original, exported, provenance


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='New local evidence directory; existing directories are refused')
    prepare(parser.parse_args().output)
