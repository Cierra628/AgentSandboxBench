"""Small isolated boot/restore comparison; records classifications, not memory benefit."""
import hashlib
import ctypes
import json
import mmap
import os
from pathlib import Path
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import traceback

from experiment_disk_guard import writer_lock, guarded_run, RESERVE_BYTES
from measure_file_physical import parse_maps, file_address, read_entry, ext4_data_offsets, PFN_MASK, collect, audit_run
from smoke_trenvx_dax import CH, CH_REPO, KERNEL, ROOT, sha


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n')


def trace_summary(text):
    lines=text.splitlines()
    return dict(completed_for_target_pages=sum(line.startswith('APF_COMPLETE ') for line in lines),
        gup_for_target_pages=[line.rsplit('flags=',1)[-1] for line in lines if line.startswith('GUP ')],
        work_functions=[line[5:] for line in lines if line.startswith('WORK ')],
        async_worker_in_stack=any(line.strip().startswith('async_pf_execute+') for line in lines))


def archive_memory(out):
    if out.resolve().parent != (ROOT/'.artifacts/dax-restore-diagnostic').resolve():
        raise ValueError('archive requires this diagnostic result directory')
    source=out/'snapshot/memory-ranges'
    if not source.exists():return
    digest=sha(source)
    archive=source.with_suffix('.gz')
    with archive.open('xb') as target:
        subprocess.run(['gzip','-1','-c',source],stdout=target,check=True,timeout=120)
    decoded=hashlib.sha256()
    with subprocess.Popen(['gzip','-dc',archive],stdout=subprocess.PIPE) as process:
        while chunk:=process.stdout.read(1024**2):decoded.update(chunk)
        assert process.wait(timeout=120)==0,'gzip verification failed'
    assert decoded.hexdigest()==digest,'snapshot archive mismatch'
    save(out/'snapshot-memory-archive.json',dict(status='PASS',source_sha256=digest,
         source_bytes=source.stat().st_size,archive_bytes=archive.stat().st_size,
         archive_sha256=sha(archive),source_removed=False))
    source.unlink()
    manifest=json.loads((out/'snapshot-memory-archive.json').read_text())
    manifest['source_removed']=True;save(out/'snapshot-memory-archive.json',manifest)


def cached_pages(layer, offsets):
    libc=ctypes.CDLL(None,use_errno=True)
    states=[]
    with layer.open('rb') as source:
        for offset in offsets:
            with mmap.mmap(source.fileno(),4096,access=mmap.ACCESS_COPY,offset=offset) as mapping:
                address=ctypes.addressof(ctypes.c_char.from_buffer(mapping))
                resident=ctypes.c_ubyte()
                if libc.mincore(ctypes.c_void_p(address),ctypes.c_size_t(4096),ctypes.byref(resident)):
                    raise OSError(ctypes.get_errno(),'mincore failed')
                states.append(bool(resident.value&1))
    return states


def main(out, cold=False, partial=False, extracted=None, siblings=False, trace_apf=False, owned_kvm_counters=False):
    assert os.geteuid()==0 and os.sysconf('SC_PAGE_SIZE')==4096
    assert shutil.disk_usage(out).free > RESERVE_BYTES+6*1024**3
    processes=[]; tracers=[]; connection=None; held_connection=None; counter=None
    assert not owned_kvm_counters or cold
    sockets=Path('/tmp')/('asb-dr-'+str(os.getpid()))
    sockets.mkdir(mode=0o700)
    current_sockets=sockets
    result=dict(status='FAIL',scope='four-page named file; direct boot versus CH memory snapshot restore')

    def run(args, timeout=30):
        r=subprocess.run(list(map(str,args)),capture_output=True,timeout=timeout)
        with (out/'commands.log').open('ab') as log:
            log.write(r.stdout+r.stderr)
        r.check_returncode()
        return r.stdout

    def stop(p):
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill();p.wait(timeout=5)

    def api(name, data=None):
        args=['curl','--silent','--show-error','--fail','--max-time','30',
              '--unix-socket',current_sockets/'api','-X','PUT','http://localhost/api/v1/'+name]
        if data is not None: args+=['-H','Content-Type: application/json','-d',json.dumps(data)]
        return run(args,40)

    def launch(label, args):
        nonlocal connection
        for name in ('api','serial'):
            (current_sockets/name).unlink(missing_ok=True)
        with (out/(label+'.log')).open('wb') as log:
            p=subprocess.Popen([str(CH),'--api-socket',str(current_sockets/'api'),*map(str,args)],
                               stdout=log,stderr=log,stdin=subprocess.DEVNULL)
        processes.append(p)
        save(out/(label+'-argv.json'),p.args)
        deadline=time.monotonic()+20
        while not (current_sockets/'serial').exists() or not (current_sockets/'api').exists():
            assert p.poll() is None and time.monotonic()<deadline,'VMM socket unavailable'
            time.sleep(.05)
        connection=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        connection.settimeout(30); connection.connect(str(current_sockets/'serial'))
        return p

    def wait(marker):
        raw=b'';deadline=time.monotonic()+45
        while marker+b'\r\n' not in raw and marker+b'\n' not in raw:
            assert time.monotonic()<deadline,'guest marker timeout'
            chunk=connection.recv(65536)
            assert chunk,'serial closed'
            raw+=chunk
        with (out/'serial.log').open('ab') as log: log.write(raw)
        return raw

    def sample(label,p,layer,offsets):
        proc=Path('/proc')/str(p.pid)
        maps=(proc/'maps').read_text(); regions=parse_maps(maps)
        (out/(label+'-maps.txt')).write_text(maps)
        (out/(label+'-smaps.txt')).write_text((proc/'smaps').read_text())
        rows=[]; counts=dict(nonresident=0,resident_file=0,resident_nonfile=0,kpageflags_anon=0)
        counts['backing_cached_pages']=sum(cached_pages(layer,offsets))
        with (proc/'pagemap').open('rb',buffering=0) as pm, open('/proc/kpageflags','rb',buffering=0) as flags:
            for offset in offsets:
                address=file_address(offset,regions,layer.stat())
                entry=read_entry(pm.fileno(),address); kflags=None
                if entry&(1<<63):
                    counts['resident_file' if entry&(1<<61) else 'resident_nonfile']+=1
                    kflags=struct.unpack('<Q',os.pread(flags.fileno(),8,(entry&PFN_MASK)*8))[0]
                    counts['kpageflags_anon']+=bool(kflags&(1<<12))
                else: counts['nonresident']+=1
                rows.append(dict(offset=offset,address=address,entry=entry,kpageflags=kflags))
            for row in rows:
                assert read_entry(pm.fileno(),row['address'])==row['entry'],'page changed during scan'
        assert (proc/'maps').read_text()==maps,'maps changed during scan'
        save(out/(label+'-pages.json'),rows)
        result.setdefault('observations',{})[label]=counts
        return rows

    def start_trace(mode,p,layer,offsets):
        regions=parse_maps(Path(f'/proc/{p.pid}/maps').read_text())
        addresses=[file_address(offset,regions,layer.stat()) for offset in offsets]
        gup_condition=' || '.join(f'arg1 == {address}' for address in addresses)
        completed_condition=' || '.join(f'args->address == {address}' for address in addresses)
        program='BEGIN { printf("READY\\n"); }\n'
        program+='tracepoint:workqueue:workqueue_execute_start { @work_function[tid] = (uint64)args->function; }\n'
        program+='tracepoint:workqueue:workqueue_execute_end { delete(@work_function[tid]); }\n'
        program+='END { clear(@work_function); }\n'
        program+=f'tracepoint:kvm:kvm_try_async_get_page /pid == {p.pid}/ {{ printf("TRY %llu %llu\\n", nsecs, args->gfn); }}\n'
        program+=f'tracepoint:kvm:kvm_async_pf_completed /{completed_condition}/ {{ printf("APF_COMPLETE %llu %lu\\n", nsecs, args->address); }}\n'
        program+=f'kprobe:get_user_pages_remote /{gup_condition}/ {{ printf("GUP %llu %lu flags=%lu\\n", nsecs, arg1, arg3); printf("WORK %s\\n", ksym(@work_function[tid])); printf("%s\\n", kstack(8)); }}\n'
        source=out/(mode+'-apf.bt');source.write_text(program)
        log=out/(mode+'-apf.log')
        with log.open('wb') as stream:
            tracer=subprocess.Popen(['bpftrace','-B','line','-q',str(source)],stdout=stream,stderr=stream)
        tracers.append(tracer)
        deadline=time.monotonic()+20
        while 'READY\n' not in log.read_text():
            assert tracer.poll() is None and time.monotonic()<deadline,'BPF trace did not become ready'
            time.sleep(.1)
        return tracer,log

    def finish_trace(tracer,log,mode):
        tracer.send_signal(signal.SIGINT);tracer.wait(timeout=10)
        assert tracer.returncode==0,'BPF trace failed'
        result.setdefault('async_page_fault_trace',{})[mode]=trace_summary(log.read_text())

    try:
        head=run(['git','-c',f'safe.directory={CH_REPO}','-C',CH_REPO,'rev-parse','HEAD']).decode().strip()
        assert head=='9e0056eb750096f3bf8ed491c345ef9241fd527e'
        assert not run(['git','-c',f'safe.directory={CH_REPO}','-C',CH_REPO,'status','--porcelain']).strip()
        names=('diagnose_dax_restore.py','trenvx_dax_restore_init.sh','dax_read_fixture.c','measure_file_physical.py','experiment_disk_guard.py','smoke_trenvx_dax.py')
        if owned_kvm_counters:names+=('owned_thread_wait.py','trace_owned_vcpu_wait.bt','trace_owned_vcpu_faults.bt')
        for name in names: shutil.copy2(ROOT/'scripts'/name,out/name)
        save(out/'config.json',dict(ch_revision=head,ch_sha256=sha(CH),kernel_sha256=sha(KERNEL),
             source_hashes={n:sha(ROOT/'scripts'/n) for n in names},guest_memory_mib=4096,
             host_kernel=os.uname().release,host_page_size=4096,partial_tail=partial,simultaneous_vms=siblings,
             trace_async_page_faults=trace_apf,owned_kvm_counters=owned_kvm_counters,
             cache_policy='fsync and FADV_DONTNEED on this run owned layer only' if cold else
                          'no cache drop; generated images remain warm; sequential direct then restore'))
        initrd=out/'initrd-tree'
        for name in ('bin','dev','proc','sys'): (initrd/name).mkdir(parents=True)
        shutil.copy2('/usr/bin/busybox',initrd/'bin/busybox')
        run(['gcc','-O2','-Wall','-Wextra','-Werror','-static',ROOT/'scripts/dax_read_fixture.c',
             '-o',initrd/'bin/dax-read'],60)
        for name in ('sh','mount','mkdir','cat','stty','sha256sum','poweroff'):
            (initrd/'bin'/name).symlink_to('busybox')
        shutil.copy2(ROOT/'scripts/trenvx_dax_restore_init.sh',initrd/'init');(initrd/'init').chmod(0o755)
        os.mknod(initrd/'dev/console',stat.S_IFCHR|0o600,os.makedev(5,1))
        entries=['.']+[str(p.relative_to(initrd)) for p in sorted(initrd.rglob('*'))]
        r=subprocess.run(['cpio','--null','-o','-H','newc'],input=('\0'.join(entries)+'\0').encode(),
                         cwd=initrd,capture_output=True,timeout=30,check=True)
        (out/'initrd.cpio').write_bytes(r.stdout)
        payload=b''.join(struct.pack('<Q',i+1)*512 for i in range(4))
        if partial:payload=payload[:-123]
        guest_path='/delta/known.bin';target='/merged/known.bin'
        if extracted:
            assert not partial,'cannot modify archived source payload'
            provenance=json.loads((extracted/'result.json').read_text())
            assert provenance['status']=='PASS'
            assert sha(extracted/'checkpoint-0001.ext4')==provenance['files']['checkpoint-0001.ext4']['sha256']
            guest_path='/delta'+provenance['target'];target='/merged'+provenance['target']
            assert guest_path=='/delta/testbed/src/language-html/utils/index.js'
            run(['debugfs','-R',f'dump {guest_path} {out}/target-source.bin',extracted/'checkpoint-0001.ext4'])
            payload=(out/'target-source.bin').read_bytes()
            assert hashlib.sha256(payload).hexdigest()==provenance['expected_target_sha256'],'archived source differs from guest'
            save(out/'input-provenance.json',provenance)
            result['scope']='actual failed Exp1 sealed source in small guest; not original service environment'
        result['payload_bytes']=len(payload)
        expected=hashlib.sha256(payload).hexdigest()
        for name in ('base','checkpoint'):
            if name=='checkpoint' and extracted:
                shutil.copyfile(extracted/'checkpoint-0001.ext4',out/'checkpoint.ext4')
                (out/'checkpoint.ext4').chmod(0o444)
                continue
            tree=out/(name+'-tree');tree.mkdir()
            if name=='checkpoint': (tree/'delta').mkdir();(tree/'delta/known.bin').write_bytes(payload)
            layer=out/(name+'.ext4')
            with layer.open('wb') as f:f.truncate(256*1024**2)
            run(['mkfs.ext4','-q','-F','-b','4096','-d',tree,layer]);layer.chmod(0o444)
        layer=out/'checkpoint.ext4';offsets=ext4_data_offsets(layer,guest_path)
        original=sha(layer)
        for mode in ('direct','restore'):
            current_sockets=sockets/mode;current_sockets.mkdir()
            upper=out/(mode+'-upper.ext4')
            with upper.open('wb') as f:f.truncate(256*1024**2)
            run(['mkfs.ext4','-q','-F','-b','4096',upper])
            if cold and not (siblings and mode=='restore'):
                with layer.open('rb') as f:
                    os.fsync(f.fileno());os.posix_fadvise(f.fileno(),0,0,os.POSIX_FADV_DONTNEED)
            result.setdefault('preboot_cached_pages',{})[mode]=sum(cached_pages(layer,offsets))
            p=launch(mode+'-boot',['--kernel',KERNEL,'--initramfs',out/'initrd.cpio',
                '--cmdline',f'console=ttyS0 rdinit=/init panic=-1 asb_target={target}','--cpus','boot=2','--memory','size=4G',
                '--console','off','--serial',f'socket={current_sockets}/serial',
                '--pmem',f'file={out}/base.ext4,discard_writes=on,id=base',
                f'file={layer},discard_writes=on,id=checkpoint','--disk',f'path={upper},id=upper'])
            wait(b'ASB_READY'); sample(mode+'-boot-unread',p,layer,offsets)
            if mode=='restore':
                snap=out/'snapshot';snap.mkdir()
                api('vm.pause');api('vm.snapshot',dict(destination_url='file://'+str(snap)))
                connection.close();stop(p)
                p=launch('restored',['--restore',f'source_url=file://{snap}'])
                api('vm.resume')
                sample('restored-unread',p,layer,offsets)
            if owned_kvm_counters:
                unread=result['observations']['restored-unread' if mode=='restore' else 'direct-boot-unread']
                assert unread['backing_cached_pages']==0 and unread['nonresident']==len(offsets),'positive fixture warmed before read'
                from owned_thread_wait import WaitTrace
                proc=Path('/proc')/str(p.pid)
                proof=[]
                for name in ('vcpu0','vcpu1'):
                    tasks=[task for task in (proc/'task').iterdir() if (task/'comm').read_text().strip()==name]
                    assert len(tasks)==1
                    task=tasks[0]
                    proof.append(dict(tid=int(task.name),name=name,start_ticks=int((task/'stat').read_text().rsplit(')',1)[1].split()[19])))
                result.setdefault('owned_kvm_identity',{})[mode]=dict(pid=p.pid,start_ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19]),binding=proof)
                counter=WaitTrace([x['tid'] for x in proof],out/(mode+'-owned-kvm'),max_seconds=60,kvm_faults=True)
            if trace_apf:tracer,trace_log=start_trace(mode,p,layer,offsets)
            connection.sendall(b'READ\n');raw=wait(b'ASB_READ_DONE')
            assert expected.encode() in raw,'guest file hash mismatch'
            if counter:
                assert p.poll() is None and int((proc/'stat').read_text().rsplit(')',1)[1].split()[19])==result['owned_kvm_identity'][mode]['start_ticks']
                for item in proof:
                    task=proc/'task'/str(item['tid'])
                    assert (task/'comm').read_text().strip()==item['name'] and int((task/'stat').read_text().rsplit(')',1)[1].split()[19])==item['start_ticks']
                owned=counter;counter=None
                result.setdefault('owned_kvm_capture',{})[mode]=owned.stop()
                counts=result['owned_kvm_capture'][mode]['kvm_faults']['threads']
                assert sum(x['try_async_get_page'] for x in counts)>0,'TRY functional validation failed'
                assert sum(x['successful_async_setup'] for x in counts)>0,'async setup functional validation failed'
            sample(mode+'-after-read',p,layer,offsets)
            if trace_apf:finish_trace(tracer,trace_log,mode)
            for command in ('MAPREAD','MAPRW','MAPWRITE'):
                connection.sendall(command.encode()+b'\n');raw=wait(b'ASB_MAP_DONE')
                assert f'sum={sum(payload)} '.encode() in raw,'mapped file contents differ'
                sample(mode+'-after-'+command.lower(),p,layer,offsets)
            connection.sendall(b'READ\n');raw=wait(b'ASB_READ_DONE')
            assert expected.encode() in raw,'private mmap write changed file'
            if siblings and mode=='direct':
                held_connection=connection;direct_process=p;connection=None
                continue
            if siblings:
                left=sample('simultaneous-direct',direct_process,layer,offsets)
                right=sample('simultaneous-restored',p,layer,offsets)
                measurement=out/'content-accounting';measurement.mkdir()
                vms=[]
                for name, process in (('direct',direct_process),('restored',p)):
                    proc=Path('/proc')/str(process.pid)
                    vms.append(dict(id=name,pid=process.pid,
                        start_time_ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19]),
                        executable=str(CH),cgroup=(proc/'cgroup').read_text().strip().split('::',1)[1],
                        files=[dict(owner=target,backing=str(layer),offsets=offsets,
                                    immutable_image=True,verify_anonymous_content=True)]))
                manifest=dict(page_size=4096,owned_root=str(out),vms=vms)
                save(measurement/'manifest.json',manifest)
                measured, measured_rows=collect(manifest)
                measured['manifest_sha256']=sha(measurement/'manifest.json')
                save(measurement/'result.json',measured);save(measurement/'pages.json',measured_rows)
                save(measurement/'audit.json',audit_run(measurement))
                result['content_accounting']=measured['content_accounting']
                assert measured['status']=='PASS','content accounting incomplete'
                with open(f'/proc/{direct_process.pid}/mem','rb',buffering=0) as lm, \
                     open(f'/proc/{p.pid}/mem','rb',buffering=0) as rm, layer.open('rb',buffering=0) as backing:
                    page_rows=[]
                    for a,b in zip(left,right):
                        assert a['offset']==b['offset'] and a['entry']&(1<<63) and b['entry']&(1<<63)
                        original_page=os.pread(backing.fileno(),4096,a['offset'])
                        la=os.pread(lm.fileno(),4096,a['address']);rb=os.pread(rm.fileno(),4096,b['address'])
                        for name,data in (('direct',la),('restored',rb)):
                            (out/f"{name}-content-{a['offset']}.bin").write_bytes(data)
                        page_rows.append(dict(offset=a['offset'],same_pfn=(a['entry']&PFN_MASK)==(b['entry']&PFN_MASK),
                            direct_file=bool(a['entry']&(1<<61)),restored_file=bool(b['entry']&(1<<61)),
                            direct_matches_backing=la==original_page,restored_matches_backing=rb==original_page,
                            direct_changed_offsets=[i for i,(x,y) in enumerate(zip(la,original_page)) if x!=y],
                            restored_changed_offsets=[i for i,(x,y) in enumerate(zip(rb,original_page)) if x!=y]))
                for process,rows in ((direct_process,left),(p,right)):
                    with open(f'/proc/{process.pid}/pagemap','rb',buffering=0) as pm:
                        assert all(read_entry(pm.fileno(),r['address'])==r['entry'] for r in rows),'sharing sample changed'
                result['simultaneous_pages']=page_rows
                result['simultaneous_unique_resident_pages']=len({r['entry']&PFN_MASK for r in left+right})
                assert all(r['direct_matches_backing'] and r['restored_matches_backing'] for r in page_rows),'mapped bytes differ from immutable backing'
            connection.sendall(b'DONE\n');wait(b'ASB_DONE');p.wait(timeout=15)
            assert p.returncode==0,'guest shutdown failed'
            connection.close();connection=None
        if held_connection is not None:
            connection=held_connection;held_connection=None
            connection.sendall(b'DONE\n');wait(b'ASB_DONE');direct_process.wait(timeout=15)
            assert direct_process.returncode==0
            connection.close();connection=None
        assert sha(layer)==original,'immutable backing changed'
        result['status']='PASS'
        result['meaning']='diagnostic protocol completed; classifications are observations, not proof of shared benefit'
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    finally:
        if counter:
            try:counter.stop()
            except Exception as exc:result['counter_cleanup_error']=repr(exc)
        if connection is not None:connection.close()
        if held_connection is not None:held_connection.close()
        for tracer in tracers:
            if tracer.poll() is None:
                tracer.send_signal(signal.SIGINT)
                try:tracer.wait(timeout=10)
                except subprocess.TimeoutExpired:stop(tracer)
        for p in processes:stop(p)
        for directory in sockets.iterdir():
            for path in directory.iterdir():path.unlink()
            directory.rmdir()
        sockets.rmdir()
        result['cleanup']='PASS' if all(p.poll() is not None for p in processes+tracers) else 'FAIL'
        if result['cleanup']!='PASS':result['status']='FAIL'
        elif (out/'snapshot/memory-ranges').exists():
            try:archive_memory(out);result['snapshot_archive']='PASS'
            except BaseException as exc:
                result['snapshot_archive']='FAIL';result['archive_error']=repr(exc);result['status']='FAIL'
        save(out/'result.json',result)
    print(f"status={result['status']} result_dir={out}",flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--archive-only':
        out=Path(sys.argv[2]).resolve()
        assert json.loads((out/'result.json').read_text())['cleanup']=='PASS'
        with writer_lock(),(out/'archive-driver.log').open('wb') as log:
            guarded_run([sys.executable,Path(__file__),'--archive-child',out],directory=out,log=log,timeout=240)
        print(f'archive=PASS result_dir={out}');sys.exit(0)
    if len(sys.argv)==3 and sys.argv[1]=='--archive-child':
        archive_memory(Path(sys.argv[2]));sys.exit(0)
    if len(sys.argv)>=3 and sys.argv[1]=='--child':
        extracted=next((Path(arg.split('=',1)[1]).resolve(strict=True) for arg in sys.argv[3:] if arg.startswith('--exp1-layer=')),None)
        sys.exit(main(Path(sys.argv[2]),'--cold-layer' in sys.argv,'--partial-tail' in sys.argv,extracted,'--siblings' in sys.argv,'--trace-apf' in sys.argv,'--owned-kvm-counters' in sys.argv))
    if any(arg not in ('--cold-layer','--partial-tail','--siblings','--trace-apf','--owned-kvm-counters') and not arg.startswith('--exp1-layer=') for arg in sys.argv[1:]):
        raise SystemExit('usage: diagnose_dax_restore.py [--cold-layer] [--partial-tail] [--siblings] [--trace-apf] [--owned-kvm-counters] [--exp1-layer=EXTRACTION_DIR]')
    os.umask(0o077)
    out=ROOT/'.artifacts/dax-restore-diagnostic'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}')
    out.mkdir(parents=True)
    with writer_lock(),(out/'driver.log').open('wb') as log:
        try:guarded_run([sys.executable,Path(__file__),'--child',out,*sys.argv[1:]],directory=out,log=log,timeout=240)
        except Exception:
            print(f'status=FAIL result_dir={out}');sys.exit(1)
    print((out/'driver.log').read_text().splitlines()[-1])
