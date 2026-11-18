"""Rebuild an idle experiment source from verified dependencies; never restore old state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import tomllib
import urllib.request

from prepare_prettier6604_cache import prepare, release
from validate_isolated_agentenv import ROOT, IMAGE, config_value, immutable_manifest
from isolated_storage import write_mounts


def call(argv, timeout=30):
    return subprocess.check_output(list(map(str, argv)), text=True, stderr=subprocess.PIPE, timeout=timeout).strip()


def main():
    assert os.geteuid() == 0
    os.umask(0o077)
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+str(os.getpid())
    out=ROOT/'.artifacts'/('source-service-rebuilt-'+tag); out.mkdir()
    cache=ROOT/'.artifacts/sphinx-backbone'/('source-deps-'+tag)
    result=dict(status='FAIL', stage='preflight', result_dir=str(out),
                semantics='new idle source service; no original process, state database, secrets or guest restored')
    container=None
    try:
        assert shutil.disk_usage(ROOT).free>50*2**30
        assert Path('/dev/kvm').is_char_device() and Path('/dev/ublk-control').is_char_device()
        call(['docker','image','inspect',IMAGE])
        cfg=json.loads((ROOT/'configs/platform-sphinx7590-cache-baseline.json').read_text())
        result['stage']='verified-dependencies'
        prepare(cache, ROOT/cfg['cache_backup_directory'], 'sphinx7590')
        seed=json.loads((cache/'seed.json').read_text())
        for name in ('env','run','tmp'): (out/name).mkdir()
        shutil.copytree(Path(seed['source_env'])/'deps', out/'env/deps')
        assert immutable_manifest(out/'env/deps')==seed['parts']['deps']
        result['dependencies']=dict(source_backup=seed['source_backup'], verified=True,
                                    bytes=sum(v.get('size',0) for v in seed['parts']['deps'].values()))
        release(cache)
        old=ROOT/'.artifacts/server-smoke-20260922T114006Z-1387374'
        config=(old/'config.diagnostic.toml').read_text()
        changes=[('image.cache.gc','enabled','false'),('pool.network','maintenance_enabled','false'),
                 ('pool.block','enabled','false'),('pool.firecracker','enabled','false'),
                 ('pool.firecracker','startup_prewarm','false'),('ublk','daemon_metrics_listen_addr','""'),
                 ('network.internal','host_interaction_cidr','"10.221.0.0/16"'),
                 ('network.internal','veth_cidr','"10.222.0.0/16"')]
        routes=call(['ip','-j','route','show','table','all'])
        assert '10.221.' not in routes and '10.222.' not in routes
        for section,key,value in changes: config=config_value(config,section,key,value)
        tomllib.loads(config); (out/'config.toml').write_text(config)
        result['config_changes']=[f'{s}.{k}={v}' for s,k,v in changes]
        with socket.socket() as sock: sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        name='asb-source-rebuilt-'+tag
        argv=['docker','create','--name',name,'--label','agentenv.experiment=source-rebuilt',
              '--label','agentsandboxbench.run_id='+out.name,'--privileged','--read-only','--log-driver','none',
              '--network','host','--ulimit','memlock=-1:-1','--mount','type=bind,src=/dev,dst=/dev',
              '--mount',f'type=bind,src={out}/config.toml,dst=/workspace/config/default.toml,readonly']
        for src,dst in write_mounts(out).items(): argv+=['--mount',f'type=bind,src={src},dst={dst}']
        argv+=['-e','AENV_CONFIG_PATH=/workspace/config/default.toml','-e','AENV_HOME_PATH=/workspace/env',
               '-e','AENV_UBLK_DAEMON_BINARY_PATH=/usr/local/bin/uvm-ublk-daemon',
               '-e',f'API_ADDR=127.0.0.1:{port}','--entrypoint','/bin/sh',IMAGE,
               '-c','mkdir -p /workspace/env/logs; exec /server >>/workspace/env/logs/server.log 2>&1']
        result['stage']='new-source-start'
        container=call(argv,60); result.update(container_id=container, service=name, address=f'http://127.0.0.1:{port}')
        (out/'container-id.txt').write_text(container+'\n'); (out/'container-name.txt').write_text(name+'\n')
        call(['docker','start',container])
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            info=json.loads(call(['docker','inspect',container]))[0]
            assert info['State']['Running'], 'new source exited; inspect retained server log'
            try:
                with opener.open(result['address']+'/health',timeout=3) as response:
                    if response.status in (200,204): break
            except OSError: time.sleep(1)
        else: raise TimeoutError('new source health deadline')
        result.update(status='PASS', stage='new-source-ready', image=info['Image'],
                      identity={k:info['State'][k] for k in ['Pid','StartedAt','Running']},
                      source_cache='empty; each real task continues using its existing verified private RAM seed',
                      health='PASS', original_process_restored=False)
    except BaseException as exc:
        result['error']=repr(exc)
        if container:
            try: call(['docker','stop','-t','10',container],30)
            except Exception as stop: result['stop_error']=repr(stop)
    finally:
        if (cache/'resources.json').exists() and not json.loads((cache/'resources.json').read_text()).get('released'):
            try: release(cache)
            except Exception as exc: result.update(status='FAIL',cache_release_error=repr(exc))
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print('status='+result['status']+' stage='+result['stage']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
