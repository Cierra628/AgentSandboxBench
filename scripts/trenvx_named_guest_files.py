"""SDK bridge for an initially resident writable-file subset; raw data stays local."""
import hashlib
import copy
import json
import os
from pathlib import Path
import time
import subprocess

from measure_file_physical import identity,ram_address,read_entry
from named_guest_file_physical import validate_guest,recount
from trenvx_diagnostic_metrics import vmm_snapshot
from trenvx_kvm_capture import attest

CH=Path('/var/lib/trenvx/deps/cloud-hypervisor/target/release/cloud-hypervisor')


def combined_pages(upper_rows,base_rows):
    from measure_file_physical import content_accounting
    rows=[dict(row,kind='file_content') for row in upper_rows]+base_rows
    result=content_accounting(rows)
    result.update(page_size=4096,declared_file_count=len({row['owner'] for row in rows}),
        scope='declared upper/base mappings across one analysis interval; host PFNs deduplicated',
        limitation='non-atomic; not whole-task or unmapped backing cache; does not prove sharing between VMs')
    return result


def run_base_files(out,origins):
    """Reuse the immutable-image probe for the two declared base files."""
    from measure_file_physical import collect
    started=time.monotonic_ns();directory=out/'base-guest-files';directory.mkdir()
    named=out/'named-guest-files'
    vm=json.loads((named/'vmm-identity.json').read_text())
    backing=(out/'data/templates/asb-task-base/image/rootfs.ext4').resolve(strict=True)
    assert backing.is_relative_to(out.resolve())
    # This is the completed, run-owned base image; guest upper writes are separate.
    backing.chmod(backing.stat().st_mode & ~0o222)
    base=[row for row in origins['files'] if row['origin']=='base']
    assert len(base)==2 and all(row['status']=='SOURCE_VERIFIED' for row in base)
    manifest=dict(page_size=4096,owned_root=str(out),vms=[dict(
        id=vm['sandbox_id'],pid=vm['pid'],start_time_ticks=vm['start_time_ticks'],
        executable=vm['executable'],cgroup=vm['cgroup'],api_socket=vm['api_socket'],
        files=[dict(owner=row['path'],backing=str(backing),guest_path=row['path'],
                    immutable_image=True,verify_anonymous_content=True) for row in base])])
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    result,base_rows=collect(manifest)
    result['manifest_sha256']=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest()
    for name,value in [('result.json',result),('pages.json',base_rows)]:
        (directory/name).write_text(json.dumps(value,indent=2)+'\n')
    assert result['status']=='PASS',result.get('first_failure')
    hashes={}
    for index,row in enumerate(base):
        target=directory/f'file-{index}.bin'
        receipt=subprocess.run(['debugfs','-R',f'dump {row["path"]} {target}',str(backing)],
                               capture_output=True,timeout=15)
        (directory/f'dump-{index}.log').write_bytes(receipt.stdout+receipt.stderr)
        assert receipt.returncode==0 and target.is_file()
        hashes[row['path']]=hashlib.sha256(target.read_bytes()).hexdigest()
    (directory/'file-hashes.json').write_text(json.dumps(hashes,indent=2)+'\n')
    upper=json.loads((named/'pages.json').read_text())
    proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'));checks=[]
    with (proc/'pagemap').open('rb',buffering=0) as pm:
        for row in upper+base_rows:
            entry=read_entry(pm.fileno(),row['address']);assert entry==row['entry'],'mapping changed across upper/base scans'
            checks.append(dict(address=row['address'],entry=entry))
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    (directory/'combined-host-rechecks.json').write_text(json.dumps(checks,indent=2)+'\n')
    combined=combined_pages(upper,base_rows)
    combined['controller_elapsed_ns']=time.monotonic_ns()-started
    (directory/'combined.json').write_text(json.dumps(combined,indent=2)+'\n')
    assert combined['status']=='PASS'
    return combined


def probe_manifest(origins,sid):
    assert origins['status']=='PASS'
    result=dict(sandbox_id=sid,page_size=4096,
        files=[row['path'] for row in origins['files'] if row['origin']=='upper'])
    assert len(result['files'])==1,'this smoke covers one declared upper file'
    return result


def with_mapping_source(snapshot,origins):
    """Retain visible identity and bind maps to the verified ordinary upper file."""
    assert origins['status']=='PASS'
    sources={row['path']:row for row in origins['files']}
    result=copy.deepcopy(snapshot)
    for file in result['files']:
        source=sources[file['path']]
        assert source['status']=='SOURCE_VERIFIED' and source['origin']=='upper'
        assert source['proof'][-1]['candidate']==source['candidate']
        assert source['proof'][-1]['identity']==source['underlying']
        assert source['proof'][-1]['kind']=='file'
        assert all(file['identity'][key]==source['visible'][key] for key in file['identity'])
        file['mapping_identity']={key:source['underlying'][key] for key in file['identity']}
        file['mapping_source']=source['candidate']
    return result


async def prepare_ram_capture(sb,out,tool,capture):
    """Fail before replay if this VMM's registrations cannot be proved."""
    cfg=json.loads((out/'config.json').read_text())
    vm=vmm_snapshot(sb.id,cfg['cgroup'],CH)
    vm.update(id=sb.id,executable=str(CH.resolve()),api_socket=f'/tmp/vmm-{sb.id}.socket',phase='before-replay')
    (capture.directory/'vmm-identity.json').write_text(json.dumps(vm,indent=2)+'\n')
    iomem=await tool(sb,'named-iomem','cat /proc/iomem')
    capture.stop();proof=attest(capture.directory,vm,iomem)
    (capture.directory/'ram-attestation.json').write_text(json.dumps(proof,indent=2)+'\n')
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))


async def run(sb,out,tool,capture,origins):
    started=time.monotonic_ns();directory=out/'named-guest-files';directory.mkdir()
    sid=sb.id;guest='/opt/asb-exp1/named-probe'
    manifest=probe_manifest(origins,sid)
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    await sb.filesystem.write_bytes('/opt/asb-exp1/named-manifest.json',(directory/'manifest.json').read_bytes(),timeout=15)
    await tool(sb,'named-start',f'nohup timeout 160 python3 /opt/asb-exp1/guest_named_file_probe.py /opt/asb-exp1/named-manifest.json {guest} >/opt/asb-exp1/named-probe.log 2>&1 </dev/null &')

    async def wait(marker):
        await tool(sb,'named-wait-'+marker,f'for i in $(seq 1 100); do test -f {guest}/{marker} && exit 0; test -f {guest}/failure.txt && break; sleep 0.1; done; cat {guest}/failure.txt /opt/asb-exp1/named-probe.log; exit 1',timeout=15)

    async def download(name,target=None):
        p=directory/(target or name);p.write_bytes(await sb.download_file(guest+'/'+name,timeout=15))
        return json.loads(p.read_text())

    try:
        await wait('ready');before=with_mapping_source(await download('before.json'),origins)
        (directory/'before-with-source.json').write_text(json.dumps(before,indent=2)+'\n')
        files=validate_guest(before,sid)
        for name in ('vmm-identity.json','ram-attestation.json'):
            (directory/name).write_bytes((capture.directory/name).read_bytes())
        vm=json.loads((directory/'vmm-identity.json').read_text())
        proof=json.loads((directory/'ram-attestation.json').read_text())
        assert vm['sandbox_id']==sid and proof['pid']==vm['pid']
        proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'));rows=[];checks=[]
        scan_started=time.monotonic_ns()
        with (proc/'pagemap').open('rb',buffering=0) as pm,(proc/'mem').open('rb',buffering=0) as memory:
            for file in files:
                for page in file['pages']:
                    address=ram_address(page['pfn'],proof['slots']);entry=read_entry(pm.fileno(),address)
                    content=os.pread(memory.fileno(),page['valid_bytes'],address)
                    assert len(content)==page['valid_bytes']
                    digest=hashlib.sha256(content).hexdigest();assert digest==page['sha256'],'guest/host RAM content mismatch'
                    rows.append(dict(vm=sid,owner=file['path'],index=page['index'],offset=page['offset'],
                        valid_bytes=page['valid_bytes'],guest_pfn=page['pfn'],address=address,entry=entry,content_sha256=digest))
            for row in rows:
                entry=read_entry(pm.fileno(),row['address']);assert entry==row['entry'],'host mapping changed'
                checks.append(dict(owner=row['owner'],index=row['index'],address=row['address'],entry=entry))
        scan_elapsed=time.monotonic_ns()-scan_started
        (directory/'pages.json').write_text(json.dumps(rows,indent=2)+'\n')
        await tool(sb,'named-after-request',f'touch {guest}/capture-after');await wait('after-ready')
        after=with_mapping_source(await download('after.json'),origins);last=[]
        (directory/'after-with-source.json').write_text(json.dumps(after,indent=2)+'\n')
        with (proc/'pagemap').open('rb',buffering=0) as pm:
            for row in rows:
                entry=read_entry(pm.fileno(),row['address']);assert entry==row['entry'],'host mapping changed across guest recheck'
                last.append(dict(owner=row['owner'],index=row['index'],address=row['address'],entry=entry))
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        (directory/'host-rechecks.json').write_text(json.dumps(dict(first=checks,after_guest=last),indent=2)+'\n')
        result=recount(before,after,rows,sid)
        await tool(sb,'named-release',f'touch {guest}/release');await wait('done');receipt=await download('result.json','guest-result.json')
        assert receipt['status']=='PASS' and [f['path'] for f in receipt['files']]==manifest['files']
        assert all(a['identity']==b['identity'] for a,b in zip(receipt['files'],files))
        result.update(guest_receipt=receipt,host_read_elapsed_ns=scan_elapsed,guest_before_elapsed_ns=before['elapsed_ns'],
            guest_after_elapsed_ns=after['elapsed_ns'],controller_elapsed_ns=time.monotonic_ns()-started,
            source_scope='one declared upper file; root overlay source verified before/after separately',
            pre_scan_scope='no preceding file-LRU scan; after frozen replay and official task tests')
        if json.loads((out/'config.json').read_text()).get('whole_cache_diagnostic'):
            result['pre_scan_scope']='after whole-cache boundary analysis; that probe maps initially resident backing pages'
        result['limitations']=[line for line in result['limitations'] if not line.startswith(('Residency checked after','No upper/backing-layer'))]
        result['limitations'].extend(['Mapping selected initially resident pages may fault them; file flags checked before/after',
            'Whole-task cache, base/DAX layers, unmapped backing cache and multi-VM sharing not measured'])
        (directory/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        return result
    finally:
        # A failed scan releases only this run's guest probe; preserve available data.
        if not (directory/'guest-result.json').exists():
            try:await tool(sb,'named-error-release',f'touch {guest}/capture-after {guest}/release')
            except Exception as exc:(directory/'release-error.txt').write_text(repr(exc)+'\n')
            if (directory/'before.json').exists():
                try:await wait('done')
                except Exception as exc:(directory/'error-wait.txt').write_text(repr(exc)+'\n')
            missing={}
            for name,target in [('after.json','error-after.json'),('result.json','error-guest-result.json'),
                                ('failure.txt','guest-failure.txt')]:
                try:(directory/target).write_bytes(await sb.download_file(guest+'/'+name,timeout=5))
                except Exception as exc:missing[name]=repr(exc)
            try:(directory/'guest-probe.log').write_bytes(await sb.download_file('/opt/asb-exp1/named-probe.log',timeout=5))
            except Exception as exc:missing['probe.log']=repr(exc)
            (directory/'failure-download.json').write_text(json.dumps(missing,indent=2)+'\n')
