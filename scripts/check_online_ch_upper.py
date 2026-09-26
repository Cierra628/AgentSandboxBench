"""Check live CH virtio-blk capture on an owned Btrfs image, without shared services."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import traceback

from check_sealed_guest import CH, KERNEL, ROOT, sha
from check_online_upper import SEALER


def inside(out):
    mounts=[]
    proc=None
    checks={}
    stage='prepare'
    status='FAIL'
    index=0
    quiesce=os.environ.get('ONLINE_CH_QUIESCE')=='1'
    overlay=quiesce and os.environ.get('ONLINE_CH_OVERLAY')=='1'
    task_cgroup=quiesce and os.environ.get('ONLINE_CH_CGROUP')=='1'
    timings={}

    def run(*args,**kwargs):
        nonlocal index
        index+=1
        started=time.monotonic_ns()
        p=subprocess.run(args,capture_output=True,timeout=40,**kwargs)
        stem=out/f'command-{index:03}'
        stem.with_suffix('.json').write_text(json.dumps(dict(argv=args,returncode=p.returncode,
            elapsed_ns=time.monotonic_ns()-started)))
        stem.with_suffix('.stdout').write_bytes(p.stdout)
        stem.with_suffix('.stderr').write_bytes(p.stderr)
        p.check_returncode()
        return p.stdout

    def mount(target,*args):
        target.mkdir()
        run('mount',*args,str(target))
        mounts.append(target)

    def interrupted(signum,frame):
        raise RuntimeError(f'interrupted {signum}')

    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        assert os.readlink('/proc/self/ns/mnt')!=(out/'parent-mount-ns.txt').read_text()
        sys.dont_write_bytecode=True
        tempfile.tempdir=str(out)
        spec=importlib.util.spec_from_file_location('sealer',SEALER)
        sealer=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sealer)
        with (out/'store.btrfs').open('wb') as f:
            f.truncate(768*1024**2)
        run('mkfs.btrfs','-q','-f',str(out/'store.btrfs'))
        store=out/'btrfs'
        mount(store,'-o','loop,noatime,compress=no',str(out/'store.btrfs'))
        upper=store/'writable-rootfs.ext4'
        with upper.open('wb') as f:
            f.truncate(128*1024**2)
        run('mkfs.ext4','-q','-F',str(upper))
        if overlay:
            base=out/'base'
            (base/'replaced').mkdir(parents=True)
            for name,value in {'first':'0','second':'0','to-delete':'old','to-rename':'moved',
                               'replaced/old':'obsolete'}.items():
                (base/name).write_text(value+'\n')
            with (out/'base.ext4').open('wb') as f:
                f.truncate(128*1024**2)
            run('mkfs.ext4','-q','-F','-d',str(base),str(out/'base.ext4'))
        tree=out/'initrd-tree'
        for name in ('bin','dev','proc','sys','upper'):
            (tree/name).mkdir(parents=True)
        shutil.copy2('/usr/bin/busybox',tree/'bin/busybox')
        for name in ('sh','mount','mkdir','cat','dd','sync','sleep','uname','poweroff',
                     'kill','grep','mv','rm','ln','chmod','tar','stat','readlink'):
            (tree/'bin'/name).symlink_to('busybox')
        os.mknod(tree/'dev/console',stat.S_IFCHR|0o600,os.makedev(5,1))
        commands=['#!/bin/sh','set -eu','export PATH=/bin',
            "trap 'echo ASB_ONLINE_FAIL; poweroff -f' 0",
            'mount -t proc proc /proc','mount -t sysfs sysfs /sys',
            'mount -t devtmpfs devtmpfs /dev','mount -t ext4 /dev/vda /upper',
            'mkdir -p /upper/root /upper/work',
            "printf '0\\n' >/upper/root/first", "printf '0\\n' >/upper/root/second", 'sync',
            # An acknowledged fsync after the initial guest sync, before host capture.
            "printf '1\\n' | dd of=/upper/root/first conv=fsync status=none",
            'test "$(cat /upper/root/first)" = 1',
            'test "$(cat /upper/root/second)" = 0','uname -r',
            'echo ASB_ONLINE_READY_1_0','sleep 15',
            "printf '1\\n' | dd of=/upper/root/second conv=fsync status=none",'sync',
            'echo ASB_ONLINE_FINISHED_1_1','trap - 0','poweroff -f']
        if os.environ.get('ONLINE_CH_SYNC_BEFORE_CLONE')=='1':
            commands.insert(commands.index('echo ASB_ONLINE_READY_1_0'),'sync')
        if quiesce:
            commands=commands[:commands.index("printf '1\\n' | dd of=/upper/root/first conv=fsync status=none")]+[
                'mkdir -p /tmp','mount -t tmpfs tmpfs /tmp',
                'echo old >/upper/root/to-delete','echo moved >/upper/root/to-rename',
                "sh -ec 'printf \"1\\n\" | dd of=/upper/root/first conv=fsync status=none; "
                'mv /upper/root/to-rename /upper/root/renamed; rm /upper/root/to-delete; '
                'chmod 640 /upper/root/first; ln /upper/root/first /upper/root/hard; '
                'ln -s first /upper/root/sym; echo 1 >/tmp/generation; '
                'kill -STOP $$; echo 2 >/tmp/generation; '
                "printf \"1\\n\" | dd of=/upper/root/second conv=fsync status=none' &",
                'writer=$!',
                'while ! grep -q "^State:.*T" /proc/$writer/status; do sleep 0.05; done',
                'echo ASB_WRITER_STOPPED','cat /proc/$writer/status',
                'echo ASB_TMP_ARCHIVE_BEGIN; cat /proc/uptime',
                'tar -C /tmp -cf /upper/root/runtime.tar generation',
                'echo ASB_TMP_ARCHIVE_END_SYNC_BEGIN; cat /proc/uptime',
                'sync','echo ASB_SYNC_END; cat /proc/uptime',
                'test "$(cat /upper/root/first)" = 1',
                'test "$(cat /upper/root/second)" = 0','uname -r',
                'echo ASB_ONLINE_READY_1_0','sleep 15','kill -CONT "$writer"','wait "$writer"',
                'test "$(cat /tmp/generation)" = 2','sync',
                'echo ASB_ONLINE_FINISHED_1_1','trap - 0','poweroff -f']
        if overlay:
            commands=[c.replace('/upper/root/','/merged/') for c in commands]
            at=commands.index('mkdir -p /upper/root /upper/work')+1
            commands[at:at]=['mkdir -p /base /merged',
                'mount -t ext4 -o ro,dax=always /dev/pmem0 /base',
                'mount -t overlay overlay -o lowerdir=/base,upperdir=/upper/root,workdir=/upper/work /merged']
            commands=[c.replace('kill -STOP $$;',
                'rm -rf /merged/replaced; mkdir /merged/replaced; echo fresh >/merged/replaced/new; kill -STOP $$;')
                for c in commands]
        if task_cgroup:
            at=commands.index('mkdir -p /upper/root /upper/work')+1
            commands[at:at]=['mkdir -p /sys/fs/cgroup',
                'mount -t cgroup2 none /sys/fs/cgroup','mkdir /sys/fs/cgroup/asb-task']
            changed=[]
            for command in commands:
                command=command.replace("sh -ec '","sh -ec 'echo $$ >/sys/fs/cgroup/asb-task/cgroup.procs; ")
                command=command.replace('kill -STOP $$;',
                    'echo ready >/tmp/asb-ready; while test ! -e /tmp/asb-release; do sleep 0.05; done;')
                if command.startswith('while ! grep -q "^State:'):
                    changed+=['while test ! -e /tmp/asb-ready; do sleep 0.05; done',
                        'echo 1 >/sys/fs/cgroup/asb-task/cgroup.freeze',
                        'while ! grep -q "frozen 1" /sys/fs/cgroup/asb-task/cgroup.events; do sleep 0.01; done',
                        'echo ASB_TASK_FROZEN','cat /sys/fs/cgroup/asb-task/cgroup.events',
                        'cat /sys/fs/cgroup/asb-task/cgroup.procs']
                elif command=='kill -CONT "$writer"':
                    changed+=['echo 0 >/sys/fs/cgroup/asb-task/cgroup.freeze','echo release >/tmp/asb-release']
                else:
                    changed.append(command)
                if command=='wait "$writer"':
                    changed+=['while grep -q "populated 1" /sys/fs/cgroup/asb-task/cgroup.events; do sleep 0.01; done',
                        'echo ASB_TASK_EMPTY']
            commands=changed
        (tree/'init').write_text('\n'.join(commands)+'\n')
        (tree/'init').chmod(0o755)
        names=['.']+[str(p.relative_to(tree)) for p in sorted(tree.rglob('*'))]
        (out/'initrd.cpio').write_bytes(run('cpio','--null','-o','-H','newc',
            input=('\0'.join(names)+'\0').encode(),cwd=tree))
        checks[stage]='PASS'
        stage='guest_ready'
        args=[str(CH),'--kernel',str(KERNEL),'--initramfs',str(out/'initrd.cpio'),
            '--cmdline','console=ttyS0 rdinit=/init panic=1','--cpus','boot=2',
            '--memory','size=4G','--console','off','--serial',f'file={out}/serial.log',
            '--disk',f'path={upper},readonly=off,id=upper']
        if quiesce:
            args+=['--api-socket',str(out/'api.sock')]
        if overlay:
            args+=['--pmem',f'file={out}/base.ext4,discard_writes=on,id=base']
        (out/'argv.json').write_text(json.dumps(args,indent=2)+'\n')
        with (out/'vmm.stdout').open('wb') as stdout,(out/'vmm.stderr').open('wb') as stderr:
            proc=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            serial=(out/'serial.log').read_text(errors='replace') if (out/'serial.log').exists() else ''
            if '\nASB_ONLINE_READY_1_0\n' in serial:
                break
            assert proc.poll() is None, 'guest exited before ready'
            time.sleep(.05)
        else:
            raise TimeoutError('guest ready marker missing')
        assert '\n6.1.134\n' in serial and 'ASB_ONLINE_FAIL' not in serial
        if task_cgroup:
            assert '\nASB_TASK_FROZEN\n' in serial
        checks[stage]='PASS'
        if quiesce:
            stage='pause'
            pause_started=time.monotonic_ns()
            run('curl','--fail','--silent','--show-error','--max-time','5','--unix-socket',
                str(out/'api.sock'),'-X','PUT','http://localhost/api/v1/vm.pause')
            info=json.loads(run('curl','--fail','--silent','--show-error','--max-time','5',
                '--unix-socket',str(out/'api.sock'),'http://localhost/api/v1/vm.info'))
            (out/'paused-info.json').write_text(json.dumps(info,indent=2)+'\n')
            assert info['state']=='Paused'
            timings['pause_and_state_query_ns']=time.monotonic_ns()-pause_started
            assert 'ASB_WRITER_STOPPED' in serial
            checks[stage]='PASS'
        stage='online_clone'
        clone=store/'captured-upper.ext4'
        started=time.monotonic_ns()
        run('cp','--reflink=auto','--sparse=always',str(upper),str(clone))
        serial=(out/'serial.log').read_text()
        assert 'ASB_ONLINE_FINISHED_1_1' not in serial and proc.poll() is None
        (out/'capture.json').write_text(json.dumps(dict(elapsed_ns=time.monotonic_ns()-started,
            source_inode=upper.stat().st_ino,clone_inode=clone.stat().st_ino,
            guest_ready=True,guest_finished=False),indent=2)+'\n')
        checks[stage]='PASS'
        timings['clone_and_window_check_ns']=time.monotonic_ns()-started
        if quiesce:
            stage='resume'
            resume_started=time.monotonic_ns()
            run('curl','--fail','--silent','--show-error','--max-time','5','--unix-socket',
                str(out/'api.sock'),'-X','PUT','http://localhost/api/v1/vm.resume')
            timings['resume_api_ns']=time.monotonic_ns()-resume_started
            timings['pause_request_to_resume_return_ns']=time.monotonic_ns()-pause_started
            checks[stage]='PASS'
        stage='seal_clone'
        started=time.monotonic_ns()
        sealer.archive_upper(clone,out/'captured-upper.tar')
        timings['archive_upper_ns']=time.monotonic_ns()-started
        started=time.monotonic_ns()
        sealer.build_layer(out/'captured-upper.tar',out/'checkpoint-0001.ext4')
        timings['build_layer_ns']=time.monotonic_ns()-started
        sealed=out/'sealed-layer'
        mount(sealed,'-o','loop,ro,noload',str(out/'checkpoint-0001.ext4'))
        captured={name:(sealed/'delta'/name).read_text().strip() for name in ('first','second')}
        (out/'captured-state.json').write_text(json.dumps(captured,indent=2)+'\n')
        checks[stage]='PASS'
        stage='journal_replay_diagnostic'
        # Recovery needs a writable loop device: use a separate owned clone only.
        replay=store/'journal-replay.ext4'
        run('cp','--reflink=auto','--sparse=always',str(clone),str(replay))
        replay_mount=out/'journal-replay'
        started=time.monotonic_ns()
        mount(replay_mount,'-o','loop',str(replay))
        timings['replay_mount_ns']=time.monotonic_ns()-started
        replayed={name:(replay_mount/'root'/name).read_text().strip() for name in ('first','second')}
        (out/'replayed-state.json').write_text(json.dumps(replayed,indent=2)+'\n')
        assert replayed=={'first':'1','second':'0'}
        checks[stage]='PASS'
        if quiesce:
            stage='metadata_and_runtime'
            import tarfile
            for number,root in enumerate((sealed/'delta',replay_mount/'root')):
                if overlay:
                    assert stat.S_ISCHR((root/'to-delete').lstat().st_mode)
                    assert (root/'to-delete').lstat().st_rdev==0
                    assert os.getxattr(root/'replaced','trusted.overlay.opaque')==b'y'
                    view=out/f'visible-{number}'
                    mount(view,'-t','overlay','-o',f'ro,lowerdir={root}:{base}','overlay')
                    root=view
                    assert not (root/'replaced/old').exists()
                    assert (root/'replaced/new').read_text().strip()=='fresh'
                assert not (root/'to-delete').exists() and not (root/'to-rename').exists()
                assert (root/'renamed').read_text().strip()=='moved'
                assert stat.S_IMODE((root/'first').stat().st_mode)==0o640
                assert (root/'first').stat().st_ino==(root/'hard').stat().st_ino
                assert (root/'sym').readlink()==Path('first')
                with tarfile.open(root/'runtime.tar') as archive:
                    assert archive.extractfile('generation').read()==b'1\n'
            checks[stage]='PASS'
        stage='parent_finishes'
        proc.wait(timeout=30)
        serial=(out/'serial.log').read_text()
        assert proc.returncode==0 and 'ASB_ONLINE_FINISHED_1_1' in serial and 'ASB_ONLINE_FAIL' not in serial
        if task_cgroup:
            assert '\nASB_TASK_EMPTY\n' in serial
            checks['task_cgroup_freeze_resume_exit']='PASS'
        parent=out/'parent-upper'
        mount(parent,'-o','loop,ro,noload',str(upper))
        state={name:(parent/'root'/name).read_text().strip() for name in ('first','second')}
        (out/'parent-state.json').write_text(json.dumps(state,indent=2)+'\n')
        assert state=={'first':'1','second':'1'}
        checks[stage]='PASS'
        stage='copy_fidelity'
        checks[stage]='PASS' if captured=={'first':'1','second':'0'} else 'FAIL'
        status=checks[stage]
        if quiesce:
            assert status=='PASS', 'captured contents differ from stopped writer boundary'
            stage='fresh_guest_restore'
            (out/'parent-exit-code.txt').write_text(str(proc.returncode)+'\n')
            (out/'parent-init.sh').write_text((tree/'init').read_text())
            restore_commands=['#!/bin/sh','set -eu','export PATH=/bin',
                "trap 'echo ASB_QUIESCE_RESTORE_FAIL; poweroff -f' 0",
                'mount -t proc proc /proc','mount -t sysfs sysfs /sys',
                'mount -t devtmpfs devtmpfs /dev','mkdir -p /layer /newupper /merged',
                'mount -t ext4 -o ro,dax=always /dev/pmem0 /layer',
                'mount -t ext4 /dev/vda /newupper','mkdir -p /newupper/root /newupper/work',
                'mount -t overlay overlay -o lowerdir=/layer/delta,upperdir=/newupper/root,workdir=/newupper/work /merged',
                'test "$(cat /merged/first)" = 1','test "$(cat /merged/second)" = 0',
                'test ! -e /merged/to-delete','test ! -e /merged/to-rename',
                'test "$(cat /merged/renamed)" = moved',
                'test "$(stat -c %a /merged/first)" = 640',
                'test "$(stat -c %i /merged/first)" = "$(stat -c %i /merged/hard)"',
                'test "$(readlink /merged/sym)" = first',
                'test "$(tar -xOf /merged/runtime.tar generation)" = 1',
                'echo ASB_QUIESCE_RESTORE_PASS','trap - 0','poweroff -f']
            if overlay:
                at=restore_commands.index('mount -t ext4 /dev/vda /newupper')
                restore_commands[at:at]=['mkdir -p /base',
                    'mount -t ext4 -o ro,dax=always /dev/pmem1 /base']
                restore_commands=[c.replace('lowerdir=/layer/delta,','lowerdir=/layer/delta:/base,')
                                  for c in restore_commands]
                at=restore_commands.index('echo ASB_QUIESCE_RESTORE_PASS')
                restore_commands[at:at]=['test ! -e /merged/replaced/old',
                    'test "$(cat /merged/replaced/new)" = fresh']
            (tree/'init').write_text('\n'.join(restore_commands)+'\n')
            (out/'restore-init.sh').write_text((tree/'init').read_text())
            (out/'restore-initrd.cpio').write_bytes(run('cpio','--null','-o','-H','newc',
                input=('\0'.join(names)+'\0').encode(),cwd=tree))
            restore_upper=store/'restore-upper.ext4'
            with restore_upper.open('wb') as f:
                f.truncate(128*1024**2)
            run('mkfs.ext4','-q','-F',str(restore_upper))
            layer_sha=sha(out/'checkpoint-0001.ext4')
            restore_args=[str(CH),'--kernel',str(KERNEL),'--initramfs',str(out/'restore-initrd.cpio'),
                '--cmdline','console=ttyS0 rdinit=/init panic=1','--cpus','boot=2','--memory','size=4G',
                '--console','off','--serial',f'file={out}/restore-serial.log',
                '--disk',f'path={restore_upper},readonly=off,id=upper',
                '--pmem',f'file={out}/checkpoint-0001.ext4,discard_writes=on,id=sealed']
            if overlay:
                restore_args+=[f'file={out}/base.ext4,discard_writes=on,id=base']
            (out/'restore-argv.json').write_text(json.dumps(restore_args,indent=2)+'\n')
            started=time.monotonic_ns()
            with (out/'restore-vmm.stdout').open('wb') as stdout,(out/'restore-vmm.stderr').open('wb') as stderr:
                proc=subprocess.Popen(restore_args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            proc.wait(timeout=30)
            timings['fresh_guest_boot_check_shutdown_ns']=time.monotonic_ns()-started
            restored=(out/'restore-serial.log').read_text()
            assert proc.returncode==0 and '\nASB_QUIESCE_RESTORE_PASS\n' in restored
            assert 'ASB_QUIESCE_RESTORE_FAIL' not in restored
            assert sha(out/'checkpoint-0001.ext4')==layer_sha
            checks[stage]='PASS'
    except Exception:
        status='FAIL'
        checks[stage]='FAIL'
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        cleanup=[]
        for target in reversed(mounts):
            try:
                run('umount',str(target))
            except Exception:
                cleanup.append(traceback.format_exc())
        remaining=[line for line in Path('/proc/self/mountinfo').read_text().splitlines() if str(out) in line]
        checks['cleanup']='PASS' if not cleanup and not remaining else 'FAIL'
        if checks['cleanup']=='FAIL':
            status='FAIL'
        (out/'cleanup.json').write_text(json.dumps(dict(errors=cleanup,mounts=remaining),indent=2)+'\n')
        (out/'timings.json').write_text(json.dumps(timings,indent=2)+'\n')
        (out/'result.json').write_text(json.dumps(dict(status=status,last_stage=stage,checks=checks,
            guest_exit_code=proc.returncode if proc else None),indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--inside':
        return inside(Path(sys.argv[2]).resolve())
    assert os.geteuid()==0
    os.umask(0o077)
    out=ROOT/'.artifacts/online-ch-upper'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    (out/'parent-mount-ns.txt').write_text(os.readlink('/proc/self/ns/mnt'))
    sources=[Path(__file__),SEALER,ROOT/'scripts/check_online_upper.py',ROOT/'scripts/check_sealed_guest.py']
    for source in sources:
        (out/source.name).write_bytes(source.read_bytes())
    (out/'config.json').write_text(json.dumps(dict(host_kernel=os.uname().release,
        ch_sha256=sha(CH),kernel_sha256=sha(KERNEL),busybox_sha256=sha(Path('/usr/bin/busybox')),
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},memory_mib=4096,vcpu=2,
        sync_before_clone=os.environ.get('ONLINE_CH_SYNC_BEFORE_CLONE')=='1',
        quiesce=os.environ.get('ONLINE_CH_QUIESCE')=='1',
        source_overlay=os.environ.get('ONLINE_CH_QUIESCE')=='1' and os.environ.get('ONLINE_CH_OVERLAY')=='1',
        task_cgroup=os.environ.get('ONLINE_CH_QUIESCE')=='1' and os.environ.get('ONLINE_CH_CGROUP')=='1',
        disk='virtio-blk, CH defaults, Btrfs backing; no network',
        scope=('task cgroup freeze at known ready boundary, tmpfs archive, guest sync, API pause, clone, resume'
               if os.environ.get('ONLINE_CH_QUIESCE')=='1' and os.environ.get('ONLINE_CH_CGROUP')=='1' else
               'cooperative writer STOP, tmpfs archive, guest sync, API pause, clone, API resume'
               if os.environ.get('ONLINE_CH_QUIESCE')=='1' else
               'guest sync then fsynced background write, capture during 15-second idle window; no active IO during copy')),indent=2)+'\n')
    with (out/'run.stdout').open('wb') as stdout,(out/'run.stderr').open('wb') as stderr:
        p=subprocess.run(['timeout','--signal=TERM','--kill-after=10s','180','unshare','--mount',
            '--propagation','private',sys.executable,str(Path(__file__).resolve()),'--inside',str(out)],
            stdout=stdout,stderr=stderr)
    (out/'wrapper-exit-code.txt').write_text(str(p.returncode)+'\n')
    print((out/'run.stdout').read_text().strip() or f'status=FAIL stage=namespace-or-timeout result_dir={out}')
    return p.returncode


if __name__=='__main__':
    raise SystemExit(main())
