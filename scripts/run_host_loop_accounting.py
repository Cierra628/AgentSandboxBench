"""Small loop accounting checks and staged initialization; no sandbox or cache flush."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from host_file_monitor import CGROOT,controller_state,counters,mincore_file
from isolated_storage import GIB,HOST_RESERVE,HOST_MARGIN,available,mount_identity

ROOT=Path(__file__).resolve().parents[1]
CAPACITY=128*2**20
PAYLOAD=64*2**20
SETTLE_SECONDS=2.2


def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def read(path):return json.loads(path.read_text())


def checked_directory(path):
    path=Path(path).resolve()
    if path.parent.parent!=ROOT/'.artifacts/host-loop-accounting' or path.name not in ('io-off','io-on'):
        raise ValueError('expected one new owned host-loop-accounting trial directory')
    return path


def call(out,label,argv,timeout=20):
    with (out/(label+'.stdout')).open('x') as stdout,(out/(label+'.stderr')).open('x') as stderr:
        subprocess.run(list(map(str,argv)),stdout=stdout,stderr=stderr,check=True,timeout=timeout)
    return (out/(label+'.stdout')).read_text().strip()


def cleanup(out):
    """Unmount/detach only the image created in this trial; never delete a failed image."""
    out=checked_directory(out);image=out/'volume.ext4';mount=out/'mount'
    if not image.exists():return dict(mount_removed=not mount.is_mount(),loop_removed=True)
    owned=read(out/'resources.json');info=image.stat()
    assert (info.st_dev,info.st_ino)==(owned['image_device'],owned['image_inode'])
    listing=subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).splitlines()
    if len(listing)>1:raise ValueError('more than one loop device attached to owned image')
    device=listing[0].split(':',1)[0] if listing else None
    if mount.is_mount():
        assert device is not None
        mount_identity(mount,device)
        subprocess.run(['umount',str(mount)],check=True,timeout=20)
    if device:subprocess.run(['losetup','-d',device],check=True,timeout=20)
    assert not subprocess.check_output(['losetup','-j',str(image)],text=True,timeout=10).strip()
    assert not mount.is_mount()
    return dict(mount_removed=True,loop_removed=True)


def compare_delta(baseline,point):
    resident=point['resident_bytes']-baseline['resident_bytes']
    inner=point['inner_after']['file']-baseline['inner_after']['file']
    outer=point['outer_after']['file']-baseline['outer_after']['file']
    return dict(inventory_growth_bytes=resident,inner_file_growth_bytes=inner,outer_file_growth_bytes=outer,
                signed_inventory_minus_inner_growth_bytes=resident-inner,
                outer_only_file_growth_bytes=outer-inner)


def first_shortfall(snapshots):
    return next((name for name,point in snapshots.items() if point['shortfall_bytes']>0),None)


def worker(args):
    if os.geteuid()!=0:raise ValueError('root required for the owned loop fixture')
    out=checked_directory(args.output);image=out/'volume.ext4';mount=out/'mount'
    assert not image.exists() and not mount.exists()
    value=next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    leaf=CGROOT/value.lstrip('/');inner=leaf.parent;outer=inner.parent
    assert inner.name==args.slice and outer.name==args.slice.rsplit('-',1)[0]+'.slice'
    expected='asblp'+args.token+('off' if args.io=='no' else 'on')
    assert outer.name==expected+'.slice' and inner.name==expected+'-trial.slice'
    initialization_only=getattr(args,'initialization_only',False)
    charge_trace=getattr(args,'charge_trace',False)
    if charge_trace and not initialization_only:raise ValueError('charge-trace requires initialization-only')
    if initialization_only and args.io!='yes':raise ValueError('initialization-only requires IOAccounting=yes')
    result=dict(status='FAIL',io_accounting_requested=args.io,host_kernel=os.uname().release,
                image_bytes=CAPACITY,payload_bytes=0 if initialization_only else PAYLOAD,settle_seconds=SETTLE_SECONDS,
                scope='One fresh inner measurement slice and its unique owned outer slice; no guest or task files',snapshots={})
    if initialization_only:result['scope']='One fresh 128 MiB image, staged initialization only; no payload, guest or task files'
    stage='initial-state';success=False;tracer=None
    def interrupted(signum,frame):raise RuntimeError('owned loop trial interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,interrupted)
    def snapshot(name,paths):
        begin=time.monotonic_ns();cpu=time.process_time_ns()
        before=counters(inner);parent_before=counters(outer)
        files=[mincore_file(path,include_resident_indices=True) if charge_trace else mincore_file(path) for path in paths]
        after=counters(inner);parent_after=counters(outer)
        row=dict(start_monotonic_ns=begin,end_monotonic_ns=time.monotonic_ns(),cpu_ns=time.process_time_ns()-cpu,
                 files=files,resident_bytes=sum(f['resident_bytes'] for f in files),
                 inner_before=before,inner_after=after,outer_before=parent_before,outer_after=parent_after)
        row['shortfall_bytes']=max(0,row['resident_bytes']-max(before['file'],after['file']))
        result['snapshots'][name]=row;write(out/'observations.json',result)
        assert all(f['stable'] for f in files),'regular backing changed during residency query'
        return row
    def sync_owned():
        subprocess.run(['sync','-f',str(mount)],check=True,timeout=20)
        fd=os.open(image,os.O_RDONLY|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)
    try:
        assert available(ROOT)>HOST_RESERVE+HOST_MARGIN+CAPACITY
        result['initial_available_bytes']=available(ROOT)
        result['control_before']={name:controller_state(group) for name,group in [('outer',outer),('inner',inner),('leaf',leaf)]}
        result['unit_properties']=subprocess.check_output(['systemctl','show',leaf.name,'--property=IOAccounting','--property=MemoryAccounting'],text=True,timeout=10)
        assert 'IOAccounting='+args.io in result['unit_properties']
        capabilities=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        assert int(capabilities['CapEff'].strip(),16)&(1<<3),'CAP_FOWNER required for honest mincore results'
        result['caller']=dict(uid=os.geteuid(),cap_eff=capabilities['CapEff'].strip())
        if initialization_only:
            assert all(result['control_before'][name]['io_stat_exists'] for name in ('outer','inner','leaf'))
            assert 'io' in result['control_before']['outer']['subtree_control'].split()
            assert 'io' in result['control_before']['inner']['subtree_control'].split()
            snapshot('initial-empty',[])
        stage='allocate-image'
        fd=os.open(image,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_CLOEXEC,0o600)
        try:info=os.fstat(fd)
        finally:os.close(fd)
        write(out/'resources.json',dict(image=str(image),image_device=info.st_dev,image_inode=info.st_ino))
        if charge_trace:
            from host_loop_charge import Capture
            stage='charge-tracer-start'
            tracer=Capture(out,image,inner,outer,leaf,read(out.parent/'charge-layout.json'))
            tracer.start();result['charge_tracer_groups']=tracer.groups
            stage='allocate-image'
        call(out,stage,['fallocate','-l',CAPACITY,image]);info=image.stat()
        assert info.st_size==CAPACITY and info.st_blocks*512>=CAPACITY
        if initialization_only:snapshot('allocated',[image])
        stage='format-owned-image';call(out,stage,['mkfs.ext4','-q','-F','-E','lazy_itable_init=0,lazy_journal_init=0',image])
        if initialization_only:snapshot('formatted',[image])
        stage='attach-owned-loop';device=call(out,stage,['losetup','--find','--show',image])
        assert re.fullmatch(r'/dev/loop\d+',device)
        result['loop_device']=device
        if initialization_only:snapshot('attached',[image])
        mount.mkdir();stage='mount-owned-image';call(out,stage,['mount','-t','ext4','-o','noatime',device,mount])
        result['mount']=mount_identity(mount,device)
        result['loop_direct_io']=(Path('/sys/class/block')/Path(device).name/'loop/dio').read_text().strip()
        assert result['loop_direct_io']=='0'
        stage='baseline';sync_owned()
        if initialization_only:snapshot('mounted-synced',[image])
        time.sleep(SETTLE_SECONDS)
        baseline=snapshot('baseline',[image])
        if initialization_only:
            assert not (mount/'synthetic.bin').exists()
            result['first_detected_shortfall_stage']=first_shortfall(result['snapshots'])
            result['classification']='INITIALIZATION_STAGE_OBSERVED_OWNERSHIP_AND_STATS_TIMING_UNRESOLVED'
            if charge_trace:
                from host_loop_charge import analyze
                stage='charge-tracer-stop';capture=tracer;tracer=None
                result['charge_trace']=capture.stop()
                result['charge_classification']=analyze(out,result['snapshots'],{inner.stat().st_ino,leaf.stat().st_ino})
                assert result['charge_classification']['baseline']['status']=='STABLE'
                result['classification']='IMAGE_RESIDENT_PAGE_OWNERSHIP_OBSERVED'
        else:
            stage='write-synthetic-file';payload=mount/'synthetic.bin'
            with payload.open('xb') as stream:
                block=b'L'*2**20
                for _ in range(PAYLOAD//len(block)):stream.write(block)
                stream.flush();os.fsync(stream.fileno())
            del block
            assert payload.stat().st_size==PAYLOAD
            sync_owned();snapshot('immediate',[image,payload])
            time.sleep(SETTLE_SECONDS)
            point=snapshot('settled',[image,payload])
            result['delta']=compare_delta(baseline,point)
        result['control_after']={name:controller_state(group) for name,group in [('outer',outer),('inner',inner),('leaf',leaf)]}
        result['available_before_cleanup_bytes']=available(ROOT)
        assert result['available_before_cleanup_bytes']>HOST_RESERVE
        success=True
    except BaseException as error:
        import traceback
        result.update(error=repr(error),failed_stage=stage);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,signal.SIG_IGN)
        if tracer is not None:
            try:result['charge_trace']=tracer.stop()
            except Exception as error:result['charge_trace_error']=repr(error);success=False
        try:result['cleanup']=cleanup(out)
        except Exception as error:result['cleanup_error']=repr(error);success=False
        if success:
            image.unlink();mount.rmdir();result['cleanup']['successful_image_removed']=True
        else:result['failed_image_retained']=image.exists()
        result['status']='PASS' if success else 'FAIL';write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if success else 1


def run(initialization_only=False,charge_trace=False):
    from experiment_disk_guard import writer_lock
    if os.geteuid()!=0:raise ValueError('root required')
    if charge_trace and not initialization_only:raise ValueError('charge-trace requires initialization-only')
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
    token=time.strftime('%Y%m%dt%H%M%S',time.gmtime())+str(os.getpid())
    out=ROOT/'.artifacts/host-loop-accounting'/stamp
    out.mkdir(parents=True,exist_ok=False);os.umask(0o077)
    result=dict(status='FAIL',scope='one io-off / io-on pair, 128 MiB images and 64 MiB synthetic files; no sandbox or benchmark')
    if initialization_only:result['scope']='one IOAccounting=yes 128 MiB initialization-only image; no payload or sandbox'
    with writer_lock():
        assert available(ROOT)>HOST_RESERVE+HOST_MARGIN+(1 if initialization_only else 2)*CAPACITY
        result.update(initial_available_bytes=available(ROOT),load=os.getloadavg(),host_kernel=os.uname().release)
        (out/'executed-runner.py').write_text(Path(__file__).read_text())
        if initialization_only:(out/'executed-host-file-monitor.py').write_text((ROOT/'scripts/host_file_monitor.py').read_text())
        if charge_trace:
            from host_loop_charge import layout
            write(out/'charge-layout.json',layout())
            (out/'executed-host-loop-charge.py').write_text((ROOT/'scripts/host_loop_charge.py').read_text())
        print('status=RUNNING result_dir='+str(out),flush=True)
        trials=[]
        try:
            for io in (('yes',) if initialization_only else ('no','yes')):
                trial=out/('io-off' if io=='no' else 'io-on');trial.mkdir()
                prefix='asblp'+token+('off' if io=='no' else 'on')
                slice_name=prefix+'-trial.slice';unit=prefix+'worker'
                argv=['systemd-run','--quiet','--wait','--pipe','--collect','--unit='+unit,'--slice='+slice_name,
                      '--property=MemoryAccounting=yes','--property=IOAccounting='+io,
                      '--property=RuntimeMaxSec=120','--property=TimeoutStopSec=60',
                      '--working-directory='+str(ROOT),sys.executable,'-B',str(Path(__file__).resolve()),'worker',
                      '--output',str(trial),'--slice',slice_name,'--token',token,'--io',io]
                if initialization_only:argv+=['--initialization-only']
                if charge_trace:argv+=['--charge-trace']
                try:
                    with (trial/'unit.log').open('x') as log:subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=210)
                    value=read(trial/'result.json');assert value['status']=='PASS'
                    trials.append(value)
                finally:
                    subprocess.run(['systemctl','stop',unit+'.service'],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=75)
                    try:
                        if (trial/'volume.ext4').exists():cleanup(trial)
                    finally:subprocess.run(['systemctl','stop',slice_name,prefix+'.slice'],check=True,timeout=20)
                    assert not (CGROOT/(prefix+'.slice')).exists(),'owned cgroup retained after stop'
            result.update(status='PASS',trials=trials)
            if initialization_only:
                result.update(first_detected_shortfall_stage=trials[0]['first_detected_shortfall_stage'],
                    interpretation='Staged initialization and cleanup passed; observed gaps do not identify page charge owner or prove stat flushing.')
            else:
                result.update(signed_gap_change_bytes=trials[0]['delta']['signed_inventory_minus_inner_growth_bytes']-trials[1]['delta']['signed_inventory_minus_inner_growth_bytes'],
                    interpretation='Pair setup, residency reads and cleanup passed. Interpret measured accounting deltas separately; no sandbox savings or old-run correction.')
        except BaseException as error:
            import traceback
            result.update(error=repr(error));(out/'failure.txt').write_text(traceback.format_exc())
        result['final_available_bytes']=available(ROOT);write(out/'result.json',result)
    print('status='+result['status']+' result_dir='+str(out),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    runner=sub.add_parser('run');runner.add_argument('--initialization-only',action='store_true',help='One IOAccounting=yes image, initialization stages only; no synthetic payload')
    runner.add_argument('--charge-trace',action='store_true',help='Initialization-only: trace actual image page charge owners and rstat events')
    child=sub.add_parser('worker');child.add_argument('--initialization-only',action='store_true')
    child.add_argument('--charge-trace',action='store_true')
    child.add_argument('--output',type=Path,required=True);child.add_argument('--slice',required=True)
    child.add_argument('--token',required=True);child.add_argument('--io',choices=('no','yes'),required=True)
    args=parser.parse_args();raise SystemExit(run(args.initialization_only,args.charge_trace) if args.mode=='run' else worker(args))
