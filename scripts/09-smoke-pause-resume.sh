#!/usr/bin/env bash
# One cold Alpine sandbox: verify guest process identity and state across pause/resume.
set -Eeuo pipefail
[[ $# -eq 1 ]] || { echo 'usage: sudo bash scripts/09-smoke-pause-resume.sh SERVER_RESULT_DIR'; exit 2; }
python3 - "$1" "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" <<'PY'
import datetime, json, os, pathlib, re, signal, subprocess, sys, tomllib, uuid
root = pathlib.Path(sys.argv[2])
server = pathlib.Path(sys.argv[1]).resolve()
result = root / '.artifacts' / ('pause-resume-smoke-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid()))
os.umask(0o077)
result.mkdir()
env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'runtime/config'))
cli = str(root / 'runtime/bin/aenv')
sid = None
stage = 'preflight'
checks = {k: 'NOT_RUN' for k in ['cold_start', 'application_before', 'pause_api', 'paused_state', 'resume_api', 'running_state', 'process_continuity', 'application_after', 'cleanup']}
rc = 1

def run(name, args, timeout=30):
    try:
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        (result / (name + '.log')).write_bytes((e.stdout or b'') + (e.stderr or b'') + b'\nTIMEOUT\n')
        raise
    (result / (name + '.log')).write_text(p.stdout + p.stderr)
    p.check_returncode()
    return p.stdout

def aenv(name, *args, timeout=30):
    return run(name, [cli, *args], timeout)

def listed(name):
    return json.loads(aenv(name, 'list', '--output', 'json'))

def check_state(name, expected):
    rows = listed(name)
    matches = [x for x in rows if x.get('sandboxID', x.get('sandbox_id')) == sid]
    assert len(matches) == 1, rows
    assert matches[0].get('state', '').lower() == expected, matches[0]

def interrupted(signum, frame):
    raise RuntimeError('interrupted by signal ' + str(signum))

for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(sig, interrupted)

probe = '''set -eu
p=$(cat /tmp/aenv-continuity/pid)
kill -0 "$p"
printf 'pid=%s\\n' "$p"
printf 'start_ticks=%s\\n' "$(awk '{print $22}' /proc/$p/stat)"
printf 'boot_id=%s\\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'marker=%s\\n' "$(cat /tmp/aenv-continuity/marker)"
printf 'counter=%s\\n' "$(cat /tmp/aenv-continuity/counter)"
'''

def values(name):
    out = aenv(name, 'exec', sid, '/bin/sh', '-c', probe)
    return dict(line.split('=', 1) for line in out.splitlines() if '=' in line)

try:
    assert os.geteuid() == 0
    assert (server / 'result.txt').read_text().strip() == 'status=PASS stage=server-ready rc=0'
    cid = (server / 'container-id.txt').read_text().strip()
    assert re.fullmatch('[a-f0-9]{64}', cid)
    info = json.loads(run('server-inspect', ['docker', 'inspect', cid]))[0]
    assert info['Config']['Labels']['agentenv.experiment'] == 'initial-smoke'
    assert info['State']['Running']
    credentials = tomllib.loads((root / 'runtime/config/aenv/credentials').read_text())
    assert credentials['url'] == 'http://' + (server / 'address.txt').read_text().strip()
    listed('list-before')
    stage = 'cold_start'
    out = aenv('start', 'start', '--cold', 'alpine:3.20', '--detach', '--timeout', '300', timeout=150)
    sid = out.strip().splitlines()[-1]
    assert re.fullmatch('[a-f0-9-]{36}', sid)
    (result / 'sandbox-id.txt').write_text(sid + '\n')
    checks[stage] = 'PASS'
    stage = 'application_before'
    marker = uuid.uuid4().hex
    setup = '''set -eu
mkdir /tmp/aenv-continuity
printf '%s\\n' "$1" > /tmp/aenv-continuity/marker
nohup /bin/sh -c 'n=0; while :; do n=$((n+1)); printf "%s\\n" "$n" > /tmp/aenv-continuity/counter.next; mv /tmp/aenv-continuity/counter.next /tmp/aenv-continuity/counter; sleep 1; done' </dev/null >/tmp/aenv-continuity/worker.log 2>&1 &
echo $! > /tmp/aenv-continuity/pid
sleep 2
'''
    aenv('setup', 'exec', sid, '/bin/sh', '-c', setup, 'sh', marker)
    before = values('before')
    assert before['marker'] == marker and int(before['counter']) >= 1
    checks[stage] = 'PASS'
    stage = 'pause_api'
    aenv('pause', 'pause', sid, timeout=90)
    checks[stage] = 'PASS'
    stage = 'paused_state'
    check_state('list-paused', 'paused')
    checks[stage] = 'PASS'
    stage = 'resume_api'
    aenv('resume', 'resume', sid, '--timeout', '600', timeout=90)
    checks[stage] = 'PASS'
    stage = 'running_state'
    check_state('list-resumed', 'running')
    checks[stage] = 'PASS'
    stage = 'process_continuity'
    after = values('after')
    for k in ['pid', 'start_ticks', 'boot_id', 'marker']:
        assert after[k] == before[k], (k, before[k], after[k])
    assert int(after['counter']) >= int(before['counter'])
    aenv('wait-progress', 'exec', sid, '/bin/sh', '-c', 'sleep 2')
    later = values('after-progress')
    for k in ['pid', 'start_ticks', 'boot_id', 'marker']:
        assert later[k] == before[k]
    assert int(later['counter']) > int(after['counter'])
    checks[stage] = 'PASS'
    stage = 'application_after'
    out = aenv('file-after', 'exec', sid, '/bin/sh', '-c', 'printf "after-resume\\n" > /tmp/aenv-continuity/after; cat /tmp/aenv-continuity/after')
    assert out.strip() == 'after-resume'
    checks[stage] = 'PASS'
    stage = 'pause-resume-functional'
    rc = 0
except Exception as e:
    if stage in checks:
        checks[stage] = 'FAIL'
    (result / 'failure.txt').write_text(type(e).__name__ + ': ' + str(e) + '\n')
finally:
    if sid:
        try:
            aenv('delete', 'delete', sid, timeout=60)
            rows = listed('list-after')
            assert not any(x.get('sandboxID', x.get('sandbox_id')) == sid for x in rows)
            checks['cleanup'] = 'PASS'
        except Exception as e:
            checks['cleanup'] = 'FAIL'
            (result / 'cleanup-failure.txt').write_text(str(e) + '\n')
            if rc == 0:
                stage = 'cleanup'
            rc = 1
    status = 'PASS' if rc == 0 else 'FAIL'
    (result / 'checks.json').write_text(json.dumps(checks, indent=2) + '\n')
    (result / 'result.txt').write_text(f'status={status} stage={stage} rc={rc} cleanup={checks["cleanup"]}\n')
    if os.environ.get('SUDO_UID') and os.environ.get('SUDO_GID'):
        for p in [result, *result.iterdir()]:
            os.chown(p, int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))
    print(f'status={status} stage={stage} cleanup={checks["cleanup"]} result_dir={result}')
sys.exit(rc)
PY
