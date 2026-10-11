"""Pinned Sphinx inputs and strict grading of its 25 selected C++ tests."""
import csv
import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from prettier6604_inputs import ROOT,sha,replay_command
from xarray6992_inputs import SHELL_PATH,normalize_patch

WORKLOAD=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/sphinx-7590-rl-fork'
HEAD='2e506c5ab457cba743bb47eb5b8c8eb9dd51d23d'
IMAGE='ghcr.io/epoch-research/swe-bench.eval.x86_64.sphinx-doc__sphinx-7590@sha256:577ebfcce825e4c9621e066dc1c8375443be94bbc6de30b9368e422b8f123731'
FILES=('sphinx/domains/cpp.py','sphinx/util/cfamily.py')
BACKGROUND_FILES=('setup.py','tox.ini')
BACKGROUND_PATCH_SHA256='75bb065627153500da2efe97397370b08e15981a91b51da7261c5e3a9c303437'


PIPELINE_REFERENCE=dict(command='e9eb4cf8916078545d524f611ca9e6fcdb8413a3d3d41fea3ffd874e51d52ab8',
    stdout='438a210ae8a9ab4e13ba125eda1a0c2bd2b7d10d836bc65a93b645cb38866e87',
    stderr='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    prefix_patch='f9273a85c8c92060e17cac887e490adf40ee96bcb74f1ef3ce54a6f013744a08')
SECOND_PIPELINE_REFERENCE=dict(command='30a9a23d8d5d3f43a51a5904f65dfe97f9136e5ba6aa8d62e2f3c9b7501a7d4b',
    stdout='7335217a4c4e4d886ff4472f0622133c195e987c5cdf6e1b35170109dd7559f5',
    stderr='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    prefix_patch='f9273a85c8c92060e17cac887e490adf40ee96bcb74f1ef3ce54a6f013744a08')


def check_pipeline_exit(segment,step,expected,actual,command,stdout,stderr,prefix_patch,policy_version=1):
    assert policy_version in (1,2),'unknown Sphinx pipeline policy'
    reference=PIPELINE_REFERENCE if (segment,step)==('read_repo','001') else SECOND_PIPELINE_REFERENCE if policy_version==2 and (segment,step)==('core_patch','001') else None
    if reference is None:
        assert actual==expected,f'{segment}/{step}: expected {expected}, actual {actual}; outside approved Sphinx pipeline policy'
        return None
    assert expected==141 and actual in (0,141),'unapproved Sphinx pipeline exit'
    for key,data in zip(('command','stdout','stderr','prefix_patch'),(command.encode(),stdout,stderr,prefix_patch)):
        assert hashlib.sha256(data).hexdigest()==reference[key],'Sphinx pipeline reference mismatch: '+key
    return dict(segment=segment,step=step,expected=expected,actual=actual) if actual!=expected else None


def verify_pipeline_replay(raw,cfg,path,samples,names,sampling=True,policy_version=1):
    """Explicit diagnostic variant; never changes frozen input or stored exits."""
    from run_prettier6604_grpo import verify_path
    assert cfg['task_id']=='sphinx-doc__sphinx-7590'
    differences=[];observed=copy.deepcopy(samples)
    for segment in path:
        out=raw/'rollouts/backbone'/segment
        manifest=raw/'frozen-input/segments'/segment/'actions.tsv'
        assert sha(manifest)==cfg['input_sha256']['segments/'+segment+'/actions.tsv']
        with manifest.open() as f:actions=list(csv.DictReader(f,delimiter='\t'))
        with (out/'steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
        assert len(steps)==len(actions)==len(cfg['expected_exit_codes'][segment])
        for action,row,expected in zip(actions,steps,cfg['expected_exit_codes'][segment]):
            assert row['step']==action['step'] and hashlib.sha256(action['command'].encode()).hexdigest()==action['sha256']
            difference=check_pipeline_exit(segment,row['step'],expected,int(row['exit_code']),action['command'],
                (out/'stdout'/f"{row['step']}.log").read_bytes(),(out/'stderr'/f"{row['step']}.log").read_bytes(),(out/'patch.diff').read_bytes(),policy_version=policy_version)
            if difference:
                differences.append(difference)
                selected=next(s for s in observed if s['role']=='backbone' and s['segment']==segment)
                selected['actions'][int(row['step'])-1]['exit_code']=int(row['exit_code'])
    result=verify_path(raw,'backbone',path,raw/'frozen-input',observed,names,sampling=sampling)
    return dict(result,**(dict(sphinx_pipeline_policy_version=2) if policy_version==2 else {}),exit_differences=differences,observed_exit_codes_status='FAIL' if differences else 'PASS',
        sphinx_pipeline_diagnostic=True)


def failed_find_action(raw,cfg):
    """Select only the original step-11 failure; not an exit-code exception."""
    directory=raw/'rollouts/backbone/trace_cpp_parser'
    if not (directory/'steps.tsv').exists():return None
    with (directory/'steps.tsv').open() as f:steps=list(csv.DictReader(f,delimiter='\t'))
    failed=next((r for r in steps if r['step']=='005' and int(r['exit_code'])==1),None)
    if failed is None:return None
    assert cfg['task_id']=='sphinx-doc__sphinx-7590' and cfg['expected_exit_codes']['trace_cpp_parser'][4]==0
    manifest=directory/'actions.tsv'
    assert sha(manifest)==cfg['input_sha256']['segments/trace_cpp_parser/actions.tsv']
    with manifest.open() as f:action=next(r for r in csv.DictReader(f,delimiter='\t') if r['step']=='005')
    assert action['sha256']=='1f6e4d89a09212e18cf773594c96e46f5aaab4bf0d55b3f27d0e17086cc800a5'
    assert hashlib.sha256(action['command'].encode()).hexdigest()==action['sha256']
    return action


def background_probe():
    return "dict(files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in "+repr(list(BACKGROUND_FILES))+"},patch=subprocess.check_output(['git','diff','--binary','--',*"+repr(list(BACKGROUND_FILES))+"],text=True))"


def verify_image_background(before,after=None):
    assert set(before['files'])==set(BACKGROUND_FILES)
    assert all(len(v)==64 for v in before['files'].values())
    assert hashlib.sha256(normalize_patch(before['patch']).encode()).hexdigest()==BACKGROUND_PATCH_SHA256,'unexpected pinned image background patch'
    if after is not None:
        assert after['files']==before['files'] and normalize_patch(after['patch'])==normalize_patch(before['patch']),'image background changed during replay'
    return dict(status='PASS',files=before['files'],normalized_patch_sha256=BACKGROUND_PATCH_SHA256,
                scope='fixed-image setup.py/tox.ini modifications; captured before tools and unchanged afterward')


def verify_final_state(source,final,cfg):
    assert source['git_status']==' M setup.py\n M tox.ini\n','unexpected initial dirty or untracked paths'
    assert final['files']==cfg['final_file_sha256']
    assert sorted(final['changed'])==sorted((*FILES,*BACKGROUND_FILES)),'unexpected final changed paths'
    return verify_image_background(source['image_background'],final['image_background'])


def validate(prepared):
    cfg=json.loads((prepared/'config.json').read_text())
    assert cfg==json.loads((ROOT/'configs/sphinx7590-backbone.json').read_text())
    assert cfg['task_id']=='sphinx-doc__sphinx-7590' and cfg['base_commit']==HEAD and cfg['source_image']==IMAGE
    for name,digest in cfg['input_sha256'].items():assert sha(WORKLOAD/name)==digest,name
    paths=json.loads((WORKLOAD/'rollouts.json').read_text());samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments']
    assert cfg['path']==paths['backbone'] and cfg['guest_cpu']==2 and cfg['guest_memory_mib']==4096
    for role,path in paths.items():
        for segment in path:
            sample=next((s for s in samples if s['role']==role and s['segment']==segment),None) or next(s for s in samples if s['role']=='backbone' and s['segment']==segment)
            rows=list(csv.DictReader((WORKLOAD/'segments'/segment/'actions.tsv').open(),delimiter='\t'))
            assert [r['step'] for r in rows]==[f'{i:03}' for i in range(1,len(sample['actions'])+1)]
            assert [r['command'] for r in rows]==[replay_command(a['command']) for a in sample['actions']]
            assert all(hashlib.sha256(r['command'].encode()).hexdigest()==r['sha256'] for r in rows)
            if role=='backbone':assert cfg['expected_exit_codes'][segment]==[a['exit_code'] for a in sample['actions']]
    assert sum(map(len,cfg['expected_exit_codes'].values()))==35
    for stage in ('base','final'):
        for name,digest in cfg[stage+'_file_sha256'].items():assert sha(prepared/(stage+'-files')/name)==digest
    task=json.loads((prepared/'swebench-task.json').read_text());assert task['instance_id']==cfg['task_id'] and task['base_commit']==HEAD
    assert sha(prepared/'swebench-task.json')==cfg['official_task_sha256']
    f2p=json.loads(task['FAIL_TO_PASS']);p2p=json.loads(task['PASS_TO_PASS'])
    assert len(f2p)==1 and len(p2p)==24 and len(set(f2p+p2p))==25
    assert json.loads((prepared/'required-tests.json').read_text())==f2p+p2p
    assert (prepared/'test.patch').read_text()==task['test_patch']
    assert sha(prepared/'reference.patch')==cfg['reference_patch_sha256']
    return cfg,WORKLOAD,samples,{s:s for path in paths.values() for s in path}


def grade_xml(content,required,f2p,p2p,exit_code,aliases=None):
    assert exit_code in (0,1), 'test collection, timeout or infrastructure failure'
    assert not aliases
    seen={}
    for case in ET.fromstring(content).iter('testcase'):
        file=case.attrib['file'];assert file=='tests/test_domain_cpp.py'
        assert case.attrib['classname'] in ('tests.test_domain_cpp','test_domain_cpp')
        node=file+'::'+case.attrib['name'];assert node not in seen
        seen[node]='error' if case.find('error') is not None else 'failed' if case.find('failure') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    assert set(seen)==set(required) and len(seen)==25 and not any(v=='error' for v in seen.values())
    failed=[n for n,v in seen.items() if v=='failed'];skipped=[n for n,v in seen.items() if v=='skipped']
    assert (exit_code==0)==(not failed)
    return dict(status='PASS' if not failed and not skipped else 'FAIL',exit_code=exit_code,passed=len(seen)-len(failed)-len(skipped),
        failed=len(failed),skipped=len(skipped),failed_tests=failed,skipped_tests=skipped,
        F2P_passed=sum(seen[n]=='passed' for n in f2p),F2P_total=len(f2p),P2P_passed=sum(seen[n]=='passed' for n in p2p),P2P_total=len(p2p),
        scope='SWE-bench selected C++ tests and test patch; direct image pytest, not full official Docker harness')
