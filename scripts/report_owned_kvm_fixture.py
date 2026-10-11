"""Recompute owned KVM counters on a private, verified cold-file fixture."""
import argparse
import hashlib
import json
from pathlib import Path
from owned_thread_wait import parse_kvm_faults,combined_wait_script
from diagnose_dax_restore import trace_summary


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report(directory):
    result=json.loads((directory/'result.json').read_text())
    config=json.loads((directory/'config.json').read_text())
    assert result['status']=='PASS' and result['cleanup']=='PASS' and result['snapshot_archive']=='PASS'
    assert config['owned_kvm_counters'] and config['trace_async_page_faults']
    assert config['cache_policy']=='fsync and FADV_DONTNEED on this run owned layer only'
    for name,value in config['source_hashes'].items():assert digest(directory/name)==value
    probe=combined_wait_script((directory/'trace_owned_vcpu_wait.bt').read_text(),(directory/'trace_owned_vcpu_faults.bt').read_text())
    observations=[]
    for mode in ('direct','restore'):
        before=result['observations']['restored-unread' if mode=='restore' else 'direct-boot-unread']
        assert before['backing_cached_pages']==0 and before['nonresident']==4
        identity=result['owned_kvm_identity'][mode]
        assert [x['name'] for x in identity['binding']]==['vcpu0','vcpu1']
        assert identity['pid']>0 and all(x['start_ticks']>0 for x in identity['binding'])
        tids=[x['tid'] for x in identity['binding']]
        raw=directory/(mode+'-owned-kvm')
        capture=json.loads(raw.with_suffix('.json').read_text())
        assert capture==result['owned_kvm_capture'][mode] and capture['status']=='PASS'
        assert {x['tid'] for x in capture['threads']}==set(tids)
        assert raw.with_suffix('.bt').read_text()==probe
        counters=parse_kvm_faults(raw.with_suffix('.stdout').read_text(),tids)
        assert counters==capture['kvm_faults']
        tries=sum(x['try_async_get_page'] for x in counters['threads'])
        success=sum(x['successful_async_setup'] for x in counters['threads'])
        assert tries>0 and success>0 and all(x['pending_setup']==0 for x in counters['threads'])
        reference=trace_summary((directory/(mode+'-apf.log')).read_text())
        assert reference==result['async_page_fault_trace'][mode]
        assert reference['completed_for_target_pages']>0 and 'async_pf_execute' in reference['work_functions']
        observations.append(dict(mode=mode,try_async_get_page=tries,successful_async_setup=success,
                                 target_page_completions=reference['completed_for_target_pages']))
    archive=json.loads((directory/'snapshot-memory-archive.json').read_text())
    assert archive['status']=='PASS' and archive['source_removed']
    assert digest(directory/'snapshot/memory-ranges.gz')==archive['archive_sha256']
    audited=dict(status='PASS',observations=observations,source_sha256=digest(directory/'result.json'),
                 limits=['CH cold-file fixture proves probe functionality, not AgentENV fault frequency',
                         'whole capture counters include other owned-vCPU activity; not exact target-file counts',
                         'no synchronous fault, fault latency or Exp1 timing measurement',
                         'snapshot content roundtrip checked during run; offline audit checks stored archive hash'])
    (directory/'owned-kvm-offline-report.json').write_text(json.dumps(audited,indent=2)+'\n')
    print(f'status=PASS result_dir={directory}')
    return audited


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    report(parser.parse_args().directory)
