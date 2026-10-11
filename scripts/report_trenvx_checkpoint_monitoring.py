"""Public metrics from one audited file-state restore; old results are read-only."""
import argparse
import hashlib
import json
import csv
from pathlib import Path

from run_trenvx_checkpoint_monitoring import audit


def report(driver):
    checked=audit(driver)
    saved=json.loads((driver/'result.json').read_text());raw=Path(saved['raw_dir'])
    memory=json.loads((driver/'memory/summary.json').read_text())
    guest=memory['guest_composition'];host=dict(memory['host_memory'])
    host['vmm_boundary_observations']=[{key:row[key] for key in ('label','pss_bytes','scan_elapsed_ns','scope')}
        for row in host.get('vmm_boundary_observations',[])]
    hashes={}
    paths=[driver/'result.json',driver/'code/sha256.json',driver/'memory/summary.json']
    paths+=[raw/name for name in ('result.json','config.json','checkpoint.json','source-before.json',
        'source-after.json','correctness.json','official-task-result.json','host/memory-samples.tsv',
        'replay/guest-memory.tsv','replay/collector-status.json','replay/steps.tsv')]
    paths+=sorted((raw/'replay/collector-segments').glob('*.json'))
    physical=[]
    for p in sorted((raw/'file-physical').iterdir()):
        result=json.loads((p/'result.json').read_text())
        physical.append(dict(label=p.name,status=result['status'],scan_elapsed_ns=result['scan_elapsed_ns'],
                             content_accounting=result['content_accounting']))
        paths+=[p/name for name in ('result.json','pages.json','manifest.json','guest-layer-proof.json')]
    for path in paths:
        hashes[str(path.relative_to(raw if path.is_relative_to(raw) else driver))+'@'+
               ('raw' if path.is_relative_to(raw) else 'driver')]=hashlib.sha256(path.read_bytes()).hexdigest()
    segments=[{k:v for k,v in r.items() if k!='pid'} for r in memory['guest_collector_segments']]
    samples=guest['samples']
    gaps=[(b['timestamp_ns']-a['timestamp_ns'])/1e9 for a,b in zip(samples,samples[1:])]
    return dict(status=checked['status'],third_initial_acceptance='PARTIAL',driver_id=driver.name,run_id=raw.name,
        protocol=saved['protocol'],guest_memory={k:guest[k] for k in ('status','sample_count',
            'observed_peak_nonfree_bytes','composition_at_observed_peak','peak_scope','sampling_mode','time_scope')},
        guest_collector_segments=segments,largest_guest_sample_gap_seconds=max(gaps) if gaps else None,
        gap_scope='guest wall timestamps across distinct VMs; not a checkpoint latency measurement',
        host_memory=host,phase_breakdown=memory['phase_breakdown'],named_file_mapping=memory['named_file_mapping'],
        physical_boundaries=physical,final_checks=memory['final_checks'],cleanup=memory['cleanup'],
        monitoring_impact='functional replay/restore checks passed; matched checkpoint performance control NOT_RUN',
        limitations=['file-state inheritance; no arbitrary process or open-file continuation',
            'guest sampler stops between runner segments; no invented samples during checkpoint/restore',
            'three declared files; not whole-task or unmapped host cache',
            'analysis reads can warm cache; this run is not a performance comparison'],evidence_sha256=hashes)


def plot_host(raw,output):
    """Plot host counters alone; never stack them with guest/PSS/physical totals."""
    if output.exists():raise FileExistsError(output)
    with (raw/'host/memory-samples.tsv').open() as stream:rows=list(csv.DictReader(stream,delimiter='\t'))
    assert rows
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    times=[(int(r['timestamp_ns'])-int(rows[0]['timestamp_ns']))/1e9 for r in rows]
    fig,axes=plt.subplots(2,1,figsize=(9,6),sharex=True,constrained_layout=True)
    axes[0].plot(times,[int(r['cgroup_current_bytes'])/2**20 for r in rows],label='memory.current')
    for name in ('anon','file','kernel'):
        axes[1].plot(times,[int(r[f'cgroup_{name}_bytes'])/2**20 for r in rows],label=name)
    axes[0].set(title='Owned sandbox parent cgroup: '+raw.name,ylabel='Observed memory (MiB)')
    axes[1].set(xlabel='Host seconds since first sample',ylabel='Separate counters (MiB)')
    for axis in axes:axis.legend();axis.grid(alpha=.2)
    fig.savefig(output);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('driver',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--host-plot',type=Path);args=parser.parse_args()
    result=report(args.driver)
    with args.output.open('x') as stream:stream.write(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    if args.host_plot:plot_host(Path(json.loads((args.driver/'result.json').read_text())['raw_dir']),args.host_plot)
    print(f'status={result["status"]} third_initial_acceptance=PARTIAL output={args.output}')
