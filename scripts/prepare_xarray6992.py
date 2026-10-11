"""Build references from the three original frozen edits, without model/gold code."""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import urllib.parse

from xarray6992_inputs import WORKLOAD, HEAD, IMAGE, FILES, SHELL_PATH, validate, normalize_patch
from prettier6604_inputs import ROOT, sha


def fetch(directory):
    """Freeze only the selected test row; never save the benchmark gold patch."""
    directory=directory.resolve();assert directory.is_relative_to(ROOT/'.artifacts/python-backbone')
    directory.mkdir(parents=True,exist_ok=False)
    def download(url,limit):
        with urllib.request.urlopen(url,timeout=45) as response:data=response.read(limit+1)
        assert len(data)<=limit
        return data
    query=urllib.parse.urlencode(dict(dataset='princeton-nlp/SWE-bench_Verified',config='default',
        split='test',where='"instance_id"=\'pydata__xarray-6992\'',length=2))
    url='https://datasets-server.huggingface.co/filter?'+query
    response=download(url,4*2**20);rows=json.loads(response)['rows'];assert len(rows)==1
    row=rows[0]['row'];assert row['instance_id']=='pydata__xarray-6992' and row['base_commit']==HEAD
    selected={key:row[key] for key in ('instance_id','repo','base_commit','version','test_patch','FAIL_TO_PASS','PASS_TO_PASS')}
    (directory/'swebench-task.json').write_text(json.dumps(selected,indent=2)+'\n')
    expected=json.loads((ROOT/'configs/xarray6992-backbone.json').read_text())
    assert sha(directory/'swebench-task.json')==expected['official_task_sha256'], 'selected benchmark test row changed'
    (directory/'dataset-source.json').write_text(json.dumps(dict(url=url,response_sha256=hashlib.sha256(response).hexdigest(),
        scope='frozen selected row hash; filter API response not pinned to a parquet revision'),indent=2)+'\n')
    for name in FILES:
        data=download('https://raw.githubusercontent.com/pydata/xarray/'+HEAD+'/'+name,2*2**20)
        assert hashlib.sha256(data).hexdigest()==expected['base_file_sha256'][name]
        target=directory/'base-files'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)


def patch(directory):
    result=''
    for name in FILES:
        r=subprocess.run(['git','diff','--no-index','--binary','base-files/'+name,'final-files/'+name],
                         cwd=directory,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        assert r.returncode in (0,1)
        result+=r.stdout.replace('a/base-files/','a/').replace('b/final-files/','b/')
    return result


def prepare(directory):
    directory=directory.resolve();assert directory.is_relative_to(ROOT/'.artifacts/python-backbone')
    assert not (directory/'config.json').exists()
    assert sha(directory/'base-files'/FILES[0])=='ad2a1dcde480fffe1d49027746b9cc79e0268d6ef34dab4b14fea8fdd29e3ba8'
    assert sha(directory/'base-files'/FILES[1])=='565ab0a37e046aa13955b1e3ef2aad54f82a0c617f3c923cf5d949e08bd63bc2'
    shutil.copytree(directory/'base-files',directory/'final-files')
    samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments']
    selected={s['segment']:s['actions'] for s in samples if s['role']=='backbone'}
    edits=[('core_patch',3),('core_verify',7),('backbone_finish',4)];provenance=[]
    def guarded_open(name,mode='r'):
        assert name in FILES and mode in ('r','w')
        return (directory/'final-files'/name).open(mode)
    def guarded_import(name,*args,**kwargs):
        assert name=='re';return re
    with (directory/'reference-rebuild.log').open('x') as log,contextlib.redirect_stdout(log):
        for segment,index in edits:
            if segment=='backbone_finish':
                assert normalize_patch(patch(directory))==normalize_patch(selected['backbone_finish'][0]['output'])
            command=selected[segment][index-1]['command']
            fragment=command.split("<<'EOF'\n",1)[1].removesuffix('\nEOF')
            assert fragment and '<<' not in fragment
            scope={'open':guarded_open,'print':print,'__builtins__':{'__import__':guarded_import}}
            exec(compile(fragment,'frozen-'+segment+'-'+str(index),'exec'),scope)
            provenance.append(dict(segment=segment,step=index,command=command))
    (directory/'reference.patch').write_text(patch(directory))
    (directory/'reference-provenance.json').write_text(json.dumps(dict(scope='three unchanged sampled Python edit blocks applied to pinned base files; no benchmark gold patch',edits=provenance),indent=2)+'\n')
    official=json.loads((directory/'swebench-task.json').read_text());assert official['base_commit']==HEAD
    (directory/'test.patch').write_text(official['test_patch'])
    (directory/'required-tests.json').write_text(json.dumps(json.loads(official['FAIL_TO_PASS'])+json.loads(official['PASS_TO_PASS']),indent=2)+'\n')
    cfg=dict(task_id='pydata__xarray-6992',base_commit=HEAD,source_image=IMAGE,
             workload=str(WORKLOAD.relative_to(ROOT)),path=json.loads((WORKLOAD/'rollouts.json').read_text())['backbone'],
             guest_cpu=2,guest_memory_mib=4096,sample_interval_seconds=.1,action_timeout_seconds=600,
             frozen_shell_environment=dict(PATH=SHELL_PATH,unset=['CONDA_PREFIX','CONDA_DEFAULT_ENV','CONDA_SHLVL'],
                 reason='restore image-configured PATH; original sampled which/python outputs select base interpreter'),
             expected_exit_codes={s:[a['exit_code'] for a in selected[s]] for s in selected},
             input_sha256={str(p.relative_to(WORKLOAD)):sha(p) for p in [WORKLOAD/'task.json',WORKLOAD/'agent-sampling.json',WORKLOAD/'rollouts.json',WORKLOAD/'guest-runner.sh',*sorted((WORKLOAD/'segments').glob('*/actions.tsv'))]},
             base_file_sha256={name:sha(directory/'base-files'/name) for name in FILES},
             final_file_sha256={name:sha(directory/'final-files'/name) for name in FILES},
             reference_patch_sha256=sha(directory/'reference.patch'),official_task_sha256=sha(directory/'swebench-task.json'),
             scope='one frozen Xarray backbone; complete patch reference from sampled edits; direct SWE-bench selected-case evaluation')
    with (directory/'config.json').open('x') as stream:stream.write(json.dumps(cfg,indent=2)+'\n')
    validate(directory)
    print('status=PASS result_dir='+str(directory))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    parser.add_argument('--fetch',action='store_true',help='Download the pinned base files and hash-checked selected test row to a new directory')
    args=parser.parse_args()
    if args.fetch:fetch(args.directory)
    prepare(args.directory)
