"""Five ordered off/on pairs for declared TrEnv-X monitoring combinations."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback

from archive_trenvx_run_data import allocated_bytes,sha256
from audit_exp1_metrics import audit_run
from experiment_disk_guard import guarded_run
from report_memory_metrics import report,write_report
from run_exp1_monitor_balanced import estimate
from run_trenvx_file_origins import ROOT,SOURCES as OLD_SOURCES,post_scan_proof
from run_trenvx_fixed_identity import cpu_policies

SOURCES=tuple(dict.fromkeys(OLD_SOURCES+('run_trenvx_combined_controls.py',)))
ORDERS=(('off','on'),('on','off'),('off','on'),('on','off'),('off','on'))
METRICS=('guest_tools_ns','runner_calls_ns','task_and_final_checks_ns',
         'checkpoint_ns','restore_ns','full_run_ns')


def inspect(raw,label,mode):
    load=lambda name:json.loads((raw/name).read_text())
    cfg=load('config.json');result=load('result.json');replay=load('replay-config.json')
    assert result['status']=='PASS' and all(v=='PASS' for v in result['checks'].values())
    assert cfg['monitoring_control']==label and cfg['guest_collector']=='persistent'
    assert cfg['guest_memory_sampling']==(label=='on')
    assert cfg['host_sample_interval_seconds']==(.1 if label=='on' else 0)
    assert cfg['checkpoint_after']==('022' if mode=='checkpoint' else None)
    audit_mode=('checkpoint-022-diagnostic' if label=='on' else 'checkpoint-022') if mode=='checkpoint' else 'full-light' if label=='on' else 'full'
    checked=audit_run(dict(backend='trenvx',mode=audit_mode,raw=str(raw)))
    assert checked['final_checks']['status']==checked['cleanup']['status']=='PASS'
    assert load('official-task-result.json')['parser_results']['num_tests_passed']==4
    assert checked['guest_memory']['sample_count']>=2 if label=='on' else checked['guest_memory']['sample_count']==0
    assert checked['host_memory']['sample_count']>=2 if label=='on' else checked['host_memory']['sample_count']==0
    if mode=='checkpoint':
        assert load('checkpoint.json')['next_step']=='023'
        assert result['checks']['modified_source_preserved']==result['checks']['prefix_state_preserved']=='PASS'
        if label=='on':
            from measure_file_physical import audit_run as audit_physical
            for boundary in ('restored-before-source-read','after-continuation'):
                assert audit_physical(raw/'file-physical'/boundary)['measurement_status']=='PASS'
    else:
        assert post_scan_proof(raw)['status']=='PASS'
        if label=='on':
            from trenvx_whole_cache import recount
            measured=recount(raw/'whole-cache')
            saved=load('whole-cache/result.json')
            assert measured=={k:v for k,v in saved.items() if k!='controller_elapsed_ns'}
    events=[json.loads(line) for line in (raw/'diagnostic-events.jsonl').read_text().splitlines()]
    assert all(row['status']=='PASS' for row in events)
    phases={row['phase']:row for row in events}
    start=phases['tool:replay-prefix' if mode=='checkpoint' else 'tool:replay-all']['start_monotonic_ns']
    end=phases['tool:official-task-tests' if mode=='checkpoint' else 'tool:official-task-post-scan']['end_monotonic_ns']
    steps=list(csv.DictReader((raw/'replay/steps.tsv').open(),delimiter='\t'))
    row=dict(label=label,raw_dir=str(raw),run_id=raw.name,
        guest_tools_ns=sum(int(step['duration_ns']) for step in steps),
        runner_calls_ns=checked['timing_ns']['controller_runner_call_ns'],
        task_and_final_checks_ns=end-start,
        checkpoint_ns=phases['checkpoint']['elapsed_ns'] if mode=='checkpoint' else None,
        restore_ns=phases['restore-to-first-tool-success']['elapsed_ns'] if mode=='checkpoint' else None,
        full_run_ns=result['controller_elapsed_ns'],
        retained_bytes=allocated_bytes(raw),guest_samples=checked['guest_memory']['sample_count'],
        host_samples=checked['host_memory']['sample_count'],
        physical_analysis_ns=sum(e['elapsed_ns'] for e in events if e['phase'].startswith('file-physical:') or e['phase']=='whole-cache-analysis'))
    fixed={key:cfg[key] for key in ('seed_sha256','ch_sha256','kernel_sha256','envd_sha256',
        'binaries','patches','source_revisions','host_kernel','cpu','memory_mib','isolation')}
    fixed.update(action_manifest_sha256=sha256(raw/'replay/actions.tsv'),
        patch_sha256=sha256(raw/'replay/patch.diff'),runner_sha256=replay['runner_sha256'],
        cache_policy=replay['cache_policy'],cwd=replay['cwd'],session_semantics=replay['session_semantics'],
        environment={k:v for k,v in replay['environment'].items() if k!='GUEST_MEMORY_SAMPLING'})
    return row,fixed


def summarize(pairs):
    result={}
    for metric in METRICS:
        values=[]
        for pair in pairs:
            rows={row['label']:row for row in pair}
            if rows['off'][metric] is None:continue
            values.append(100*(rows['on'][metric]/rows['off'][metric]-1))
        if values:result[metric]=estimate(values)
    return result


def audit(out):
    saved=json.loads((out/'result.json').read_text());assert saved['status']=='PASS'
    assert saved['protocol']==json.loads((out/'protocol.json').read_text())
    assert len(saved['pairs'])==5
    for pair,order in zip(saved['pairs'],ORDERS):
        assert [r['label'] for r in pair]==list(order)
        for row in pair:
            measured,fixed=inspect(Path(row['raw_dir']),row['label'],saved['protocol']['mode'])
            assert measured==row and fixed==saved['identity']
    assert summarize(saved['pairs'])==saved['analysis']
    sources=json.loads((out/'code/sha256.json').read_text())
    assert sources=={name:sha256(out/'code'/name) for name in sources}
    assert json.loads((out/'cpu-before.json').read_text())==json.loads((out/'cpu-after.json').read_text())
    return dict(status='PASS',run_count=10,paired_comparison=saved['analysis'],
                limitation='five pairs; order count 3/2; uncontrolled host cache/frequency/load; no PPT threshold')


def main(mode):
    os.umask(0o077)
    out=ROOT/'.artifacts/trenvx-combined-controls'/f'{time.strftime("%Y%m%dT%H%M%SZ",time.gmtime())}-{os.getpid()}-{mode}'
    out.mkdir(parents=True);(out/'code').mkdir()
    protocol=dict(mode=mode,pair_count=5,orders=ORDERS,threshold=None,
        treatment=('persistent guest/host 100ms + boundary PSS + two declared-file analyses at restore/continuation' if mode=='checkpoint' else
                   'persistent guest/host 100ms + owned KVM capture + whole-cache and three-file analysis'),
        shared_condition='same persistent runner, frozen actions, source checks, official tests, resource settings and image input',
        cache_policy='fresh private VM; host cache preserved; no global drop',
        timing_units='nanoseconds; percentages are paired (on/off - 1) * 100',
        retention='raw, hashes, results retained; successful private images removed; failures archived',
        stop_policy='first failure; first pair must pass before remaining four pairs; no retry',
        performance_scope='combined collection impact, not pure periodic-sampler overhead; inventory/cleanup in full_run')
    (out/'protocol.json').write_text(json.dumps(protocol,indent=2)+'\n')
    before=cpu_policies();(out/'cpu-before.json').write_text(json.dumps(before,indent=2)+'\n')
    hashes={name:sha256(ROOT/'scripts'/name) for name in SOURCES}
    for name in SOURCES:shutil.copy2(ROOT/'scripts'/name,out/'code'/name)
    (out/'code/sha256.json').write_text(json.dumps(hashes,indent=2)+'\n')
    result=dict(status='FAIL',protocol=json.loads((out/'protocol.json').read_text()),pairs=[])
    print(f'status=RUNNING result_dir={out}',flush=True)
    try:
        for number,order in enumerate(ORDERS,1):
            pair=[];result['pairs'].append(pair)
            for label in order:
                assert hashes=={name:sha256(ROOT/'scripts'/name) for name in SOURCES},'source changed'
                assert cpu_policies()==before,'CPU policy changed'
                case=out/f'pair-{number}-{label}';case.mkdir()
                free=shutil.disk_usage(out).free;gate=(48 if mode=='checkpoint' else 40.125)*2**30
                (case/'storage-plan.json').write_text(json.dumps(dict(free_bytes=free,required_bytes=gate,remaining_runs=(6-number)*2-len(pair)),indent=2)+'\n')
                assert free>gate,'disk launch budget not met'
                argv=['bash',str(ROOT/'scripts/45-replay-trenvx-exp1.sh'),'--record-phases',
                    '--guest-collector','persistent','--monitoring-control',label,
                    '--reference',str(ROOT/'.artifacts/trenvx-service-smoke/20261001T134743Z-3005780'),
                    '--official-task',str(ROOT/'.artifacts/v1-closeout-inputs/official-task')]
                if mode=='checkpoint':argv+=['--checkpoint-after','022','--discard-verified-checkpoint-images']
                else:argv+=['--fixed-git-identity','--file-origin-diagnostic','--discard-success-images']
                if label=='on':
                    argv+=['--guest-memory-sampling','--guest-sample-interval','0.1','--host-sample-interval','0.1']
                    if mode=='checkpoint':argv+=['--diagnostic-metrics','--file-physical-diagnostic','--file-physical-manifest',str(ROOT/'configs/trenvx-exp1-named-files.json')]
                    else:argv+=['--monitoring-acceptance','--guest-file-physical-diagnostic','--base-file-physical-diagnostic','--whole-cache-diagnostic']
                with (case/'driver.log').open('w') as log:guarded_run(argv,directory=case,log=log,timeout=600)
                paths=re.findall(r'result_dir=(\S+)',(case/'driver.log').read_text());assert len(paths)==1
                raw=Path(paths[0]).resolve(strict=True);assert raw.parent==ROOT/'.artifacts/trenvx-service-smoke'
                row,fixed=inspect(raw,label,mode)
                result.setdefault('identity',fixed);assert fixed==result['identity'],'effective inputs changed'
                limit=(16 if mode=='checkpoint' else 128)*2**20
                assert row['retained_bytes']<=limit,'raw result budget exceeded'
                pair.append(row)
                if label=='on':write_report(report(dict(backend='trenvx',mode='checkpoint-022-diagnostic' if mode=='checkpoint' else 'full-light',raw=str(raw))),case/'memory')
                (out/'progress.json').write_text(json.dumps(result,indent=2)+'\n')
                print(f'status=PASS pair={number}/5 case={label} result_dir={out}',flush=True)
        result.update(status='PASS',analysis=summarize(result['pairs']))
    except BaseException as exc:
        result['error']=repr(exc);(out/'failure.txt').write_text(traceback.format_exc())
    (out/'cpu-after.json').write_text(json.dumps(cpu_policies(),indent=2)+'\n')
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    if result['status']=='PASS':
        try:(out/'offline-audit.json').write_text(json.dumps(audit(out),indent=2)+'\n')
        except BaseException as exc:
            result.update(status='FAIL',offline_audit_error=repr(exc))
            (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
            (out/'audit-failure.txt').write_text(traceback.format_exc())
    print(f'status={result["status"]} result_dir={out}',flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('checkpoint','whole-cache'),default='checkpoint')
    parser.add_argument('--audit',type=Path);args=parser.parse_args()
    if args.audit:print(json.dumps(audit(args.audit)))
    else:raise SystemExit(main(args.mode))
