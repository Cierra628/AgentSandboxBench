"""Task configuration and pinned inputs; model receives no reference patch."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def load(path, inputs=None):
    task=json.loads(Path(path).read_text())
    assert task['version']==1 and task['cwd'].startswith('/')
    assert task['profile'] in ('sphinx7590','prettier6604')
    assert task['memory_mib'] in (512,4096)
    assert isinstance(task['prompt'],str) and task['prompt']
    assert all(p.startswith('/') for p in task['tracked_files'])
    if task['profile']=='prettier6604':
        from exp1_official_task import load as official
        value=official(Path(inputs))
        assert value['instance_id']==task['task_id'] and value['base_commit']==task['base_commit']
        assert task['problem_field']=='problem_statement'
        task['prompt']+='\n\n'+value['problem_statement']
    else:assert task['task_id']=='csv-category-sum-v1'
    return task


def state_request(task):
    return dict(kind='state',files=task['tracked_files'],**({'repo':task['repo']} if task.get('repo') else {}))
