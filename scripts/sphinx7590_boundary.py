"""Sphinx step-29 file/progress boundary; strict original exits, no SIGPIPE exception."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
from xarray6992_boundary import capture as capture_files

PREFIX=('read_repo','trace_cpp_parser','core_patch','core_verify')
NEXT='backbone_finish'
BACKGROUND=('setup.py','tox.ini')


def configuration(cfg,base,sampling,background):
    """Rebuild just the three original pre-checkpoint edits, without running tools."""
    contents={name:(base/name).read_text() for name in cfg['base_file_sha256']}
    assert all(hashlib.sha256(contents[name].encode()).hexdigest()==digest for name,digest in cfg['base_file_sha256'].items())
    def guarded_open(name,mode='r'):
        assert name in contents and mode in ('r','w')
        class File(io.StringIO):
            def write(self,text):
                assert mode=='w'
                count=super().write(text);contents[name]=self.getvalue();return count
        return File(contents[name] if mode=='r' else '')
    def guarded_import(name,*args,**kwargs):
        assert name=='re';return re
    sample=next(s for s in json.loads(sampling.read_text())['segments'] if s['role']=='backbone' and s['segment']=='core_verify')
    with contextlib.redirect_stdout(io.StringIO()):
        for index in (3,4,5):
            fragments=re.findall(r"python3 - <<'EOF'\n(.*?)\nEOF",sample['actions'][index-1]['command'],re.S)
            assert len(fragments)==1
            exec(compile(fragments[0],'original-core_verify-'+str(index),'exec'),{'open':guarded_open,'print':print,'__builtins__':{'__import__':guarded_import}})
    assert set(background)==set(BACKGROUND)
    return dict(cfg,checkpoint_file_sha256={name:hashlib.sha256(text.encode()).hexdigest() for name,text in contents.items()},
                checkpoint_background_sha256=background)


def verify(before,after,cfg):
    assert before==after,'restored file state or execution progress changed'
    assert after['base_commit']==cfg['base_commit'] and cfg['task_id']=='sphinx-doc__sphinx-7590'
    assert after['next_segment']==NEXT and after['next_segment_absent'] is True
    expected=[dict(segment=s,step=f'{i:03}',exit_code=code) for s in PREFIX for i,code in enumerate(cfg['expected_exit_codes'][s],1)]
    assert len(expected)==29 and after['completed_steps']==expected,'prefix incomplete, repeated, reordered or exit changed'
    assert set(cfg['checkpoint_file_sha256'])==set(cfg['base_file_sha256'])
    assert set(cfg['checkpoint_background_sha256'])==set(BACKGROUND)
    for name,digest in {**cfg['checkpoint_file_sha256'],**cfg['checkpoint_background_sha256']}.items():
        assert after['files_sha256']['/testbed/'+name]==digest,'unexpected checkpoint source or image file: '+name
    for segment in PREFIX:
        key='/workspace/artifacts/exp5/backbone/'+segment+'/actions.tsv'
        assert after['files_sha256'][key]==cfg['input_sha256']['segments/'+segment+'/actions.tsv']
    return dict(status='PASS',checkpoint_after_global_step=29,continuation_global_step=30,next_segment=NEXT,
                files_compared=len(after['files_sha256']),scope='three original edits, unchanged image files, frozen inputs and exact 29-step prefix; next segment absent')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    cfg=json.loads(parser.parse_args().config.read_text())
    print(json.dumps(capture_files(cfg,PREFIX,NEXT,verify,BACKGROUND)))
