"""Freeze selected Sphinx tests and rebuild only the original sampled edits."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import urllib.parse
from sphinx7590_inputs import ROOT,WORKLOAD,HEAD,FILES,validate,sha


def fetch(directory):
    directory.mkdir(parents=True,exist_ok=False)
    cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
    def download(url):
        with urllib.request.urlopen(url,timeout=45) as r:data=r.read(16*2**20+1)
        assert len(data)<=16*2**20
        return data
    # Cached rows remain usable when the service's SQL filter endpoint fails.
    receipts=[];selected=None
    for offset in (0,100,200,300,400):
        query=urllib.parse.urlencode(dict(dataset='princeton-nlp/SWE-bench_Verified',config='default',split='test',offset=offset,length=100))
        url='https://datasets-server.huggingface.co/rows?'+query;data=download(url)
        receipts.append(dict(url=url,response_sha256=hashlib.sha256(data).hexdigest()))
        matches=[x['row'] for x in json.loads(data)['rows'] if x['row']['instance_id']==cfg['task_id']]
        assert len(matches)<=1
        if matches:
            row=matches[0];assert row['base_commit']==HEAD
            selected={k:row[k] for k in ('instance_id','repo','base_commit','version','test_patch','FAIL_TO_PASS','PASS_TO_PASS')}
            break
    assert selected is not None,'task not found in original 500-row dataset'
    (directory/'swebench-task.json').write_text(json.dumps(selected,indent=2)+'\n')
    assert sha(directory/'swebench-task.json')==cfg['official_task_sha256'],'selected tests changed'
    (directory/'dataset-source.json').write_text(json.dumps(dict(responses=receipts,scope='only selected test metadata saved; selected-row hash frozen, API parquet revision not pinned'),indent=2)+'\n')
    for name in FILES:
        data=download('https://raw.githubusercontent.com/sphinx-doc/sphinx/'+HEAD+'/'+name)
        assert hashlib.sha256(data).hexdigest()==cfg['base_file_sha256'][name]
        target=directory/'base-files'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)


def prepare(directory):
    cfg=json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
    assert not (directory/'config.json').exists()
    for name in FILES:assert sha(directory/'base-files'/name)==cfg['base_file_sha256'][name]
    assert sha(directory/'swebench-task.json')==cfg['official_task_sha256']
    shutil.copytree(directory/'base-files',directory/'final-files')
    samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments'];edits=[]
    def guarded_open(name,mode='r'):
        assert name in FILES and mode in ('r','w')
        return (directory/'final-files'/name).open(mode)
    def guarded_import(name,*args,**kwargs):assert name=='re';return re
    with (directory/'reference-rebuild.log').open('x') as log,contextlib.redirect_stdout(log):
        for segment,index in [('core_verify',3),('core_verify',4),('core_verify',5),('backbone_finish',4)]:
            sample=next(s for s in samples if s['role']=='backbone' and s['segment']==segment)
            command=sample['actions'][index-1]['command']
            fragments=re.findall(r"python3 - <<'EOF'\n(.*?)\nEOF",command,re.S)
            assert len(fragments)==(2 if segment=='backbone_finish' else 1)
            for fragment in fragments:
                scope={'open':guarded_open,'print':print,'__builtins__':{'__import__':guarded_import}}
                exec(compile(fragment,'frozen-'+segment+'-'+str(index),'exec'),scope)
                edits.append(dict(segment=segment,step=index,python_fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest()))
    reference=''
    for name in FILES:
        assert sha(directory/'final-files'/name)==cfg['final_file_sha256'][name]
        r=subprocess.run(['git','diff','--no-index','--binary','base-files/'+name,'final-files/'+name],cwd=directory,stdout=subprocess.PIPE,text=True,timeout=10)
        assert r.returncode==1
        reference+=r.stdout.replace('a/base-files/','a/').replace('b/final-files/','b/')
    (directory/'reference.patch').write_text(reference);assert sha(directory/'reference.patch')==cfg['reference_patch_sha256']
    (directory/'reference-provenance.json').write_text(json.dumps(dict(scope='five original sampled edits; no gold source patch',edits=edits),indent=2)+'\n')
    task=json.loads((directory/'swebench-task.json').read_text())
    (directory/'test.patch').write_text(task['test_patch'])
    (directory/'required-tests.json').write_text(json.dumps(json.loads(task['FAIL_TO_PASS'])+json.loads(task['PASS_TO_PASS']),indent=2)+'\n')
    (directory/'config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    validate(directory);print('status=PASS result_dir='+str(directory))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);parser.add_argument('--fetch',action='store_true')
    args=parser.parse_args();directory=args.directory.resolve();assert directory.is_relative_to(ROOT/'.artifacts/sphinx-backbone')
    if args.fetch:fetch(directory)
    prepare(directory)
