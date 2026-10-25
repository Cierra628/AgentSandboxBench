"""Host-side model loop; shell actions run only through the captured guest bridge."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from tool_trajectory import capture, write

from online_task import load as load_task

CWD = '/workspace/asb-tool-session'


def configuration(path):
    cfg = json.loads(Path(path).read_text())
    assert cfg['version'] == 1 and cfg['model'] and cfg['base_url'].startswith(('http://', 'https://'))
    for name in ('max_model_calls', 'max_tool_calls', 'request_timeout_seconds',
                 'tool_timeout_seconds', 'max_completion_tokens', 'max_run_seconds'):
        assert isinstance(cfg[name], int) and 0 < cfg[name], name
    return cfg


def complete(cfg, key, payload, timeout):
    req = urllib.request.Request(cfg['base_url'].rstrip('/')+'/chat/completions',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer '+key}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode(errors='replace').replace(key, '[REDACTED]')
        raise RuntimeError('model HTTP '+str(error.code)+': '+detail) from None


def timed_complete(cfg, key, payload, timeout, record, timing_path, completion=complete):
    """Record the whole HTTP response wait, including failed attempts."""
    record.update(timeout_seconds=timeout, started_unix_ns=time.time_ns())
    begin = time.monotonic_ns()
    try:
        response = completion(cfg, key, payload, timeout)
        record['status'] = 'response_received'
        return response
    except Exception as error:
        record.update(status='request_failed', error_type=type(error).__name__,
                      error=str(error).replace(key, '[REDACTED]'))
        raise
    finally:
        record['elapsed_ns'] = time.monotonic_ns()-begin
        write(timing_path, record)


def run(config_path, out, trajectory, cli, sid, env, mark, completion=complete, key=None, task=None, after_tool=None):
    cfg = configuration(config_path)
    if key is None:
        key = os.environ.get(cfg['api_key_env']) or Path(cfg['api_key_file']).read_text().strip()
    assert key, 'model credential unavailable'
    out.mkdir(exist_ok=False)
    write(out/'effective-config.json', cfg)
    cwd = task['cwd'] if task else CWD
    task = task or load_task(Path(__file__).resolve().parents[1]/'configs/online-task-csv.json')
    prompt = task['prompt']
    messages = [{'role': 'system', 'content': 'You are a coding agent. Execute shell tools in the supplied sandbox to complete the task. '
                 'Call run_shell with arguments such as {"command": "cat src/example.js"}. '
                 'The command value is plain shell source text, not another JSON object or serialized function arguments.'},
                {'role': 'user', 'content': prompt}]
    write(out/'task.json', {'task_id': task['task_id'], 'prompt': prompt})
    tool = {'type': 'function', 'function': {'name': 'run_shell', 'description': 'Execute a shell command in the task directory. Put JSON only in the function arguments, not inside command.',
        'parameters': {'type': 'object', 'properties': {'command': {'type': 'string',
            'description': 'Plain shell source text, for example cat src/example.js. Do not wrap it in another {"command": ...} object.'}}, 'required': ['command']}}}
    started = time.monotonic_ns(); calls = []; requests = []; steps = []; finished = False
    def remaining():
        value = cfg['max_run_seconds']-(time.monotonic_ns()-started)/1e9
        if value <= 0: raise TimeoutError('online agent time budget exceeded')
        return value
    try:
        for number in range(1, cfg['max_model_calls']+1):
            mark('model-wait')
            request_messages=messages
            if task.get('report_remaining_budget'):
                guidance=f" Remaining budget: {cfg['max_model_calls']-number+1} model responses including this one; {cfg['max_tool_calls']-len(steps)} shell calls. Reserve calls for source edits and actual tests."
                request_messages=[dict(messages[0],content=messages[0]['content']+guidance),*messages[1:]]
            payload = {'model': cfg['model'], 'messages': request_messages, 'tools': [tool],
                       'max_tokens': cfg['max_completion_tokens']}
            write(out/f'{number:03}-request.json', payload)
            timeout = min(cfg['request_timeout_seconds'], remaining())
            attempt = {'number': number}; requests.append(attempt)
            response = timed_complete(cfg, key, payload, timeout, attempt,
                                      out/f'{number:03}-timing.json', completion)
            duration = attempt['elapsed_ns']
            # Authorization is never part of either stored document.
            response = json.loads(json.dumps(response).replace(key, '[REDACTED]'))
            write(out/f'{number:03}-response.json', response)
            choice = response['choices'][0]; message = choice['message']
            calls.append({'number': number, 'elapsed_ns': duration, 'usage': response.get('usage'),
                          'finish_reason': choice.get('finish_reason')})
            if choice.get('finish_reason') == 'length': raise RuntimeError('model response exceeded token limit')
            messages.append(message)
            tools = message.get('tool_calls') or []
            if not tools:
                assert steps, 'model finished without executing a tool'
                finished = True; break
            for call in tools:
                if len(steps) >= cfg['max_tool_calls']: raise RuntimeError('online tool call budget exceeded')
                assert call['type'] == 'function' and call['function']['name'] == 'run_shell'
                args = json.loads(call['function']['arguments'])
                assert set(args) == {'command'} and isinstance(args['command'], str)
                mark('tool-'+str(len(steps)+1))
                begin = time.monotonic_ns()
                result = capture(trajectory, cli, sid, args['command'], cwd=cwd, env=env,
                                 timeout_seconds=min(cfg['tool_timeout_seconds'], max(1, int(remaining()))))
                step = trajectory/'steps'/f'{len(steps)+1:03}'
                feedback = {'exit_code': result['exit_code'],
                    'stdout': (step/'stdout.bin').read_bytes().decode(errors='replace')[:16000],
                    'stderr': (step/'stderr.bin').read_bytes().decode(errors='replace')[:16000]}
                steps.append({'step': step.name, 'tool_call_id': call['id'], 'model_call': number,
                              'guest_elapsed_ns': result['guest_elapsed_ns'],
                              'controller_elapsed_ns': result['controller_elapsed_ns'],
                              'bridge_elapsed_ns': time.monotonic_ns()-begin, 'exit_code': result['exit_code']})
                if after_tool: after_tool(step)
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(feedback)})
        assert finished, 'model call budget exhausted without completion'
        result = {'status': 'PASS', 'model': cfg['model'], 'model_calls': calls, 'tool_calls': steps,
            'model_wait_sum_ns': sum(c['elapsed_ns'] for c in calls),
            'model_requests': requests, 'model_wait_total_ns': sum(r['elapsed_ns'] for r in requests),
            'guest_tool_sum_ns': sum(s['guest_elapsed_ns'] for s in steps),
            'controller_tool_sum_ns': sum(s['controller_elapsed_ns'] for s in steps),
            'scope': 'model completion only; independent task check and frozen replay are separate'}
        write(out/'result.json', result)
        return result
    except Exception as error:
        write(out/'result.json', {'status': 'FAIL', 'error': str(error).replace(key, '[REDACTED]'),
                                  'model_calls': calls, 'tool_calls': steps, 'model_requests': requests,
                                  'model_wait_sum_ns': sum(c['elapsed_ns'] for c in calls),
                                  'model_wait_total_ns': sum(r['elapsed_ns'] for r in requests)})
        raise
    finally:
        write(out/'messages.json', messages)
