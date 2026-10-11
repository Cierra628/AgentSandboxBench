"""Read-only deployment/resource queries and explicit local run retention."""
import ctypes
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def save(path,value):
    with path.open('x') as stream:stream.write(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def resources(config_home,output):
    output=output.resolve();assert not output.exists() and output.is_relative_to(ROOT/'.artifacts')
    env=dict(os.environ,XDG_CONFIG_HOME=str(config_home.resolve()))
    queries={'instances':['list'],'templates':['template','list'],'snapshots':['snapshot','list']}
    allowed={'sandboxID','templateID','buildID','buildStatus','aliases','names','cpuCount','memoryMB','createdAt','updatedAt','startedAt','endAt','snapshotID','imageRef','state'}
    result={'status':'PASS','scope':'read-only native AgentENV resources; local image archives are not snapshots','resources':{}}
    for kind,args in queries.items():
        proc=subprocess.run([str(ROOT/'runtime/bin/aenv'),*args,'--output','json'],env=env,capture_output=True,text=True,timeout=25)
        if proc.returncode:raise RuntimeError(kind+' query failed with exit '+str(proc.returncode))
        rows=json.loads(proc.stdout);assert isinstance(rows,list)
        result['resources'][kind]=[{k:v for k,v in row.items() if k in allowed} for row in rows]
    output.mkdir(parents=True);save(output/'result.json',result)
    return result


def retain(directory,reason):
    directory=directory.resolve()
    if not directory.is_relative_to(ROOT/'.artifacts') or directory==ROOT/'.artifacts':raise ValueError('project run directory required')
    receipt=directory/'result.json'
    if not receipt.is_file() or not reason.strip():raise ValueError('existing run receipt and reason required')
    receipt_value=json.loads(receipt.read_text());paths=[directory]
    if receipt_value.get('storage_result_dir'):
        child=Path(receipt_value['storage_result_dir']).resolve()
        if child.parent!=ROOT/'.artifacts/storage-isolated-service' or not (child/'result.json').is_file():raise ValueError('invalid linked storage run')
        paths.append(child)
    if any((p/'retention.json').exists() for p in paths):raise ValueError('run already has a retention marker')
    value={'retain':True,'reason':reason,'created_ns':time.time_ns(),'result_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest(),
           'protected_directories':list(map(str,paths)),
           'scope':'local run evidence and archives; does not recreate deleted native snapshots or keep a live VM'}
    for p in paths:save(p/'retention.json',value)
    return value


def protected(directory):
    directory=Path(directory).resolve()
    while directory.is_relative_to(ROOT/'.artifacts') and directory!=ROOT/'.artifacts':
        marker=directory/'retention.json'
        if marker.exists() and json.loads(marker.read_text()).get('retain'):return True
        directory=directory.parent
    return False


def deployment(output,offline=False):
    output=output.resolve();assert not output.exists() and output.is_relative_to(ROOT/'.artifacts')
    from platform_tasks import configuration,preflight
    inputs=[]
    for p in sorted((ROOT/'configs').glob('platform-*.json')):
        if json.loads(p.read_text())['version']!=2:continue
        try:
            cfg=configuration(p);preflight(cfg,offline=True)
            inputs.append({'config':str(p.relative_to(ROOT)),'status':'PASS','required_local_paths':{k:cfg[k] for k in ('inputs','reference','cache_backup_directory','frozen_export') if cfg.get(k)}})
        except Exception as error:inputs.append({'config':str(p.relative_to(ROOT)),'status':'FAIL','error':str(error)})
    revisions=json.loads((ROOT/'configs/source-revisions.json').read_text());sources={}
    for name,v in revisions.items():
        proc=subprocess.run(['git','-C',str(ROOT/name),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10)
        actual=proc.stdout.strip() if proc.returncode==0 else None
        sources[name]={'expected':v['commit'],'actual':actual,'match':actual==v['commit']}
        for subdir,expected in v.get('submodules',{}).items():
            sub=subprocess.run(['git','-C',str(ROOT/name/subdir),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10)
            head=sub.stdout.strip() if sub.returncode==0 else None
            sources[name+'/'+subdir]={'expected':expected,'actual':head,'match':head==expected}
    capabilities={}
    for path in ['/dev/kvm','/dev/ublk-control']:
        try:
            fd=os.open(path,os.O_RDWR)
            try:capabilities[path]=fcntl.ioctl(fd,0xAE00,0)==12 if path=='/dev/kvm' else True
            finally:os.close(fd)
        except OSError:capabilities[path]=False
    size=os.sysconf('SC_PAGE_SIZE');capabilities['page_size']=size
    with mmap.mmap(-1,size) as mapped:
        mapped[0]=1;address=ctypes.addressof(ctypes.c_char.from_buffer(mapped))
        with open('/proc/self/pagemap','rb',buffering=0) as stream:stream.seek(address//size*8);entry=struct.unpack('Q',stream.read(8))[0]
        capabilities['host_pfn_visible']=bool(entry&((1<<55)-1))
    capabilities['cgroup_v2']=Path('/sys/fs/cgroup/cgroup.controllers').exists()
    capabilities['host_btf']=Path('/sys/kernel/btf/vmlinux').exists()
    tools={n:shutil.which(n) is not None for n in ['docker','bpftrace','bpftool','losetup','mount','mkfs.ext4','pigz','git']}
    result={'status':'PASS' if all(r['status']=='PASS' for r in inputs) and all(v['match'] for v in sources.values()) and all(tools.values()) and all(v for k,v in capabilities.items() if k!='page_size') and size==4096 else 'PARTIAL',
            'scope':'prepared-host inventory, no installs/service restarts/cache drops; not a new-machine deployment test',
            'sources':sources,'inputs':inputs,'tools':tools,'capabilities':capabilities,'host_kernel':os.uname().release,
            'cli_sha256':hashlib.sha256((ROOT/'runtime/bin/aenv').read_bytes()).hexdigest(),
            'available_disk_bytes':shutil.disk_usage(ROOT).free,'mem_available_bytes':int(next(l for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')).split()[1])*1024,
            'second_machine_deployment':'NOT_VERIFIED','local_input_archives_required':True}
    if not offline:
        from validate_isolated_agentenv import SERVICE,IMAGE
        proc=subprocess.run(['docker','inspect','--format','{{.State.Running}} {{.Image}}',SERVICE],capture_output=True,text=True,timeout=15)
        result['prepared_service_matches']=proc.returncode==0 and proc.stdout.strip()=='true '+IMAGE
        if not result['prepared_service_matches']:result['status']='PARTIAL'
    output.mkdir(parents=True);save(output/'result.json',result);return result
