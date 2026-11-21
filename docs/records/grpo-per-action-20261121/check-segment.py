"""Run in a private mount/network namespace, with no live guest or service operations."""
import csv, json, os, pathlib, shutil, subprocess as sp, sys, tempfile, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
from grpo_action_gate import gated_runner
from sandbox_runtime_monitor import boundary_runner, GUEST_ROOT
from run_prettier6604_grpo import validated_export
clock=time.monotonic_ns
cmd=sp.check_call
def rows(path):return list(csv.DictReader(path.open(),delimiter='\t'))
def run():
    os.chdir(ROOT)
    export=ROOT/json.load(open('configs/platform-prettier6604-grpo.json'))['frozen_export']
    validated_export(export)
    w=export/'workload'; role=seg='early_1'
    hist=ROOT/'.artifacts/storage-isolated-service/20261107T084731Z-3799106/prettier6604/rollouts'/role/seg
    manifest=w/'segments'/seg/'actions.tsv'
    assert manifest.read_bytes()==(hist/'actions.tsv').read_bytes()
    img=json.load(open('configs/prettier6604-backbone.json'))['source_image']
    meta=json.loads(sp.check_output(['docker','image','inspect',img]))[0]
    data=meta['GraphDriver']['Data']; lowers=data['UpperDir']+':'+data['LowerDir']
    with tempfile.TemporaryDirectory() as t:
        t=Path(t); root=t/'root'; root.mkdir()
        for name in ['upper','work']: (t/name).mkdir()
        cmd(['mount','-t','overlay','overlay','-o',f'lowerdir={lowers},upperdir={t}/upper,workdir={t}/work',root])
        try:
            cmd(['mount','-t','proc','-o','ro,nosuid,nodev,noexec','proc',root/'proc'])
            runner=root/'asb-segment';shutil.copytree(w,runner)
            (root/'dev/null').touch(exist_ok=True);cmd(['mount','--bind','/dev/null',root/'dev/null'])
            script=runner/'guest-runner.sh';(root/GUEST_ROOT.lstrip('/')).mkdir(parents=True,exist_ok=True);script.write_text(gated_runner(boundary_runner(script.read_text(),guest_phases=True)))
            out=root/'asb-result';env=dict(x.split('=',1) for x in meta['Config']['Env'])
            env.update(HOME='/root',ROUND=seg,BRANCH=role,ACTIONS_ROOT='/asb-segment/segments',OUTPUT_DIR='/asb-result',ACTION_TIMEOUT_SECONDS='600')
            p=sp.Popen(['/usr/sbin/chroot',root,'/bin/bash','/asb-segment/guest-runner.sh'],env=env,start_new_session=True)
            trace=[]
            try:
                for i,r in enumerate(rows(manifest),1):
                    step=r['step']; token=f'{role}:{seg}:{step}'; key=out/(step+'.ready')
                    deadline=time.monotonic()+90
                    while not key.exists() and p.poll() is None and time.monotonic()<deadline:time.sleep(.01)
                    assert key.read_text().strip()==token
                    observed=clock(); time.sleep(.08)
                    assert len(rows(out/'steps.tsv'))==i and not (out/'stdout'/f'{i+1:03}.log').exists() and p.poll() is None
                    tmp=out/(step+'.tmp');tmp.write_text(token+'\n')
                    # Record immediately after publishing. Next guest start is measured on the same kernel clock.
                    tmp.rename(out/(step+'.go'));release=clock()
                    trace.append(dict(step=step,observed_ns=observed,release_ns=release))
                assert p.wait(timeout=10)==0
            finally:
                if p.poll() is None:
                    import signal
                    os.killpg(p.pid,signal.SIGKILL);p.wait()
            assert (out/'actions.tsv').read_bytes()==manifest.read_bytes()
            actual=rows(out/'steps.tsv');expected=rows(hist/'steps.tsv')
            for a,b,event in zip(actual,expected,trace,strict=True):
                assert all(a[k]==b[k] for k in ('branch','round','step','exit_code'))
                event.update(exit_code=int(a['exit_code']),start_ns=int((out/(a['step']+'.start')).read_text()))
            assert all(a['release_ns']<b['start_ns'] for a,b in zip(trace,trace[1:]))
            diff=(out/'patch.diff').read_bytes();assert diff and diff==(hist/'patch.diff').read_bytes()
            assert json.load(open(w/'rollouts.json'))[role][-1]==seg
            return dict(status='PASS',image=img,history=str(hist.relative_to(ROOT)),patch_bytes=len(diff),events=trace)
        finally:cmd(['umount','-R','-l',root])
if __name__=='__main__':
    if sys.argv[1:]==['--private']:
        assert os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
        print(json.dumps(run(),indent=2))
    else:
        # Inspect Docker via local unix socket; no network or container is started.
        sys.exit(sp.call(['unshare','--fork','--pid','--mount','--net','--propagation','private',sys.executable,'-B',__file__,'--private']))
