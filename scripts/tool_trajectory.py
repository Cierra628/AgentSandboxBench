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


def output_content(content,policy,command=None):
    if policy=='strict':return content
    assert policy in ('jest-timing-v1','prettier-runtime-v1')
    import re
    text=content.decode(errors='strict')
    if policy=='prettier-runtime-v1':
        # These two directory timestamps change when a clean VM is seeded.
        # Keep all other listing fields and all other commands byte-for-byte.
        if command in ('cd /testbed && ls -la','cd /testbed && pwd && ls -la'):
            text=re.sub(r'(?m)^(d[rwxstST-]{9}\s+\d+ root root\s+4096 )'
                        r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}( (?:\.\.|\.git))$',
                        r'\1<time>\2',text)
        candidate=re.sub(r'\(node:\d+\)(?= \[DEP0128\])','(node:<pid>)',text)
        warning="(node:<pid>) [DEP0128] DeprecationWarning: Invalid 'main' field in '/testbed/node_modules/find-parent-dir/package.json' of 'find-parent-dir.js'. Please either fix that or report it to the module author\n(Use `node --trace-deprecation ...` to show where the warning was created)\n"
        if candidate==warning:text=candidate
    # Only standard Jest/Yarn timing fields; outcomes and test names stay intact.
    text=re.sub(r'(?m)^(Time:\s+)\d+(?:\.\d+)?s(, estimated \d+s)?$',r'\1<duration>',text)
    text=re.sub(r'(?m)^Done in \d+(?:\.\d+)?s\.$','Done in <duration>.',text)
    text=re.sub(r'(?m)^(PASS|FAIL) (tests/\S+) \(\d+(?:\.\d+)?s\)$',r'\1 \2 (<duration>)',text)
    text=re.sub(r'(?m)^(\s+[✓✕] .*?) \(\d+(?:\.\d+)?ms\)$',r'\1 (<duration>)',text)
    match=re.search(r'{\s*"numFailedTestSuites"',text)
    if match:
        value,end=json.JSONDecoder().raw_decode(text[match.start():])
        for key in ('startTime',):
            if key in value:value[key]='<time>'
        for suite in value.get('testResults',[]):
            for key in ('startTime','endTime'):
                if key in suite:suite[key]='<time>'
            for assertion in suite.get('assertionResults',[]):
                if 'duration' in assertion:assertion['duration']='<duration>'
        text=text[:match.start()]+json.dumps(value,sort_keys=True)+text[match.start()+end:]
    return text.encode()


def compare_step(a,b,context):
    expected=json.loads((a/'result.json').read_text());actual=json.loads((b/'result.json').read_text())
    assert (a/'request.json').read_bytes()==(b/'request.json').read_bytes(),'command/cwd/env changed'
    for k in ('exit_code','cwd','env'):assert actual[k]==expected[k],a.name+': '+k+' differs'
    policy=context.get('output_policy','strict')
    command=json.loads((a/'request.json').read_text())['command']
    for kind in ('stdout','stderr'):
        assert sha(a/(kind+'.bin'))==expected[kind+'_sha256'] and sha(b/(kind+'.bin'))==actual[kind+'_sha256']
        assert output_content((a/(kind+'.bin')).read_bytes(),policy,command if kind=='stdout' else None)==output_content((b/(kind+'.bin')).read_bytes(),policy,command if kind=='stdout' else None),a.name+': '+kind+' differs'


def replay(directory,output,cli,sid,after_step=None,output_policy=None):
    manifest=validate(directory);context=json.loads((directory/'context.json').read_text())
    frozen_policy=context.get('output_policy','strict')
    if output_policy:context=dict(context,output_policy=output_policy)
    state=context.get('state_request',{'kind':'state','files':context['tracked_files']})
    initial=request(cli,sid,state)
    assert initial==context['initial_state'],'replay did not start from captured initial file state'
    begin(output,context,initial)
    for step in sorted((directory/'steps').iterdir()):
        expected=json.loads((step/'result.json').read_text());value=json.loads((step/'request.json').read_text())
        actual=capture(output,cli,sid,value['command'],cwd=value['cwd'],env=value['env'],timeout_seconds=value['timeout_seconds'])
        compare_step(step,output/'steps'/step.name,context)
        if after_step:after_step(int(step.name))
    final=request(cli,sid,state);assert final==manifest['final_state']
    frozen=freeze(output,final);assert frozen['expected_failures']==manifest['expected_failures']
    result={'status':'PASS','action_count':manifest['action_count'],'expected_failures':manifest['expected_failures'],
            'captured_manifest_sha256':sha(directory/'manifest.json'),'final_state':final,'output_policy':context.get('output_policy','strict'),'captured_output_policy':frozen_policy,
            'strict_output_status':'FAIL' if any(sha(p/(kind+'.bin'))!=sha(output/'steps'/p.name/(kind+'.bin')) for p in sorted((directory/'steps').iterdir()) for kind in ('stdout','stderr')) else 'PASS',
            'dynamic_output_differences':[p.name+'/'+kind for p in sorted((directory/'steps').iterdir()) for kind in ('stdout','stderr') if sha(p/(kind+'.bin'))!=sha(output/'steps'/p.name/(kind+'.bin'))],
            'scope':'order, command hashes, stdout/stderr, exit codes, cwd/env and selected final files; fresh shells, no persistent PTY or model calls'}
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
    differences=[p.name+'/'+kind for p in sorted((raw/'captured/steps').iterdir()) for kind in ('stdout','stderr') if sha(p/(kind+'.bin'))!=sha(raw/'replayed/steps'/p.name/(kind+'.bin'))]
    if 'dynamic_output_differences' in proof:
        assert differences==proof['dynamic_output_differences']
        assert proof['strict_output_status']==('FAIL' if differences else 'PASS')
    for p in sorted((raw/'captured/steps').iterdir()):
        a=json.loads((p/'result.json').read_text());b=json.loads((raw/'replayed/steps'/p.name/'result.json').read_text())
        assert (p/'request.json').read_bytes()==(raw/'replayed/steps'/p.name/'request.json').read_bytes()
        compare_step(p,raw/'replayed/steps'/p.name,dict(json.loads((raw/'captured/context.json').read_text()),output_policy=proof.get('output_policy','strict')))
    output.mkdir(parents=True)
    metrics=write_report(raw,output)
    timings={}
    for role,folder in [('capture','captured'),('replay','replayed')]:
        steps=[json.loads((p/'result.json').read_text()) for p in sorted((raw/folder/'steps').iterdir())]
        timings[role]=dict(unit='ns',guest_tool_sum_ns=sum(v['guest_elapsed_ns'] for v in steps),controller_tool_call_sum_ns=sum(v['controller_elapsed_ns'] for v in steps),
            scope='captured sequential tool calls only; model wait, setup, cold start, report and cleanup are separate')
    online=result.get('online')
    if online:
        assert online['status'] in ('PASS','FAIL') and len(online['tool_calls'])==proof['action_count']
    if 'task_outcome' in result:
        assert result['checks'].get('independent_task',result['checks'].get('independent_csv_task'))==result['checks'].get('independent_replayed_task',result['checks'].get('independent_replayed_csv_task'))==result['task_outcome']
        assert result['checks']['task_outcome_preserved']=='PASS'
        checks=[json.loads((raw/('task-check-'+role+'.json')).read_text()) for role in ('capture','replay')]
        assert checks[0]['exit_code']==checks[1]['exit_code']
        if 'official_signature' in checks[0]:
            import base64
            from audit_prettier6604_official import inspect,signature
            assert checks[0]['official_signature']==checks[1]['official_signature']
            for check in checks:
                assert signature(inspect(raw/'prepared-input',base64.b64decode(check['stdout_b64']).decode()))==check['official_signature']
            assert checks[0]['oracle']['exit_code']==checks[1]['oracle']['exit_code']
        assert result['task_outcome']==checks[0].get('task_outcome','PASS' if checks[0]['exit_code']==0 else 'FAIL')
    if online:assert online==json.loads((raw/'online/result.json').read_text())
    if result.get('online_agent') is False:
        timings['capture']['scope']='inherited source trajectory timings; capture was not executed in this analysis run'
    write(output/'summary.json',dict(status='PASS',task_id=result.get('task_id','tool-interface-demo'),official_task_status=json.loads((raw/'task-check-replay.json').read_text()).get('official_signature',{}).get('status','NOT_RUN') if (raw/'task-check-replay.json').exists() else 'NOT_RUN',tool_capture_demo=True,online_agent=bool(online),online=online,task_outcome=result.get('task_outcome'),replay=proof,runtime_metrics=metrics,native_catalog=result['native_catalog'],cleanup=result['cleanup'],tool_timing=timings))
