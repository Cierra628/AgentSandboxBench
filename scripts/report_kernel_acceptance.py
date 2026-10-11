"""Join independently audited final pairs, targeted checks and valid history."""
import argparse,json
from pathlib import Path
from archive_trenvx_run_data import sha256
from report_whole_analysis_controls import report
from report_v1_closeout import audit as audit_history


def acceptance(agentenv,trenvx,smokes,history_config,closeout):
    groups=[report(agentenv),report(trenvx)]
    assert [r['backend'] for r in groups]==['agentenv','trenvx']
    for group in groups:
        assert group['status']=='PASS' and group['protocol']['version']=='exact-kernel-frozen-v1'
        assert len(group['pairs'])==5
        for pair in group['pairs']:
            assert sorted(r['label'] for r in pair)==['off','on']
            for row in pair:
                assert row['memory']['periodic_guest']['status']=='PASS'
                assert row['memory']['host']['sample_count']>=2 and row['cleanup']['status']=='PASS'
                if row['label']=='on':
                    point=row['final_frozen_kernel_cache']
                    assert point['cache_scope_status']==point['frozen_cache_physical_status']==point['classification_status']=='PASS'
                    assert point['host_unknown_pages']==0
    targeted=json.loads(smokes.read_text());assert targeted['status']=='PASS' and len(targeted['runs'])==4
    assert all(r['task_after_analysis']==r['cleanup']=='PASS' for r in targeted['runs'])
    historical=audit_history(json.loads(history_config.read_text()));assert historical['status']=='PASS'
    resources=json.loads(closeout.read_text());assert resources['status']=='PASS'
    return dict(status='PASS',scope='prepared host; single frozen Exp1 Prettier #14400 trajectory; defined measurement ranges only',
        initial_acceptance={'trajectory':'PASS','checkpoint_restore_continuation':'PASS','memory_timing_and_collection_validation':'PASS'},
        acceptance_3_scope=dict(guest='100ms logical memory during runner; components and observed peaks',
            host='owned VMM PSS/RSS or owned sandbox cgroup; separate service/backing metrics',
            physical='one paused all-System-RAM file-LRU point per analysis-on run, host PFN dedup; owned backing cache reported separately',
            timing='recorded tool/start/checkpoint/restore/analysis/check/cleanup phases; missing phases stay null',
            impact='new final analysis five pairs per backend, common periodic sampling/preparation; old sampling and restore evidence retained',
            semantics='source/patch, expected failures, independent checks, official four assertions and cleanup; old failed interval retained'),
        final_analysis_controls=groups,targeted_checks=targeted,
        history=dict(status=historical['status'],scope=historical['scope'],runs=historical['runs'],evidence_sha256=historical['evidence_sha256']),
        closeout=resources,input_sha256={str(path):sha256(path) for path in (smokes,history_config,closeout)},
        limitations=['Observed peaks are sampled maxima; guest periodic curves end at runner exit.',
            'Paused guest does not freeze host activity; PFN identity rechecks and analysis read costs remain explicit.',
            'File-LRU excludes non-LRU file pages and guest DAX content; backing cache includes metadata and is not all task content.',
            'Guest logical memory, host PSS/cgroup and physical cache are separate scopes; never add them.',
            'Provider anonymous cache, whole-task per-inode attribution, multi-VM sharing benefit and PPT three-task comparison remain later research.',
            'No performance threshold is inferred; matched differences quantify cost, not zero overhead or unchanged cache.',
            'TrEnv-X restoration preserves files in a new VM; arbitrary process state is unsupported.',
            'Prepared-host evidence does not validate clean deployment on a new server or online trajectory capture.'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('agentenv','trenvx','smokes','history-config','closeout','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();value=acceptance(a.agentenv,a.trenvx,a.smokes,a.history_config,a.closeout)
    with a.output.open('x') as f:json.dump(value,f,indent=2);f.write('\n')
    print(f'status=PASS formal_runs=20 result_file={a.output}')
