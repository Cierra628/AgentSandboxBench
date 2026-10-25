"""Original TrEnv-X Exp2 snapshot promotion on a private service and fixed volume."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import traceback
from types import SimpleNamespace
from run_exp2 import ROOT, run, validate, sha, write
from smoke_trenvx_service import BIN, CH, KERNEL, command
from isolated_storage import configured_boundary
from experiment_disk_guard import guarded_run
from archive_trenvx_run_data import archive_run_data
from trenvx_diagnostic_metrics import vmm_snapshot
from host_sandbox_monitor import identity

SOURCE = Path('/var/lib/trenvx/templates/prettier-6604-exp2-ch-dax')
PROMOTER = ROOT/'IncrementalDAX_moti/motivation/experiments/exp2-multi-rounds/systems/trenvx/client.py'
BASE = SOURCE.name


def copy_image(source, target):
    # Btrfs cp --reflink shares the immutable base in the original experiment.
    # On this private ext4 volume share only that read-only file through a link.
    if source.name=='rootfs.ext4' and source.parent.name=='image':
        os.link(source,target)
    else:
        subprocess.run(['cp','--reflink=auto','--sparse=always','--preserve=mode,timestamps',str(source),str(target)],check=True,timeout=90)


def promoter():
    spec=importlib.util.spec_from_file_location('mentor_exp2_client',PROMOTER)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.copy_reflink=copy_image
    return module.promote_template


class CHBinding:
    def __init__(self, group): self.group=group
    def owned(self, pid):
        try:return (Path('/proc')/str(pid)/'cgroup').read_text().strip().split('::')[1].startswith('/'+self.group+'/')
        except OSError:return False
    def resolve(self, sid):
        proof=vmm_snapshot(sid,self.group,CH)
        return dict(identity(proof['pid']),cgroup='/'+self.group+'/'+sid,binding_source='owned TrEnv-X cgroup and exact Cloud Hypervisor sandbox socket argument')


class Bridge:
    def __init__(self, out):
        from sandbox_sdk.sandbox import Sandbox
        import sandbox_sdk.sandbox.sandbox_connection as connection
        import sandbox_sdk.sandbox.main as main
        connection.ORCHESTRATOR_PORT=15005
        main.ORCHESTRATOR_PORT=15005
        def direct(self, port=None): return f'{self._sandbox.private_ip}:{port or 49982}'
        connection.SandboxConnection.get_sbx_url=direct
        self.Sandbox=Sandbox;self.out=out;self.data=out/'data';self.live={};self.templates={};self.snapshots={}
        self.promote=promoter();self.binding=CHBinding('asb-trenvx-'+out.name)
        self.provenance=json.loads((out/'config.json').read_text())
        self.loop=asyncio.new_event_loop();self.thread=threading.Thread(target=self.loop.run_forever,daemon=True);self.thread.start()
        self.counter=0
    async def execute(self, sid, args, timeout):
        process=await self.live[sid].simple_process.start(shlex.join(list(map(str,args))),user='root')
        value=await process.wait(timeout=timeout)
        if value.exit_code:raise RuntimeError(f'guest tool exit {value.exit_code}: {value.stderr}')
        return value.stdout
    async def delete_sandbox(self, sid, close_first=True):
        timings=[]
        async def measured(name, operation):
            started=time.monotonic_ns()
            try:await operation()
            finally:timings.append(dict(stage=name,duration_ns=time.monotonic_ns()-started))
        async def close():await self.live[sid].close()
        async def kill():await self.Sandbox.kill(sid,target_addr='127.0.0.1')
        async def settle():await asyncio.sleep(1.5)
        try:
            if close_first:
                try:await measured('sdk-close',close)
                finally:await measured('delete-rpc',kill)
                await measured('cleanup-wait',settle)
            else:
                await measured('delete-rpc',kill);await measured('cleanup-wait',settle)
                await measured('sdk-close',close)
            del self.live[sid]
        finally:
            path=self.out/'delete-timings.json'
            records=json.loads(path.read_text()) if path.exists() else []
            records.append(dict(sandbox_id=sid,close_first=close_first,timings=timings))
            write(path,records)
    async def operate(self, argv, timeout):
        verb=argv[0]
        if verb=='start':
            template=BASE if '--cold' in argv else argv[1]
            sb=await self.Sandbox.create(template=template,target_addr='127.0.0.1',timeout=timeout)
            self.live[sb.id]=sb;self.templates[sb.id]=template;return sb.id
        if verb=='exec':return await self.execute(argv[1],argv[2:],timeout)
        if verb=='upload':
            _,sid,local,remote=argv;local=Path(local)
            if local.is_dir():
                self.counter+=1;pack=self.out/f'upload-{self.counter}.tar.gz'
                with tarfile.open(pack,'w:gz') as tar:tar.add(local,arcname='.')
                with pack.open('rb') as stream:path=await self.live[sid].upload_file(stream,timeout=timeout)
                await self.execute(sid,['mkdir','-p',remote],timeout)
                await self.execute(sid,['tar','-C',remote,'-xzf',path],timeout)
            else:
                with local.open('rb') as stream:path=await self.live[sid].upload_file(stream,timeout=timeout)
                await self.execute(sid,['cp',path,remote],timeout)
            return ''
        if verb=='download':
            _,sid,remote,dest=argv;dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
            self.counter+=1;pack=f'/tmp/asb-exp2-download-{self.counter}.tar.gz'
            path=Path(remote)
            await self.execute(sid,['tar','-C',path.parent,'-czf',pack,path.name],timeout)
            data=await self.live[sid].download_file(pack,timeout=timeout)
            archive=self.out/f'download-{self.counter}.tar.gz';archive.write_bytes(data)
            with tarfile.open(archive,'r:gz') as tar:tar.extractall(dest,filter='data')
            return ''
        if verb=='snapshot':
            sid=argv[2];alias=argv[argv.index('--name')+1]
            value=await self.live[sid].snapshot(delete=False,timeout=timeout)
            path=Path(value.path);assert path.is_relative_to(self.data/'templates')
            self.snapshots[alias]=(sid,path);return str(path)
        if verb=='promote':
            sid,alias=argv[1:];saved_sid,path=self.snapshots[alias];assert saved_sid==sid
            self.promote(self.data,self.templates[sid],alias,sid,path)
            shutil.rmtree(path);return ''
        if verb=='delete':
            await self.delete_sandbox(argv[1]);return ''
        if verb=='template' and argv[1]=='delete':
            alias=argv[2];path=self.data/'templates'/alias
            if path.exists():shutil.rmtree(path)
            if alias in self.snapshots:
                snapshot=self.snapshots.pop(alias)[1]
                if snapshot.exists():shutil.rmtree(snapshot)
            return ''
        if verb=='list':
            rows=await self.Sandbox.list(target_addr='127.0.0.1')
            return json.dumps([{'sandboxID':row.sandbox_id} for row in rows])
        raise ValueError('unsupported Exp2 bridge call: '+verb)
    def command(self, label, argv, timeout):
        future=asyncio.run_coroutine_threadsafe(self.operate(argv,timeout),self.loop)
        try:return future.result(timeout=timeout+5)
        except BaseException:future.cancel();raise
    def close(self):
        assert not self.live
        self.loop.call_soon_threadsafe(self.loop.stop);self.thread.join(timeout=5)
        assert not self.thread.is_alive();self.loop.close()


def prepare(out):
    for label,argv in [('mount-private',['mount','--make-rprivate','/']),('netns-private',['mount','-t','tmpfs','tmpfs','/run/netns']),
                       ('tmp-private',['mount','-t','tmpfs','-o','size=1G','tmpfs','/tmp'])]:command(out,label,argv)
    for label,argv in [('loopback',['link','set','lo','up']),('dummy',['link','add','asb-uplink','type','dummy']),
        ('dummy-address',['addr','add','198.18.0.1/30','dev','asb-uplink']),('dummy-up',['link','set','asb-uplink','up']),
        ('dummy-route',['route','add','default','via','198.18.0.2','dev','asb-uplink'])]:command(out,label,['ip',*argv])
    command(out,'forwarding',['sysctl','-w','net.ipv4.ip_forward=1'])
    data=out/'data';data.mkdir();(data/'kernels').symlink_to('/var/lib/trenvx/kernels',target_is_directory=True)
    target=data/'templates'/BASE/'image';target.mkdir(parents=True)
    for source in (SOURCE/'image').iterdir():
        assert source.is_file() and not source.is_symlink()
        command(out,'copy-'+source.name,['cp','--reflink=never','--sparse=always',source,target/source.name])
        assert sha(source)==sha(target/source.name)
    shutil.copy2(SOURCE/'template.toml',target.parent/'template.toml')
    (target/'rootfs.ext4').chmod(0o444)
    old=str(SOURCE/'run');new=str(target.parent/'run')
    for name in ('config.json','state.json'):
        p=target/name;text=p.read_text();assert old in text;p.write_text(text.replace(old,new));assert old not in p.read_text()
    cfg=f'''ch_binary_path = "{CH}"
data_root = "{data}"
[orchestrator]
host = "127.0.0.1"
port = 15005
subnet = "10.173.0.0/16"
cgroup_name = "asb-trenvx-{out.name}"
[template_manager]
subnet = "10.165.0.0/30"
kernel_debug_output = false
rootfs_build_mode = "normal"
template_id = "{BASE}"
envd_path = "{ROOT/'runtime/bin/envd-task-cgroup'}"
'''
    (out/'controller.toml').write_text(cfg)


def diagnose_delete(bridge):
    records=[]
    for close_first in (False,True):
        sid=bridge.command('diagnostic-start',['start','--cold'],30)
        marker=bridge.command('diagnostic-tool',['exec',sid,'printf','delete-order-tool-ok'],10)
        assert marker=='delete-order-tool-ok'
        future=asyncio.run_coroutine_threadsafe(bridge.delete_sandbox(sid,close_first),bridge.loop)
        future.result(timeout=40)
        assert json.loads(bridge.command('diagnostic-list',['list'],10))==[]
        group=Path('/sys/fs/cgroup')/bridge.binding.group/sid
        assert not group.exists() or not (group/'cgroup.procs').read_text().strip()
        records.append(dict(sandbox_id=sid,close_first=close_first,tool_marker=marker,native_list_empty=True,owned_cgroup_empty=True))
    write(bridge.out/'delete-diagnostic.json',dict(status='PASS',runs=records))


def inner(out, diagnose=False):
    out=out.resolve();service=None;bridge=None;result=dict(status='FAIL',checks={});started=time.monotonic_ns()
    try:
        prepare(out);result['checks']['private_template_prepare']='PASS'
        for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):os.environ.pop(k,None)
        with (out/'orchestrator.log').open('wb') as log:
            service=subprocess.Popen([BIN/'orchestrator','--config',out/'controller.toml'],stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+15
            while True:
                try:
                    with socket.create_connection(('127.0.0.1',15005),timeout=.3):break
                except OSError:
                    assert service.poll() is None and time.monotonic()<deadline,'private orchestrator unavailable';time.sleep(.1)
            bridge=Bridge(out)
            if diagnose:
                diagnose_delete(bridge);result['checks']['delete_order_diagnostic']='PASS'
            rc=run(SimpleNamespace(cache_mode='drop',check_only=False,output=out/'replay',config_home=None,container='owned-trenvx',service_url=None),bridge)
            assert rc==0
            result['checks'].update(exp2_four_rounds_restore_state_and_continuation='PASS',sdk_cleanup='PASS')
            result['status']='PASS'
    except BaseException as error:
        result['error']=repr(error);(out/'inner-error.txt').write_text(traceback.format_exc())
    finally:
        if bridge:
            try:bridge.close()
            except Exception as error:result.update(status='FAIL',bridge_cleanup_error=repr(error))
        if service:
            service.terminate()
            try:service.wait(timeout=15)
            except subprocess.TimeoutExpired:service.kill();service.wait(timeout=5)
            result['checks']['service_shutdown']='PASS' if service.returncode==0 else 'FAIL'
            if service.returncode:result['status']='FAIL'
        result['controller_elapsed_ns']=time.monotonic_ns()-started;write(out/'inner-result.json',result)
    return 0 if result['status']=='PASS' else 1


def outer(diagnose=False):
    cfg,_=validate();boundary=configured_boundary();assert boundary and os.geteuid()==0
    ident=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid());out=boundary.raw_root/ident;out.mkdir()
    provenance=dict(scenario='exp2',seed=str(SOURCE/'image/rootfs.ext4'),seed_sha256=sha(SOURCE/'image/rootfs.ext4'),
        source_template=str(SOURCE),source_files={p.name:sha(p) for p in (SOURCE/'image').iterdir()},
        promoter_sha256=sha(PROMOTER),source_image=cfg['source_image'],ch_sha256=sha(CH),kernel_sha256=sha(KERNEL),
        source_revisions=json.loads((ROOT/'configs/source-revisions.json').read_text()),
        scope='existing pinned local Exp2 template copied and hash-checked; original derived Docker tag, not a new digest rebuild; sparse copies and shared immutable base on private ext4')
    write(out/'config.json',provenance)
    for name in ['run_exp2.py','run_exp2_trenvx.py','report_exp2.py','sandbox_runtime_monitor.py','host_sandbox_monitor.py','guest_checkpoint_probe.py']:
        shutil.copy2(ROOT/'scripts'/name,out/name)
    group=Path('/sys/fs/cgroup')/('asb-trenvx-'+ident);result=dict(status='FAIL',run_id=ident,checks={},restore_semantics='complete VM state via native snapshot and writable-rootfs template promotion')
    try:
        with (out/'namespace.log').open('wb') as log:
            argv=['unshare','--mount','--net','--pid','--fork','--mount-proc','--kill-child',sys.executable,'-B',Path(__file__),'--inner',out]
            if diagnose:argv.append('--diagnose-delete')
            guarded_run(argv,directory=out,log=log,timeout=540,free_bytes=boundary.host_free)
        result.update(json.loads((out/'inner-result.json').read_text()))
    except BaseException as error:
        result['outer_error']=repr(error);(out/'outer-error.txt').write_text(traceback.format_exc())
        if (out/'inner-result.json').exists():result.update(json.loads((out/'inner-result.json').read_text()));result['status']='FAIL'
    finally:
        cleanup=dict(cgroup_removed=True)
        try:
            if group.exists():
                for p in sorted(group.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                    if p.is_dir():p.rmdir()
                group.rmdir()
        except OSError as error:cleanup.update(cgroup_removed=False,error=repr(error));result['status']='FAIL'
        p=subprocess.run(['losetup','--list','--noheadings','--output','BACK-FILE'],capture_output=True,text=True,timeout=10)
        cleanup.update(loop_query_exit_code=p.returncode,owned_loop_backings=[s for s in p.stdout.splitlines() if str(out) in s]);result['host_cleanup']=cleanup
        if p.returncode or cleanup['owned_loop_backings']:result['status']='FAIL'
        write(out/'result.json',result)
        if (out/'data').exists() and cleanup['cgroup_removed'] and not cleanup['owned_loop_backings']:
            try:
                archive=archive_run_data(out,root=boundary.raw_root,compressor='pigz-fast')
                result['checks']['data_archive']='PASS';result['data_retention']=archive
            except BaseException as error:result.update(status='FAIL',archive_error=repr(error))
        write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out));return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inner',type=Path);parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--diagnose-delete',action='store_true',help='Compare both delete orders on two disposable VMs before the four-round replay')
    args=parser.parse_args()
    if args.check_only:
        validate()
        for p in [SOURCE/'template.toml',SOURCE/'image/rootfs.ext4',SOURCE/'image/memory-ranges',CH,KERNEL,BIN/'orchestrator',PROMOTER]:assert p.is_file(),str(p)
        print('status=VALIDATED experiment=exp2 backend=trenvx')
    else:raise SystemExit(inner(args.inner,args.diagnose_delete) if args.inner else outer(args.diagnose_delete))
