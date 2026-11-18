"""Recompute the no-pause declared-file cache pairing from saved metadata and PFNs."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from live_cache_metadata import decode, summarize

ROOT = Path(__file__).resolve().parents[1]


def report(source, output):
    source, output = source.resolve(), output.resolve()
    assert source.is_relative_to(ROOT/'.artifacts') and output.is_relative_to(ROOT/'.artifacts')
    assert not output.is_relative_to(source) and not source.is_relative_to(output)
    assert not output.exists(), 'output must be a new directory'
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    result = json.loads((source/'result.json').read_text())
    assert result['status']=='PASS' and all(result['cleanup'].values())
    for name, digest in result['source_sha256'].items():
        assert sha(source/'code'/name)==digest, 'executed source hash differs: '+name
    rows=[]
    for line in (source/'samples.jsonl').read_text().splitlines():
        raw=json.loads(line)
        snapshot=decode(bytes.fromhex(raw['metadata_hex']))
        expected={k:v for k,v in snapshot.items() if k!='file_pfns'}
        expected.update(summarize(snapshot,raw['host_rows']))
        actual=raw['observation']
        assert all(actual[k]==v for k,v in expected.items()), 'saved observation differs from PFN recomputation'
        rows.append(actual)
    assert rows==result['observations'] and len(rows)==result['sample_count']
    phases={}
    for phase in ('empty','half','full','reread','released','complete'):
        selected=[r for r in rows if r['phase']==phase]
        assert selected, 'missing phase: '+phase
        phases[phase]=dict(samples=len(selected),
            guest_file_cache_bytes=sorted({r['declared_file_guest_cache_bytes'] for r in selected}),
            host_confirmed_cache_bytes=sorted({r['host_confirmed_cache_bytes'] for r in selected}))
    summary=dict(status='PASS',source=str(source.relative_to(ROOT)),scope=result['scope'],
        vm_pause=False,whole_guest_physical_cache=False,config=result['config'],guest_kernel=result['guest_kernel'],
        sample_count=len(rows),skipped_publications=result['skipped_publications'],phases=phases,
        guest_heartbeat_first=rows[0]['heartbeat'],guest_heartbeat_last=rows[-1]['heartbeat'],
        guest_cached_bytes_min=min(r['guest_cached_bytes'] for r in rows),
        guest_cached_bytes_max=max(r['guest_cached_bytes'] for r in rows),
        host_scan_mean_ns=statistics.mean(r['host_scan_ns'] for r in rows),
        host_scan_max_ns=max(r['host_scan_ns'] for r in rows),
        host_interval_max_ns=max(b['host_timestamp_ns']-a['host_timestamp_ns'] for a,b in zip(rows,rows[1:])),
        host_mapping_changed_pages=sum(r['host_mapping_changed_pages'] for r in rows),
        host_nonresident_pages=sum(r['host_nonresident_pages'] for r in rows),
        host_swapped_pages=sum(r['host_swapped_pages'] for r in rows),
        preexisting_vmm_count=len(json.loads((source/'firecracker-before.json').read_text())),
        cleanup=result['cleanup'],observed_min_free_bytes=result['observed_min_free_bytes'],
        final_free_bytes=result['final_free_bytes'],limitations=result['limitations'],
        source_sha256=result['source_sha256'],
        metric_definitions=dict(guest_file_cache='mincore resident file pages exported as guest PFNs, bytes; declared file only',
            host_cache='distinct confirmed resident host PFNs for those guest file pages, bytes',
            guest_cached='whole guest /proc/meminfo Cached, KiB converted to bytes; broader than the fixture',
            scan_time='host metadata reads plus two pagemap scans; excludes guest sampling CPU and task overhead'),
        raw_sha256={name:sha(source/name) for name in ('result.json','samples.jsonl','ram-attestation.json','vmm-identity.json','bootstrap.json')})
    output.mkdir(parents=True,exist_ok=False)
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['svg.hashsalt']='agentenv-live-cache-v1'
    import matplotlib.pyplot as plt
    t=[(r['host_timestamp_ns']-rows[0]['host_timestamp_ns'])/1e9 for r in rows]
    fig,axes=plt.subplots(2,1,figsize=(9,6),sharex=True)
    axes[0].step(t,[r['declared_file_guest_cache_bytes']/2**20 for r in rows],where='post',label='Guest declared-file cache',linewidth=3,alpha=.5)
    axes[0].step(t,[r['host_confirmed_cache_bytes']/2**20 for r in rows],where='post',label='Host resident physical memory',linestyle='--')
    axes[0].set(title='AgentENV running guest: declared 1 MiB file, no VM pause',ylabel='Cache (MiB)')
    axes[0].legend()
    axes[1].plot(t,[r['guest_cached_bytes']/2**20 for r in rows],label='Whole guest Cached (different scope)')
    axes[1].set(xlabel='Host elapsed time (s)',ylabel='Whole guest Cached (MiB)')
    axes[1].legend()
    for ax in axes:ax.grid(alpha=.25)
    fig.tight_layout()
    fig.savefig(output/'cache.svg',metadata={'Date':None})
    fig.savefig(output/'cache.png')
    plt.close(fig)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    summary=report(args.source,args.output)
    print(json.dumps(dict(status=summary['status'],samples=summary['sample_count'],output=str(args.output))))


if __name__=='__main__':main()
