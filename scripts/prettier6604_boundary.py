"""Guest state after fixed step 11 or 24; no task commands are executed."""
import csv
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PREFIX=('read_repo','read_typescript','core_patch','core_verify')


def capture(checkpoint_step=24):
    assert checkpoint_step in (11,24)
    prefix=PREFIX[:2] if checkpoint_step==11 else PREFIX
    files={};progress=[]
    roots=[Path('/workspace/mixfs-workload')]
    custom=Path('/testbed/tests/typescript/custom/parenthesizedType')
    if custom.exists():roots.append(custom)
    if checkpoint_step==24:assert custom.is_dir()
    for segment in prefix:
        p=Path('/workspace/artifacts/exp5/backbone')/segment
        with (p/'steps.tsv').open() as stream:steps=list(csv.DictReader(stream,delimiter='\t'))
        assert [r['step'] for r in steps]==[f'{i:03}' for i in range(1,len(steps)+1)]
        progress.extend(dict(segment=segment,step=r['step'],exit_code=int(r['exit_code'])) for r in steps)
        roots.append(p)
    assert len(progress)==checkpoint_step
    next_segment='core_patch' if checkpoint_step==11 else 'backbone_finish'
    assert not (Path('/workspace/artifacts/exp5/backbone')/next_segment).exists()
    paths=[Path('/testbed/src/language-js/needs-parens.js'),Path('/testbed/package.json')]
    paths+=list(Path('/tmp').glob('*.ts'))+list(Path('/tmp').glob('*.js'))
    for root in roots:
        assert root.is_dir();paths.extend(p for p in root.rglob('*') if p.is_file())
    for p in sorted(set(paths)):
        assert not p.is_symlink()
        files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
    return dict(base_commit=subprocess.check_output(['git','-C','/testbed','rev-parse','HEAD'],text=True).strip(),
                cwd='/testbed',completed_steps=progress,next_segment=next_segment,
                git_status=subprocess.check_output(['git','-C','/testbed','status','--porcelain'],text=True),
                files_sha256=files)


def verify(before,after,checkpoint_step=24):
    assert checkpoint_step in (11,24)
    prefix=PREFIX[:2] if checkpoint_step==11 else PREFIX
    assert before==after,'restored files or execution boundary changed'
    expected=[dict(segment=segment,step=f'{i:03}',exit_code=127 if segment=='read_repo' and i==2 else 0)
              for segment,count in zip(prefix,(5,6,7,6)) for i in range(1,count+1)]
    next_segment='core_patch' if checkpoint_step==11 else 'backbone_finish'
    assert after['completed_steps']==expected and after['next_segment']==next_segment
    assert '/testbed/src/language-js/needs-parens.js' in after['files_sha256']
    return dict(status='PASS',checkpoint_after_global_step=checkpoint_step,continuation_global_step=checkpoint_step+1,
                next_segment=next_segment,files_compared=len(after['files_sha256']),
                scope='prefix progress, source, new tests, frozen inputs, prefix outputs, known temporary tool inputs')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--after-global-step',type=int,choices=[11,24],default=24)
    print(json.dumps(capture(parser.parse_args().after_global_step)))
