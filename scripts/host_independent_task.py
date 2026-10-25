"""One verified Prettier checkpoint imported into independent writable services."""
import csv
import json
import hashlib
import tarfile
from pathlib import Path
import shutil

from native_checkpoint_bundle import ARCHIVE_LIMIT, IMAGE, _export_repository
from host_file_matched_task import INSTANCES, matched_paths

PHASE_ROLES={'capture':(), 'single':INSTANCES[:1], 'dual':INSTANCES[1:]}


def phase_options(args):
    phase=getattr(args,'host_independent_phase',None)
    bundle=getattr(args,'host_checkpoint_bundle',None)
    source=getattr(args,'host_checkpoint_source',None)
    if phase is None and getattr(args,'host_scaling_mode',None)=='restore':
        if not (bundle and source) or getattr(args,'host_matched_task',False):
            raise ValueError('restore scaling requires its existing bundle and prefix evidence')
        return None
    if phase is None:
        if bundle or source:raise ValueError('host checkpoint inputs require an independent phase')
        return None
    if phase not in PHASE_ROLES or not getattr(args,'host_matched_task',False) or getattr(args,'host_trace_dual',False):
        raise ValueError('independent phase requires ordinary matched-task without tracing')
    if (phase=='capture' and (bundle or source)) or (phase!='capture' and not (bundle and source)):
        raise ValueError('capture has no imports; single/dual require bundle and original prefix evidence')
    return phase


def phase_arguments(phase,bundle=None,source=None):
    return ['--host-independent-phase',phase,*(['--host-checkpoint-bundle',str(bundle),
        '--host-checkpoint-source',str(source)] if phase!='capture' else [])]


def execution_paths(rollouts,phase):
    paths=matched_paths(rollouts)
    return {role:paths[role] for role in ('backbone',*PHASE_ROLES[phase])}


def prefix_metadata(raw):
    raw=Path(raw);task=json.loads((raw/'result.json').read_text());saved=json.loads((raw/'checkpoint-11.json').read_text())
    assert task['status']=='PASS' and task['task_id']=='prettier__prettier-6604'
    assert task['host_independent_phase']=='capture' and task['actual_action_count']==11
    assert task['reused_action_count']==0 and task['paths']['backbone']['action_count']==11
    assert task['checks']['sampled_actions_order_exit_codes_and_distinct_instances']=='PASS'
    assert task['checks']['checkpoint_saved_at_step_11']=='PASS'
    assert task['cleanup']['snapshots_retained_for_export']==[saved['template']]
    assert task['cleanup']['service_sandbox_list_empty']
    assert saved['status']=='PASS' and saved['step']==11
    return dict(status='PASS',kind='prettier-host-step11-v1',server_image=IMAGE,
        task_id=task['task_id'],checkpoint=saved,prefix_result=task['paths']['backbone'])


def export_prefix(repository,output,raw):
    """Validate the real Prettier capture, without posing as the native demo."""
    return _export_repository(Path(repository),Path(output),prefix_metadata(raw),ARCHIVE_LIMIT)


def checked_prefix(bundle,source):
    # The isolated storage entry verifies archive integrity while importing it.
    # This check binds the original task evidence to the imported checkpoint metadata.
    value=json.loads((Path(bundle)/'bundle.json').read_text())
    assert value['status']=='PASS' and value['format']=='agentenv-posix-snapshot-bundle-v1'
    assert value['server_image']==IMAGE and value['capture']==prefix_metadata(source)
    return value['capture']['checkpoint']


def import_prefix(bundle,source,out):
    saved=checked_prefix(bundle,source);source=Path(source);out=Path(out)
    shutil.copytree(source/'rollouts/backbone',out/'rollouts/backbone')
    shutil.copy2(source/'checkpoint-11.json',out/'checkpoint-11.json')
    with (source/'segments.tsv').open() as stream:
        reader=csv.DictReader(stream,delimiter='\t');fields=reader.fieldnames
        rows=[row for row in reader if row['rollout']=='backbone']
    assert [row['segment'] for row in rows]==['read_repo','read_typescript']
    assert all(row['sandbox_id']==saved['parent'] for row in rows)
    with (out/'segments.tsv').open('a') as stream:
        csv.DictWriter(stream,fieldnames=fields,delimiter='\t').writerows(rows)
    return saved


def verify_repository_archive(bundle):
    """Verify a recovery copy before deleting its only source volume; never extract it."""
    bundle=Path(bundle);value=json.loads((bundle/'bundle.json').read_text())
    assert value['status']=='PASS' and value['capture']['kind']=='prettier-host-step11-v1'
    expected=value['repository_files'];seen=set();total=0
    with tarfile.open(bundle/'repository.tar.gz','r|gz') as archive:
        for member in archive:
            path=Path(member.name)
            assert not path.is_absolute() and '..' not in path.parts and path.parts[0]=='snapshot-store'
            if member.isdir():continue
            assert member.isfile(),'unexpected archive member'
            key=str(path.relative_to('snapshot-store'))
            assert key in expected and key not in seen and member.size==expected[key]['size']
            with archive.extractfile(member) as stream:
                assert hashlib.file_digest(stream,'sha256').hexdigest()==expected[key]['sha256'],key
            seen.add(key);total+=member.size
    assert seen==set(expected) and total==value['repository_file_bytes']
    return dict(status='PASS',repository_contents_verified=True,verified_files=len(seen),verified_bytes=total,
        snapshot_id=value['capture']['checkpoint']['template'],archive_bytes=value['archive_bytes'],archive_sha256=value['archive_sha256'])
