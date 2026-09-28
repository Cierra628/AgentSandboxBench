"""Network-free CH/virtio-pmem fixture; not a TrEnv-X lifecycle acceptance test."""
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import traceback
from experiment_disk_guard import guarded_run, writer_lock, RESERVE_BYTES
from measure_file_physical import collect

ROOT = Path(__file__).resolve().parent.parent
CH_REPO = Path('/var/lib/trenvx/deps/cloud-hypervisor')
CH = CH_REPO / 'target/release/cloud-hypervisor'
KERNEL = Path('/var/lib/trenvx/kernels/ch-6.1.134/vmlinux')
COLLECTOR = ROOT / 'IncrementalDAX_moti/motivation/experiments/exp3-RL-fork/physical_cache.py'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def complete_marker(data, marker):
    return re.search(re.escape(marker)+rb'[^\r\n]*\r?\n', data) is not None


def main():
    mib = int(sys.argv[1])
    assert mib in (1, 16) and os.geteuid() == 0 and os.sysconf('SC_PAGE_SIZE') == 4096
    os.umask(0o077)
    run_id = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + f'-{os.getpid()}'
    out = ROOT / '.artifacts/trenvx-dax-primitives' / run_id
    out.mkdir(parents=True)
    procs, sockets, buffers = {}, {}, {}
    selector = selectors.DefaultSelector()
    stage, status = 'preflight', 'FAIL'
    checks = {}

    def run(label, args, timeout=30, **kwargs):
        p = subprocess.run(args, capture_output=True, timeout=timeout, **kwargs)
        (out / f'{label}.stdout').write_bytes(p.stdout)
        (out / f'{label}.stderr').write_bytes(p.stderr)
        (out / f'{label}.rc').write_text(str(p.returncode)+'\n')
        p.check_returncode()
        return p.stdout

    def wait_for(marker, timeout=45):
        deadline=time.monotonic()+timeout
        while not all(complete_marker(buffers.get(name,b''),marker) for name in ('A','B')):
            assert time.monotonic()<deadline, f'guest deadline waiting for {marker!r}'
            for key,_ in selector.select(.2):
                name=key.data
                chunk=key.fileobj.recv(65536)
                assert chunk, f'{name} serial closed before {marker!r}'
                buffers[name]+=chunk
                with (out/f'{name}.serial.log').open('ab') as f:
                    f.write(chunk)
            for name,p in procs.items():
                assert p.poll() is None, f'{name} VMM exited rc={p.returncode}'

    try:
        head=run('ch-revision',['git','-c',f'safe.directory={CH_REPO}','-C',str(CH_REPO),'rev-parse','HEAD']).decode().strip()
        assert head=='9e0056eb750096f3bf8ed491c345ef9241fd527e'
        assert not run('ch-status',['git','-c',f'safe.directory={CH_REPO}','-C',str(CH_REPO),'status','--porcelain']).strip()
        assert shutil.disk_usage(out).free > RESERVE_BYTES + 2 * 1024**3
        sources=[Path(__file__),ROOT/'scripts/trenvx_dax_init.sh',COLLECTOR,
                 ROOT/'scripts/measure_file_physical.py', ROOT/'scripts/experiment_disk_guard.py']
        for p in sources:
            (out/p.name).write_bytes(p.read_bytes())
        config=dict(mib=mib,host_kernel=os.uname().release,ch_commit=head,
                    ch_sha256=sha(CH),kernel_sha256=sha(KERNEL),busybox_sha256=sha(Path('/usr/bin/busybox')),
                    sources={p.name:sha(p) for p in sources},guest_memory_mib=4096,guest_cpu=2,
                    scope='CH virtio-pmem + ext4 DAX + overlay known-file fixture; no TrEnv-X service or checkpoint sealing')
        (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
        checks[stage]='PASS'
        stage='fixture_build'
        initrd=out/'initrd-tree'
        for name in ('bin','dev','proc','sys'):
            (initrd/name).mkdir(parents=True)
        shutil.copy2('/usr/bin/busybox',initrd/'bin/busybox')
        for name in ('sh','mount','mkdir','cat','stty','sha256sum','cut','uname','dd','sync','poweroff'):
            (initrd/'bin'/name).symlink_to('busybox')
        shutil.copy2(ROOT/'scripts/trenvx_dax_init.sh',initrd/'init')
        (initrd/'init').chmod(0o755)
        os.mknod(initrd/'dev/console',stat.S_IFCHR|0o600,os.makedev(5,1))
        names=['.']+[str(p.relative_to(initrd)) for p in sorted(initrd.rglob('*'))]
        archive=run('cpio',['cpio','--null','-o','-H','newc'],input=('\0'.join(names)+'\0').encode(),cwd=initrd)
        (out/'initrd.cpio').write_bytes(archive)
        base_tree=out/'base-tree'
        layer_tree=out/'layer-tree'
        base_tree.mkdir()
        (base_tree/'base-marker').write_text('base\n')
        (layer_tree/'delta').mkdir(parents=True)
        data=b''.join(struct.pack('<Q',i+1)*512 for i in range(mib*256))
        (layer_tree/'delta/known.bin').write_bytes(data)
        expected=hashlib.sha256(data).hexdigest()
        altered=hashlib.sha256(b'X'+data[1:]).hexdigest()
        for name,tree in [('rootfs.ext4',base_tree),('checkpoint-0001.ext4',layer_tree)]:
            path=out/name
            with path.open('wb') as f:
                f.truncate(256*1024**2)
            run('mkfs-'+name,['mkfs.ext4','-q','-F','-b','4096','-d',str(tree),str(path)])
            path.chmod(0o444)
        layer=out/'checkpoint-0001.ext4'
        blocks=[int(x) for x in run('known-blocks',['debugfs','-R','blocks /delta/known.bin',str(layer)]).split()]
        assert len(blocks)==mib*256 and len(set(blocks))==len(blocks)
        lower_hashes={name:sha(out/name) for name in ('rootfs.ext4','checkpoint-0001.ext4')}
        (out/'expected.json').write_text(json.dumps(dict(original_sha256=expected,altered_sha256=altered,
            blocks=blocks,layer_inode=layer.stat().st_ino,lower_image_sha256=lower_hashes),indent=2)+'\n')
        checks[stage]='PASS'
        stage='boot'
        # Short socket names avoid the AF_UNIX path length limit.
        socket_root=Path('/tmp')/f'asb-dax-{os.getpid()}'
        socket_root.mkdir(mode=0o700)
        for name in ('A','B'):
            branch=out/name
            branch.mkdir()
            for filename in lower_hashes:
                os.link(out/filename,branch/filename)
            upper=branch/'writable-rootfs.ext4'
            with upper.open('wb') as f:
                f.truncate(256*1024**2)
            run('mkfs-upper-'+name,['mkfs.ext4','-q','-F','-b','4096',str(upper)])
            sockpath=socket_root/name
            args=[str(CH),'--kernel',str(KERNEL),'--initramfs',str(out/'initrd.cpio'),
                  '--cmdline',f'console=ttyS0 rdinit=/init panic=-1 asb_branch={name}',
                  '--cpus','boot=2','--memory','size=4G','--console','off','--serial',f'socket={sockpath}',
                  '--pmem',f'file={branch}/rootfs.ext4,discard_writes=on,id=base',
                  f'file={branch}/checkpoint-0001.ext4,discard_writes=on,id=checkpoint',
                  '--disk',f'path={upper},readonly=off,id=upper']
            (branch/'argv.json').write_text(json.dumps(args,indent=2))
            with (out/f'{name}.vmm.stdout').open('wb') as stdout,(out/f'{name}.vmm.stderr').open('wb') as stderr:
                procs[name]=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr)
            deadline=time.monotonic()+15
            while not sockpath.exists():
                assert procs[name].poll() is None and time.monotonic()<deadline, f'{name} serial socket unavailable'
                time.sleep(.05)
            sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(str(sockpath))
            sock.setblocking(False)
            sockets[name]=sock
            buffers[name]=b''
            selector.register(sock,selectors.EVENT_READ,name)
        wait_for(b'ASB_READY')
        for name in ('A','B'):
            text=buffers[name].decode(errors='replace')
            assert f'ASB_READY branch={name} hash={expected} kernel=6.1.134' in text
            assert '/dev/pmem0 /base ext4 ro,' in text and '/dev/pmem1 /checkpoint ext4 ro,' in text
            assert all('dax=always' in line for line in text.splitlines() if '/dev/pmem' in line and ' ext4 ' in line)
        checks[stage]='PASS'
        spec=importlib.util.spec_from_file_location('senior_physical',COLLECTOR)
        collector=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)

        def sample(label):
            sets={}
            values={}
            with (out/f'{label}-pages.tsv').open('w') as table:
                table.write('branch\tfile_block\thost_address\tpagemap_entry\n')
                for name,p in procs.items():
                    maps=Path(f'/proc/{p.pid}/maps').read_text()
                    (out/f'{label}-{name}.maps').write_text(maps)
                    (out/f'{label}-{name}.smaps').write_text(Path(f'/proc/{p.pid}/smaps').read_text())
                    regions=[]
                    for line in maps.splitlines():
                        fields=line.split(maxsplit=5)
                        if int(fields[4])==layer.stat().st_ino and len(fields)==6 and fields[5]==str(out/name/'checkpoint-0001.ext4'):
                            start,end=(int(x,16) for x in fields[0].split('-'))
                            regions.append((start,end,int(fields[2],16)))
                    assert regions, 'checkpoint file has no VMM mapping'
                    pfns=set()
                    fd=os.open(f'/proc/{p.pid}/pagemap',os.O_RDONLY)
                    try:
                        for block in blocks:
                            offset=block*4096
                            addresses=[start+offset-file_offset for start,end,file_offset in regions
                                       if file_offset<=offset<file_offset+end-start]
                            assert len(addresses)==1
                            entry=struct.unpack('Q',os.pread(fd,8,addresses[0]//4096*8))[0]
                            pfn=entry&((1<<55)-1)
                            assert entry&(1<<63) and pfn, 'known DAX file page not resident/visible'
                            pfns.add(pfn)
                            table.write(f'{name}\t{block}\t{addresses[0]:x}\t{entry:x}\n')
                    finally:
                        os.close(fd)
                    assert len(pfns)==len(blocks)
                    sets[name]=pfns
                    values[name]=dict(dax_mapping_pss_kib=collector.dax_pss_kib(p.pid),
                                      vmm_pss_kib=collector.vmm_pss_kib(p.pid))
            shared=len(sets['A']&sets['B'])
            assert shared==len(blocks), 'known immutable layer pages were not fully shared'
            result=dict(status='PASS',shared_known_pages=shared,unique_known_pages=len(sets['A']|sets['B']),
                        known_physical_mib=len(sets['A']|sets['B'])/256,service_values=values)
            (out/f'{label}.json').write_text(json.dumps(result,indent=2)+'\n')
            # Independent scoped collector. Only initialized data extents of the
            # immutable lower file are named; upper cache remains NOT_MEASURED.
            vms=[]
            for name,p in procs.items():
                proc=Path('/proc')/str(p.pid)
                guest_text=buffers[name].decode(errors='replace')
                section=re.search(r'ASB_GUEST_'+label.upper()+r'_BEGIN\r?\n(.*?)ASB_GUEST_END',
                                  guest_text,re.S)
                assert section, 'guest memory/layout observation missing'
                memory=section[1]
                assert re.search(r'^ASB_PAGE_KIB 4\r?$',memory,re.M), 'unverified guest page size'
                meminfo={k:int(v)*1024 for k,v in re.findall(r'^(MemTotal|MemAvailable|Cached|Buffers):\s+(\d+) kB',memory,re.M)}
                assert 'MemTotal' in meminfo and 'MemAvailable' in meminfo
                vms.append(dict(id=name,pid=p.pid,executable=str(CH),
                    start_time_ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19]),
                    cgroup=(proc/'cgroup').read_text().strip().split('::',1)[1],
                    guest_memory=dict(status='PASS',meminfo_bytes=meminfo,raw_layout=memory,
                                      scope='guest /proc/meminfo and iomem at serial boundary; not host usage'),
                    files=[dict(backing=str(out/name/'checkpoint-0001.ext4'),owner='/delta/known.bin',
                                guest_path='/delta/known.bin',immutable_image=True)]))
            manifest=dict(page_size=4096,owned_root=str(out),vms=vms)
            measured,raw_pages=collect(manifest)
            measured['upper_file_content']={'status':'NOT_MEASURED',
                'reason':'initramfs fixture has no guest PFN probe; disk block allocation is not physical memory'}
            (out/f'{label}-file-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            (out/f'{label}-file-physical.json').write_text(json.dumps(measured,indent=2)+'\n')
            (out/f'{label}-file-pages.json').write_text(json.dumps(raw_pages)+'\n')
            assert measured['status']=='PASS', measured['first_failure']
            assert measured['file_content']['file_content_unique_pages']==len(blocks)
            return result

        stage='before_write'
        before=sample('before')
        checks[stage]='PASS'
        stage='write_isolation'
        for sock in sockets.values():
            sock.sendall(b'GO\n')
        wait_for(b'ASB_AFTER')
        for name in ('A','B'):
            expected_after=altered if name=='A' else expected
            assert f'ASB_AFTER branch={name} hash={expected_after} lower={expected}' in buffers[name].decode(errors='replace')
        after=sample('after')
        assert all(sha(out/name)==digest for name,digest in lower_hashes.items())
        checks[stage]='PASS'
        stage='guest_shutdown'
        for sock in sockets.values():
            sock.sendall(b'DONE\n')
        for name,p in procs.items():
            p.wait(timeout=15)
            assert p.returncode==0, f'{name} shutdown rc={p.returncode}'
        checks[stage]='PASS'
        status='PASS'
    except Exception:
        checks[stage]='FAIL'
        (out/'failure.txt').write_text(traceback.format_exc())
    finally:
        for name,p in procs.items():
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
        for sock in sockets.values():
            sock.close()
        selector.close()
        socket_root=Path('/tmp')/f'asb-dax-{os.getpid()}'
        if socket_root.exists():
            for path in socket_root.iterdir():
                path.unlink()
            socket_root.rmdir()
        checks['cleanup']='PASS' if all(p.poll() is not None for p in procs.values()) else 'FAIL'
        if checks['cleanup']!='PASS':
            status='FAIL'
        (out/'result.json').write_text(json.dumps(dict(status=status,last_stage=stage,checks=checks),indent=2)+'\n')
        print(f'status={status} stage={stage} result_dir={out}',flush=True)
    return 0 if status=='PASS' else 1


def guarded_main():
    """Guard only this fixture's process group; preserve all existing images."""
    os.umask(0o077)
    ident=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+f'-{os.getpid()}'
    out=ROOT/'.artifacts/trenvx-dax-guard'/ident
    out.mkdir(parents=True)
    try:
        with writer_lock(), (out/'driver.log').open('wb') as log:
            guarded_run([sys.executable,Path(__file__),sys.argv[1] if len(sys.argv)>1 else '1',
                         '--guarded-child'],directory=out,log=log,timeout=180)
        print((out/'driver.log').read_text().splitlines()[-1],flush=True)
        return 0
    except Exception as exc:
        (out/'failure.txt').write_text(repr(exc)+'\n')
        print(f'status=FAIL guard_dir={out}',flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main() if '--guarded-child' in sys.argv else guarded_main())
