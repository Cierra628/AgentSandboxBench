"""Capture actual tool calls, freeze their results, and replay through the same bridge."""
import base64
import hashlib
import json
from pathlib import Path
import time


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path,value):
    with path.open('x') as stream:stream.write(json.dumps(value,indent=2)+'\n')


def request(cli,sid,value):
    payload=base64.b64encode(json.dumps(value).encode()).decode()
    return json.loads(cli('tool-'+value['kind'],'exec',sid,'python3','/workspace/asb-tool-capture.py',payload,timeout=value.get('timeout_seconds',30)+15))


def begin(directory,context,initial_state):
    directory.mkdir(parents=True,exist_ok=False)
    assert context['session_semantics']=='fresh bash per tool call; filesystem persists'
    write(directory/'context.json',dict(context,initial_state=initial_state))
    (directory/'steps').mkdir()


def capture(directory,cli,sid,command,*,cwd,env,timeout_seconds=30):
    assert not (directory/'manifest.json').exists(),'trajectory already frozen'
    index=len(list((directory/'steps').iterdir()))+1
    step=directory/'steps'/f'{index:03}';step.mkdir()
    value={'kind':'exec','command':command,'cwd':cwd,'env':env,'timeout_seconds':timeout_seconds}
    assert cwd.startswith('/') and all(isinstance(k,str) and isinstance(v,str) for k,v in env.items())
    write(step/'request.json',value);started=time.monotonic_ns()
    result=request(cli,sid,value)
    assert result['cwd']==cwd and result['env']==env
    result['controller_elapsed_ns']=time.monotonic_ns()-started
    stdout=base64.b64decode(result.pop('stdout_b64'));stderr=base64.b64decode(result.pop('stderr_b64'))
    (step/'stdout.bin').write_bytes(stdout);(step/'stderr.bin').write_bytes(stderr)
    result.update(stdout_sha256=sha(step/'stdout.bin'),stderr_sha256=sha(step/'stderr.bin'))
    write(step/'result.json',result);return result


def freeze(directory,final_state):
    steps=sorted((directory/'steps').iterdir());assert steps
    assert [p.name for p in steps]==[f'{i:03}' for i in range(1,len(steps)+1)]
    results=[json.loads((p/'result.json').read_text()) for p in steps]
    hashes={str(p.relative_to(directory)):sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}
    manifest={'version':1,'format':'agent-tool-trajectory-v1','action_count':len(steps),'sha256':hashes,
              'expected_failures':{p.name:r['exit_code'] for p,r in zip(steps,results) if r['exit_code']!=0},'final_state':final_state,
              'scope':'captured tool interface; model decisions and task correctness are not inferred'}
    write(directory/'manifest.json',manifest);return manifest


def validate(directory):
    manifest=json.loads((directory/'manifest.json').read_text());assert manifest['version']==1
    actual={str(p.relative_to(directory)):sha(p) for p in sorted(directory.rglob('*')) if p.is_file() and p!=directory/'manifest.json'}
    assert actual==manifest['sha256'],'frozen trajectory changed or contains extra files'
    assert len(list((directory/'steps').iterdir()))==manifest['action_count']
    return manifest


def replay(directory,output,cli,sid):
    manifest=validate(directory);context=json.loads((directory/'context.json').read_text())
    initial=request(cli,sid,{'kind':'state','files':context['tracked_files']})
    assert initial==context['initial_state'],'replay did not start from captured initial file state'
    begin(output,context,initial)
    for step in sorted((directory/'steps').iterdir()):
        expected=json.loads((step/'result.json').read_text());value=json.loads((step/'request.json').read_text())
        actual=capture(output,cli,sid,value['command'],cwd=value['cwd'],env=value['env'],timeout_seconds=value['timeout_seconds'])
        for k in ('exit_code','stdout_sha256','stderr_sha256','cwd','env'):assert actual[k]==expected[k],step.name+': '+k+' differs'
    final=request(cli,sid,{'kind':'state','files':context['tracked_files']});assert final==manifest['final_state']
    frozen=freeze(output,final);assert frozen['expected_failures']==manifest['expected_failures']
    result={'status':'PASS','action_count':manifest['action_count'],'expected_failures':manifest['expected_failures'],
            'captured_manifest_sha256':sha(directory/'manifest.json'),'final_state':final,'scope':'order, command hashes, stdout/stderr, exit codes, cwd/env and selected final files; fresh shells, no persistent PTY or model calls'}
    write(output.parent/'replay-proof.json',result);return result


def report(raw,output):
    from report_sandbox_runtime import write_report
    raw=raw.resolve();output=output.resolve();assert not output.exists() and not output.is_relative_to(raw)
    result=json.loads((raw/'result.json').read_text());assert result['status']=='PASS' and all(result['cleanup'].values())
    original=validate(raw/'captured');replayed=validate(raw/'replayed');assert original['action_count']==replayed['action_count'] and original['final_state']==replayed['final_state']
    proof=json.loads((raw/'replay-proof.json').read_text());assert proof['status']=='PASS'
    assert proof['captured_manifest_sha256']==sha(raw/'captured/manifest.json')
    assert proof['action_count']==original['action_count'] and proof['final_state']==original['final_state']
    assert proof['expected_failures']==original['expected_failures']==replayed['expected_failures']
    assert result['replay']==proof
    for p in sorted((raw/'captured/steps').iterdir()):
        a=json.loads((p/'result.json').read_text());b=json.loads((raw/'replayed/steps'/p.name/'result.json').read_text())
        assert (p/'request.json').read_bytes()==(raw/'replayed/steps'/p.name/'request.json').read_bytes()
        for k in ('exit_code','stdout_sha256','stderr_sha256','cwd','env'):assert a[k]==b[k]
    output.mkdir(parents=True)
    metrics=write_report(raw,output)
    timings={}
    for role,folder in [('capture','captured'),('replay','replayed')]:
        steps=[json.loads((p/'result.json').read_text()) for p in sorted((raw/folder/'steps').iterdir())]
        timings[role]=dict(unit='ns',guest_tool_sum_ns=sum(v['guest_elapsed_ns'] for v in steps),controller_tool_call_sum_ns=sum(v['controller_elapsed_ns'] for v in steps),
            scope='four sequential tool calls only; collection setup, cold start, report and cleanup are separate')
    write(output/'summary.json',dict(status='PASS',tool_capture_demo=True,replay=proof,runtime_metrics=metrics,native_catalog=result['native_catalog'],cleanup=result['cleanup'],tool_timing=timings))
