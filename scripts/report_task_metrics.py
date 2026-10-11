"""Recount short task cache calibration, sampling and costs without live access."""
import argparse
import csv
import json
from pathlib import Path
import re

from run_task_metrics_smoke import audit
from archive_trenvx_run_data import sha256


def report(raw,out):
    raw=raw.resolve();out=out.resolve();assert not out.exists() and not out.is_relative_to(raw)
    hashes=lambda:{str(p.relative_to(raw)):sha256(p) for p in sorted(raw.rglob('*')) if p.is_file()}
    before=hashes();summary=audit(raw)
    point=json.loads((raw/'analysis_on/file-inventory/inventory-before.json').read_text())
    values={name:int(size)*1024 for name,size in re.findall(r'^(\S+):\s+(\d+) kB$',point['meminfo'],re.M)}
    used=values['MemTotal']-values['MemFree']
    names=('AnonPages','Cached','Buffers','Shmem','Slab','SReclaimable','SUnreclaim')
    summary['pre_pause_logical_guest_memory']=dict(timestamp_ns=point['timestamp_ns'],used_bytes=used,
        components_bytes={k:values[k] for k in names},components_percent_of_used={k:100*values[k]/used for k in names},
        scope='guest /proc/meminfo before pause, not the atomic physical point; Shmem overlaps Cached; SReclaimable/SUnreclaim are parts of Slab')
    import matplotlib
    matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='short-task-metrics-v1'
    import matplotlib.pyplot as plt
    out.mkdir(parents=True,exist_ok=False)
    fig,axes=plt.subplots(3,1,figsize=(10,8))
    for case in summary['cases']:
        mode=case['mode']
        with (raw/mode/'host-vmm.tsv').open() as stream:host=list(csv.DictReader(stream,delimiter='\t'))
        origin=int(host[0]['timestamp_ns'])
        for axis,key in [(axes[1],'pss_bytes'),(axes[2],'service_cgroup_current_bytes')]:
            axis.plot([(int(r['timestamp_ns'])-origin)/1e9 for r in host],[int(r[key])/2**20 for r in host],'.-' if len(host)==1 else '-',label=mode)
        with (raw/mode/'rollouts/backbone/read_repo/guest-memory.tsv').open() as stream:guest=list(csv.DictReader(stream,delimiter='\t'))
        if guest:axes[0].plot([(int(r['timestamp_ns'])-origin)/1e9 for r in guest],[(int(r['MemTotal_kib'])-int(r['MemFree_kib']))/1024 for r in guest],label=mode)
    for axis,title in zip(axes,['Guest used: six frozen tools only','Owned VMM PSS: case timeline','Owned service cgroup: case timeline']):
        axis.set_title(title);axis.set_xlabel('Seconds since each case first host sample');axis.set_ylabel('MiB');axis.legend();axis.grid(alpha=.25)
    fig.tight_layout();fig.savefig(out/'memory.svg',metadata={'Date':None});plt.close(fig)
    assert before==hashes()
    summary.update(raw_directory=str(raw),raw_file_count=len(before),raw_hashes_unchanged=True)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    (out/'raw-sha256.json').write_text(json.dumps(before,indent=2)+'\n')
    print('status=PASS result_dir='+str(out))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('raw',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report(args.raw,args.output)
