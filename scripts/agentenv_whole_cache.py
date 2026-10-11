"""AgentENV whole-cache adapter: actual RAM slots and digest-bound layer files."""
import json
import os
from pathlib import Path
import re
import posixpath
import stat
import time

from guest_whole_cache_probe import ram_ranges,selected_flags
from measure_file_physical import PAGE,PFN_MASK,identity,ram_address,read_entry,validate_slots
from trenvx_whole_cache import RECORD,dump,records,scan_backing,same_physical_mapping,file_identity


def slots_for_ram(lines,pid,iomem):
    latest={}
    for line in lines.splitlines():
        row=line.split('\t')
        if len(row)!=6 or not row[0].isdigit() or int(row[0])!=pid:continue
        assert int(row[5])==0 and int(row[3],16)>0,'failed or removed KVM RAM slot'
        latest[int(row[1])]=dict(guest=int(row[2],16),size=int(row[3],16),host=int(row[4],16))
    ranges=ram_ranges(iomem)
    chosen=[slot for slot in latest.values() if any(slot['guest']<b*PAGE and slot['guest']+slot['size']>a*PAGE for a,b in ranges)]
    validate_slots(chosen)
    assert all(any(slot['guest']<=a*PAGE and b*PAGE<=slot['guest']+slot['size'] for slot in chosen) for a,b in ranges)
    return chosen


def bind_vm(directory,pids,cgroup,exe,sid):
    iomem=(directory/'iomem.txt').read_text();marker=json.loads((directory/'marker.json').read_text())
    assert marker['sandbox_id']==sid
    found=[]
    for pid in pids:
        proc=Path('/proc')/str(pid)
        vm=dict(pid=pid,start_time_ticks=int((proc/'stat').read_text().rsplit(')',1)[1].split()[19]),
                executable=exe,cgroup=cgroup,sandbox_id=sid)
        identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
        slots=slots_for_ram((directory/'kvm-slots.tsv').read_text(),pid,iomem)
        with (proc/'mem').open('rb',buffering=0) as stream:
            actual=os.pread(stream.fileno(),len(marker['text']),ram_address(marker['pfn'],slots))
        if actual==marker['text'].encode():found.append((vm,slots))
    assert len(found)==1,'guest marker must identify one owned cold VMM'
    vm,slots=found[0];dump(directory/'vmm-identity.json',vm)
    proof=dict(status='PASS',slots=slots,iomem=iomem,marker_verified=True,
               scope='successful RAM registration selected against actual guest iomem, marker and process identity')
    dump(directory/'ram-attestation.json',proof)
    return vm,proof


def namespace_path(path,base):
    return posixpath.normpath(posixpath.join(base,path)) if path else ''


def normalized_lowers(layers,base='/'):
    defaults=dict(file='',digest='',size=0,gzipIndex='',targetFile='',dir='',repoBlobUrl='',uuid='',targetDigest='')
    result=[dict(defaults,**row) for row in layers]
    for row in result:
        for key in ('file','gzipIndex','targetFile','dir'):row[key]=namespace_path(row[key],base)
    return result


def layer_inventory(proc,daemon,image,*,evidence=None):
    digest=image.rsplit('@sha256:',1)[1];assert re.fullmatch('[0-9a-f]{64}',digest)
    root=proc/'root';config=root/f'workspace/env/image-cache/configs/sha256-{digest}-image.json'
    cfg=json.loads(config.read_text());assert cfg['lowers']
    daemon_fds={}
    for fd in (daemon/'fd').iterdir():
        try:s=fd.stat()
        except OSError:continue
        if stat.S_ISREG(s.st_mode):daemon_fds[(s.st_dev,s.st_ino)]=str(fd)
    vm_fds=[];workdirs=set()
    for fd in (proc/'fd').iterdir():
        try:name=os.readlink(fd);s=fd.stat()
        except OSError:continue
        if stat.S_ISREG(s.st_mode) and re.fullmatch('agentenv_volume_slot_[0-9]+.img',Path(name).name):
            workdirs.add(str(Path(name).parent));vm_fds.append(name)
    assert len(workdirs)==1,'private VM workdir not uniquely attested by volume descriptors'
    workdir=next(iter(workdirs));assert re.fullmatch('/workspace/env/firecracker-work/agentenv-fc-[A-Za-z0-9]+',workdir)
    image_cfg=root/workdir.lstrip('/')/'overlaybd/image.json'
    assert image_cfg.is_file(),'private OverlayBD runtime config unavailable'
    runtime=json.loads(image_cfg.read_text())
    if evidence:
        dump(evidence/'image-config.json',cfg);dump(evidence/'runtime-image-config.json',runtime)
    assert normalized_lowers(runtime['lowers'],workdir+'/overlaybd')==normalized_lowers(cfg['lowers']),\
        'runtime lowers differ from selected image digest'
    files=[]
    for lower in cfg['lowers']:
        name=lower['file'];assert name.startswith('/workspace/env/image-cache/commits/')
        files.append((name,'shared-image-layer'))
    for value in (runtime['upper']['data'],runtime['upper']['index']):
        name=namespace_path(value,workdir+'/overlaybd')
        assert name.startswith(workdir+'/overlaybd/');files.append((name,'private-upper'))
    files += [(name,'private-attached-drive') for name in vm_fds]
    result=[];seen=set()
    for name,role in files:
        path=root/name.lstrip('/');s=path.stat();key=(s.st_dev,s.st_ino)
        assert key not in seen;seen.add(key)
        if role!='private-attached-drive':assert key in daemon_fds,'selected layer not open by the project ublk provider'
        result.append(dict(path=str(path),namespace_path=name,role=role,identity=file_identity(path),
            provider_fd=daemon_fds.get(key),shared=role=='shared-image-layer'))
    return dict(status='PASS',image=image,config_sha256=__import__('hashlib').sha256(config.read_bytes()).hexdigest(),
        runtime_config_sha256=__import__('hashlib').sha256(image_cfg.read_bytes()).hexdigest(),
        workdir=workdir,files=result,scope='digest-selected shared layer files plus private upper and attached drives; excludes unrelated daemon files')


def ram_snapshot(directory,vm,proof,label):
    meta=json.loads((directory/(label+'.json')).read_text())
    pfns=selected_flags((directory/(label+'-flags.bin')).read_bytes(),meta['ram_ranges_pfns'])
    proc=identity(vm,Path('/proc'),Path('/sys/fs/cgroup'));rows=[]
    with (proc/'pagemap').open('rb',buffering=0) as stream:
        for pfn in pfns:rows.append((pfn,read_entry(stream.fileno(),ram_address(pfn,proof['slots'])),0))
        rows=[(pfn,entry,read_entry(stream.fileno(),ram_address(pfn,proof['slots']))) for pfn,entry,_ in rows]
    identity(vm,Path('/proc'),Path('/sys/fs/cgroup'))
    (directory/(label+'-ram.bin')).write_bytes(b''.join(RECORD.pack(*row) for row in rows))


def recount(directory):
    ram={};unknown={};selected_pages={};unstable=set();sets={'shared-image-layer':set(),'private-upper':set(),'private-attached-drive':set()}
    for label in ('before','after'):
        info=json.loads((directory/(label+'.json')).read_text())
        selected=set(selected_flags((directory/(label+'-flags.bin')).read_bytes(),info['ram_ranges_pfns']))
        selected_pages[label]=selected
        rows=records(directory/(label+'-ram.bin'));assert {row[0] for row in rows}==selected
        ram[label]=set();unknown[label]=0
        for _,first,last in rows:
            if not same_physical_mapping(first,last) or not first&(1<<63):unknown[label]+=1;continue
            assert first&PFN_MASK,'resident RAM PFN hidden';ram[label].add(first&PFN_MASK)
    inventory=json.loads((directory/'layer-inventory.json').read_text());scans=json.loads((directory/'backing-scans.json').read_text())
    assert len(inventory['files'])==len(scans)
    mutable_changed=[]
    for index,(item,scan) in enumerate(zip(inventory['files'],scans)):
        before=(directory/f'backing-{index}-before.bin').read_bytes();after=(directory/f'backing-{index}-after.bin').read_bytes()
        rows=records(directory/f'backing-{index}-pages.bin')
        assert len(before)==len(after)==(scan['identity']['st_size']+PAGE-1)//PAGE
        assert {row[0] for row in rows}=={i for i,v in enumerate(before) if v}
        if scan['identity']!=item['identity']:
            assert item['role']=='private-upper','immutable layer identity changed'
            assert all(scan['identity'][k]==item['identity'][k] for k in ('st_dev','st_ino')),'upper file replaced'
            mutable_changed.append(index)
        for offset,first,last in rows:
            assert first&(1<<63) and first&(1<<61) and first&PFN_MASK
            if same_physical_mapping(first,last) and after[offset]:sets[item['role']].add(first&PFN_MASK)
            else:unstable.add(first&PFN_MASK)
    for values in sets.values():values-=unstable
    total=set().union(*sets.values());union=ram['after']|total
    changed=selected_pages['before']!=selected_pages['after']
    return dict(status='PASS',measurement_status='PARTIAL' if unstable or any(unknown.values()) or changed or mutable_changed else 'PASS',page_size=PAGE,
        mutable_files_changed_before_scan=mutable_changed,
        guest_file_lru_before_pages=len(selected_pages['before']),guest_file_lru_after_pages=len(selected_pages['after']),
        guest_classification_added_pages=len(selected_pages['after']-selected_pages['before']),
        guest_classification_removed_pages=len(selected_pages['before']-selected_pages['after']),
        guest_file_cache_before_observed_physical_bytes=len(ram['before'])*PAGE,
        guest_file_cache_end_observed_physical_bytes=len(ram['after'])*PAGE,
        guest_unknown_pages=unknown,shared_layer_cache_stable_physical_bytes=len(sets['shared-image-layer'])*PAGE,
        private_upper_cache_stable_physical_bytes=len(sets['private-upper'])*PAGE,
        attached_drive_cache_stable_physical_bytes=len(sets['private-attached-drive'])*PAGE,
        backing_cache_stable_physical_bytes=len(total)*PAGE,cache_union_end_observed_physical_bytes=len(union)*PAGE,
        duplicate_pages_removed=len(ram['after'])+sum(map(len,sets.values()))-len(union),unstable_unique_pages=len(unstable),
        scope='all actual guest System RAM file-LRU and digest-bound image-layer files; shared layers are not per-VM exclusive bytes',
        whole_task_file_content_physical_bytes=None,
        limitations=['non-atomic; includes filesystem/layer metadata, not all inode-attributed task contents',
            'layer files may encode indexed/compressed content; not equivalent to raw ext4 capacity',
            'userspace provider anonymous/decompression cache and shared service overhead excluded',
            'probe uses tmpfs and touches only initially resident host file pages; analysis is active',
            'do not add guest, VMM PSS, cgroup and these physical totals'])
