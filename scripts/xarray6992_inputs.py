"""Pinned Xarray backbone inputs and strict parsing of its selected pytest cases."""
import csv
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from prettier6604_inputs import ROOT, sha, replay_command

WORKLOAD=ROOT/'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/workloads/xarray-6992-rl-fork'
HEAD='45c0a114e2b7b27b83c9618bc05b36afac82183c'
IMAGE='ghcr.io/epoch-research/swe-bench.eval.x86_64.pydata__xarray-6992@sha256:c05101ef7105599eca1b7d15279a2928ac5453876cd2b83008deb9165d752655'
FILES=('xarray/core/dataset.py','xarray/tests/test_dataset.py')
SHELL_PATH='/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
PIPE_COMMAND_SHA='2fa29bcf6bd7967f3c258410094086895013329848ea329c2b659a3ff52a3299'
PIPE_STDOUT_SHA='66a4fc50cddc48f5feac753a6dde7cc8f246d6ad6f9bcf74a60370ad04496f6d'


def check_replay_exit(segment,step,expected,actual,command,stdout,diagnostic=False):
    known=segment=='trace_index_state' and step=='002'
    if diagnostic and known:
        assert hashlib.sha256(command.encode()).hexdigest()==PIPE_COMMAND_SHA
        assert hashlib.sha256(stdout).hexdigest()==PIPE_STDOUT_SHA and len(stdout)==955
        assert expected==0 and actual in (0,141), 'unapproved pipeline exit'
        if actual!=expected:return dict(segment=segment,step=step,expected=expected,actual=actual,
            reason='SIGPIPE with exact original stdout; diagnostic continuation only',stdout_sha256=PIPE_STDOUT_SHA)
    else:assert actual==expected, 'unexpected exit at '+segment+'/'+step
    return None


def verify_diagnostic_replay(raw,cfg,path):
    count=duration=0;peaks=[];differences=[]
    for segment in path:
        out=raw/'rollouts/backbone'/segment
        manifest=raw/'frozen-input/segments'/segment/'actions.tsv'
        assert sha(out/'actions.tsv')==sha(manifest)==cfg['input_sha256']['segments/'+segment+'/actions.tsv']
        with manifest.open() as stream:actions=list(csv.DictReader(stream,delimiter='\t'))
        with (out/'steps.tsv').open() as stream:steps=list(csv.DictReader(stream,delimiter='\t'))
        expected=cfg['expected_exit_codes'][segment]
        assert len(actions)==len(steps)==len(expected)
        assert [r['step'] for r in steps]==[f'{i:03}' for i in range(1,len(expected)+1)]
        for action,row,code in zip(actions,steps,expected):
            assert row['branch']=='backbone' and row['round']==segment and row['step']==action['step']
            assert hashlib.sha256(action['command'].encode()).hexdigest()==action['sha256']
            difference=check_replay_exit(segment,row['step'],code,int(row['exit_code']),action['command'],
                (out/'stdout'/f"{row['step']}.log").read_bytes(),True)
            if difference:differences.append(difference)
            assert int(row['duration_ns'])>0;duration+=int(row['duration_ns']);count+=1
        with (out/'guest-memory.tsv').open() as stream:memory=list(csv.DictReader(stream,delimiter='\t'))
        assert memory
        peaks.extend((int(r['MemTotal_kib'])-int(r['MemFree_kib']))*1024 for r in memory)
    return dict(action_count=count,guest_action_sum_ns=duration,guest_observed_peak_bytes=max(peaks),
        final_patch_sha256=sha(raw/'rollouts/backbone'/path[-1]/'patch.diff'),exit_differences=differences,
        observed_exit_codes_status='FAIL' if differences else 'PASS',diagnostic_continuation=True)


def frozen_shell(command):
    # bash -lc may activate testbed even though the image PATH selects base.
    # The original sampled `which python` selects /opt/miniconda3/bin/python.
    return 'export PATH='+SHELL_PATH+'; unset CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL; '+command


def normalize_patch(content):
    return '\n'.join(line for line in content.splitlines() if not line.startswith('index '))+'\n'


def validate(prepared):
    prepared=prepared.resolve();cfg=json.loads((prepared/'config.json').read_text())
    assert cfg['task_id']=='pydata__xarray-6992' and cfg['base_commit']==HEAD and cfg['source_image']==IMAGE
    task=json.loads((WORKLOAD/'task.json').read_text());assert task['base_commit']==HEAD and task['source_image']==IMAGE
    for filename,digest in cfg['input_sha256'].items():assert sha(WORKLOAD/filename)==digest,filename
    samples=json.loads((WORKLOAD/'agent-sampling.json').read_text())['segments']
    paths=json.loads((WORKLOAD/'rollouts.json').read_text());assert cfg['path']==paths['backbone']
    names={s:s for path in paths.values() for s in path};count=0
    for role,path in paths.items():
        for segment in path:
            selected=next((s for s in samples if s['role']==role and s['segment']==segment),None)
            selected=selected or next(s for s in samples if s['role']=='backbone' and s['segment']==segment)
            with (WORKLOAD/'segments'/segment/'actions.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
            assert [r['step'] for r in rows]==[f'{i:03}' for i in range(1,len(selected['actions'])+1)]
            assert [r['command'] for r in rows]==[replay_command(a['command']) for a in selected['actions']]
            assert all(hashlib.sha256(r['command'].encode()).hexdigest()==r['sha256'] for r in rows)
            if role=='backbone':
                assert [a['exit_code'] for a in selected['actions']]==cfg['expected_exit_codes'][segment]
                count+=len(rows)
    assert count==35 and cfg['guest_cpu']==2 and cfg['guest_memory_mib']==4096
    if 'frozen_shell_environment' in cfg:assert cfg['frozen_shell_environment']['PATH']==SHELL_PATH
    for stage in ('base','final'):
        for name,digest in cfg[stage+'_file_sha256'].items():assert sha(prepared/(stage+'-files')/name)==digest
    official=json.loads((prepared/'swebench-task.json').read_text())
    assert sha(prepared/'swebench-task.json')==cfg['official_task_sha256']
    assert official['instance_id']==cfg['task_id'] and official['base_commit']==HEAD
    f2p=json.loads(official['FAIL_TO_PASS']);p2p=json.loads(official['PASS_TO_PASS'])
    assert len(f2p)==12 and len(p2p)==945 and not set(f2p)&set(p2p)
    assert len(set(f2p+p2p))==957
    assert json.loads((prepared/'required-tests.json').read_text())==f2p+p2p
    assert (prepared/'test.patch').read_text()==official['test_patch']
    assert sha(prepared/'reference.patch')==cfg['reference_patch_sha256']
    return cfg,WORKLOAD,samples,names


def grade_xml(content,required,f2p,p2p,exit_code,aliases=None):
    assert exit_code in (0,1), 'test command timed out, failed collection, or had an infrastructure error'
    aliases=aliases or {}
    assert len(set(aliases.values()))==len(aliases)
    assert all(short in required and '[' in short and not short.endswith(']') and actual.endswith(']')
               and actual.partition(' ')[0]==short for actual,short in aliases.items()), 'invalid test ID alias'
    root=ET.fromstring(content);seen={}
    for case in root.iter('testcase'):
        file=case.attrib['file'];assert file.startswith('xarray/tests/') and '..' not in Path(file).parts
        module=file.removesuffix('.py').replace('/','.')
        classname=case.attrib['classname'];assert classname==module or classname.startswith(module+'.')
        suffix=classname[len(module):].strip('.').replace('.','::')
        node=file+'::'+(suffix+'::' if suffix else '')+case.attrib['name']
        node=aliases.get(node,node)
        assert node not in seen,'duplicate test case: '+node
        seen[node]='error' if case.find('error') is not None else 'failed' if case.find('failure') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
    assert set(seen)==set(required) and len(seen)==len(required), 'selected tests missing or unexpected tests collected'
    assert not any(v=='error' for v in seen.values()), 'pytest setup/teardown or collection error'
    failed=[n for n,v in seen.items() if v=='failed'];skipped=[n for n,v in seen.items() if v=='skipped']
    assert (exit_code==0)==(not failed), 'exit code disagrees with pytest case results'
    return dict(status='PASS' if not failed and not skipped else 'FAIL',exit_code=exit_code,
                passed=len(seen)-len(failed)-len(skipped),failed=len(failed),skipped=len(skipped),
                failed_tests=failed,skipped_tests=skipped,
                F2P_passed=sum(seen[n]=='passed' for n in f2p),F2P_total=len(f2p),
                P2P_passed=sum(seen[n]=='passed' for n in p2p),P2P_total=len(p2p),
                scope='SWE-bench test patch and exact F2P/P2P case selection; direct image pytest, not full official Docker harness')
