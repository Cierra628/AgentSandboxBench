"""Bounded DAMON change/check in an explicitly named, newly created guest only."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
import uuid

MODES = ('default', 'damon_disabled')
SMOKE = ['default', 'damon_disabled']
COMPARISON = ['default', 'damon_disabled', 'damon_disabled', 'default']
REVERSE_COMPARISON = ['damon_disabled', 'default', 'default', 'damon_disabled']
PARAMETERS = '/sys/module/damon_reclaim/parameters'
PROGRAM = '''set -eu
p=PARAMETERS
printf 'kernel='; uname -r
printf 'enabled_before=%s\\n' "$(cat "$p/enabled")"
printf 'worker_before=%s\\n' "$(cat "$p/kdamond_pid")"
if test "$1" = prepare && test "$2" = damon_disabled; then
  printf N > "$p/enabled"
  for attempt in $(seq 1 50); do
    test "$(cat "$p/kdamond_pid")" = -1 && break
    sleep 0.1
  done
fi
printf 'enabled_after=%s\\n' "$(cat "$p/enabled")"
printf 'worker_after=%s\\n' "$(cat "$p/kdamond_pid")"
'''.replace('PARAMETERS', shlex.quote(PARAMETERS))


def parse(text, mode, phase):
    assert mode in MODES and phase in ('prepare', 'before_task', 'after_task')
    fields = dict(line.split('=', 1) for line in text.splitlines())
    assert len(text.splitlines()) == len(fields) == 5
    assert set(fields) == {'kernel', 'enabled_before', 'worker_before', 'enabled_after', 'worker_after'}
    assert fields['kernel'] == '6.1.175'
    def check(suffix, expected):
        assert fields['enabled_' + suffix] == expected
        pid = int(fields['worker_' + suffix])
        assert pid > 0 if expected == 'Y' else pid == -1
    check('before', 'Y' if phase == 'prepare' or mode == 'default' else 'N')
    check('after', 'Y' if mode == 'default' else 'N')
    return fields


def instrument(text, helper, mode):
    assert mode in MODES
    def call(phase):
        return 'python3 ' + shlex.quote(str(helper)) + ' "$sandbox_id" ' + mode + ' ' + phase + ' "$raw_dir/damon-' + phase + '.json"\n'
    anchor = 'aenv upload "$sandbox_id" "$workload_root/actions" /workspace/replay-actions'
    assert text.count(anchor) == 1
    text = text.replace(anchor, 'sha256sum "$0" > "$raw_dir/damon-host-runner-sha256.txt"\n' + call('prepare') + anchor)
    anchor = 'task_window_start_ns="$(date +%s%N)"'
    assert text.count(anchor) == 1
    text = text.replace(anchor, call('before_task') + anchor)
    anchor = '# Delete before rebuilding the report so the summary can confirm cleanup.'
    assert text.count(anchor) == 1
    return text.replace(anchor, call('after_task') + anchor)


def audit(raw, mode):
    sid = (raw / 'sandbox-id.txt').read_text().strip()
    assert str(uuid.UUID(sid)) == sid
    receipts = {}
    for phase in ('prepare', 'before_task', 'after_task'):
        receipt = json.loads((raw / f'damon-{phase}.json').read_text())
        assert receipt['status'] == 'PASS' and receipt['sandbox_id'] == sid
        assert receipt['exit_code'] == 0
        assert receipt['mode'] == mode and receipt['phase'] == phase
        assert receipt['observations'] == parse(receipt['stdout'], mode, phase)
        assert 0 < receipt['controller_elapsed_ns'] < 30 * 10**9
        receipts[phase] = receipt
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sandbox_id'); parser.add_argument('mode', choices=MODES)
    parser.add_argument('phase', choices=('prepare', 'before_task', 'after_task'))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    assert str(uuid.UUID(args.sandbox_id)) == args.sandbox_id
    started = time.monotonic_ns()
    receipt = dict(status='FAIL', sandbox_id=args.sandbox_id, mode=args.mode, phase=args.phase)
    try:
        program = 'set -- ' + args.phase + ' ' + args.mode + '\n' + PROGRAM
        p = subprocess.run(['aenv', 'exec', args.sandbox_id, 'bash', '-lc', program], capture_output=True, text=True, timeout=25)
        receipt.update(stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode)
        p.check_returncode()
        receipt['observations'] = parse(p.stdout, args.mode, args.phase)
        receipt['status'] = 'PASS'
    except Exception as exc:
        receipt['error'] = repr(exc)
    receipt['controller_elapsed_ns'] = time.monotonic_ns() - started
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    return 0 if receipt['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
