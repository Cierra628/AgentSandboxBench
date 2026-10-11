"""Temporary fixed-capacity TrEnv-X check; old service/data remain untouched."""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

from experiment_disk_guard import writer_lock
from isolated_storage import GIB,HOST_RESERVE,available,capacity_budget,execution_window,Boundary,mount_identity
from probe_storage_boundary import identities
from storage_supervisor import supervised_run

ROOT=Path(__file__).resolve().parents[1]
CAPACITY=24*GIB


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def record_archive_removal(run, volume, proof):
    manifest=json.loads((run/'archive-manifest.json').read_text())
    if volume.exists() or (run/'data.tar.gz').exists() or (run/'data').exists():
        raise ValueError('volume/image removal not confirmed')
    if (manifest['gzip_test']!='PASS' or manifest['tar_compare']!='PASS' or not manifest['source_removed'] or
            proof['run_id']!=run.name or proof['sha256']!=manifest['archive_sha256'] or proof['bytes']!=manifest['archive_bytes']):
        raise ValueError('archive verification proof mismatch')
    with (run/'archive-removal.json').open('x') as stream:
        json.dump(dict(mode='verified-successful-fixed-volume-removal',archive_sha256=proof['sha256'],
            archive_bytes=proof['bytes'],volume=str(volume),volume_removed=True,raw_preserved=True,
            timestamp_ns=time.time_ns()),stream,indent=2);stream.write('\n')


def verify_runtime():
    expected=ROOT/'runtime/trenvx-venv'
    if Path(sys.prefix).resolve()!=expected.resolve():
        raise RuntimeError('use validate-isolated-trenvx.sh with the pinned TrEnv-X Python environment')
    sdk=importlib.import_module('sandbox_sdk')
    return dict(python_executable=sys.executable,python_prefix=sys.prefix,sdk_source=sdk.__file__)


def backend_command(args):
    command=[sys.executable,'-B',ROOT/'scripts/smoke_trenvx_service.py','--archive-compressor','pigz-fast']
    cfg=None
    if getattr(args,'platform_config',None):
        from use_platform import configuration
        cfg=configuration(args.platform_config)
        if cfg['storage']!='isolated' or cfg['backends']!=['trenvx']:
            raise ValueError('TrEnv-X validator requires isolated single-backend config')
        expected='checkpoint' if cfg['checkpoint_after_step'] else 'full'
        if args.mode!=expected:raise ValueError('mode does not match platform checkpoint boundary')
    if args.mode in ('full','checkpoint'):
        if args.mode=='checkpoint':
            if not args.reference:raise ValueError('checkpoint requires --reference to a successful frozen full run')
            from exp1_inputs import MANIFEST_SHA256,PATCH_SHA256
            reference=Path(args.reference).resolve()
            if not reference.is_relative_to(ROOT/'.artifacts'):raise ValueError('reference must belong to project artifacts')
            result=json.loads((reference/'result.json').read_text())
            config=json.loads((reference/'config.json').read_text())
            if (result['status']!='PASS' or not result.get('full_trajectory_verified') or
                    config['scenario']!='exp1' or config.get('checkpoint_after') is not None or
                    file_sha256(reference/'replay/actions.tsv')!=MANIFEST_SHA256 or
                    file_sha256(reference/'replay/patch.diff')!=PATCH_SHA256):
                raise ValueError('reference is not a verified complete frozen replay')
            command+=['--checkpoint-after','022','--reference',reference]
        frozen_cfg=cfg or json.loads((ROOT/'configs/platform-exp1.json').read_text())
        interval=str(cfg['sample_interval_seconds']) if cfg else '1'
        command+=['--scenario','exp1','--record-phases',
                  '--official-task',ROOT/frozen_cfg['official_task'],'--host-sample-interval',interval,
                  '--guest-memory-sampling','--guest-collector','persistent','--guest-sample-interval',interval]
    return command


def main(args):
    os.umask(0o077)
    if os.geteuid()!=0:raise RuntimeError('root required')
    runtime=verify_runtime()
    command=backend_command(args)
    subprocess.run(list(map(str,command))+['--check-only'],check=True,capture_output=True,timeout=10)
    window=execution_window(args.window_start_utc,args.window_end_utc,automatic=getattr(args,'automatic_window',False) or not getattr(args,'confirmed_window',False))
    ident=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+str(os.getpid())
    out=ROOT/'.artifacts/storage-isolated-trenvx'/ident
    out.mkdir(parents=True,exist_ok=False)
    (out/'code').mkdir()
    for name in ['validate_isolated_trenvx.py','isolated_storage.py','smoke_trenvx_service.py',
                 'replay_trenvx_exp1.py','archive_trenvx_run_data.py','experiment_disk_guard.py',
                 'storage_supervisor.py','storage_lifecycle.py','storage_observer.py']:
        shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (out/'code').iterdir()}
    (out/'source-sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    before=identities();(out/'firecracker-before.json').write_text(json.dumps(before,indent=2)+'\n')
    receipt=dict(status='FAIL',scope='temporary TrEnv-X storage-boundary functional validation; no performance comparison',
                 mode=args.mode,capacity_bytes=CAPACITY,coordination=window,runtime=runtime,checks={})
    image=out/'volume.ext4';mount=out/'mount';mount.mkdir()
    device=None;mounted=False;success=False;stage='preflight';sampler=None
    stopping=threading.Event();sample_errors=[];commands=[]

    def call(label,argv,timeout=90):
        start=time.monotonic_ns()
        r=subprocess.run(list(map(str,argv)),capture_output=True,timeout=timeout)
        (out/(label+'.stdout')).write_bytes(r.stdout[:1024**2])
        (out/(label+'.stderr')).write_bytes(r.stderr[:1024**2])
        commands.append(dict(stage=label,exit_code=r.returncode,duration_ns=time.monotonic_ns()-start))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+': exit '+str(r.returncode))
        return r.stdout.decode().strip()

    def sample():
        try:
            with (out/'disk-samples.jsonl').open('x') as stream:
                while not stopping.is_set():
                    usage=shutil.disk_usage(mount)
                    stream.write(json.dumps(dict(timestamp_ns=time.time_ns(),monotonic_ns=time.monotonic_ns(),
                        host_available_bytes=available(ROOT),volume_used_bytes=usage.used,
                        volume_available_bytes=available(mount)))+'\n');stream.flush()
                    stopping.wait(.25)
        except Exception as exc:sample_errors.append(repr(exc))

    print('status=RUNNING result_dir='+str(out),flush=True)
    try:
        free=available(ROOT);budget=capacity_budget(free,CAPACITY)
        receipt['budget']=budget
        assert budget['ready'] and free>60*GIB, 'original 60 GiB host launch check not satisfied'
        stage='fixed-volume'
        with image.open('xb') as stream:os.posix_fallocate(stream.fileno(),0,CAPACITY);os.fsync(stream.fileno())
        assert image.stat().st_blocks*512>=CAPACITY
        call('mkfs',['mkfs.ext4','-q','-F','-m','0','-E','nodiscard,lazy_itable_init=0,lazy_journal_init=0',image],120)
        device=call('attach',['losetup','--find','--show','--',image])
        discard=Path('/sys/class/block')/Path(device).name/'queue/discard_max_bytes'
        discard.write_text('0\n');assert discard.read_text().strip()=='0'
        call('mount',['mount','-t','ext4','-o','nodiscard',device,mount])
        mounted=True
        mount_identity(mount,device)
        (mount/'trenvx-runs').mkdir();(mount/'controller').mkdir()
        with (mount/'cleanup-reserve').open('xb') as stream:os.posix_fallocate(stream.fileno(),0,512*2**20)
        st=image.stat()
        spec=dict(version=1,workspace=str(ROOT),mountpoint=str(mount),device=device,image=str(image),
            capacity_bytes=CAPACITY,image_device=st.st_dev,image_inode=st.st_ino,
            initial_host_available_bytes=free,raw_root=str(mount/'trenvx-runs'),
            volume_reserve_bytes=512*2**20,window=window)
        spec_path=out/'boundary.json';spec_path.write_text(json.dumps(spec,indent=2)+'\n')
        boundary=Boundary(spec_path)
        receipt['boundary']=spec
        boundary.check(volume_headroom=6*GIB)
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        child_env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',ASB_STORAGE_BOUNDARY=str(spec_path),
            ASB_WRITER_LOCK=str(mount/'controller/child-writer.lock'))
        # Parent holds the original project lock; the child owns only its private lock.
        remaining=(datetime.fromisoformat(window['end_utc'])-datetime.now(timezone.utc)).total_seconds()
        assert remaining>120, 'not enough time left in the confirmed window'
        stage='trenvx-'+args.mode
        invocation=supervised_run(command,out=mount/'controller',label='trenvx',env=child_env,
            filesystem=ROOT,raw_root=mount/'trenvx-runs',timeout=min(600,remaining-30),
            cleanup_timeout=120,free_bytes=boundary.host_free)
        receipt['invocation']=invocation
        runs=list((mount/'trenvx-runs').iterdir());assert len(runs)==1
        run=runs[0]
        result=json.loads((run/'result.json').read_text())
        assert result['status']=='PASS' and result['host_cleanup']['cgroup_removed']
        assert result['host_cleanup']['owned_loop_backings']==[]
        manifest=json.loads((run/'archive-manifest.json').read_text())
        assert manifest['gzip_test']==manifest['tar_compare']=='PASS' and manifest['source_removed']
        assert manifest['archive_sha256']==file_sha256(run/'data.tar.gz')
        receipt['checks']['lossless_archive_inside_volume']='PASS'
        receipt['archive_manifest']=manifest
        receipt['backend_result']=result
        if args.mode in ('full','checkpoint'):
            required=['frozen_input_hashes','unfixed_seed_oracle','exp1_replay_correctness',
                      'official_task_tests','sdk_cleanup','service_shutdown']
            if args.mode=='checkpoint':
                required+=['checkpoint_after_022','file_restore_interface','modified_source_preserved',
                           'prefix_state_preserved','patch_matches_full_replay']
            for key in required:assert result.get('checks',{}).get(key)=='PASS', 'missing checkpoint check: '+key
            correctness=json.loads((run/'correctness.json').read_text())
            assert correctness['independent_svg_properties']=='PASS'
            assert correctness['expected_failures']=={'008':127,'016':127}
            official=json.loads((run/'official-task-result.json').read_text())
            assert official['status']=='PASS' and official['parser_results']['num_tests_passed']==4
            if args.mode=='checkpoint':
                checkpoint=json.loads((run/'checkpoint.json').read_text());assert checkpoint['next_step']=='023'
                receipt['checkpoint']=checkpoint
            receipt['checks']['full_exp1_file_restore_and_official_tests' if args.mode=='checkpoint' else 'full_exp1_and_official_tests']='PASS'
        else:
            for key in ['private_template_prepare','template_build','service_create_and_tool',
                        'controller_capture_and_seal','restore_interface','file_restore_and_continue',
                        'parent_child_write_isolation','sdk_cleanup','service_shutdown']:
                assert result.get('checks',{}).get(key)=='PASS', 'missing smoke check: '+key
            receipt['checks']['service_smoke']='PASS'
        receipt['checks']['fixed_backing_allocation']='PASS'
        assert image.stat().st_size==CAPACITY and image.stat().st_blocks*512>=CAPACITY
        assert available(ROOT)>HOST_RESERVE
        success=True
    except BaseException as exc:
        receipt['error']=repr(exc);receipt['failed_stage']=stage
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,signal.SIG_IGN)
        if sampler:
            stopping.set();sampler.join(timeout=5)
            if sampler.is_alive() or sample_errors:receipt['sampler_error']=sample_errors;success=False
        cleanup={}
        try:
            if mounted:
                reserve=mount/'cleanup-reserve'
                if reserve.exists():reserve.unlink()
                runs=list((mount/'trenvx-runs').iterdir())
                preserved=out/'raw';preserved.mkdir()
                for run in runs:
                    target=preserved/run.name
                    shutil.copytree(run,target,ignore=shutil.ignore_patterns('data','data.tar.gz','data.tar.gz.partial','seed-mount'))
                    # Old archive contents are never removed; this fixture archive is disposable only on success.
                    if (run/'data.tar.gz').exists():
                        receipt.setdefault('fixture_archives',[]).append(dict(run_id=run.name,
                            bytes=(run/'data.tar.gz').stat().st_size,
                            sha256=file_sha256(run/'data.tar.gz')))
                    group=Path('/sys/fs/cgroup')/('asb-trenvx-'+run.name)
                    if group.exists():
                        # No signals here. Remove only an empty run-owned cgroup left after writer exit.
                        for child in sorted(group.rglob('*'),key=lambda p:len(p.parts),reverse=True):
                            if child.is_dir():child.rmdir()
                        group.rmdir()
                shutil.copytree(mount/'controller',out/'controller')
                loops=json.loads(call('loops-before-unmount',['losetup','-J','-l','-O','NAME,BACK-FILE']))
                extras=[row for row in loops['loopdevices'] if str(mount) in (row.get('back-file') or '')]
                assert not extras,'private image loop remains attached'
                call('unmount',['umount',mount],30);mounted=False
            if device:
                assert call('verify-loop',['losetup','-j',image]).startswith(device+':')
                call('detach',['losetup','-d',device]);device=None
            assert not call('remaining-own-loop',['losetup','-j',image])
            cleanup['mount_and_loop_removed']=True
            if success:
                image.unlink();cleanup['successful_fixture_removed']=True
                for proof in receipt.get('fixture_archives',[]):
                    record_archive_removal(out/'raw'/proof['run_id'],image,proof)
            else:cleanup['failure_volume_retained']=image.exists()
        except BaseException as exc:cleanup['error']=repr(exc);success=False
        after=identities();(out/'firecracker-after.json').write_text(json.dumps(after,indent=2)+'\n')
        cleanup['original_firecracker_identities_unchanged']=before==after
        if before!=after:success=False
        samples=out/'disk-samples.jsonl'
        if samples.exists():
            rows=[json.loads(line) for line in samples.read_text().splitlines()]
            if rows:
                receipt['disk_sample_count']=len(rows)
                receipt['observed_volume_used_peak_bytes']=max(r['volume_used_bytes'] for r in rows)
                receipt['observed_host_available_min_bytes']=min(r['host_available_bytes'] for r in rows)
        receipt['cleanup']=cleanup
        receipt['status']='PASS' if success else 'FAIL'
        receipt['host_available_after_cleanup_bytes']=available(ROOT)
        receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
        receipt['archive_retention']='successful temporary fixture archive removed; raw/config/logs/hash preserved; failed volume retained'
        (out/'result.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    print('status='+receipt['status']+' result_dir='+str(out),flush=True)
    return 0 if receipt['status']=='PASS' else 1


def cancelled(signum,frame):raise RuntimeError('owned validation interrupted: '+str(signum))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['smoke','full','checkpoint'],default='smoke')
    parser.add_argument('--platform-config',type=Path,help='Validated unified isolated Exp1 config')
    parser.add_argument('--reference',type=Path,help='successful frozen full run; required for checkpoint mode')
    parser.add_argument('--confirmed-window',action='store_true',help='Legacy explicit confirmation; no longer required')
    parser.add_argument('--automatic-window',action='store_true',help='Runtime budget under standing authorization; no exclusive shared-disk guarantee')
    parser.add_argument('--window-start-utc')
    parser.add_argument('--window-end-utc')
    args=parser.parse_args()
    for sig in [signal.SIGTERM,signal.SIGINT]:signal.signal(sig,cancelled)
    with writer_lock():raise SystemExit(main(args))
