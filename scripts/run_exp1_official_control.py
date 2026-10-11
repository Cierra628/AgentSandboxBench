"""Official task negative control in one disposable, explicitly owned AgentENV VM."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import tomllib
import traceback
from exp1_official_task import guest_command, grade

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('task', type=Path)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text())
    runtime = (ROOT/cfg.get('runtime_dir', 'runtime')).resolve()
    server = ROOT/cfg['server_result']
    cid = (server/'container-id.txt').read_text().strip()
    env = dict(os.environ, PATH=str(runtime/'bin')+':'+os.environ['PATH'], XDG_CONFIG_HOME=str(runtime/'config'))
    out = ROOT/'.artifacts/exp1-official-control'/(time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+f'-{os.getpid()}')
    os.umask(0o077); out.mkdir(parents=True)
    sid = None
    result = dict(status='FAIL', scope='official unfixed negative control; not a replay or performance sample')

    def interrupted(signum, frame):
        raise RuntimeError(f'negative control interrupted: {signum}')
    signal.signal(signal.SIGTERM, interrupted)

    def call(name, argv, timeout=30):
        p = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
        (out/(name+'.stdout')).write_text(p.stdout)
        (out/(name+'.stderr')).write_text(p.stderr)
        p.check_returncode()
        return p.stdout.strip()

    try:
        info = json.loads(call('server', ['docker', 'inspect', cid]))[0]
        assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment') == 'initial-smoke'
        assert tomllib.loads((runtime/'config/aenv/credentials').read_text())['url'] == 'http://'+(server/'address.txt').read_text().strip()
        repo = ROOT/cfg['repository']
        import re
        text = (repo/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/config.env').read_text()
        image = re.search(r"WORKLOAD_IMAGE=\$\{WORKLOAD_IMAGE:-'([^']+)'\}", text).group(1)
        sid = call('create', ['aenv', 'start', '--cold', image, '--detach', '--timeout', '600', '--cpu', '2', '--memory', '4096'], 150)
        assert re.fullmatch(r'[0-9a-fA-F-]{36}', sid)
        (out/'sandbox-id.txt').write_text(sid+'\n')
        call('ready', ['aenv', 'exec', sid, 'true'])
        call('upload-tests', ['aenv', 'upload', sid, str(args.task/'test.patch'), '/workspace/official-test.patch'], 60)
        output = call('official-tests', ['aenv', 'exec', sid, 'bash', '-lc', guest_command(args.task, '/workspace/official-test.patch')], 145)
        result['evaluation'] = grade(args.task, output, baseline=True)
        result['status'] = 'PASS'
    except Exception:
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if sid:
            try:
                call('delete', ['aenv', 'delete', sid], 60)
                listing = json.loads(call('list-after', ['aenv', 'list', '--output', 'json']))
                assert not any(row['sandboxID'] == sid for row in listing)
                result['cleanup'] = 'PASS'
            except Exception:
                result.update(status='FAIL', cleanup='FAIL')
                (out/'cleanup-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(f"status={result['status']} result_dir={out}", flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
