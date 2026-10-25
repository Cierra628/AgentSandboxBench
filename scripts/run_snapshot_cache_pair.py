"""One pair of restored guests: shared reads then different file modifications."""
import argparse,json,os,signal,shutil,subprocess,sys,time,traceback
from pathlib import Path
from agentenv_cold_start import create
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--container',required=True);p.add_argument('--config-home',type=Path,required=True)
    p.add_argument('--service-url',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();assert out.parent.name=='controller' and out.parent.parent.name=='mount'
    out.mkdir();env=dict(os.environ,XDG_CONFIG_HOME=str(a.config_home));sids={};workers={};snapshot=None;success=False;events=[];stage='boot'
    result=dict(status='FAIL',task_id='snapshot-cache-pair',vm_pause=False,phases=[],checks={})
    guest='/dev/shm/asb-snapshot-fixture'
    def cli(label,*argv,timeout=60):
        t=time.monotonic_ns();v=subprocess.run([str(ROOT/'runtime/bin/aenv'),*map(str,argv)],env=env,capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(v.stdout);(out/(label+'.stderr')).write_bytes(v.stderr)
        events.append(dict(stage=label,duration_ns=time.monotonic_ns()-t,returncode=v.returncode))
        v.check_returncode();return v.stdout.decode().strip()
    def guest_wait(side,name):
        sid=sids[side]
        cli(side+'-wait-'+name,'exec',sid,'bash','-lc',f'for i in $(seq 1 150); do test -f {guest}/{name} && exit 0; sleep .1; done; cat /tmp/asb-snapshot-fixture.log; exit 1',timeout=20)
    def state(side,name):
        value=json.loads(cli(side+'-'+name,'exec',sids[side],'cat',guest+'/'+name+'.json'))
        (out/(side+'-'+name+'.json')).write_text(json.dumps(value)+'\n');return value
    def wait_local(side,name):
        d=out/'live-cache'/side;deadline=time.monotonic()+30
        while not (d/name).exists():
            assert workers[side].poll() is None,side+' sampler exited'
            assert time.monotonic()<deadline,name+' deadline'
            time.sleep(.05)
    def stop_workers():
        for side in workers:(out/'live-cache'/side/'stop').touch()
        for side,w in workers.items():
            try:code=w.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(w.pid,signal.SIGTERM);w.wait(timeout=10);raise
            assert code==0,side+' sampler failed'
    try:
        image=json.loads((ROOT/'configs/exp1-monitor-calibration-smoke.json').read_text())['workload_image']
        sids['parent']=create(a.config_home,a.service_url,image,request_timeout=90)
        cli('ready','exec',sids['parent'],'true',timeout=15)
        tools=out/'tools';tools.mkdir();shutil.copy2(ROOT/'scripts/guest_snapshot_cache_fixture.py',tools)
        cli('upload','upload',sids['parent'],tools,'/workspace/asb-snapshot-tools')
        cli('fixture-start','exec',sids['parent'],'bash','-lc',f'nohup timeout 210 python3 /workspace/asb-snapshot-tools/guest_snapshot_cache_fixture.py {guest} >/tmp/asb-snapshot-fixture.log 2>&1 </dev/null &')
        guest_wait('parent','ready.json');before=state('parent','ready')
        snapshot='cache-pair-'+out.parents[2].name
        stage='checkpoint';cli('checkpoint','snapshot','create',sids['parent'],'--name',snapshot,timeout=150)
        cli('delete-parent','delete',sids.pop('parent'),timeout=90)
        for side in ['child-a','child-b']:
            log=(out/(side+'-sampler.log')).open('x')
            workers[side]=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/collect_snapshot_child.py'),
                '--output',str(out),'--container',a.container,'--config-home',str(a.config_home),'--side',side],
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True);log.close()
            wait_local(side,'capture-ready')
        stage='restore-two-guests'
        for side in workers:
            sids[side]=cli(side+'-restore','start',snapshot,'--detach','--timeout','1800',timeout=150)
            (out/'live-cache'/side/'sandbox-id.txt').write_text(sids[side]+'\n')
        assert len(set(sids.values()))==2
        for side in workers:wait_local(side,'sampling-ready')
        for command,name,duration in [('observe','waiting',3),('read','read-done',4),('write','write-done',4)]:
            stage=name
            if command=='write':
                cli('child-b-select-branch','exec',sids['child-b'],'bash','-lc',f"printf C >{guest}/write-byte")
            for side in workers:
                cli(side+'-request-'+command,'exec',sids[side],'touch',guest+'/'+command)
                guest_wait(side,name+'.json');value=state(side,name)
                assert all(value[k]==before[k] for k in ['pid','boot_id','start_ticks'])
            left=time.time_ns();time.sleep(duration)
            result['phases'].append(dict(name=name,start_ns=left,end_ns=time.time_ns()))
        stop_workers()
        hashes={}
        for side in workers:
            cli(side+'-finish','exec',sids[side],'touch',guest+'/finish');guest_wait(side,'done')
            r=state(side,'result');assert r['status']=='PASS';hashes[side]=r['sha256']
        assert hashes['child-a']!=hashes['child-b']
        result.update(file_sha256=hashes,checks=dict(checkpoint='PASS',two_restored_guests='PASS',
            process_identity='PASS',file_content_checks='PASS',different_branch_contents='PASS'))
        success=True
    except BaseException as exc:
        result.update(error=repr(exc),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        cleanup={}
        try:stop_workers();cleanup['samplers_stopped']=True
        except BaseException as exc:cleanup['samplers_error']=repr(exc);success=False
        for side,sid in sids.items():
            try:cli(side+'-delete','delete',sid,timeout=90)
            except BaseException as exc:cleanup[side+'-delete-error']=repr(exc);success=False
        cleanup['vms_removed']=not any(k.endswith('delete-error') for k in cleanup)
        if snapshot and success:
            try:cli('delete-snapshot','template','delete',snapshot,timeout=60);cleanup['snapshot_removed']=True
            except BaseException as exc:cleanup['snapshot_error']=repr(exc);success=False
        result.update(status='PASS' if success else 'FAIL',cleanup=cleanup)
        (out/'events.json').write_text(json.dumps(events,indent=2)+'\n');(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1

if __name__=='__main__':raise SystemExit(main())
