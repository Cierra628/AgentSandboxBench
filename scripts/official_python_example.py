"""Run upstream documentation statements, with validation and scoped cleanup."""
import ast
import contextlib
import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parent.parent
server = Path(sys.argv[1]).resolve()
os.umask(0o077)
result_dir = ROOT / '.artifacts' / ('official-python-example-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid()))
result_dir.mkdir()
cli = str(ROOT / 'runtime/bin/aenv')
env = dict(os.environ, XDG_CONFIG_HOME=str(ROOT / 'runtime/config'))
name = 'official-ubuntu-' + result_dir.name.removeprefix('official-python-example-')
checks = {}
ns = {}
stage = 'preflight'
rc = 1

def command(label, args, timeout=30):
    try:
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        (result_dir / (label + '.log')).write_bytes((e.stdout or b'') + (e.stderr or b'') + b'\nTIMEOUT\n')
        raise
    (result_dir / (label + '.log')).write_text(p.stdout + p.stderr)
    p.check_returncode()
    return p.stdout

def listing(label, templates=False):
    args = ['template', 'list'] if templates else ['list']
    return json.loads(command(label, [cli, *args, '--output', 'json']))

def alarm(signum, frame):
    raise TimeoutError('official example exceeded 120 seconds')

try:
    assert (server / 'result.txt').read_text().strip() == 'status=PASS stage=server-ready rc=0'
    cid = (server / 'container-id.txt').read_text().strip()
    assert re.fullmatch('[a-f0-9]{64}', cid)
    meta = json.loads(command('server', ['docker', 'inspect', cid]))[0]
    assert meta['State']['Running'] and meta['Config']['Labels'].get('agentenv.experiment') == 'initial-smoke'
    creds = tomllib.loads((ROOT / 'runtime/config/aenv/credentials').read_text())
    assert creds['url'] == 'http://' + (server / 'address.txt').read_text().strip()
    os.environ.update(E2B_API_URL=creds['url'], E2B_SANDBOX_URL=creds['url'], E2B_API_KEY=creds['api_key'])
    listing('sandboxes-before')
    listing('templates-before', True)
    checks['preflight'] = 'PASS'
    doc = ROOT / 'AgentENV/docs/src/integration/e2b.md'
    raw = doc.read_text().split('### Python SDK', 1)[1]
    source = re.search(r'```python\n(.*?)\n```', raw, re.S).group(1)
    assert source.count('<template-id>') == 1
    source = source.replace('<template-id>', name)
    (result_dir / 'official-example.py').write_text(source + '\n')
    versions = {'e2b': importlib.metadata.version('e2b'), 'upstream_doc_sha256': hashlib.sha256(doc.read_bytes()).hexdigest(), 'template_image': 'ubuntu:22.04', 'source_commit': command('commit', ['git', '-C', str(ROOT / 'AgentENV'), 'rev-parse', 'HEAD']).strip()}
    (result_dir / 'versions.json').write_text(json.dumps(versions, indent=2) + '\n')
    stage = 'official-quickstart-template'
    # Same image and pull operation as upstream README; unique alias avoids collisions.
    command('template-pull', [cli, 'pull', 'ubuntu:22.04', '--name', name], timeout=360)
    checks[stage] = 'PASS'
    print(f'status=RUNNING stage=official-python-example result_dir={result_dir}', flush=True)
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(120)
    # Execute the original AST statements in order, adding checks between statements.
    with (result_dir / 'example.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        for node in ast.parse(source).body:
            stage = 'example-line-' + str(node.lineno)
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(result_dir / 'official-example.py'), 'exec'), ns)
            segment = ast.get_source_segment(source, node)
            if segment.startswith('sandbox = Sandbox.create'):
                sid = ns['sandbox'].sandbox_id
                (result_dir / 'sandbox-id.txt').write_text(sid + '\n')
                checks['sdk_create'] = 'PASS'
            elif segment.startswith('print(running.next_items())'):
                assert any(x['sandboxID'] == sid for x in listing('list-running'))
                checks['sdk_list'] = 'PASS'
            elif segment.startswith('result = sandbox.commands.run'):
                assert ns['result'].exit_code == 0 and ns['result'].stdout == 'hello world\n'
                checks['sdk_command'] = 'PASS'
            elif segment == 'sandbox.beta_pause()':
                rows = [x for x in listing('list-paused') if x['sandboxID'] == sid]
                assert len(rows) == 1 and rows[0]['state'].lower() == 'paused', rows
                checks['sdk_pause'] = 'PASS'
            elif segment == 'sandbox.kill()':
                assert not any(x['sandboxID'] == sid for x in listing('list-killed'))
                checks['sdk_kill'] = 'PASS'
    signal.alarm(0)
    assert all(checks.get(k) == 'PASS' for k in ['sdk_create', 'sdk_list', 'sdk_command', 'sdk_pause', 'sdk_kill'])
    stage = 'official-python-example'
    rc = 0
except Exception as e:
    signal.alarm(0)
    checks[stage] = 'FAIL'
    import traceback
    (result_dir / 'failure.txt').write_text(traceback.format_exc())
finally:
    try:
        sandbox = ns.get('sandbox')
        if sandbox and checks.get('sdk_kill') != 'PASS':
            command('cleanup-sandbox', [cli, 'delete', sandbox.sandbox_id], timeout=60)
        templates = listing('templates-cleanup-before', True)
        owned = [x for x in templates if name in x.get('names', []) or name in x.get('aliases', [])]
        for t in owned:
            command('cleanup-template', [cli, 'template', 'delete', t['templateID']], timeout=60)
        final_templates = listing('templates-final', True)
        assert not any(name in x.get('names', []) or name in x.get('aliases', []) for x in final_templates)
        final_sandboxes = listing('sandboxes-final')
        if sandbox:
            assert not any(x['sandboxID'] == sandbox.sandbox_id for x in final_sandboxes)
        checks['cleanup'] = 'PASS'
    except Exception:
        import traceback
        (result_dir / 'cleanup-failure.txt').write_text(traceback.format_exc())
        checks['cleanup'] = 'FAIL'
        if rc == 0:
            stage = 'cleanup'
        rc = 1
    status = 'PASS' if rc == 0 else 'FAIL'
    (result_dir / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    (result_dir / 'result.txt').write_text(f'status={status} stage={stage} rc={rc} cleanup={checks.get("cleanup")}\n')
    print(f'status={status} stage={stage} cleanup={checks.get("cleanup")} result_dir={result_dir}', flush=True)
sys.exit(rc)
