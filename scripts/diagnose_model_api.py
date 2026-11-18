"""Bounded host-only requests. Returned shell commands are never executed."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from model_tool_agent import configuration, timed_complete
from tool_trajectory import write


def response_status(response):
    choice = response['choices'][0]
    message = choice['message']
    if choice.get('finish_reason') == 'length':
        return 'truncated'
    tools = message.get('tool_calls') or []
    if tools:
        for tool in tools:
            assert tool['type'] == 'function' and tool['function']['name'] == 'run_shell'
            args = json.loads(tool['function']['arguments'])
            assert set(args) == {'command'} and isinstance(args['command'], str) and args['command'].strip()
        return 'valid_tool_call'
    return 'final_text' if message.get('content') else 'empty_reply'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--request', type=Path, action='append', default=[], help='Saved request; at most two')
    parser.add_argument('--skip-short', action='store_true', help='Only diagnose the supplied saved requests')
    parser.add_argument('--timeout', type=float, default=60, help='Per-request socket timeout in seconds, at most 60')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert 0 < args.timeout <= 60 and len(args.request) <= 2
    cfg = configuration(args.config)
    key = os.environ.get(cfg['api_key_env']) or Path(cfg['api_key_file']).read_text().strip()
    assert key, 'model credential unavailable'
    # Preserve the selected model and request bodies; use a shorter diagnostic timeout.
    cases = [('short', {'model': cfg['model'], 'messages': [{'role': 'user', 'content': 'Reply only OK.'}],
                        'max_tokens': 512}, None)]
    if args.skip_short:
        cases = []
    for source in args.request:
        payload = json.loads(source.read_text())
        assert payload['model'] == cfg['model']
        cases.append((source.stem, payload, source))
    assert cases, 'no diagnostic requests selected'
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'effective-config.json', cfg)
    rows = []
    for number, (name, payload, source) in enumerate(cases, 1):
        prefix = args.output/f'{number:03}'
        write(Path(str(prefix)+'-request.json'), json.loads(json.dumps(payload).replace(key, '[REDACTED]')))
        row = {'number': number, 'name': name, 'source': str(source) if source else None,
               'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest() if source else None,
               'message_count': len(payload['messages']), 'max_tokens': payload['max_tokens']}
        try:
            response = timed_complete(cfg, key, payload, args.timeout, row, Path(str(prefix)+'-timing.json'))
            response = json.loads(json.dumps(response).replace(key, '[REDACTED]'))
            write(Path(str(prefix)+'-response.json'), response)
            row['response_status'] = response_status(response)
            choice = response['choices'][0]
            row.update(finish_reason=choice.get('finish_reason'), usage=response.get('usage'),
                       content_chars=len(choice['message'].get('content') or ''),
                       reasoning_chars=len(choice['message'].get('reasoning_content') or ''),
                       tool_call_count=len(choice['message'].get('tool_calls') or []))
        except Exception as error:
            row.update(response_status='request_failed' if row.get('status') == 'request_failed' else 'invalid_response',
                       error=str(error).replace(key, '[REDACTED]'))
        rows.append(row)
        write(Path(str(prefix)+'-result.json'), row)
        print(json.dumps({'number': number, 'status': row['response_status'],
                          'elapsed_seconds': row['elapsed_ns']/1e9, 'result_dir': str(args.output)}), flush=True)
    write(args.output/'result.json', {'status': 'COMPLETE', 'model': cfg['model'],
          'base_url': cfg['base_url'], 'requests': rows,
          'scope': 'Whole non-streaming HTTP response wait; no VM, shell execution, or repair validation. '
                   'Different prompts/token limits; not a causal latency comparison.'})


if __name__ == '__main__':
    main()
