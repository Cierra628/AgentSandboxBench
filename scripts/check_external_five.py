"""Read-only five-task preflight; preserves blockers, never starts or retries a VM."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

from external_five import COUNTS
from external_workload import load
from isolated_storage import available, capacity_budget, GIB

ROOT=Path(__file__).resolve().parents[1]


def check(upstream, output, tasks=None, oci_inputs=None, configs=None):
    output.mkdir(parents=True,exist_ok=False)
    result=dict(status='BLOCKED',timestamp_ns=time.time_ns(),upstream=str(upstream.resolve()),tasks=[],blockers=[],
                measurements='NOT_RUN',cleanup='No service, VM, volume or loop created; existing artifacts untouched')
    budget=capacity_budget(available(ROOT),32*GIB);result['disk']=budget
    if not budget['ready']:result['blockers'].append('available disk must be strictly greater than 58 GiB')
    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    result['mem_available_bytes']=int(mem['MemAvailable'].split()[0])*1024
    if result['mem_available_bytes']<=64*GIB:result['blockers'].append('MemAvailable must exceed 64 GiB')
    result['client']=dict(path=str(ROOT/'runtime/bin/aenv'),exists=(ROOT/'runtime/bin/aenv').is_file())
    if not result['client']['exists']:result['blockers'].append('runtime/bin/aenv missing')
    result['uid']=os.geteuid()
    result['python_version']=sys.version
    result['benchmark_base_revision']=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,timeout=10).strip()
    result['programs']={name:shutil.which(name) for name in ('docker','systemd-run','timeout','gcc','mkfs.ext4','losetup','mount','umount')}
    for name,path in result['programs'].items():
        if not path:result['blockers'].append('missing host program: '+name)
    result['cgroup_v2']=Path('/sys/fs/cgroup/cgroup.controllers').is_file()
    if not result['cgroup_v2']:result['blockers'].append('cgroup v2 unavailable')
    try:
        docker=subprocess.run(['docker','ps','--format','{{.Names}} {{.Image}}'],capture_output=True,text=True,timeout=15)
        result['docker_access']=dict(exit_code=docker.returncode,stderr=docker.stderr.strip(),running_services=docker.stdout.splitlines())
        if docker.returncode:result['blockers'].append('Docker access unavailable')
    except (OSError,subprocess.TimeoutExpired) as error:
        result['docker_access']=dict(error=repr(error));result['blockers'].append('Docker access unavailable')
    result['devices']={p:dict(exists=Path(p).exists(),readable=os.access(p,os.R_OK),writable=os.access(p,os.W_OK)) for p in ('/dev/kvm','/dev/ublk-control')}
    for path,state in result['devices'].items():
        if not state['exists']:result['blockers'].append('host device missing: '+path)
    result['source_service']=os.environ.get('ASB_AGENTENV_SOURCE_SERVICE')
    if not result['source_service']:result['blockers'].append('owned ASB_AGENTENV_SOURCE_SERVICE not specified')
    for task in (tasks or COUNTS):
        config=(configs or {}).get(task,ROOT/'configs'/f'external-{task}-measured.json')
        row=dict(task_id=task,config=str(config),measurement='NOT_RUN')
        try:
            cfg,replay,reference=load(config,upstream)
            row.update(inputs='PASS',run=str(upstream/cfg['run']),actions=len(reference),files=len(replay['files']),
                       image=cfg['image'],memory_mib=cfg['memory_mib'],expected_nonzero=[dict(step=i,exit_code=int(r['exit_code'])) for i,r in enumerate(reference,1) if int(r['exit_code'])],
                       original_validation=replay['validation'])
            layout=(oci_inputs or {}).get(task)
            if layout is None:
                row['native_image_input']='MISSING_LOCAL_OCI'
                result['blockers'].append(task+': supply --oci-input with the primary OCI manifest/config/layers')
            else:
                from external_five_oci import validate
                origin=replay['origin']
                row['native_image_input'],_=validate(layout,origin)
        except Exception as error:
            row['input_error']=repr(error);result['blockers'].append(task+': input/preflight failed')
        result['tasks'].append(row)
    if result['source_service'] and result['docker_access'].get('exit_code')==0:
        p=subprocess.run(['docker','inspect','--format','{{.State.Running}} {{.Image}}',result['source_service']],capture_output=True,text=True,timeout=15)
        result['service_inspection']=dict(exit_code=p.returncode,stdout=p.stdout.strip(),stderr=p.stderr.strip())
        from validate_isolated_agentenv import IMAGE
        if p.returncode or p.stdout.strip()!='true '+IMAGE:result['blockers'].append('source service unavailable or incompatible image')
    if os.geteuid()!=0:result['blockers'].append('real isolated entry requires root; current process is not root')
    result['status']='BLOCKED' if result['blockers'] else 'READY'
    (output/'preflight.json').write_text(json.dumps(result,indent=2)+'\n')
    print('status='+result['status']+' result_dir='+str(output))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('upstream',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--task',choices=tuple(COUNTS));p.add_argument('--config',type=Path);p.add_argument('--oci-input',type=Path)
    a=p.parse_args()
    if (a.config or a.oci_input) and not a.task:p.error('--config/--oci-input requires --task')
    result=check(a.upstream,a.output,[a.task] if a.task else None,{a.task:a.oci_input} if a.oci_input else None,{a.task:a.config} if a.config else None)
    raise SystemExit(0 if result['status']=='READY' else 1)
