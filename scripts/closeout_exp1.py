"""Bounded Exp1 acceptance diagnostics, separate from performance experiments."""
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import traceback

ROOT=Path(__file__).resolve().parent.parent


def main():
    os.umask(0o077)
    out=ROOT/'.artifacts/exp1-closeout'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    cfg=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())
    senior=ROOT/'IncrementalDAX_moti'
    workload=senior/'motivation/experiments/exp1-single-app-smoke/workloads/prettier-14400'
    runner=senior/'motivation/experiments/exp1-single-app-smoke/systems/agentenv/guest-runner.sh'
    cid=(ROOT/'.artifacts/server-smoke-20260922T114006Z-1387374/container-id.txt').read_text().strip()
    env=dict(os.environ,PATH=str(ROOT/'runtime/bin')+':'+os.environ['PATH'],
        XDG_CONFIG_HOME=str(ROOT/'runtime/config'),AGENTENV_CONTAINER=cid)
    sid=None
    checks={}
    stage='preflight'
    status='FAIL'
    def interrupted(signum,frame): raise RuntimeError(f'interrupted {signum}')
    signal.signal(signal.SIGTERM,interrupted)
    def run(label,args,timeout=60):
        started=time.monotonic_ns()
        p=subprocess.run(args,cwd=ROOT,env=env,capture_output=True,timeout=timeout)
        returned=time.monotonic_ns()
        (out/(label+'.stdout')).write_bytes(p.stdout)
        (out/(label+'.stderr')).write_bytes(p.stderr)
        archived=time.monotonic_ns()
        (out/(label+'.json')).write_text(json.dumps(dict(argv=args,returncode=p.returncode,
            invocation_ns=returned-started,archive_ns=archived-returned),indent=2)+'\n')
        return p
    def checked(label,args,timeout=60):
        p=run(label,args,timeout);p.check_returncode();return p
    def guest(label,command,timeout=60):
        return run(label,['aenv','exec',sid,'bash','-lc',command],timeout)
    try:
        info=json.loads(checked('server',['docker','inspect',cid]).stdout)[0]
        assert info['State']['Running'] and info['Config']['Labels'].get('agentenv.experiment')=='initial-smoke'
        assert json.loads(checked('listing-before',['aenv','list','--output','json']).stdout)==[]
        snapshot=out/'code';snapshot.mkdir()
        paths=[Path(__file__),ROOT/'scripts/guest_closeout_probe.py',ROOT/'scripts/exp1_independent_check.cjs',runner,workload/'actions.tsv']
        for path in paths: (snapshot/path.name).write_bytes(path.read_bytes())
        (out/'config.json').write_text(json.dumps(dict(image=cfg['workload_image'],cpu=2,memory_mib=4096,
            source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            scope='diagnostic only; no monitoring overhead estimate; task tests outside frozen replay'),indent=2)+'\n')
        for line in (workload/'actions.tsv').read_text().splitlines():
            step,digest,_=line.split('\t',2)
            assert hashlib.sha256((workload/'actions'/f'{step}.sh').read_bytes()).hexdigest()==digest
        sid=checked('create',['aenv','start','--cold',cfg['workload_image'],'--detach','--timeout','900','--cpu','2','--memory','4096'],180).stdout.decode().strip()
        assert len(sid)==36
        (out/'sandbox-id.txt').write_text(sid+'\n')
        for attempt in range(30):
            if guest('ready','true',15).returncode==0: break
            time.sleep(1)
        else: raise TimeoutError('guest unavailable')
        for path,target in [(workload/'actions','/workspace/replay-actions'),(workload/'actions.tsv','/workspace/replay-actions.tsv'),
                            (runner,'/workspace/guest-runner.sh'),(ROOT/'scripts/guest_closeout_probe.py','/workspace/probe.py'),
                            (ROOT/'scripts/exp1_independent_check.cjs','/workspace/check.cjs')]:
            checked('upload-'+path.name,['aenv','upload',sid,str(path),target],120)
        stage='independent_before'
        before=guest('independent-before','node /workspace/check.cjs')
        before_result=json.loads(before.stdout)
        assert before.returncode==1 and not before_result['svg']['javascript_formatted']
        assert before_result['html_control']['javascript_formatted']
        checks[stage]='PASS'
        stage='replay'
        guest('replay','GUEST_MEMORY_SAMPLING=0 DROP_GUEST_CACHES=0 ACTION_TIMEOUT_SECONDS=60 bash /workspace/guest-runner.sh',180).check_returncode()
        checked('download-replay',['aenv','download',sid,'/workspace/artifacts/replay',str(out)],120)
        reference=ROOT/'.artifacts/platform-exp1-agentenv-default/raw/20260924T035910Z-1677178/replay'
        assert (out/'replay/patch.diff').read_bytes()==(reference/'patch.diff').read_bytes()
        with (out/'replay/steps.tsv').open() as f: rows=list(csv.DictReader(f,delimiter='\t'))
        assert [r['step'] for r in rows]==[f'{i:03}' for i in range(1,27)]
        assert {r['step']:int(r['exit_code']) for r in rows if int(r['exit_code'])}=={'008':127,'016':127}
        checks[stage]='PASS'
        stage='independent_after'
        after=guest('independent-after','node /workspace/check.cjs');after.check_returncode()
        assert all(v['javascript_formatted'] and v['idempotent'] for v in json.loads(after.stdout).values())
        checks[stage]='PASS'
        stage='upstream_svg_tests'
        guest('upstream-tests','cd /testbed && CI=1 node --experimental-vm-modules node_modules/jest/bin/jest.js tests/format/html/svg --runInBand --ci',120).check_returncode()
        checks[stage]='PASS'
        stage='timing'
        guest('timed-action','python3 /workspace/probe.py timing /workspace/timing.json').check_returncode()
        checked('download-timing',['aenv','download',sid,'/workspace/timing.json',str(out)])
        assert (out/'timed-action.stdout').read_bytes()==(reference/'stdout/024.log').read_bytes()
        checks[stage]='PASS'
        stage='task_cgroup'
        probe=guest('task-cgroup','python3 /workspace/probe.py freezer /workspace/freezer.json',30)
        checked('download-freezer',['aenv','download',sid,'/workspace/freezer.json',str(out)])
        probe.check_returncode()
        assert json.loads((out/'freezer.json').read_text())['status']=='PASS'
        checks[stage]='PASS'
        status='PASS'
    except Exception:
        checks[stage]='FAIL'
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if sid:
            try:
                checked('cleanup',['aenv','delete',sid],90)
                listing=json.loads(checked('listing-after',['aenv','list','--output','json']).stdout)
                assert not any(x['sandboxID']==sid for x in listing)
                checks['cleanup']='PASS'
            except Exception:
                checks['cleanup']='FAIL';status='FAIL'
                (out/'cleanup-failure.txt').write_text(traceback.format_exc())
        (out/'result.json').write_text(json.dumps(dict(status=status,last_stage=stage,checks=checks),indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
