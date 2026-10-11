"""Audit an existing upper-file run and write a public summary without addresses."""
import argparse
import hashlib
import json
from pathlib import Path

from run_trenvx_file_origins import audit


def report(driver):
    checked = audit(driver)
    saved = json.loads((driver / 'result.json').read_text())
    raw = Path(saved['run']['raw_dir'])
    outer = json.loads((raw / 'result.json').read_text())
    assert outer['status'] == 'PASS'
    physical = saved['physical']
    evidence = {}
    for base, names in ((driver, ['result.json', 'code/sha256.json']),
                        (raw, ['result.json', 'config.json', 'correctness.json',
                               'official-task-result.json', 'image-cleanup.json',
                               'named-guest-files/result.json',
                               'named-guest-files/pages.json',
                               'named-guest-files/ram-attestation.json'])):
        for name in names:
            path = base / name
            evidence[str(Path(base.parent.name) / base.name / name)] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = dict(
        status=checked['status'], third_initial_acceptance='PARTIAL',
        driver_id=driver.name, run_id=raw.name,
        protocol=saved['protocol'],
        configuration={key: saved['identity'][key] for key in ('cpu', 'memory_mib', 'host_kernel', 'source_revisions')},
        physical={key: physical[key] for key in (
            'status', 'page_size', 'verified_unique_host_pages',
            'verified_file_cache_physical_bytes', 'unknown_resident_unique_pages',
            'classification_conflict_pages', 'scope', 'limitations',
            'host_read_elapsed_ns', 'guest_before_elapsed_ns',
            'guest_after_elapsed_ns', 'controller_elapsed_ns')},
        files=[dict(path=f['path'], bytes=f['identity']['st_size'],
                    initially_resident_pages=f['initially_resident_pages']) for f in physical['files']],
        timing_ns={key: saved['run'][key] for key in (
            'task_window_ns', 'guest_action_sum_ns', 'controller_end_to_end_ns')},
        timing_scope=dict(task_window_ns='controller replay call',
                          guest_action_sum_ns='sum of frozen guest actions',
                          controller_end_to_end_ns='outer launch through cleanup and image retention'),
        checks=outer['checks'], host_cleanup=outer['host_cleanup'],
        observed_min_free_bytes=saved['run']['observed_min_free_bytes'],
        evidence_sha256=evidence,
        unmeasured=['periodic memory curves in this run', 'whole-task physical cache',
                    'base/DAX and upper at the same boundary', 'unmapped host backing cache',
                    'multi-VM sharing', 'checkpoint/restore and fine-scan impact comparison'])
    if saved['protocol'].get('monitoring_acceptance',False):
        memory=json.loads((driver/'memory/summary.json').read_text())
        composition=memory['guest_composition']
        result['guest_memory']={key:composition[key] for key in (
            'status','sample_count','observed_peak_nonfree_bytes','composition_at_observed_peak',
            'peak_scope','sampling_mode','time_scope')}
        result['host_memory']=memory['host_memory']
        result['phase_breakdown']=memory['phase_breakdown']
        result['post_scan_correctness']=saved['post_scan_correctness']
        result['monitoring_impact']='NOT_VALIDATED_BY_THIS_RUN; matched historical controls have different configuration'
        result['unmeasured'].remove('periodic memory curves in this run')
        for base,names in ((driver,['memory/summary.json']),
                           (raw,['post-scan-correctness.json','official-task-post-scan-result.json',
                                 'replay/guest-memory.tsv','host/memory-samples.tsv'])):
            for name in names:
                path=base/name
                evidence[str(Path(base.parent.name)/base.name/name)]=hashlib.sha256(path.read_bytes()).hexdigest()
    if saved['protocol'].get('base_file_physical',False):
        result['combined_physical']=saved['combined_physical']
        base=json.loads((raw/'base-guest-files/result.json').read_text())
        result['base_physical']=dict(status=base['status'],content_accounting=base['content_accounting'],
            scan_elapsed_ns=base['scan_elapsed_ns'],vmm_pss_at_analysis=base['vms'][0]['process_pss'],
            scope='two declared immutable base file data mappings; measured separately from upper')
        result['unmeasured'].remove('base/DAX and upper at the same boundary')
        result['unmeasured'].append('atomic simultaneous page-state snapshot')
        for name in ('manifest.json','pages.json','result.json','combined.json',
                     'combined-host-rechecks.json','file-hashes.json'):
            path=raw/'base-guest-files'/name
            evidence[str(Path(raw.parent.name)/raw.name/'base-guest-files'/name)]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('driver', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = report(args.driver)
    with args.output.open('x') as stream:
        stream.write(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(f"status={result['status']} third_initial_acceptance=PARTIAL output={args.output}")
